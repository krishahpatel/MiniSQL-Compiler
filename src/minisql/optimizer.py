"""
optimizer.py — Phase 5: Query Optimization
==========================================
Implements six optimisation passes on the logical query plan tree.
Each pass is a separate, togglable function that takes a plan node
and returns a (possibly modified) plan node, reporting what it changed.

PASSES (in order of application)
---------------------------------
1. constant_fold     — evaluate constant sub-expressions at compile time
                       e.g.  WHERE 1+2 > 2  ->  WHERE 3 > 2  ->  WHERE TRUE
2. bool_simplify     — Boolean tautology / contradiction elimination
                       x AND TRUE -> x, x OR TRUE -> TRUE, x AND FALSE -> FALSE
3. predicate_pushdown — move Filter nodes below Join nodes so rows are
                        discarded early (reduces join cardinality)
4. projection_prune  — wrap each Scan in a Project with only needed columns
5. join_reorder      — for two-table joins, put the smaller table on the
                       right (build side for hash join)
6. cse_tac           — Common Subexpression Elimination on TAC instructions

After all passes, an "Optimization Report" is produced listing which
rules fired and what they changed.

Design note
-----------
Each pass is implemented as a recursive tree walker.  The optimizer
keeps a list of "events" (OptEvent records) so the report can show
exactly what happened.
"""

from __future__ import annotations
import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from . import ast_nodes as ast
from .ir import (
    PlanNode, ScanNode, FilterNode, ProjectNode, JoinNode,
    AggregateNode, SortNode, LimitNode, DistinctNode,
    InsertNode, UpdateNode, DeleteNode, TACProgram, TACInstr,
    _expr_compact,
)
from .catalog import Catalog


# ---------------------------------------------------------------------------
# Optimization event record
# ---------------------------------------------------------------------------

