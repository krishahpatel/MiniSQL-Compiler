"""
vm.py — Phase 7: Stack-Based Virtual Machine
=============================================
Executes bytecode programs produced by codegen.py against the Storage layer.

ARCHITECTURE
------------
The VM maintains:
  - A value stack (list)
  - A current-row pointer per active scan
  - A result buffer (list of row dicts)
  - Aggregate accumulators (dict)
  - Hash tables for hash joins

For each SELECT, the VM runs in a pipeline:
  1. SCAN sets up a row iterator
  2. NEXT_ROW advances to next row (pushes True) or signals end (pushes False)
  3. EVAL_EXPR evaluates a predicate expression against the current row
  4. JMP_IF_FALSE skips to the loop end if predicate fails
  5. EMIT_ROW adds the current row to the result buffer
  6. After the loop: SORT, LIMIT, DISTINCT post-process the buffer
  7. HALT returns the result buffer

NULL / three-valued logic is handled by the sql_* helpers in storage.py.

The VM is intentionally simple and readable — the goal is to demonstrate
the concept, not maximum performance.
"""

from __future__ import annotations
import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from .codegen import BytecodeProgram, Instruction
from .storage import Storage, Table, coerce, sql_compare, sql_like, _truthy
from . import ast_nodes as ast
from .ir import (
    AggregateNode, DistinctNode, FilterNode, JoinNode, LimitNode, PlanNode,
    ProjectNode, ScanNode, SortNode, _expr_compact,
)


# ---------------------------------------------------------------------------
# VM Execution Result
# ---------------------------------------------------------------------------

class VMResult:
    """The output of executing a bytecode program."""

    def __init__(
        self,
        rows: List[Dict[str, Any]],
        columns: List[str],
        rows_affected: int = 0,
        elapsed_ms: float = 0.0,
        statement_type: str = "SELECT",
    ):
        self.rows          = rows
        self.columns       = columns
        self.rows_affected = rows_affected
        self.elapsed_ms    = elapsed_ms
        self.statement_type = statement_type

    def __repr__(self) -> str:
        return (
            f"VMResult({self.statement_type}, {len(self.rows)} rows, "
            f"{self.elapsed_ms:.2f} ms)"
        )


# ---------------------------------------------------------------------------
# Expression Evaluator
# ---------------------------------------------------------------------------

