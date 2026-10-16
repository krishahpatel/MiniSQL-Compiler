"""
ast_nodes.py — Abstract Syntax Tree Node Definitions
=====================================================
All AST nodes are plain Python dataclasses.  Each node carries the
source location (line, col) for error messages.

Node hierarchy (abbreviated):

  Statement
    CreateTableStmt
    InsertStmt
    SelectStmt
    UpdateStmt
    DeleteStmt
    DropTableStmt

  Expression
    Literal            -- INT, FLOAT, TEXT, BOOL, NULL
    ColumnRef          -- [table.]column
    BinaryOp           -- e.g. a + b, a AND b, a > b
    UnaryOp            -- -expr, NOT expr
    BetweenExpr        -- expr BETWEEN lo AND hi
    InListExpr         -- expr IN (v1, v2, ...)
    LikeExpr           -- expr LIKE pattern
    IsNullExpr         -- expr IS [NOT] NULL
    FunctionCall       -- COUNT(*), SUM(x), ...
    Star               -- * in SELECT list

After semantic analysis each expression node gains a `resolved_type`
attribute set by the semantic analyser.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Any


# ---------------------------------------------------------------------------
# Base classes
# ---------------------------------------------------------------------------

@dataclass
class Node:
    """Root of the AST hierarchy.  Carries source location."""
    line: int = field(default=0, repr=False)
    col:  int = field(default=0, repr=False)


@dataclass
class Statement(Node):
    """Any top-level SQL statement."""
    pass


@dataclass
class Expression(Node):
    """Any expression node.  Semantic analyser sets resolved_type."""
    resolved_type: Optional[str] = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Literals
# ---------------------------------------------------------------------------

@dataclass
class Literal(Expression):
    """
    A constant value: integer, float, string, boolean, or NULL.

    Attributes
    ----------
    value : int | float | str | bool | None
    kind  : 'INT' | 'FLOAT' | 'TEXT' | 'BOOL' | 'NULL'
    """
    value: Any   = None
    kind:  str   = "NULL"

    def __post_init__(self):
        # Auto-set resolved_type from kind for convenience
        if self.resolved_type is None:
            self.resolved_type = self.kind


# ---------------------------------------------------------------------------
# Column reference
# ---------------------------------------------------------------------------

@dataclass
class ColumnRef(Expression):
    """
    A reference to a column, optionally qualified with a table/alias.

    Examples: salary  |  e.salary  |  employees.salary
    """
    table:  Optional[str] = None   # qualifier (may be alias)
    column: str = ""
    # Filled by semantic analyser:
    resolved_table: Optional[str] = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Star  (SELECT *)
# ---------------------------------------------------------------------------

@dataclass
class Star(Expression):
    """The * wildcard in a SELECT list or COUNT(*)."""
    table: Optional[str] = None   # for table.*


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

@dataclass
class BinaryOp(Expression):
    """
    A binary operation: arithmetic, comparison, or logical.

    op values: + - * / %  |  = != <> < <= > >=  |  AND OR  |  LIKE
    """
    op:    str        = ""
    left:  Expression = field(default_factory=lambda: Literal())
    right: Expression = field(default_factory=lambda: Literal())


@dataclass
class UnaryOp(Expression):
    """
    A unary operation.

    op values: - (negation) | NOT (boolean)
    """
    op:      str        = ""
    operand: Expression = field(default_factory=lambda: Literal())


# ---------------------------------------------------------------------------
# Special expressions
# ---------------------------------------------------------------------------

@dataclass
class BetweenExpr(Expression):
    """expr BETWEEN lo AND hi  (equivalent to lo <= expr AND expr <= hi)."""
    expr:    Expression = field(default_factory=lambda: Literal())
    lo:      Expression = field(default_factory=lambda: Literal())
    hi:      Expression = field(default_factory=lambda: Literal())
    negated: bool       = False   # NOT BETWEEN


@dataclass
class InListExpr(Expression):
    """expr IN (v1, v2, ...)."""
    expr:    Expression       = field(default_factory=lambda: Literal())
    values:  List[Expression] = field(default_factory=list)
    negated: bool             = False   # NOT IN


@dataclass
class LikeExpr(Expression):
    """expr LIKE pattern  (pattern is a string literal with % and _)."""
    expr:    Expression = field(default_factory=lambda: Literal())
    pattern: Expression = field(default_factory=lambda: Literal())
    negated: bool       = False   # NOT LIKE


@dataclass
class IsNullExpr(Expression):
    """expr IS [NOT] NULL."""
    expr:    Expression = field(default_factory=lambda: Literal())
    negated: bool       = False   # IS NOT NULL


@dataclass
class FunctionCall(Expression):
    """
    Aggregate function call: COUNT(*), COUNT(col), SUM(x), AVG(x), MIN(x), MAX(x).

    Attributes
    ----------
    func : 'COUNT' | 'SUM' | 'AVG' | 'MIN' | 'MAX'
    arg  : Expression | None  -- None for COUNT(*)
    """
    func: str                  = ""
    arg:  Optional[Expression] = None    # None means COUNT(*)


# ---------------------------------------------------------------------------
# SELECT statement building blocks
# ---------------------------------------------------------------------------

@dataclass
class SelectItem(Node):
    """
    One item in a SELECT list.

    Examples:
      *               -> expr=Star(), alias=None
      salary          -> expr=ColumnRef('salary'), alias=None
      salary * 1.1 AS new_sal -> expr=BinaryOp(...), alias='new_sal'
      COUNT(*) AS cnt -> expr=FunctionCall(...), alias='cnt'
    """
    expr:  Expression      = field(default_factory=Star)
    alias: Optional[str]   = None


@dataclass
class TableRef(Node):
    """
    A table reference in FROM or JOIN.

    Attributes
    ----------
    name  : table name
    alias : optional AS alias
    """
    name:  str           = ""
    alias: Optional[str] = None


@dataclass
class JoinClause(Node):
    """
    A JOIN clause.

    join_type: 'INNER' | 'LEFT' | 'RIGHT' | 'FULL' | 'CROSS'
    condition: ON expression
    """
    join_type: str          = "INNER"
    table:     TableRef     = field(default_factory=TableRef)
    condition: Optional[Expression] = None


@dataclass
class OrderByItem(Node):
    """One column in an ORDER BY list."""
    expr:      Expression = field(default_factory=lambda: ColumnRef())
    direction: str        = "ASC"   # 'ASC' | 'DESC'


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------

@dataclass
class ColumnDef(Node):
    """Column definition inside CREATE TABLE."""
    name:        str  = ""
    type_name:   str  = ""   # 'INT' | 'FLOAT' | 'TEXT' | 'BOOL'
    primary_key: bool = False


@dataclass
class CreateTableStmt(Statement):
    """CREATE TABLE name (col_def, ...) ;"""
    table_name: str             = ""
    columns:    List[ColumnDef] = field(default_factory=list)


@dataclass
class InsertStmt(Statement):
    """INSERT INTO name [(cols)] VALUES (...), (...) ;"""
    table_name:  str                    = ""
    columns:     Optional[List[str]]    = None   # None = all columns in order
    value_rows:  List[List[Expression]] = field(default_factory=list)


@dataclass
class SelectStmt(Statement):
    """Full SELECT statement."""
    distinct:    bool             = False
    items:       List[SelectItem] = field(default_factory=list)
    from_table:  Optional[TableRef] = None
    joins:       List[JoinClause]  = field(default_factory=list)
    where:       Optional[Expression] = None
    group_by:    List[Expression]     = field(default_factory=list)
    having:      Optional[Expression] = None
    order_by:    List[OrderByItem]    = field(default_factory=list)
    limit:       Optional[int]        = None


@dataclass
class Assignment(Node):
    """col = expr  inside UPDATE SET."""
    column: str        = ""
    value:  Expression = field(default_factory=lambda: Literal())


@dataclass
class UpdateStmt(Statement):
    """UPDATE name SET col=expr, ... [WHERE expr] ;"""
    table_name:  str             = ""
    assignments: List[Assignment] = field(default_factory=list)
    where:       Optional[Expression] = None


@dataclass
class DeleteStmt(Statement):
    """DELETE FROM name [WHERE expr] ;"""
    table_name: str               = ""
    where:      Optional[Expression] = None


@dataclass
class DropTableStmt(Statement):
    """DROP TABLE name ;"""
    table_name: str = ""


@dataclass
class Program(Node):
    """The root of the AST: a list of statements."""
    statements: List[Statement] = field(default_factory=list)


# ---------------------------------------------------------------------------
# AST tree printer
# ---------------------------------------------------------------------------

@dataclass
class _TreeGroup:
    label: str
    children: List[tuple[str, Any]]


def pretty_print(node: Node, level: int = 0) -> str:
    """Render the AST with branch connectors to show parent/child structure."""

    def tree_parts(current: Node) -> tuple[str, List[tuple[str, Any]]]:
        if isinstance(current, Program):
            return "Program", [
                (f"Statement {index}", statement)
                for index, statement in enumerate(current.statements, start=1)
            ]
        if isinstance(current, Literal):
            return f"Literal({current.kind}, {current.value!r})", []
        if isinstance(current, ColumnRef):
            qualifier = f"{current.table}." if current.table else ""
            return f"ColumnRef({qualifier}{current.column})", []
        if isinstance(current, Star):
            qualifier = f"{current.table}." if current.table else ""
            return f"Star({qualifier}*)", []
        if isinstance(current, BinaryOp):
            return f"BinaryOp({current.op})", [
                ("Left", current.left),
                ("Right", current.right),
            ]
        if isinstance(current, UnaryOp):
            return f"UnaryOp({current.op})", [("Operand", current.operand)]
        if isinstance(current, BetweenExpr):
            return f"Between(negated={current.negated})", [
                ("Expression", current.expr),
                ("Lower bound", current.lo),
                ("Upper bound", current.hi),
            ]
        if isinstance(current, InListExpr):
            children = [("Expression", current.expr)]
            children.extend(
                (f"Value {index}", value)
                for index, value in enumerate(current.values, start=1)
            )
            return f"In(negated={current.negated})", children
        if isinstance(current, LikeExpr):
            return f"Like(negated={current.negated})", [
                ("Expression", current.expr),
                ("Pattern", current.pattern),
            ]
        if isinstance(current, IsNullExpr):
            return f"IsNull(negated={current.negated})", [
                ("Expression", current.expr)
            ]
        if isinstance(current, FunctionCall):
            label = f"FunctionCall({current.func})"
            if current.arg is None:
                return f"{label}(*)", []
            return label, [("Argument", current.arg)]
        if isinstance(current, SelectItem):
            label = "SelectItem"
            if current.alias:
                label += f" AS {current.alias}"
            return label, [("Expression", current.expr)]
        if isinstance(current, TableRef):
            label = f"Table({current.name})"
            if current.alias:
                label += f" AS {current.alias}"
            return label, []
        if isinstance(current, JoinClause):
            children = [("Table", current.table)]
            if current.condition is not None:
                children.append(("ON", current.condition))
            return f"Join({current.join_type})", children
        if isinstance(current, OrderByItem):
            return f"OrderBy({current.direction})", [("Expression", current.expr)]
        if isinstance(current, ColumnDef):
            primary_key = ", PRIMARY KEY" if current.primary_key else ""
            return f"Column({current.name}: {current.type_name}{primary_key})", []
        if isinstance(current, CreateTableStmt):
            return f"CreateTable({current.table_name})", [
                ("Column", column) for column in current.columns
            ]
        if isinstance(current, InsertStmt):
            columns = ", ".join(current.columns) if current.columns else "<all>"
            children = []
            for row_index, row in enumerate(current.value_rows, start=1):
                children.append((
                    f"Values row {row_index}",
                    _TreeGroup("Row", [
                        (f"Value {index}", value)
                        for index, value in enumerate(row, start=1)
                    ]),
                ))
            return f"Insert({current.table_name}, columns=[{columns}])", children
        if isinstance(current, SelectStmt):
            children = [(
                "Projection",
                _TreeGroup("Select List", [
                    (f"Item {index}", item)
                    for index, item in enumerate(current.items, start=1)
                ]),
            )]
            if current.from_table:
                children.append(("From", current.from_table))
            children.extend(("Join", join) for join in current.joins)
            if current.where:
                children.append(("Where", current.where))
            if current.group_by:
                children.append((
                    "Group By",
                    _TreeGroup("Expressions", [
                        (f"Expression {index}", expression)
                        for index, expression in enumerate(current.group_by, start=1)
                    ]),
                ))
            if current.having:
                children.append(("Having", current.having))
            if current.order_by:
                children.append((
                    "Order By",
                    _TreeGroup("Orderings", [
                        (f"Ordering {index}", order)
                        for index, order in enumerate(current.order_by, start=1)
                    ]),
                ))
            if current.limit is not None:
                children.append((
                    "Limit",
                    _TreeGroup(str(current.limit), []),
                ))
            return f"Select(distinct={current.distinct})", children
        if isinstance(current, Assignment):
            return f"Assignment({current.column})", [("Value", current.value)]
        if isinstance(current, UpdateStmt):
            children = [("Assignment", assignment) for assignment in current.assignments]
            if current.where:
                children.append(("Where", current.where))
            return f"Update({current.table_name})", children
        if isinstance(current, DeleteStmt):
            children = []
            if current.where:
                children.append(("Where", current.where))
            return f"Delete({current.table_name})", children
        if isinstance(current, DropTableStmt):
            return f"DropTable({current.table_name})", []
        return type(current).__name__, []

    def render(
        current: Node,
        prefix: str,
        is_last: bool,
        root: bool = False,
        edge_label: Optional[str] = None,
    ) -> List[str]:
        if isinstance(current, _TreeGroup):
            label, children = current.label, current.children
        else:
            label, children = tree_parts(current)
        if root:
            line = "  " * level + label
        else:
            connector = "└── " if is_last else "├── "
            branch_label = f"{edge_label}: " if edge_label else ""
            line = prefix + connector + branch_label + label
        child_prefix = (
            prefix if root else prefix + ("    " if is_last else "│   ")
        )
        lines = [line]
        for index, (child_label, child) in enumerate(children):
            lines.extend(
                render(
                    child,
                    child_prefix,
                    index == len(children) - 1,
                    edge_label=child_label,
                )
            )
        return lines

    return "\n".join(render(node, "", True, root=True))