@dataclass
class OptEvent:
    """Records one optimization that fired."""
    pass_name: str
    description: str
    before: str = ""
    after:  str = ""

    def __str__(self) -> str:
        lines = [f"  [{self.pass_name}] {self.description}"]
        if self.before:
            lines.append(f"    Before: {self.before}")
            lines.append(f"    After:  {self.after}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Optimizer
# ---------------------------------------------------------------------------

class Optimizer:
    """
    Applies optimization passes to a logical query plan.

    Usage:
        opt = Optimizer(catalog)
        opt_plan = opt.optimize(plan)
        print(opt.report())
    """

    def __init__(
        self,
        catalog: Catalog,
        passes: Optional[List[str]] = None,
    ):
        self.catalog = catalog
        # Default: all passes enabled; caller may override
        self._enabled: Set[str] = set(passes) if passes else {
            "constant_fold",
            "bool_simplify",
            "predicate_pushdown",
            "projection_prune",
            "join_reorder",
            "cse_tac",
        }
        self._events: List[OptEvent] = []

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def optimize(self, plan: PlanNode) -> PlanNode:
        """Apply all enabled passes to *plan* and return the optimised plan."""
        self._events = []

        if "constant_fold" in self._enabled:
            plan = self._fold_plan(plan)

        if "bool_simplify" in self._enabled:
            plan = self._simplify_plan(plan)

        if "predicate_pushdown" in self._enabled:
            plan = self._pushdown_plan(plan)

        if "projection_prune" in self._enabled:
            plan = self._prune_plan(plan, needed_cols=None)

        if "join_reorder" in self._enabled:
            plan = self._reorder_joins(plan)

        return plan

    def optimize_tac(self, tac: TACProgram) -> TACProgram:
        """Apply CSE pass to a TAC program."""
        if "cse_tac" in self._enabled:
            return self._cse(tac)
        return tac

    def report(self) -> str:
        """Return the human-readable optimization report."""
        if not self._events:
            return "Optimization Report: no optimizations fired."
        lines = ["=" * 60, "OPTIMIZATION REPORT", "=" * 60]
        for ev in self._events:
            lines.append(str(ev))
        lines.append(f"\nTotal: {len(self._events)} optimization(s) applied.")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Pass 1: Constant Folding
    # ------------------------------------------------------------------

    def _fold_plan(self, plan: PlanNode) -> PlanNode:
        """Walk the plan tree and constant-fold all predicates/expressions."""
        if isinstance(plan, FilterNode):
            new_pred = self._fold_expr(plan.predicate)
            plan = FilterNode(child=self._fold_plan(plan.child), predicate=new_pred)
        elif isinstance(plan, ProjectNode):
            new_cols = [
                ast.SelectItem(
                    expr=self._fold_expr(item.expr),
                    alias=item.alias,
                    line=item.line, col=item.col,
                )
                for item in plan.columns
            ]
            plan = ProjectNode(child=self._fold_plan(plan.child), columns=new_cols)
        elif isinstance(plan, JoinNode):
            cond = self._fold_expr(plan.condition) if plan.condition else None
            plan = JoinNode(
                left=self._fold_plan(plan.left),
                right=self._fold_plan(plan.right),
                condition=cond,
                join_type=plan.join_type,
                strategy=plan.strategy,
            )
        elif isinstance(plan, AggregateNode):
            having = self._fold_expr(plan.having) if plan.having else None
            plan = AggregateNode(
                child=self._fold_plan(plan.child),
                group_by=plan.group_by,
                aggregates=plan.aggregates,
                having=having,
            )
        elif isinstance(plan, (SortNode, LimitNode, DistinctNode)):
            child = self._fold_plan(plan.child)
            plan  = copy.copy(plan)
            plan.child = child
        return plan

    def _fold_expr(self, expr: Optional[ast.Expression]) -> Optional[ast.Expression]:
        """Constant-fold a single expression recursively."""
        if expr is None:
            return None

        if isinstance(expr, ast.BinaryOp):
            left  = self._fold_expr(expr.left)
            right = self._fold_expr(expr.right)
            # Try to evaluate if both sides are constants
            if isinstance(left, ast.Literal) and isinstance(right, ast.Literal):
                result = _eval_binary(expr.op, left.value, right.value)
                if result is not None:
                    before = _expr_compact(expr)
                    kind   = _python_kind(result)
                    folded = ast.Literal(value=result, kind=kind,
                                         line=expr.line, col=expr.col)
                    self._events.append(OptEvent(
                        pass_name="constant_fold",
                        description=f"Folded constant expression",
                        before=before,
                        after=_expr_compact(folded),
                    ))
                    return folded
            return ast.BinaryOp(
                op=expr.op, left=left, right=right,
                line=expr.line, col=expr.col,
                resolved_type=expr.resolved_type,
            )

        if isinstance(expr, ast.UnaryOp):
            operand = self._fold_expr(expr.operand)
            if isinstance(operand, ast.Literal):
                val = _eval_unary(expr.op, operand.value)
                if val is not None:
                    before = _expr_compact(expr)
                    kind   = _python_kind(val)
                    folded = ast.Literal(value=val, kind=kind,
                                         line=expr.line, col=expr.col)
                    self._events.append(OptEvent(
                        pass_name="constant_fold",
                        description="Folded unary constant",
                        before=before,
                        after=_expr_compact(folded),
                    ))
                    return folded
            return ast.UnaryOp(
                op=expr.op, operand=operand,
                line=expr.line, col=expr.col,
                resolved_type=expr.resolved_type,
            )

        return expr

    # ------------------------------------------------------------------
    # Pass 2: Boolean Simplification
    # ------------------------------------------------------------------

    def _simplify_plan(self, plan: PlanNode) -> PlanNode:
        if isinstance(plan, FilterNode):
            new_pred = self._simplify_expr(plan.predicate)
            # If predicate folded to TRUE, remove the filter
            if isinstance(new_pred, ast.Literal) and new_pred.value is True:
                self._events.append(OptEvent(
                    pass_name="bool_simplify",
                    description="Removed always-true filter",
                    before=_expr_compact(plan.predicate),
                    after="TRUE",
                ))
                return self._simplify_plan(plan.child)
            plan = FilterNode(child=self._simplify_plan(plan.child), predicate=new_pred)
        elif isinstance(plan, JoinNode):
            cond = self._simplify_expr(plan.condition) if plan.condition else None
            plan = JoinNode(
                left=self._simplify_plan(plan.left),
                right=self._simplify_plan(plan.right),
                condition=cond,
                join_type=plan.join_type,
                strategy=plan.strategy,
            )
        elif isinstance(plan, AggregateNode):
            having = self._simplify_expr(plan.having) if plan.having else None
            plan = AggregateNode(
                child=self._simplify_plan(plan.child),
                group_by=plan.group_by,
                aggregates=plan.aggregates,
                having=having,
            )
        elif hasattr(plan, "child"):
            child = self._simplify_plan(plan.child)
            plan  = copy.copy(plan)
            plan.child = child
        return plan

    def _simplify_expr(self, expr: Optional[ast.Expression]) -> Optional[ast.Expression]:
        """Apply Boolean simplification rules recursively."""
        if expr is None:
            return None
        if not isinstance(expr, (ast.BinaryOp, ast.UnaryOp)):
            return expr

        if isinstance(expr, ast.UnaryOp) and expr.op == "NOT":
            inner = self._simplify_expr(expr.operand)
            # NOT TRUE -> FALSE, NOT FALSE -> TRUE
            if isinstance(inner, ast.Literal):
                if inner.value is True:
                    self._events.append(OptEvent(
                        "bool_simplify", "NOT TRUE -> FALSE",
                        _expr_compact(expr), "FALSE"
                    ))
                    return ast.Literal(value=False, kind="BOOL",
                                       line=expr.line, col=expr.col)
                if inner.value is False:
                    self._events.append(OptEvent(
                        "bool_simplify", "NOT FALSE -> TRUE",
                        _expr_compact(expr), "TRUE"
                    ))
                    return ast.Literal(value=True, kind="BOOL",
                                       line=expr.line, col=expr.col)
            return ast.UnaryOp(op="NOT", operand=inner,
                               line=expr.line, col=expr.col)

        if isinstance(expr, ast.BinaryOp):
            left  = self._simplify_expr(expr.left)
            right = self._simplify_expr(expr.right)
            op    = expr.op
            before = _expr_compact(expr)

            def T(e): return isinstance(e, ast.Literal) and e.value is True
            def F(e): return isinstance(e, ast.Literal) and e.value is False

            if op == "AND":
                if F(left) or F(right):
                    r = ast.Literal(value=False, kind="BOOL",
                                    line=expr.line, col=expr.col)
                    self._events.append(OptEvent("bool_simplify", "x AND FALSE -> FALSE",
                                                  before, "FALSE"))
                    return r
                if T(left):
                    self._events.append(OptEvent("bool_simplify", "TRUE AND x -> x",
                                                  before, _expr_compact(right)))
                    return right
                if T(right):
                    self._events.append(OptEvent("bool_simplify", "x AND TRUE -> x",
                                                  before, _expr_compact(left)))
                    return left

            elif op == "OR":
                if T(left) or T(right):
                    r = ast.Literal(value=True, kind="BOOL",
                                    line=expr.line, col=expr.col)
                    self._events.append(OptEvent("bool_simplify", "x OR TRUE -> TRUE",
                                                  before, "TRUE"))
                    return r
                if F(left):
                    self._events.append(OptEvent("bool_simplify", "FALSE OR x -> x",
                                                  before, _expr_compact(right)))
                    return right
                if F(right):
                    self._events.append(OptEvent("bool_simplify", "x OR FALSE -> x",
                                                  before, _expr_compact(left)))
                    return left

            return ast.BinaryOp(op=op, left=left, right=right,
                                 line=expr.line, col=expr.col,
                                 resolved_type=expr.resolved_type)
        return expr

    # ------------------------------------------------------------------
    # Pass 3: Predicate Pushdown
    # ------------------------------------------------------------------

    def _pushdown_plan(self, plan: PlanNode) -> PlanNode:
        """
        Move Filter nodes below Join nodes when the predicate only references
        columns from one side of the join.

        Before: Filter(pred, Join(L, R, cond))
        After:  Join(Filter(pred_L, L), Filter(pred_R, R), remaining_cond)
        """
        if isinstance(plan, FilterNode):
            child = self._pushdown_plan(plan.child)
            if isinstance(child, JoinNode):
                # Split predicate into left-only, right-only, both
                left_tables  = _collect_tables(child.left)
                right_tables = _collect_tables(child.right)
                pred_parts   = _split_conjuncts(plan.predicate)

                left_preds, right_preds, join_preds = [], [], []
                for p in pred_parts:
                    refs = _expr_tables(p)
                    if refs <= left_tables:
                        left_preds.append(p)
                    elif refs <= right_tables:
                        right_preds.append(p)
                    else:
                        join_preds.append(p)

                if left_preds or right_preds:
                    self._events.append(OptEvent(
                        "predicate_pushdown",
                        f"Pushed {len(left_preds)} predicate(s) to left, "
                        f"{len(right_preds)} to right of join",
                    ))

                new_left  = child.left
                new_right = child.right
                if left_preds:
                    new_left  = FilterNode(child=child.left,
                                           predicate=_conjoin(left_preds))
                if right_preds:
                    new_right = FilterNode(child=child.right,
                                           predicate=_conjoin(right_preds))

                remaining = _conjoin(join_preds) if join_preds else child.condition
                new_join  = JoinNode(
                    left=self._pushdown_plan(new_left),
                    right=self._pushdown_plan(new_right),
                    condition=remaining,
                    join_type=child.join_type,
                    strategy=child.strategy,
                )
                return new_join

            return FilterNode(child=child, predicate=plan.predicate)

        # Recurse into children
        if isinstance(plan, JoinNode):
            return JoinNode(
                left=self._pushdown_plan(plan.left),
                right=self._pushdown_plan(plan.right),
                condition=plan.condition,
                join_type=plan.join_type,
                strategy=plan.strategy,
            )
        if isinstance(plan, (ProjectNode, DistinctNode, SortNode, LimitNode)):
            child = self._pushdown_plan(plan.child)
            plan  = copy.copy(plan)
            plan.child = child
            return plan
        if isinstance(plan, AggregateNode):
            child = self._pushdown_plan(plan.child)
            return AggregateNode(
                child=child,
                group_by=plan.group_by,
                aggregates=plan.aggregates,
                having=plan.having,
            )
        return plan

    # ------------------------------------------------------------------
    # Pass 4: Projection Pruning
    # ------------------------------------------------------------------

    def _prune_plan(
        self, plan: PlanNode, needed_cols: Optional[Set[str]]
    ) -> PlanNode:
        """
        For each ScanNode, if we know which columns are needed, record it.
        (Full pruning requires knowing the full column set — we annotate
        ScanNode with the needed columns for the code generator to use.)
        """
        if isinstance(plan, ScanNode):
            if needed_cols is not None:
                plan = copy.copy(plan)
                # The code generator can read plan.needed_cols
                plan.needed_cols = needed_cols   # type: ignore[attr-defined]
                self._events.append(OptEvent(
                    "projection_prune",
                    f"Scan({plan.table}) will only read columns: {sorted(needed_cols)}",
                ))
            return plan

        if isinstance(plan, ProjectNode):
            # Collect column names from the projection
            cols: Set[str] = set()
            for item in plan.columns:
                cols |= _expr_col_refs(item.expr)
            child = self._prune_plan(plan.child, cols)
            return ProjectNode(child=child, columns=plan.columns)

        if isinstance(plan, FilterNode):
            extra = _expr_col_refs(plan.predicate)
            merged = (needed_cols | extra) if needed_cols else extra
            child  = self._prune_plan(plan.child, merged)
            return FilterNode(child=child, predicate=plan.predicate)

        if isinstance(plan, JoinNode):
            left_child  = self._prune_plan(plan.left,  needed_cols)
            right_child = self._prune_plan(plan.right, needed_cols)
            return JoinNode(
                left=left_child, right=right_child,
                condition=plan.condition,
                join_type=plan.join_type, strategy=plan.strategy,
            )

        if isinstance(plan, AggregateNode):
            gb_cols  = set()
            for g in plan.group_by:
                gb_cols |= _expr_col_refs(g)
            agg_cols = set()
            for item in plan.aggregates:
                agg_cols |= _expr_col_refs(item.expr)
            child = self._prune_plan(plan.child, gb_cols | agg_cols)
            return AggregateNode(
                child=child, group_by=plan.group_by,
                aggregates=plan.aggregates, having=plan.having,
            )

        if hasattr(plan, "child"):
            child = self._prune_plan(plan.child, needed_cols)
            plan  = copy.copy(plan)
            plan.child = child
        return plan

    # ------------------------------------------------------------------
    # Pass 5: Join Reordering & Strategy Selection
    # ------------------------------------------------------------------

    def _reorder_joins(self, plan: PlanNode) -> PlanNode:
        """
        For each JoinNode:
          - Choose HASH JOIN if either side has many rows (threshold: 500)
          - For INNER joins, put the smaller table on the right (build side)
        """
        if isinstance(plan, JoinNode):
            left  = self._reorder_joins(plan.left)
            right = self._reorder_joins(plan.right)

            left_rows  = _estimated_rows(left,  self.catalog)
            right_rows = _estimated_rows(right, self.catalog)

            strategy = "NESTED_LOOP"
            if left_rows > 500 or right_rows > 500:
                strategy = "HASH"
                self._events.append(OptEvent(
                    "join_reorder",
                    f"Selected HASH JOIN (left={left_rows} rows, right={right_rows} rows)",
                ))

            # Swap for smaller build side in inner joins
            if (plan.join_type == "INNER"
                    and left_rows < right_rows
                    and strategy == "HASH"):
                left, right = right, left
                self._events.append(OptEvent(
                    "join_reorder",
                    "Swapped join sides to put smaller table as build side",
                ))

            return JoinNode(
                left=left, right=right,
                condition=plan.condition,
                join_type=plan.join_type,
                strategy=strategy,
            )

        if hasattr(plan, "child"):
            child = self._reorder_joins(plan.child)
            plan  = copy.copy(plan)
            plan.child = child
        if isinstance(plan, JoinNode):
            pass   # already handled above
        return plan

    # ------------------------------------------------------------------
    # Pass 6: CSE on TAC
    # ------------------------------------------------------------------

    def _cse(self, tac: TACProgram) -> TACProgram:
        """
        Common Subexpression Elimination:
        If two instructions compute the same (op, arg1, arg2), replace
        the second with a COPY from the first result.
        """
        new_instrs: List[TACInstr] = []
        # Maps (op, arg1, arg2) -> result temp
        expr_map: Dict[Tuple, str] = {}
        # Maps old temp -> canonical temp (for substitution)
        subst: Dict[str, str] = {}
        eliminated = 0

        for instr in tac.instructions:
            # Substitute args that were previously eliminated
            a1 = subst.get(instr.arg1, instr.arg1) if isinstance(instr.arg1, str) else instr.arg1
            a2 = subst.get(instr.arg2, instr.arg2) if isinstance(instr.arg2, str) else instr.arg2

            key = (instr.op, a1, a2)
            if (instr.op not in ("LABEL", "COPY", "NOP")
                    and instr.result is not None
                    and key in expr_map):
                # CSE hit: reuse earlier computation
                canonical = expr_map[key]
                subst[instr.result] = canonical
                new_instrs.append(TACInstr(
                    op="COPY", arg1=canonical, result=instr.result
                ))
                eliminated += 1
            else:
                if instr.result is not None and instr.op not in ("LABEL", "COPY", "NOP"):
                    expr_map[key] = instr.result
                new_instrs.append(TACInstr(
                    op=instr.op, arg1=a1, arg2=a2, result=instr.result
                ))

        if eliminated:
            self._events.append(OptEvent(
                "cse_tac",
                f"Eliminated {eliminated} common sub-expression(s) in TAC",
            ))

        new_tac = TACProgram()
        new_tac.instructions = new_instrs
        new_tac._temp_count  = tac._temp_count
        return new_tac


# ===========================================================================
# Utility functions
# ===========================================================================

def _eval_binary(op: str, a: Any, b: Any) -> Any:
    """Try to evaluate a binary operation on two Python constants."""
    if a is None or b is None:
        return None   # NULL semantics: don't fold
    try:
        if op == "+":   return a + b
        if op == "-":   return a - b
        if op == "*":   return a * b
        if op == "/":   return a / b if b != 0 else None
        if op == "%":   return a % b if b != 0 else None
        if op == "=":   return a == b
        if op in ("!=", "<>"): return a != b
        if op == "<":   return a < b
        if op == "<=":  return a <= b
        if op == ">":   return a > b
        if op == ">=":  return a >= b
        if op == "AND": return bool(a) and bool(b)
        if op == "OR":  return bool(a) or bool(b)
    except (TypeError, ZeroDivisionError):
        pass
    return None


def _eval_unary(op: str, a: Any) -> Any:
    if a is None:
        return None
    try:
        if op == "-":   return -a
        if op == "NOT": return not bool(a)
    except TypeError:
        pass
    return None


def _python_kind(v: Any) -> str:
    if isinstance(v, bool): return "BOOL"
    if isinstance(v, int):  return "INT"
    if isinstance(v, float): return "FLOAT"
    if isinstance(v, str):  return "TEXT"
    return "NULL"


def _split_conjuncts(expr: Optional[ast.Expression]) -> List[ast.Expression]:
    """Split a tree of AND nodes into a flat list of conjuncts."""
    if expr is None:
        return []
    if isinstance(expr, ast.BinaryOp) and expr.op == "AND":
        return _split_conjuncts(expr.left) + _split_conjuncts(expr.right)
    return [expr]


def _conjoin(exprs: List[ast.Expression]) -> Optional[ast.Expression]:
    """Join a list of expressions with AND."""
    if not exprs:
        return None
    result = exprs[0]
    for e in exprs[1:]:
        result = ast.BinaryOp(op="AND", left=result, right=e)
    return result


def _collect_tables(plan: PlanNode) -> Set[str]:
    """Return the set of table names referenced under a plan node."""
    if isinstance(plan, ScanNode):
        tables = {plan.table.lower()}
        if plan.alias:
            tables.add(plan.alias.lower())
        return tables
    tables: Set[str] = set()
    if isinstance(plan, JoinNode):
        tables |= _collect_tables(plan.left)
        tables |= _collect_tables(plan.right)
    elif hasattr(plan, "child"):
        tables |= _collect_tables(plan.child)
    return tables


def _expr_tables(expr: ast.Expression) -> Set[str]:
    """Return the set of table qualifiers referenced in an expression."""
    if isinstance(expr, ast.ColumnRef) and expr.table:
        return {expr.table.lower()}
    if isinstance(expr, ast.BinaryOp):
        return _expr_tables(expr.left) | _expr_tables(expr.right)
    if isinstance(expr, ast.UnaryOp):
        return _expr_tables(expr.operand)
    return set()


def _expr_col_refs(expr: Optional[ast.Expression]) -> Set[str]:
    """Return column names referenced in an expression."""
    if expr is None:
        return set()
    if isinstance(expr, ast.ColumnRef):
        return {expr.column.lower()}
    if isinstance(expr, ast.BinaryOp):
        return _expr_col_refs(expr.left) | _expr_col_refs(expr.right)
    if isinstance(expr, ast.UnaryOp):
        return _expr_col_refs(expr.operand)
    if isinstance(expr, ast.FunctionCall) and expr.arg:
        return _expr_col_refs(expr.arg)
    return set()


def _estimated_rows(plan: PlanNode, catalog: Catalog) -> int:
    """Rough cardinality estimate for a plan node."""
    if isinstance(plan, ScanNode):
        return catalog.get_row_count(plan.table)
    if isinstance(plan, FilterNode):
        return max(1, _estimated_rows(plan.child, catalog) // 3)
    if isinstance(plan, JoinNode):
        l = _estimated_rows(plan.left,  catalog)
        r = _estimated_rows(plan.right, catalog)
        return max(1, l * r // max(l, r))
    if hasattr(plan, "child"):
        return _estimated_rows(plan.child, catalog)
    return 100