class ExprEvaluator:
    """
    Evaluates an AST expression against a current row context.
    Used by the VM when it encounters EVAL_EXPR instructions.

    This is separate from code generation so it can handle any expression
    without needing to lower everything to bytecode.
    """

    def __init__(
        self,
        row: Dict[str, Any],
        extra_rows: Optional[Dict[str, Dict]] = None,
        aggregate_values: Optional[Dict[str, Any]] = None,
    ):
        """
        Parameters
        ----------
        row        : current row dict (merged for joins)
        extra_rows : optional per-table rows for qualified column refs
        """
        self.row        = row
        self.extra_rows = extra_rows or {}
        self.aggregate_values = aggregate_values or {}

    def eval(self, expr: ast.Expression) -> Any:
        """Evaluate *expr* and return a Python value (or None for NULL)."""
        if isinstance(expr, ast.Literal):
            return expr.value

        if isinstance(expr, ast.ColumnRef):
            if expr.table:
                # Qualified: try per-table context first
                tbl_row = self.extra_rows.get(expr.table.lower(), self.row)
                val = tbl_row.get(expr.column) if tbl_row else None
                if val is None and self.row:
                    val = self.row.get(f"{expr.table}.{expr.column}")
                if val is None and self.row:
                    val = self.row.get(expr.column)
            else:
                val = self.row.get(expr.column) if self.row else None
                if val is None and self.row:
                    # Try case-insensitive lookup
                    lower = expr.column.lower()
                    for k, v in self.row.items():
                        if k.lower() == lower:
                            val = v
                            break
            return val

        if isinstance(expr, ast.BinaryOp):
            return self._eval_binary(expr)

        if isinstance(expr, ast.UnaryOp):
            return self._eval_unary(expr)

        if isinstance(expr, ast.BetweenExpr):
            val = self.eval(expr.expr)
            lo  = self.eval(expr.lo)
            hi  = self.eval(expr.hi)
            if val is None or lo is None or hi is None:
                return None
            result = lo <= val <= hi
            return (not result) if expr.negated else result

        if isinstance(expr, ast.InListExpr):
            val  = self.eval(expr.expr)
            vals = [self.eval(v) for v in expr.values]
            if val is None:
                return None
            result = any(v == val for v in vals if v is not None)
            return (not result) if expr.negated else result

        if isinstance(expr, ast.LikeExpr):
            val     = self.eval(expr.expr)
            pattern = self.eval(expr.pattern)
            result  = sql_like(
                None if val     is None else str(val),
                None if pattern is None else str(pattern),
            )
            if result is None:
                return None
            return (not result) if expr.negated else result

        if isinstance(expr, ast.IsNullExpr):
            val = self.eval(expr.expr)
            result = (val is None)
            return (not result) if expr.negated else result

        if isinstance(expr, ast.FunctionCall):
            aggregate_key = _expr_compact(expr)
            if aggregate_key in self.aggregate_values:
                return self.aggregate_values[aggregate_key]
            # Aggregates are handled by AGG_UPDATE in the VM
            # If we reach here during row evaluation it means a scalar call
            if expr.arg is not None:
                return self.eval(expr.arg)
            return None

        if isinstance(expr, ast.Star):
            return self.row

        return None

    def _eval_binary(self, expr: ast.BinaryOp) -> Any:
        op = expr.op
        # Short-circuit for AND/OR
        if op == "AND":
            l = self.eval(expr.left)
            if l is False:
                return False
            r = self.eval(expr.right)
            if r is False:
                return False
            if l is None or r is None:
                return None
            return True
        if op == "OR":
            l = self.eval(expr.left)
            if l is True:
                return True
            r = self.eval(expr.right)
            if r is True:
                return True
            if l is None or r is None:
                return None
            return False

        l = self.eval(expr.left)
        r = self.eval(expr.right)

        if op in ("=", "!=", "<>", "<", "<=", ">", ">="):
            cmp_op = "!=" if op == "<>" else op
            return sql_compare(cmp_op, l, r)

        if op == "LIKE":
            return sql_like(
                None if l is None else str(l),
                None if r is None else str(r),
            )

        # Arithmetic
        if l is None or r is None:
            return None
        try:
            if op == "+":  return l + r
            if op == "-":  return l - r
            if op == "*":  return l * r
            if op == "/":  return (l / r) if r != 0 else None
            if op == "%":  return (l % r) if r != 0 else None
        except (TypeError, ZeroDivisionError):
            return None
        return None

    def _eval_unary(self, expr: ast.UnaryOp) -> Any:
        val = self.eval(expr.operand)
        if val is None:
            return None
        if expr.op == "-":
            try:
                return -val
            except TypeError:
                return None
        if expr.op == "NOT":
            if val is None:
                return None
            return not bool(val)
        return None


# ---------------------------------------------------------------------------
# Virtual Machine
# ---------------------------------------------------------------------------

