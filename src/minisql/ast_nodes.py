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
# AST pretty printer
# ---------------------------------------------------------------------------

def _indent(level: int) -> str:
    return "  " * level


def pretty_print(node: Node, level: int = 0) -> str:
    """Recursively produce a human-readable textual dump of the AST."""
    ind   = _indent(level)
    name  = type(node).__name__

    if isinstance(node, Program):
        lines = [f"{ind}Program:"]
        for s in node.statements:
            lines.append(pretty_print(s, level + 1))
        return "\n".join(lines)

    if isinstance(node, Literal):
        return f"{ind}Literal({node.kind}, {node.value!r})"

    if isinstance(node, ColumnRef):
        qual = f"{node.table}." if node.table else ""
        return f"{ind}ColumnRef({qual}{node.column})"

    if isinstance(node, Star):
        qual = f"{node.table}." if node.table else ""
        return f"{ind}Star({qual}*)"

    if isinstance(node, BinaryOp):
        lines = [f"{ind}BinaryOp({node.op!r})"]
        lines.append(pretty_print(node.left,  level + 1))
        lines.append(pretty_print(node.right, level + 1))
        return "\n".join(lines)

    if isinstance(node, UnaryOp):
        lines = [f"{ind}UnaryOp({node.op!r})"]
        lines.append(pretty_print(node.operand, level + 1))
        return "\n".join(lines)

    if isinstance(node, BetweenExpr):
        lines = [f"{ind}Between(negated={node.negated})"]
        lines.append(pretty_print(node.expr, level + 1))
        lines.append(pretty_print(node.lo,   level + 1))
        lines.append(pretty_print(node.hi,   level + 1))
        return "\n".join(lines)

    if isinstance(node, InListExpr):
        lines = [f"{ind}In(negated={node.negated})"]
        lines.append(pretty_print(node.expr, level + 1))
        for v in node.values:
            lines.append(pretty_print(v, level + 1))
        return "\n".join(lines)

    if isinstance(node, LikeExpr):
        lines = [f"{ind}Like(negated={node.negated})"]
        lines.append(pretty_print(node.expr,    level + 1))
        lines.append(pretty_print(node.pattern, level + 1))
        return "\n".join(lines)

    if isinstance(node, IsNullExpr):
        lines = [f"{ind}IsNull(negated={node.negated})"]
        lines.append(pretty_print(node.expr, level + 1))
        return "\n".join(lines)

    if isinstance(node, FunctionCall):
        arg_str = "*" if node.arg is None else ""
        lines = [f"{ind}FunctionCall({node.func}, {arg_str})"]
        if node.arg is not None:
            lines.append(pretty_print(node.arg, level + 1))
        return "\n".join(lines)

    if isinstance(node, SelectItem):
        alias = f" AS {node.alias}" if node.alias else ""
        lines = [f"{ind}SelectItem{alias}"]
        lines.append(pretty_print(node.expr, level + 1))
        return "\n".join(lines)

    if isinstance(node, TableRef):
        alias = f" AS {node.alias}" if node.alias else ""
        return f"{ind}TableRef({node.name}{alias})"

    if isinstance(node, JoinClause):
        lines = [f"{ind}JoinClause({node.join_type})"]
        lines.append(pretty_print(node.table, level + 1))
        if node.condition:
            lines.append(pretty_print(node.condition, level + 1))
        return "\n".join(lines)

    if isinstance(node, OrderByItem):
        lines = [f"{ind}OrderBy({node.direction})"]
        lines.append(pretty_print(node.expr, level + 1))
        return "\n".join(lines)

    if isinstance(node, ColumnDef):
        pk = " PK" if node.primary_key else ""
        return f"{ind}ColumnDef({node.name}: {node.type_name}{pk})"

    if isinstance(node, CreateTableStmt):
        lines = [f"{ind}CreateTable({node.table_name})"]
        for col in node.columns:
            lines.append(pretty_print(col, level + 1))
        return "\n".join(lines)

    if isinstance(node, InsertStmt):
        cols = ", ".join(node.columns) if node.columns else "<all>"
        lines = [f"{ind}Insert({node.table_name}, cols=[{cols}])"]
        for row in node.value_rows:
            lines.append(f"{_indent(level+1)}Row:")
            for v in row:
                lines.append(pretty_print(v, level + 2))
        return "\n".join(lines)

    if isinstance(node, SelectStmt):
        d = " DISTINCT" if node.distinct else ""
        lines = [f"{ind}Select{d}"]
        for item in node.items:
            lines.append(pretty_print(item, level + 1))
        if node.from_table:
            lines.append(pretty_print(node.from_table, level + 1))
        for j in node.joins:
            lines.append(pretty_print(j, level + 1))
        if node.where:
            lines.append(f"{_indent(level+1)}Where:")
            lines.append(pretty_print(node.where, level + 2))
        if node.group_by:
            lines.append(f"{_indent(level+1)}GroupBy:")
            for g in node.group_by:
                lines.append(pretty_print(g, level + 2))
        if node.having:
            lines.append(f"{_indent(level+1)}Having:")
            lines.append(pretty_print(node.having, level + 2))
        if node.order_by:
            lines.append(f"{_indent(level+1)}OrderBy:")
            for o in node.order_by:
                lines.append(pretty_print(o, level + 2))
        if node.limit is not None:
            lines.append(f"{_indent(level+1)}Limit({node.limit})")
        return "\n".join(lines)

    if isinstance(node, Assignment):
        lines = [f"{ind}Set({node.column})"]
        lines.append(pretty_print(node.value, level + 1))
        return "\n".join(lines)

    if isinstance(node, UpdateStmt):
        lines = [f"{ind}Update({node.table_name})"]
        for a in node.assignments:
            lines.append(pretty_print(a, level + 1))
        if node.where:
            lines.append(f"{_indent(level+1)}Where:")
            lines.append(pretty_print(node.where, level + 2))
        return "\n".join(lines)

    if isinstance(node, DeleteStmt):
        lines = [f"{ind}Delete({node.table_name})"]
        if node.where:
            lines.append(f"{_indent(level+1)}Where:")
            lines.append(pretty_print(node.where, level + 2))
        return "\n".join(lines)

    if isinstance(node, DropTableStmt):
        return f"{ind}DropTable({node.table_name})"

    # Fallback
    return f"{ind}{name}(...)"
