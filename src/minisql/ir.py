"""
ir.py — Intermediate Representation (IR) Node Definitions
==========================================================
Defines two complementary IRs:

1. LOGICAL QUERY PLAN — a relational algebra tree
   Each node represents one algebraic operator.
   Pretty-printed as: pi_name(sigma_age>20(Scan(Students)))

2. THREE-ADDRESS CODE (TAC) — a flat linear IR for expressions
   Each instruction is a quadruple: (op, arg1, arg2, result)
   TAC is used for expression evaluation in the code generator.

The plan tree is what classical textbooks call the "logical query plan"
or "internal form" before physical operator selection.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from . import ast_nodes as ast


# ===========================================================================
# PART 1 — LOGICAL QUERY PLAN (Relational Algebra Tree)
# ===========================================================================

class PlanNode:
    """Base class for all logical plan nodes."""

    def pretty(self, indent: int = 0) -> str:
        """Return a nested textual representation of the plan tree."""
        raise NotImplementedError

    def ra_str(self) -> str:
        """Return concise relational-algebra notation (single-line)."""
        raise NotImplementedError


@dataclass
class ScanNode(PlanNode):
    """
    Full table scan.
    Relational algebra: Scan(table)
    """
    table:  str
    alias:  Optional[str] = None
    # Set by optimizer: estimated output row count
    estimated_rows: int = 0

    def pretty(self, indent: int = 0) -> str:
        a = f" AS {self.alias}" if self.alias else ""
        return "  " * indent + f"Scan({self.table}{a})"

    def ra_str(self) -> str:
        a = f" AS {self.alias}" if self.alias else ""
        return f"Scan({self.table}{a})"


@dataclass
class FilterNode(PlanNode):
    """
    Filter (selection) — sigma in relational algebra.
    sigma_predicate(child)
    """
    child:     PlanNode
    predicate: ast.Expression

    def pretty(self, indent: int = 0) -> str:
        pred = _expr_compact(self.predicate)
        lines = ["  " * indent + f"Filter(σ {pred})"]
        lines.append(self.child.pretty(indent + 1))
        return "\n".join(lines)

    def ra_str(self) -> str:
        pred = _expr_compact(self.predicate)
        return f"σ_{pred}({self.child.ra_str()})"


@dataclass
class ProjectNode(PlanNode):
    """
    Projection — pi in relational algebra.
    pi_col1,col2,...(child)
    """
    child:   PlanNode
    columns: List[ast.SelectItem]

    def pretty(self, indent: int = 0) -> str:
        cols = ", ".join(_select_item_compact(c) for c in self.columns)
        lines = ["  " * indent + f"Project(π {cols})"]
        lines.append(self.child.pretty(indent + 1))
        return "\n".join(lines)

    def ra_str(self) -> str:
        cols = ", ".join(_select_item_compact(c) for c in self.columns)
        return f"π_{cols}({self.child.ra_str()})"


@dataclass
class JoinNode(PlanNode):
    """
    Join operator (inner, left, right, full, cross).
    left ⋈_condition right
    """
    left:      PlanNode
    right:     PlanNode
    condition: Optional[ast.Expression]
    join_type: str = "INNER"          # INNER | LEFT | RIGHT | FULL | CROSS
    strategy:  str = "NESTED_LOOP"   # NESTED_LOOP | HASH — set by optimizer

    def pretty(self, indent: int = 0) -> str:
        cond = f" ON {_expr_compact(self.condition)}" if self.condition else ""
        lines = ["  " * indent + f"Join({self.join_type}{cond}) [{self.strategy}]"]
        lines.append(self.left.pretty(indent + 1))
        lines.append(self.right.pretty(indent + 1))
        return "\n".join(lines)

    def ra_str(self) -> str:
        sym = "⋈"
        if self.join_type == "LEFT":
            sym = "⟕"
        elif self.join_type == "RIGHT":
            sym = "⟖"
        cond = f"_{_expr_compact(self.condition)}" if self.condition else ""
        return f"({self.left.ra_str()} {sym}{cond} {self.right.ra_str()})"


@dataclass
class AggregateNode(PlanNode):
    """
    Aggregation (GROUP BY + aggregate functions).
    gamma_group;aggs(child)
    """
    child:      PlanNode
    group_by:   List[ast.Expression]
    aggregates: List[ast.SelectItem]   # items that contain FunctionCall
    having:     Optional[ast.Expression] = None

    def pretty(self, indent: int = 0) -> str:
        gb   = ", ".join(_expr_compact(g) for g in self.group_by)
        aggs = ", ".join(_select_item_compact(a) for a in self.aggregates)
        lines = ["  " * indent + f"Aggregate(γ group=[{gb}] aggs=[{aggs}])"]
        if self.having:
            lines.append("  " * (indent+1) + f"Having: {_expr_compact(self.having)}")
        lines.append(self.child.pretty(indent + 1))
        return "\n".join(lines)

    def ra_str(self) -> str:
        gb   = ", ".join(_expr_compact(g) for g in self.group_by)
        aggs = ", ".join(_select_item_compact(a) for a in self.aggregates)
        return f"γ_{gb};{aggs}({self.child.ra_str()})"


@dataclass
class SortNode(PlanNode):
    """
    Sorting (ORDER BY).
    tau_col_ASC(child)
    """
    child:    PlanNode
    order_by: List[ast.OrderByItem]

    def pretty(self, indent: int = 0) -> str:
        key = ", ".join(
            f"{_expr_compact(o.expr)} {o.direction}" for o in self.order_by
        )
        lines = ["  " * indent + f"Sort(τ {key})"]
        lines.append(self.child.pretty(indent + 1))
        return "\n".join(lines)

    def ra_str(self) -> str:
        key = ",".join(
            f"{_expr_compact(o.expr)}_{o.direction}" for o in self.order_by
        )
        return f"τ_{key}({self.child.ra_str()})"


@dataclass
class LimitNode(PlanNode):
    """
    LIMIT — take at most n rows.
    """
    child: PlanNode
    n:     int

    def pretty(self, indent: int = 0) -> str:
        lines = ["  " * indent + f"Limit({self.n})"]
        lines.append(self.child.pretty(indent + 1))
        return "\n".join(lines)

    def ra_str(self) -> str:
        return f"Limit_{self.n}({self.child.ra_str()})"


@dataclass
class DistinctNode(PlanNode):
    """
    DISTINCT — deduplicate rows (delta in some texts).
    """
    child: PlanNode

    def pretty(self, indent: int = 0) -> str:
        lines = ["  " * indent + "Distinct(δ)"]
        lines.append(self.child.pretty(indent + 1))
        return "\n".join(lines)

    def ra_str(self) -> str:
        return f"δ({self.child.ra_str()})"


@dataclass
class InsertNode(PlanNode):
    """Physical INSERT plan node."""
    table:      str
    columns:    Optional[List[str]]
    value_rows: List[List[ast.Expression]]

    def pretty(self, indent: int = 0) -> str:
        cols = ", ".join(self.columns) if self.columns else "<all>"
        return "  " * indent + f"Insert({self.table} [{cols}], {len(self.value_rows)} rows)"

    def ra_str(self) -> str:
        return f"Insert({self.table})"


@dataclass
class UpdateNode(PlanNode):
    """Physical UPDATE plan node."""
    table:       str
    assignments: List[ast.Assignment]
    predicate:   Optional[ast.Expression]

    def pretty(self, indent: int = 0) -> str:
        assigns = ", ".join(f"{a.column}={_expr_compact(a.value)}" for a in self.assignments)
        pred = f" WHERE {_expr_compact(self.predicate)}" if self.predicate else ""
        return "  " * indent + f"Update({self.table} SET {assigns}{pred})"

    def ra_str(self) -> str:
        return f"Update({self.table})"


@dataclass
class DeleteNode(PlanNode):
    """Physical DELETE plan node."""
    table:     str
    predicate: Optional[ast.Expression]

    def pretty(self, indent: int = 0) -> str:
        pred = f" WHERE {_expr_compact(self.predicate)}" if self.predicate else ""
        return "  " * indent + f"Delete({self.table}{pred})"

    def ra_str(self) -> str:
        return f"Delete({self.table})"


# ===========================================================================
# PART 2 — THREE-ADDRESS CODE (TAC)
# ===========================================================================

@dataclass
class TACInstr:
    """
    A single three-address-code instruction in quadruple form.

    result = op(arg1, arg2)

    For unary ops: result = op(arg1)   (arg2 is None)
    For copies:    result = arg1       (op='COPY', arg2 is None)
    For PARAM:     arg1 = value to push (result is None)
    """
    op:     str
    arg1:   Any = None
    arg2:   Any = None
    result: Any = None

    def __str__(self) -> str:
        if self.op == "COPY":
            return f"  {self.result} = {self.arg1}"
        if self.op in ("JMP_IF_FALSE", "JMP"):
            return f"  {self.op} {self.arg1} -> {self.result}"
        if self.op == "LABEL":
            return f"{self.result}:"
        if self.op == "CALL":
            return f"  {self.result} = CALL {self.arg1}({self.arg2})"
        if self.arg2 is not None:
            return f"  {self.result} = {self.arg1} {self.op} {self.arg2}"
        if self.arg1 is not None:
            return f"  {self.result} = {self.op} {self.arg1}"
        return f"  {self.op}"


class TACProgram:
    """
    A sequence of TAC instructions for one expression or sub-query.
    Maintains a temporary variable counter.
    """

    def __init__(self):
        self.instructions: List[TACInstr] = []
        self._temp_count = 0
        self._label_count = 0

    def new_temp(self) -> str:
        """Allocate a fresh temporary variable name."""
        t = f"t{self._temp_count}"
        self._temp_count += 1
        return t

    def new_label(self) -> str:
        """Allocate a fresh label."""
        lbl = f"L{self._label_count}"
        self._label_count += 1
        return lbl

    def emit(self, op: str, arg1=None, arg2=None, result=None) -> TACInstr:
        instr = TACInstr(op=op, arg1=arg1, arg2=arg2, result=result)
        self.instructions.append(instr)
        return instr

    def __str__(self) -> str:
        return "\n".join(str(i) for i in self.instructions)


# ===========================================================================
# HELPERS — compact expression pretty-printer for plan display
# ===========================================================================

def _expr_compact(expr: Optional[ast.Expression]) -> str:
    """Return a single-line string representation of an expression."""
    if expr is None:
        return ""
    if isinstance(expr, ast.Literal):
        if expr.kind == "NULL":
            return "NULL"
        if expr.kind == "TEXT":
            return f"'{expr.value}'"
        return str(expr.value)
    if isinstance(expr, ast.ColumnRef):
        if expr.table:
            return f"{expr.table}.{expr.column}"
        return expr.column
    if isinstance(expr, ast.Star):
        if expr.table:
            return f"{expr.table}.*"
        return "*"
    if isinstance(expr, ast.BinaryOp):
        return f"({_expr_compact(expr.left)} {expr.op} {_expr_compact(expr.right)})"
    if isinstance(expr, ast.UnaryOp):
        return f"{expr.op}({_expr_compact(expr.operand)})"
    if isinstance(expr, ast.BetweenExpr):
        neg = "NOT " if expr.negated else ""
        return (
            f"{_expr_compact(expr.expr)} {neg}BETWEEN "
            f"{_expr_compact(expr.lo)} AND {_expr_compact(expr.hi)}"
        )
    if isinstance(expr, ast.InListExpr):
        vals = ", ".join(_expr_compact(v) for v in expr.values)
        neg = "NOT " if expr.negated else ""
        return f"{_expr_compact(expr.expr)} {neg}IN ({vals})"
    if isinstance(expr, ast.LikeExpr):
        neg = "NOT " if expr.negated else ""
        return f"{_expr_compact(expr.expr)} {neg}LIKE {_expr_compact(expr.pattern)}"
    if isinstance(expr, ast.IsNullExpr):
        neg = " NOT" if expr.negated else ""
        return f"{_expr_compact(expr.expr)} IS{neg} NULL"
    if isinstance(expr, ast.FunctionCall):
        arg = "*" if expr.arg is None else _expr_compact(expr.arg)
        return f"{expr.func}({arg})"
    return "?"


def _select_item_compact(item: ast.SelectItem) -> str:
    s = _expr_compact(item.expr)
    if item.alias:
        s += f" AS {item.alias}"
    return s