class VM:
    """
    The MiniSQL stack-based virtual machine.

    Each execute() call runs one BytecodeProgram and returns a VMResult.
    The VM shares a Storage instance across calls so tables persist.
    """

    def __init__(self, storage: Storage):
        self.storage = storage
        # Execution state (reset per program)
        self._stack:    List[Any] = []
        self._pc:       int       = 0
        self._iterators: Dict[str, iter] = {}
        self._cur_rows:  Dict[str, Optional[Dict]] = {}
        self._result_buf: List[Dict[str, Any]] = []
        self._result_cols: List[str] = []
        self._agg_state: Dict = {}
        self._hash_tables: Dict[str, Dict] = {}
        self._limit_n: Optional[int] = None
        self._emitted: int = 0
        self._project_spec: Optional[List[Tuple[str, str]]] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(self, prog: BytecodeProgram) -> VMResult:
        """Execute *prog* and return the result."""
        start = time.perf_counter()
        self._reset()

        try:
            self._run(prog)
        except _VMHalt:
            pass

        elapsed = (time.perf_counter() - start) * 1000

        # Determine if this was a DML or SELECT
        opcodes = {i.opcode for i in prog.instructions}
        if "INSERT_ROW" in opcodes:
            stype = "INSERT"
        elif "UPDATE_ROW" in opcodes:
            stype = "UPDATE"
        elif "DELETE_ROW" in opcodes:
            stype = "DELETE"
        else:
            stype = "SELECT"

        return VMResult(
            rows=self._result_buf,
            columns=self._result_cols,
            rows_affected=self._emitted,
            elapsed_ms=elapsed,
            statement_type=stype,
        )

    # ------------------------------------------------------------------
    # Main execution loop
    # ------------------------------------------------------------------

    def _run(self, prog: BytecodeProgram) -> None:
        self._pc = 0
        instructions = prog.instructions
        n = len(instructions)

        while self._pc < n:
            instr = instructions[self._pc]
            self._pc += 1
            self._execute_instr(instr, prog)

    def _execute_instr(self, instr: Instruction, prog: BytecodeProgram) -> None:
        op   = instr.opcode
        args = instr.args

        # ---- Control flow ----
        if op == "HALT":
            raise _VMHalt()

        elif op == "JMP":
            self._pc = args[0]

        elif op == "JMP_IF_FALSE":
            val = self._pop()
            if not _truthy(val):
                self._pc = args[0]

        # ---- Table scanning ----
        elif op == "SCAN":
            table_name = args[0]
            alias      = args[1] if len(args) > 1 else table_name
            tbl = self.storage.get_table(table_name)
            if tbl is None:
                self._iterators[table_name] = iter([])
            else:
                self._iterators[table_name] = iter(tbl.rows)
            self._cur_rows[table_name] = None
            self._cur_rows[alias]      = None

        elif op == "NEXT_ROW":
            table_name = args[0]
            it = self._iterators.get(table_name)
            if it is None:
                self._push(False)
            else:
                try:
                    row = next(it)
                    self._cur_rows[table_name] = row
                    # Also store by alias
                    for key, val in list(self._cur_rows.items()):
                        if val is self._cur_rows.get(table_name, object()):
                            self._cur_rows[key] = row
                    self._push(True)
                except StopIteration:
                    self._cur_rows[table_name] = None
                    self._push(False)

        # ---- Stack operations ----
        elif op == "PUSH_CONST":
            self._push(args[0])

        elif op == "LOAD_COL":
            col_name = args[0]
            row      = self._current_row()
            val      = row.get(col_name) if row else None
            self._push(val)

        # ---- Expression evaluation ----
        elif op == "EVAL_EXPR":
            expr = args[0]
            row  = self._merged_row()
            ev   = ExprEvaluator(row, self._cur_rows)
            val  = ev.eval(expr)
            self._push(val)

        # ---- Row emission ----
        elif op == "EMIT_ROW":
            if self._limit_n is not None and self._emitted >= self._limit_n:
                raise _VMHalt()
            row = self._merged_row()
            if self._project_spec:
                projected = self._apply_projection(row, self._project_spec)
            else:
                projected = dict(row)
            self._result_buf.append(projected)
            self._emitted += 1
            # Update column list
            if not self._result_cols and projected:
                self._result_cols = list(projected.keys())

        elif op == "PROJECT":
            # Set projection spec for next EMIT_ROW
            self._project_spec = args[0]   # list of (expr_str, alias)

        # ---- Join ----
        elif op == "JOIN_EMIT":
            join_type = args[0] if args else "INNER"
            row = self._merged_row()
            if self._project_spec:
                row = self._apply_projection(row, self._project_spec)
            self._result_buf.append(row)
            self._emitted += 1
            if not self._result_cols and row:
                self._result_cols = list(row.keys())

        elif op == "EXEC_PLAN":
            plan_rows = self._execute_plan(args[0])
            self._result_buf.extend(row for row, _ in plan_rows)
            self._emitted += len(plan_rows)
            if not self._result_cols and plan_rows:
                self._result_cols = list(plan_rows[0][0].keys())

        elif op == "HASH_BUILD":
            hash_var   = args[0]
            table_name = args[1]
            key_val    = self._pop()
            row        = dict(self._cur_rows.get(table_name) or {})
            if hash_var not in self._hash_tables:
                self._hash_tables[hash_var] = defaultdict(list)
            self._hash_tables[hash_var][key_val].append(row)

        elif op == "HASH_PROBE":
            hash_var = args[0]
            key_val  = self._pop()
            ht       = self._hash_tables.get(hash_var, {})
            matches  = ht.get(key_val, [])
            self._push(matches)

        # ---- Aggregation ----
        elif op == "AGG_INIT":
            agg_specs = args[0]   # list of (func, col, alias)
            gb_cols   = args[1]   # list of column name strings
            self._agg_state = {
                "specs":   agg_specs,
                "gb_cols": gb_cols,
                "groups":  {},   # group_key -> {alias: accumulator}
            }

        elif op == "AGG_UPDATE":
            gb_cols   = args[0]
            agg_specs = args[1]
            row       = self._merged_row()
            ev        = ExprEvaluator(row, self._cur_rows)

            # Build group key
            group_key = tuple(row.get(c) for c in gb_cols)
            groups    = self._agg_state["groups"]
            if group_key not in groups:
                # Initialise accumulators
                groups[group_key] = {
                    alias: {"func": func, "val": None, "count": 0}
                    for func, col, alias in agg_specs
                }
                # Copy group-by values into the group record
                groups[group_key]["_gb"] = {c: row.get(c) for c in gb_cols}

            for func, col_str, alias in agg_specs:
                acc = groups[group_key][alias]
                # Evaluate the argument column
                if col_str == "*":
                    col_val = 1   # COUNT(*)
                else:
                    col_val = row.get(col_str)
                    if col_val is None:
                        # Try expression evaluation
                        try:
                            col_val = ev.eval(
                                ast.ColumnRef(column=col_str)
                            )
                        except Exception:
                            col_val = None

                if col_val is None and func != "COUNT":
                    continue   # skip NULLs for SUM/AVG/MIN/MAX

                acc["count"] += 1
                if func == "COUNT":
                    acc["val"] = (acc["val"] or 0) + 1
                elif func == "SUM":
                    acc["val"] = (acc["val"] or 0) + col_val
                elif func == "AVG":
                    acc["val"] = (acc["val"] or 0) + col_val
                elif func == "MIN":
                    acc["val"] = col_val if acc["val"] is None else min(acc["val"], col_val)
                elif func == "MAX":
                    acc["val"] = col_val if acc["val"] is None else max(acc["val"], col_val)

        elif op == "AGG_HAVING":
            # Post-process: filter groups by HAVING expression
            having_str = args[0]
            # We'll do this in AGG_EMIT

        elif op == "AGG_EMIT":
            # Finalise aggregates and push rows into result buffer
            groups    = self._agg_state.get("groups", {})
            agg_specs = self._agg_state.get("specs", [])
            gb_cols   = self._agg_state.get("gb_cols", [])

            for group_key, accumulators in groups.items():
                row: Dict[str, Any] = {}
                # Group-by columns
                gb_vals = accumulators.get("_gb", {})
                row.update(gb_vals)
                # Aggregate values
                for func, col_str, alias in agg_specs:
                    acc = accumulators.get(alias, {})
                    val = acc.get("val")
                    if func == "AVG" and acc.get("count", 0) > 0:
                        val = val / acc["count"] if val is not None else None
                    row[alias] = val
                self._result_buf.append(row)
                self._emitted += 1

            if not self._result_cols and self._result_buf:
                self._result_cols = list(self._result_buf[0].keys())

        # ---- Post-processing ----
        elif op == "SORT":
            sort_keys = args[0]   # list of (col_str, direction)
            def sort_key(row):
                vals = []
                for col_str, direction in sort_keys:
                    v = row.get(col_str)
                    # Handle None for sorting (put NULLs last)
                    if v is None:
                        v = ("", float("inf"))[0]
                    vals.append((v, direction == "DESC"))
                return vals

            def multi_sort_key(row):
                keys = []
                for col_str, direction in sort_keys:
                    v = row.get(col_str)
                    null_sort = 1 if v is None else 0
                    if direction == "DESC":
                        try:
                            keys.append((null_sort, _NullsLastDesc(v)))
                        except Exception:
                            keys.append((null_sort, v))
                    else:
                        keys.append((null_sort, v if v is not None else ""))
                return keys

            try:
                self._result_buf.sort(key=multi_sort_key)
            except TypeError:
                pass   # Graceful degradation for non-comparable types

        elif op == "LIMIT":
            n = args[0]
            self._result_buf = self._result_buf[:n]
            self._emitted    = len(self._result_buf)

        elif op == "DISTINCT":
            seen: List[tuple] = []
            unique: List[Dict] = []
            for row in self._result_buf:
                key = tuple(sorted(row.items()))
                if key not in seen:
                    seen.append(key)
                    unique.append(row)
            self._result_buf = unique
            self._emitted    = len(unique)

        # ---- DML ----
        elif op == "INSERT_ROW":
            table_name = args[0]
            col_names  = args[1]   # list of column names
            expr_strs  = args[2]   # list of compact expr strings
            value_rows = args[3]   # list of list of ast.Expression

            tbl = self.storage.get_table(table_name)
            if tbl is None:
                return

            schema_cols = tbl.schema.column_names
            for row_exprs in value_rows:
                row_dict: Dict[str, Any] = {}
                targets = col_names if col_names else schema_cols
                ev = ExprEvaluator({})
                for cname, val_expr in zip(targets, row_exprs):
                    row_dict[cname] = ev.eval(val_expr)
                tbl.insert(row_dict)
                self._emitted += 1
            self.storage.update_row_counts()

        elif op == "UPDATE_ROW":
            table_name  = args[0]
            assign_list = args[1]   # list of (col, expr_str, ast.Expression)
            predicate   = args[2]   # ast.Expression or None

            tbl = self.storage.get_table(table_name)
            if tbl is None:
                return

            count = 0
            for row in tbl.rows:
                ev   = ExprEvaluator(row)
                keep = True
                if predicate is not None:
                    keep = _truthy(ev.eval(predicate))
                if keep:
                    for col, _, val_expr in assign_list:
                        row[col] = ev.eval(val_expr)
                    count += 1
            self._emitted = count
            self.storage.update_row_counts()

        elif op == "DELETE_ROW":
            table_name = args[0]
            predicate  = args[1]   # ast.Expression or None

            tbl = self.storage.get_table(table_name)
            if tbl is None:
                return

            if predicate is None:
                self._emitted = len(tbl.rows)
                tbl.rows.clear()
            else:
                before = len(tbl.rows)
                tbl.rows = [
                    r for r in tbl.rows
                    if not _truthy(ExprEvaluator(r).eval(predicate))
                ]
                self._emitted = before - len(tbl.rows)
            self.storage.update_row_counts()

        # Unknown opcode: skip
        # else: raise VMError(f"Unknown opcode {op}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _execute_plan(
        self, plan: PlanNode
    ) -> List[Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]]:
        if isinstance(plan, ScanNode):
            table = self.storage.get_table(plan.table)
            if table is None:
                return []
            alias = (plan.alias or plan.table).lower()
            return [
                (dict(row), {alias: row})
                for row in table.scan()
            ]

        if isinstance(plan, FilterNode):
            return [
                (row, rows_by_alias)
                for row, rows_by_alias in self._execute_plan(plan.child)
                if _truthy(ExprEvaluator(row, rows_by_alias).eval(plan.predicate))
            ]

        if isinstance(plan, ProjectNode):
            specs = [
                (
                    item.expr,
                    _expr_compact(item.expr),
                    item.alias or _expr_compact(item.expr),
                )
                for item in plan.columns
            ]
            return [
                (self._apply_projection(row, specs), rows_by_alias)
                for row, rows_by_alias in self._execute_plan(plan.child)
            ]

        if isinstance(plan, JoinNode):
            if plan.join_type not in {"INNER", "CROSS"}:
                raise ValueError(
                    f"Plan execution does not support {plan.join_type} joins"
                )
            result = []
            left_rows = self._execute_plan(plan.left)
            right_rows = self._execute_plan(plan.right)
            for left_row, left_aliases in left_rows:
                for right_row, right_aliases in right_rows:
                    merged = dict(left_row)
                    merged.update(right_row)
                    aliases = dict(left_aliases)
                    aliases.update(right_aliases)
                    if plan.condition is None or _truthy(
                        ExprEvaluator(merged, aliases).eval(plan.condition)
                    ):
                        result.append((merged, aliases))
            return result

        if isinstance(plan, SortNode):
            result = self._execute_plan(plan.child)
            for order in reversed(plan.order_by):
                def sort_key(entry):
                    value = ExprEvaluator(entry[0], entry[1]).eval(order.expr)
                    if value is None:
                        return (1, "")
                    if order.direction == "DESC":
                        return (0, _NullsLastDesc(value))
                    return (0, value)

                result.sort(key=sort_key)
            return result

        if isinstance(plan, LimitNode):
            return self._execute_plan(plan.child)[:plan.n]

        if isinstance(plan, DistinctNode):
            result = []
            seen = set()
            for row, aliases in self._execute_plan(plan.child):
                key = tuple(sorted(row.items()))
                if key not in seen:
                    seen.add(key)
                    result.append((row, aliases))
            return result

        if isinstance(plan, AggregateNode):
            groups: Dict[
                Tuple[Any, ...],
                List[Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]],
            ] = {}
            source_rows = self._execute_plan(plan.child)
            for row, aliases in source_rows:
                evaluator = ExprEvaluator(row, aliases)
                key = tuple(evaluator.eval(expr) for expr in plan.group_by)
                groups.setdefault(key, []).append((row, aliases))
            if not plan.group_by and not groups:
                groups[()] = []

            result = []
            for key, grouped_rows in groups.items():
                rows = [row for row, _ in grouped_rows]
                aggregate_row: Dict[str, Any] = {}
                if rows:
                    for expr, value in zip(plan.group_by, key):
                        aggregate_row[_expr_compact(expr)] = value
                        if isinstance(expr, ast.ColumnRef):
                            aggregate_row[expr.column] = value
                            if expr.table:
                                aggregate_row[f"{expr.table}.{expr.column}"] = value
                for item in plan.aggregates:
                    if not isinstance(item.expr, ast.FunctionCall):
                        continue
                    func = item.expr.func.upper()
                    values: List[Any] = [
                        ExprEvaluator(row, aliases).eval(item.expr.arg)
                        for row, aliases in grouped_rows
                    ] if item.expr.arg is not None else [1 for _ in rows]
                    non_null: List[Any] = [
                        value for value in values if value is not None
                    ]
                    if func == "COUNT":
                        value = len(non_null)
                    elif not non_null:
                        value = None
                    elif func == "SUM":
                        value = sum(non_null)
                    elif func == "AVG":
                        value = sum(non_null) / len(non_null)
                    elif func == "MIN":
                        value = min(non_null)
                    elif func == "MAX":
                        value = max(non_null)
                    else:
                        raise ValueError(f"Unsupported aggregate function: {func}")
                    expr_text = _expr_compact(item.expr)
                    aggregate_row[expr_text] = value
                    aggregate_row[item.alias or expr_text] = value
                aggregate_values = {
                    _expr_compact(item.expr): aggregate_row[
                        item.alias or _expr_compact(item.expr)
                    ]
                    for item in plan.aggregates
                    if isinstance(item.expr, ast.FunctionCall)
                }
                if plan.having and not _truthy(
                    ExprEvaluator(
                        aggregate_row,
                        aggregate_values=aggregate_values,
                    ).eval(plan.having)
                ):
                    continue
                result.append((aggregate_row, {}))
            return result

        raise TypeError(f"Unsupported plan node: {type(plan).__name__}")

    def _reset(self) -> None:
        self._stack     = []
        self._pc        = 0
        self._iterators = {}
        self._cur_rows  = {}
        self._result_buf = []
        self._result_cols = []
        self._agg_state  = {}
        self._hash_tables = {}
        self._limit_n   = None
        self._emitted   = 0
        self._project_spec = None

    def _push(self, val: Any) -> None:
        self._stack.append(val)

    def _pop(self) -> Any:
        return self._stack.pop() if self._stack else None

    def _peek(self) -> Any:
        return self._stack[-1] if self._stack else None

    def _current_row(self) -> Dict[str, Any]:
        """Return the most recently set current row."""
        for key in reversed(list(self._cur_rows.keys())):
            r = self._cur_rows[key]
            if r is not None:
                return r
        return {}

    def _merged_row(self) -> Dict[str, Any]:
        """Merge all current rows (for joins)."""
        merged: Dict[str, Any] = {}
        for row in self._cur_rows.values():
            if row is not None:
                merged.update(row)
        return merged

    def _apply_projection(
        self,
        row: Dict[str, Any],
        spec: List[Any],
    ) -> Dict[str, Any]:
        """Apply a projection specification to a row."""
        ev = ExprEvaluator(row, self._cur_rows)
        result: Dict[str, Any] = {}
        for item in spec:
            if len(item) == 3:
                item_expr, expr_str, alias = item
            else:
                expr_str, alias = item
                item_expr = None

            if isinstance(item_expr, ast.Star):
                result.update(row)
                continue

            if isinstance(item_expr, ast.FunctionCall) and alias in row:
                val = row[alias]
            elif item_expr is not None:
                val = ev.eval(item_expr)
                if val is None and expr_str in row:
                    val = row[expr_str]
            else:
                val = row.get(expr_str)
                if val is None and expr_str != "NULL":
                    lower = expr_str.lower()
                    for k, v in row.items():
                        if k.lower() == lower:
                            val = v
                            break
            result[alias] = val
        return result


# ---------------------------------------------------------------------------
# Sentinel exception
# ---------------------------------------------------------------------------

class _VMHalt(Exception):
    """Raised by the HALT instruction to stop VM execution."""
    pass


# ---------------------------------------------------------------------------
# Sorting helper for DESC with NULLs last
# ---------------------------------------------------------------------------

class _NullsLastDesc:
    """Wrapper to sort None last and reverse non-None values for DESC."""

    def __init__(self, val):
        self.val = val

    def __lt__(self, other):
        if self.val is None:
            return False
        if other.val is None:
            return True
        try:
            return self.val > other.val   # reversed for DESC
        except TypeError:
            return False

    def __eq__(self, other):
        return self.val == other.val


# ---------------------------------------------------------------------------
# Convenience runner
# ---------------------------------------------------------------------------

def run_program(prog: BytecodeProgram, storage: Storage) -> VMResult:
    """Create a VM, execute *prog*, and return the result."""
    return VM(storage).execute(prog)
