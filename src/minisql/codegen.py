"""
codegen.py — Phase 6: Bytecode Code Generation
===============================================
Translates the optimised logical query plan into a flat bytecode program
for the MiniSQL Virtual Machine (vm.py).

INSTRUCTION SET (see also vm.py)
----------------------------------
  SCAN table             -- push a row-iterator for the named table
  LOAD_COL col           -- push current row's column value onto stack
  PUSH_CONST val         -- push a constant
  ADD / SUB / MUL / DIV / MOD  -- arithmetic (pop 2, push result)
  NEG                    -- negate top of stack
  CMP_EQ / CMP_NEQ / CMP_LT / CMP_LE / CMP_GT / CMP_GE
  AND / OR / NOT         -- boolean ops (NULL-safe)
  IS_NULL / IS_NOT_NULL  -- NULL checks
  LIKE / NOT_LIKE        -- pattern matching
  JMP_IF_FALSE offset    -- conditional jump (0-based absolute address)
  JMP          offset    -- unconditional jump
  EMIT_ROW               -- emit the current row to the result set
  HASH_BUILD  table_var  -- build a hash table from right side of join
  HASH_PROBE  table_var  -- probe hash table, push matching rows
  AGG_INIT  agg_spec     -- initialise aggregate accumulators
  AGG_UPDATE             -- update accumulator with stack top
  AGG_EMIT               -- finalise and push aggregate result
  SORT  cols dirs        -- sort the result buffer
  LIMIT n                -- stop after n rows emitted
  DISTINCT               -- deduplicate result buffer
  INSERT_ROW  table      -- insert TOS dict into table
  UPDATE_ROW  table      -- apply TOS updates matching predicate
  DELETE_ROW  table      -- delete rows matching predicate
  HALT                   -- end of program

CODE GENERATION STRATEGY
--------------------------
For a SELECT query with plan:
  Limit(Sort(Project(Filter(Scan(t)))))

We generate a "pipeline" loop:
  SCAN t
  LOOP_START:                  <-- address saved
    LOAD_COL col1
    ... emit condition bytecode ...
    JMP_IF_FALSE LOOP_END
    LOAD_COL col2              <-- projected columns
    EMIT_ROW
    JMP LOOP_START
  LOOP_END:
  SORT ...
  LIMIT n
  HALT
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import ast_nodes as ast
from .ir import (
    PlanNode, ScanNode, FilterNode, ProjectNode, JoinNode,
    AggregateNode, SortNode, LimitNode, DistinctNode,
    InsertNode, UpdateNode, DeleteNode, _expr_compact,
)


# ---------------------------------------------------------------------------
# Bytecode Instruction
# ---------------------------------------------------------------------------

@dataclass
class Instruction:
    """
    A single VM instruction.

    opcode : string mnemonic
    args   : list of arguments (constants, column names, addresses)
    """
    opcode: str
    args:   List[Any] = field(default_factory=list)

    def __str__(self) -> str:
        if self.args:
            arg_str = ", ".join(repr(a) for a in self.args)
            return f"{self.opcode:<15} {arg_str}"
        return self.opcode


# ---------------------------------------------------------------------------
# Bytecode Program
# ---------------------------------------------------------------------------

@dataclass
class BytecodeProgram:
    """A list of Instructions plus metadata."""
    instructions: List[Instruction] = field(default_factory=list)
    # Human-readable label map (addr -> label string)
    labels: Dict[int, str] = field(default_factory=dict)

    def emit(self, opcode: str, *args) -> int:
        """Append an instruction and return its address (0-based index)."""
        addr = len(self.instructions)
        self.instructions.append(Instruction(opcode, list(args)))
        return addr

    def patch(self, addr: int, arg_index: int, value: Any) -> None:
        """Back-patch an argument in a previously emitted instruction."""
        self.instructions[addr].args[arg_index] = value

    def disassemble(self) -> str:
        """Return a human-readable disassembly listing."""
        lines = ["DISASSEMBLY", "=" * 50]
        for i, instr in enumerate(self.instructions):
            lbl = self.labels.get(i, "")
            prefix = f"{i:>4}  {lbl:<10}"
            lines.append(f"{prefix}  {instr}")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self.instructions)


# ---------------------------------------------------------------------------
# Code Generator
# ---------------------------------------------------------------------------

class CodeGenerator:
    """
    Translates a logical plan tree into a BytecodeProgram.

    Strategy: each plan node type maps to a specific code-generation
    method.  We use a "produce/consume" pipeline model where inner plan
    nodes generate code to iterate rows, and outer nodes wrap them.
    """

    def __init__(self):
        self.prog = BytecodeProgram()
        self._label_counter = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, plan: PlanNode) -> BytecodeProgram:
        """Generate bytecode for a single plan tree."""
        if _contains_join(plan):
            # Keep nested joins and their filters intact for VM execution.
            self.prog.emit("EXEC_PLAN", plan)
            self.prog.emit("HALT")
            return self.prog

        self._gen_plan(plan)
        self.prog.emit("HALT")
        return self.prog

    # ------------------------------------------------------------------
    # Plan node dispatch
    # ------------------------------------------------------------------

    def _gen_plan(self, plan: PlanNode) -> None:
        if isinstance(plan, ScanNode):
            self._gen_scan(plan)
        elif isinstance(plan, FilterNode):
            self._gen_filter(plan)
        elif isinstance(plan, ProjectNode):
            self._gen_project(plan)
        elif isinstance(plan, JoinNode):
            self._gen_join(plan)
        elif isinstance(plan, AggregateNode):
            self._gen_aggregate(plan)
        elif isinstance(plan, SortNode):
            self._gen_sort(plan)
        elif isinstance(plan, LimitNode):
            self._gen_limit(plan)
        elif isinstance(plan, DistinctNode):
            self._gen_distinct(plan)
        elif isinstance(plan, InsertNode):
            self._gen_insert(plan)
        elif isinstance(plan, UpdateNode):
            self._gen_update(plan)
        elif isinstance(plan, DeleteNode):
            self._gen_delete(plan)
        else:
            # Fallback for _SingleRowNode etc.
            self.prog.emit("PUSH_CONST", {})
            self.prog.emit("EMIT_ROW")

    # ------------------------------------------------------------------
    # SCAN
    # ------------------------------------------------------------------

    def _gen_scan(self, plan: ScanNode) -> None:
        """
        Generate a full table scan loop:
          SCAN table
          LOOP_START: JMP_IF_FALSE LOOP_END
          ... (inner code emitted by caller wrappers) ...
          JMP LOOP_START
          LOOP_END:
        """
        alias = plan.alias or plan.table
        self.prog.emit("SCAN", plan.table, alias)
        # The VM will set up an iterator; inner code goes between NEXT_ROW checks
        # We emit NEXT_ROW which advances iterator and pushes True/False
        loop_start = self.prog.emit("NEXT_ROW", plan.table)
        jmp_end    = self.prog.emit("JMP_IF_FALSE", -1)   # patched later
        self.prog.emit("EMIT_ROW")
        self.prog.emit("JMP", loop_start)
        end_addr = len(self.prog)
        self.prog.patch(jmp_end, 0, end_addr)
        self.prog.labels[loop_start] = "LOOP_START"
        self.prog.labels[end_addr]   = "LOOP_END"

    # ------------------------------------------------------------------
    # FILTER (WHERE)
    # ------------------------------------------------------------------

    def _gen_filter(self, plan: FilterNode) -> None:
        """
        Wrap the child's scan loop with a filter:
          SCAN child
          LOOP:
            NEXT_ROW
            JMP_IF_FALSE END
            <evaluate predicate>
            JMP_IF_FALSE LOOP    -- skip row if predicate fails
            EMIT_ROW
            JMP LOOP
          END:
        """
        # Detect the innermost scan
        scan = _find_scan(plan.child)
        alias = (scan.alias or scan.table) if scan else "?"
        table = scan.table if scan else "?"

        self.prog.emit("SCAN", table, alias)
        loop_start = self.prog.emit("NEXT_ROW", table)
        jmp_end    = self.prog.emit("JMP_IF_FALSE", -1)

        # Evaluate predicate
        self._gen_expr(plan.predicate)
        jmp_skip = self.prog.emit("JMP_IF_FALSE", -1)

        # Inner content (nested plan, e.g. projection)
        self._gen_inner(plan.child, is_scan=True)

        self.prog.emit("JMP", loop_start)
        end_addr = len(self.prog)
        self.prog.patch(jmp_end,  0, end_addr)
        self.prog.patch(jmp_skip, 0, loop_start)
        self.prog.labels[loop_start] = "FILTER_LOOP"
        self.prog.labels[end_addr]   = "FILTER_END"

    def _gen_inner(self, plan: PlanNode, is_scan: bool = False) -> None:
        """
        Generate the "emit" portion of a plan without generating the
        outer loop (the loop is already handled by the parent filter/scan).
        """
        if isinstance(plan, ScanNode) and is_scan:
            self.prog.emit("EMIT_ROW")
        elif isinstance(plan, ProjectNode):
            self._gen_project_emit(plan)
        else:
            self._gen_plan(plan)

    # ------------------------------------------------------------------
    # PROJECT
    # ------------------------------------------------------------------

    def _gen_project(self, plan: ProjectNode) -> None:
        col_specs = []
        for item in plan.columns:
            expr_str = _expr_compact(item.expr)
            alias    = item.alias or expr_str
            col_specs.append((item.expr, expr_str, alias))

        if isinstance(plan.child, AggregateNode):
            self.prog.emit("PROJECT", col_specs)
            self._gen_aggregate(plan.child)
            return

        if isinstance(plan.child, JoinNode):
            self.prog.emit("PROJECT", col_specs)
            self._gen_join(plan.child)
            return

        self.prog.emit("PROJECT", col_specs)
        scan = _find_scan(plan.child)
        table = scan.table if scan else "?"
        alias = (scan.alias or scan.table) if scan else "?"

        self.prog.emit("SCAN", table, alias)
        loop_start = self.prog.emit("NEXT_ROW", table)
        jmp_end    = self.prog.emit("JMP_IF_FALSE", -1)

        # If child has a filter, evaluate it first
        filt = plan.child if isinstance(plan.child, FilterNode) else None
        if filt:
            self._gen_expr(filt.predicate)
            jmp_skip = self.prog.emit("JMP_IF_FALSE", loop_start)

        self.prog.emit("EMIT_ROW")
        self.prog.emit("JMP", loop_start)
        end_addr = len(self.prog)
        self.prog.patch(jmp_end, 0, end_addr)

        self.prog.labels[loop_start] = "PROJECT_LOOP"
        self.prog.labels[end_addr]   = "PROJECT_END"

    def _gen_project_emit(self, plan: ProjectNode) -> None:
        """Emit PROJECT_ROW instruction with the list of (item.expr, expr_str, alias) tuples."""
        col_specs = []
        for item in plan.columns:
            expr_str = _expr_compact(item.expr)
            alias    = item.alias or expr_str
            col_specs.append((item.expr, expr_str, alias))
        # We push the SELECT column specs and EMIT_ROW handles projection in the VM
        self.prog.emit("PROJECT", col_specs)
        self.prog.emit("EMIT_ROW")

    # ------------------------------------------------------------------
    # JOIN
    # ------------------------------------------------------------------

    def _gen_join(self, plan: JoinNode) -> None:
        """
        Generate a nested-loop join or hash join.

        NESTED LOOP:
          SCAN left
          LOOP_LEFT:
            NEXT_ROW left
            JMP_IF_FALSE END
            SCAN right
            LOOP_RIGHT:
              NEXT_ROW right
              JMP_IF_FALSE LOOP_LEFT
              <evaluate condition>
              JMP_IF_FALSE LOOP_RIGHT
              EMIT_ROW
              JMP LOOP_RIGHT
          END:

        HASH:
          SCAN right; HASH_BUILD hash_var
          SCAN left
          LOOP_LEFT:
            NEXT_ROW left
            JMP_IF_FALSE END
            HASH_PROBE hash_var
            EMIT_ROW
            JMP LOOP_LEFT
          END:
        """
        left_scan  = _find_scan(plan.left)
        right_scan = _find_scan(plan.right)
        lt  = left_scan.table  if left_scan  else "left"
        la  = (left_scan.alias or lt)  if left_scan  else "left"
        rt  = right_scan.table if right_scan else "right"
        ra  = (right_scan.alias or rt) if right_scan else "right"
        hash_var = f"_hash_{rt}"

        if plan.strategy == "HASH":
            # Build phase
            self.prog.emit("SCAN", rt, ra)
            build_loop = self.prog.emit("NEXT_ROW", rt)
            build_end  = self.prog.emit("JMP_IF_FALSE", -1)
            if plan.condition:
                self._gen_join_key(plan.condition, rt)
            else:
                self.prog.emit("PUSH_CONST", True)
            self.prog.emit("HASH_BUILD", hash_var, rt)
            self.prog.emit("JMP", build_loop)
            build_end_addr = len(self.prog)
            self.prog.patch(build_end, 0, build_end_addr)

            # Probe phase
            self.prog.emit("SCAN", lt, la)
            probe_loop = self.prog.emit("NEXT_ROW", lt)
            probe_end  = self.prog.emit("JMP_IF_FALSE", -1)
            if plan.condition:
                self._gen_join_key(plan.condition, lt)
            else:
                self.prog.emit("PUSH_CONST", True)
            self.prog.emit("HASH_PROBE", hash_var)
            self.prog.emit("JOIN_EMIT", plan.join_type)
            self.prog.emit("JMP", probe_loop)
            end_addr = len(self.prog)
            self.prog.patch(probe_end, 0, end_addr)
            self.prog.labels[probe_loop] = "HASH_PROBE_LOOP"
        else:
            # Nested-loop join
            self.prog.emit("SCAN", lt, la)
            loop_left  = self.prog.emit("NEXT_ROW", lt)
            end_jmp    = self.prog.emit("JMP_IF_FALSE", -1)
            self.prog.emit("SCAN", rt, ra)
            loop_right = self.prog.emit("NEXT_ROW", rt)
            skip_right = self.prog.emit("JMP_IF_FALSE", loop_left)
            if plan.condition:
                self._gen_expr(plan.condition)
                jmp_skip = self.prog.emit("JMP_IF_FALSE", loop_right)
            self.prog.emit("JOIN_EMIT", plan.join_type)
            self.prog.emit("JMP", loop_right)
            end_addr = len(self.prog)
            self.prog.patch(end_jmp, 0, end_addr)
            self.prog.labels[loop_left]  = "NL_LEFT_LOOP"
            self.prog.labels[loop_right] = "NL_RIGHT_LOOP"
            self.prog.labels[end_addr]   = "NL_END"

    def _gen_join_key(
        self, condition: ast.Expression, table_side: str
    ) -> None:
        """
        For a hash join, generate code to push the join key.
        We use a simplified approach: push the left-side expression.
        """
        if isinstance(condition, ast.BinaryOp) and condition.op == "=":
            self._gen_expr(condition.left)
        else:
            self._gen_expr(condition)

    # ------------------------------------------------------------------
    # AGGREGATE
    # ------------------------------------------------------------------

    def _gen_aggregate(self, plan: AggregateNode) -> None:
        """
        Generate aggregate computation:
          AGG_INIT agg_specs, group_by_cols
          SCAN ...
          LOOP:
            NEXT_ROW
            JMP_IF_FALSE AGG_FINALIZE
            AGG_UPDATE
            JMP LOOP
          AGG_FINALIZE:
          AGG_EMIT
        """
        # Describe aggregates
        agg_specs = []
        for item in plan.aggregates:
            if isinstance(item.expr, ast.FunctionCall):
                arg_str = "*" if item.expr.arg is None else _expr_compact(item.expr.arg)
                agg_specs.append((item.expr.func, arg_str, item.alias or item.expr.func))

        gb_cols = [_expr_compact(g) for g in plan.group_by]
        self.prog.emit("AGG_INIT", agg_specs, gb_cols)

        # Find base scan
        scan = _find_scan(plan.child)
        table = scan.table if scan else "?"
        alias = (scan.alias or table) if scan else "?"
        self.prog.emit("SCAN", table, alias)

        loop_start = self.prog.emit("NEXT_ROW", table)
        jmp_end    = self.prog.emit("JMP_IF_FALSE", -1)

        # Filter if present
        filt = plan.child if isinstance(plan.child, FilterNode) else None
        if filt:
            self._gen_expr(filt.predicate)
            jmp_skip = self.prog.emit("JMP_IF_FALSE", loop_start)

        self.prog.emit("AGG_UPDATE", gb_cols, agg_specs)
        self.prog.emit("JMP", loop_start)

        end_addr = len(self.prog)
        self.prog.patch(jmp_end, 0, end_addr)

        if plan.having:
            self.prog.emit("AGG_HAVING", _expr_compact(plan.having))

        self.prog.emit("AGG_EMIT")
        self.prog.labels[loop_start] = "AGG_LOOP"
        self.prog.labels[end_addr]   = "AGG_END"

    # ------------------------------------------------------------------
    # SORT / LIMIT / DISTINCT
    # ------------------------------------------------------------------

    def _gen_sort(self, plan: SortNode) -> None:
        self._gen_plan(plan.child)
        # Replace HALT with SORT then re-add HALT
        sort_keys = [(
            _expr_compact(ob.expr),
            ob.direction,
        ) for ob in plan.order_by]
        self.prog.emit("SORT", sort_keys)

    def _gen_limit(self, plan: LimitNode) -> None:
        self._gen_plan(plan.child)
        self.prog.emit("LIMIT", plan.n)

    def _gen_distinct(self, plan: DistinctNode) -> None:
        self._gen_plan(plan.child)
        self.prog.emit("DISTINCT")

    # ------------------------------------------------------------------
    # INSERT / UPDATE / DELETE
    # ------------------------------------------------------------------

    def _gen_insert(self, plan: InsertNode) -> None:
        row_vals_list = []
        for row in plan.value_rows:
            row_vals_list.append([_expr_compact(expr) for expr in row])
        self.prog.emit("INSERT_ROW", plan.table,
                       plan.columns or [], row_vals_list, plan.value_rows)

    def _gen_update(self, plan: UpdateNode) -> None:
        assigns = [(a.column, _expr_compact(a.value), a.value)
                   for a in plan.assignments]
        pred_str = _expr_compact(plan.predicate) if plan.predicate else None
        self.prog.emit("UPDATE_ROW", plan.table, assigns, plan.predicate)

    def _gen_delete(self, plan: DeleteNode) -> None:
        pred_str = _expr_compact(plan.predicate) if plan.predicate else None
        self.prog.emit("DELETE_ROW", plan.table, plan.predicate)

    # ------------------------------------------------------------------
    # Expression code generation
    # ------------------------------------------------------------------

    def _gen_expr(self, expr: Optional[ast.Expression]) -> None:
        """
        Generate code to evaluate *expr* and leave the result on the stack.
        The VM evaluates these lazily based on the current row context.
        We encode the expression as a single EVAL_EXPR instruction with the
        AST node embedded (the VM has an expression evaluator).
        """
        if expr is None:
            self.prog.emit("PUSH_CONST", True)
            return
        # Emit a compact expression descriptor; the VM evaluates it
        self.prog.emit("EVAL_EXPR", expr)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _find_scan(plan: PlanNode) -> Optional[ScanNode]:
    """Find the first ScanNode in a plan subtree."""
    if isinstance(plan, ScanNode):
        return plan
    if isinstance(plan, FilterNode):
        return _find_scan(plan.child)
    if isinstance(plan, ProjectNode):
        return _find_scan(plan.child)
    if isinstance(plan, AggregateNode):
        return _find_scan(plan.child)
    if isinstance(plan, (SortNode, LimitNode, DistinctNode)):
        return _find_scan(plan.child)
    if isinstance(plan, JoinNode):
        return _find_scan(plan.left)
    return None


def _contains_join(plan: PlanNode) -> bool:
    if isinstance(plan, JoinNode):
        return True
    if isinstance(
        plan,
        (AggregateNode, DistinctNode, FilterNode, LimitNode, ProjectNode, SortNode),
    ):
        return _contains_join(plan.child)
    return False


# ---------------------------------------------------------------------------
# Public convenience
# ---------------------------------------------------------------------------

def generate_bytecode(plan: PlanNode) -> BytecodeProgram:
    """Generate and return a BytecodeProgram for *plan*."""
    return CodeGenerator().generate(plan)
