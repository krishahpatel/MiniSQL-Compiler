"""
semantic.py — Phase 3: Semantic Analysis
=========================================
Walks the AST produced by the parser and performs:

  1. Name resolution
     - Table references (must be in Catalog)
     - Column references (must exist in referenced tables; ambiguity detection)
     - Alias tracking (table aliases, column aliases)

  2. Type checking
     - Arithmetic operand types (TEXT + INT is illegal)
     - Comparison operand compatibility
     - WHERE / HAVING / JOIN ON must evaluate to BOOL
     - INSERT column count / type compatibility
     - UPDATE assignment type compatibility
     - BETWEEN: lo <= hi (best-effort constant check)

  3. Aggregate rules
     - Aggregate functions only allowed in SELECT / HAVING
     - Non-aggregated SELECT columns must appear in GROUP BY
     - HAVING only valid with GROUP BY

  4. Error enrichment
     - "Did you mean X?" suggestions for typos in table/column names

  5. AST Decoration
     - Sets expression.resolved_type on every expression node
     - Sets ColumnRef.resolved_table

The analyser is a Visitor that calls _analyse_<NodeType> methods.
"""

from __future__ import annotations
from typing import Dict, List, Optional, Set, Tuple

from .errors import ErrorReporter, did_you_mean
from .catalog import Catalog, TableSchema, ColumnSchema, wider_numeric_type
from . import ast_nodes as ast
from .ir import _expr_compact


# ---------------------------------------------------------------------------
# Type helpers
# ---------------------------------------------------------------------------

NUMERIC_TYPES: frozenset = frozenset({"INT", "FLOAT"})
BOOL_OPS:      frozenset = frozenset({"AND", "OR", "NOT"})
COMPARE_OPS:   frozenset = frozenset({"=", "!=", "<>", "<", "<=", ">", ">="})
ARITH_OPS:     frozenset = frozenset({"+", "-", "*", "/", "%"})


def _type_compatible(expected: str, actual: str) -> bool:
    """Check if *actual* type can be used where *expected* is needed."""
    if actual == "NULL":
        return True   # NULL is compatible with any type (three-valued logic)
    if expected == actual:
        return True
    # INT auto-widens to FLOAT
    if expected == "FLOAT" and actual == "INT":
        return True
    return False


def _resolve_binary_type(op: str, lt: str, rt: str) -> Optional[str]:
    """
    Return the result type of op(lt, rt), or None if the types are incompatible.

    Rules:
      + - * /  : both numeric -> wider type
      %        : both INT     -> INT
      =,!=,<>  : any same-kind -> BOOL
      <,<=,>,>=: numeric or TEXT -> BOOL
      AND, OR  : both BOOL -> BOOL
    """
    if op in ARITH_OPS:
        if op == "%" :
            if lt in ("INT", "NULL") and rt in ("INT", "NULL"):
                return "INT"
            return None
        return wider_numeric_type(lt, rt)

    if op in COMPARE_OPS:
        # Allow NULL on either side
        if lt == "NULL" or rt == "NULL":
            return "BOOL"
        # Same type or numeric widening
        if lt == rt:
            return "BOOL"
        if {lt, rt} <= NUMERIC_TYPES:
            return "BOOL"
        return None

    if op in BOOL_OPS:
        lt_ok = lt in ("BOOL", "NULL")
        rt_ok = rt in ("BOOL", "NULL")
        return "BOOL" if lt_ok and rt_ok else None

    if op == "LIKE":
        if lt in ("TEXT", "NULL") and rt in ("TEXT", "NULL"):
            return "BOOL"
        return None

    return None


# ---------------------------------------------------------------------------
# Semantic Analyser
# ---------------------------------------------------------------------------

class SemanticAnalyser:
    """
    Walks the AST, resolves names, checks types, and annotates nodes.

    Usage:
        analyser = SemanticAnalyser(catalog, reporter)
        analyser.analyse(program)
    """

    def __init__(self, catalog: Catalog, reporter: ErrorReporter):
        self.catalog  = catalog
        self.reporter = reporter
        # Per-query context (reset for each SELECT)
        self._visible_tables: List[str] = []
        self._in_aggregate_ctx: bool    = False
        self._group_by_cols: List[str]  = []
        self._select_agg_positions: Set[int] = set()
        self._report_entries: List[str] = []
        self._select_aliases: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def analyse(self, program: ast.Program) -> None:
        """Analyse all statements in the program in order."""
        self._report_entries = []
        for index, stmt in enumerate(program.statements, start=1):
            statement_name = type(stmt).__name__.replace("Stmt", "").upper()
            if statement_name == "CREATETABLE":
                statement_name = "CREATE TABLE"
            elif statement_name == "DROPTABLE":
                statement_name = "DROP TABLE"
            self._report_entries.append(f"Statement {index}: {statement_name}")
            self._analyse_stmt(stmt)

    def report(self) -> str:
        """Return details of the semantic checks performed."""
        lines = [
            "Semantic Analysis Report",
            "=" * 24,
            (
                "Status: FAILED"
                if self.reporter.has_errors()
                else "Status: PASSED"
            ),
        ]
        if self._report_entries:
            lines.extend(["", *self._report_entries])
        if self.reporter.has_errors():
            lines.extend(["", "Errors:"])
            lines.extend(f"  - {error}" for error in self.reporter.errors)
        return "\n".join(lines)

    def _report(self, detail: str) -> None:
        self._report_entries.append(f"  - {detail}")

    # ------------------------------------------------------------------
    # Statement dispatch
    # ------------------------------------------------------------------

    def _analyse_stmt(self, stmt: ast.Statement) -> None:
        if isinstance(stmt, ast.CreateTableStmt):
            self._analyse_create(stmt)
        elif isinstance(stmt, ast.InsertStmt):
            self._analyse_insert(stmt)
        elif isinstance(stmt, ast.SelectStmt):
            self._analyse_select(stmt)
        elif isinstance(stmt, ast.UpdateStmt):
            self._analyse_update(stmt)
        elif isinstance(stmt, ast.DeleteStmt):
            self._analyse_delete(stmt)
        elif isinstance(stmt, ast.DropTableStmt):
            self._analyse_drop(stmt)

    # ------------------------------------------------------------------
    # CREATE TABLE
    # ------------------------------------------------------------------

    def _analyse_create(self, stmt: ast.CreateTableStmt) -> None:
        from .catalog import ColumnSchema, TableSchema

        tname = stmt.table_name
        if self.catalog.has_table(tname):
            self.reporter.report(
                "Semantic",
                f"Table {tname!r} already exists",
                stmt.line, stmt.col,
            )
            return

        seen_cols: Dict[str, bool] = {}
        col_schemas = []
        for i, col in enumerate(stmt.columns):
            if col.name.lower() in seen_cols:
                self.reporter.report(
                    "Semantic",
                    f"Duplicate column {col.name!r} in CREATE TABLE {tname!r}",
                    col.line, col.col,
                )
            seen_cols[col.name.lower()] = True
            col_schemas.append(ColumnSchema(
                name=col.name,
                type_name=col.type_name,
                position=i,
                primary_key=col.primary_key,
            ))

        schema = TableSchema(name=tname, columns=col_schemas)
        try:
            self.catalog.add_table(schema)
            columns = ", ".join(
                f"{col.name}: {col.type_name}"
                + (" PRIMARY KEY" if col.primary_key else "")
                for col in col_schemas
            )
            self._report(f"Created schema for {tname} ({columns}).")
        except ValueError as exc:
            self.reporter.report("Semantic", str(exc), stmt.line, stmt.col)

    # ------------------------------------------------------------------
    # DROP TABLE
    # ------------------------------------------------------------------

    def _analyse_drop(self, stmt: ast.DropTableStmt) -> None:
        if not self.catalog.has_table(stmt.table_name):
            hint = did_you_mean(stmt.table_name, self.catalog.table_names())
            self.reporter.report(
                "Semantic",
                f"Table {stmt.table_name!r} does not exist",
                stmt.line, stmt.col,
                hint=f"did you mean {hint!r}?" if hint else None,
            )
        else:
            try:
                self.catalog.drop_table(stmt.table_name)
                self._report(f"Resolved and dropped table {stmt.table_name}.")
            except KeyError:
                pass

    # ------------------------------------------------------------------
    # INSERT
    # ------------------------------------------------------------------

    def _analyse_insert(self, stmt: ast.InsertStmt) -> None:
        schema = self._require_table(stmt.table_name, stmt.line, stmt.col)
        if schema is None:
            return
        self._report(f"Resolved INSERT target {schema.name}.")

        # Resolve target columns
        if stmt.columns is not None:
            targets = stmt.columns
            # Verify each named column exists
            for cname in targets:
                col_schema = schema.get_column(cname)
                if col_schema is None:
                    candidates = schema.column_names
                    hint = did_you_mean(cname, candidates)
                    self.reporter.report(
                        "Semantic",
                        f"Column {cname!r} does not exist in table {stmt.table_name!r}",
                        stmt.line, stmt.col,
                        hint=f"did you mean {hint!r}?" if hint else None,
                    )
        else:
            targets = schema.column_names
        self._report(f"Target columns: {', '.join(targets)}.")

        for row_index, row in enumerate(stmt.value_rows, start=1):
            if len(row) != len(targets):
                self.reporter.report(
                    "Semantic",
                    f"INSERT into {stmt.table_name!r} expects {len(targets)} "
                    f"values but got {len(row)}",
                    stmt.line, stmt.col,
                )
                continue
            for val_expr, cname in zip(row, targets):
                col_schema = schema.get_column(cname)
                if col_schema is None:
                    continue
                # Analyse the value expression (for type resolution)
                self._visible_tables = []
                errors_before = len(self.reporter.errors)
                self._analyse_expr(val_expr, [])
                vtype = val_expr.resolved_type or "NULL"
                if not _type_compatible(col_schema.type_name, vtype):
                    self.reporter.report(
                        "Semantic",
                        f"Type mismatch in INSERT: column {cname!r} has type "
                        f"{col_schema.type_name} but value has type {vtype}",
                        val_expr.line, val_expr.col,
                    )
                elif len(self.reporter.errors) == errors_before:
                    self._report(
                        f"VALUES row {row_index}: {cname} expects "
                        f"{col_schema.type_name}; expression resolves to {vtype}."
                    )

    # ------------------------------------------------------------------
    # UPDATE
    # ------------------------------------------------------------------

    def _analyse_update(self, stmt: ast.UpdateStmt) -> None:
        schema = self._require_table(stmt.table_name, stmt.line, stmt.col)
        if schema is None:
            return
        self._report(f"Resolved UPDATE target {schema.name}.")
        self.catalog.clear_aliases()
        self._visible_tables = [stmt.table_name]

        for assign in stmt.assignments:
            col_schema = schema.get_column(assign.column)
            if col_schema is None:
                candidates = schema.column_names
                hint = did_you_mean(assign.column, candidates)
                self.reporter.report(
                    "Semantic",
                    f"Column {assign.column!r} does not exist in {stmt.table_name!r}",
                    assign.line, assign.col,
                    hint=f"did you mean {hint!r}?" if hint else None,
                )
                continue
            errors_before = len(self.reporter.errors)
            self._analyse_expr(assign.value, self._visible_tables)
            vtype = assign.value.resolved_type or "NULL"
            if not _type_compatible(col_schema.type_name, vtype):
                self.reporter.report(
                    "Semantic",
                    f"Type mismatch: column {assign.column!r} is {col_schema.type_name}, "
                    f"but assigned type is {vtype}",
                    assign.line, assign.col,
                )
            elif len(self.reporter.errors) == errors_before:
                self._report(
                    f"Assignment {assign.column}: {vtype} is compatible with "
                    f"{col_schema.type_name}."
                )

        if stmt.where is not None:
            errors_before = len(self.reporter.errors)
            self._analyse_expr(stmt.where, self._visible_tables)
            self._require_bool(stmt.where, "WHERE")
            if (
                len(self.reporter.errors) == errors_before
                and stmt.where.resolved_type in ("BOOL", "NULL")
            ):
                self._report(
                    f"WHERE condition resolves to {stmt.where.resolved_type}: "
                    f"{_expr_compact(stmt.where)}."
                )

    # ------------------------------------------------------------------
    # DELETE
    # ------------------------------------------------------------------

    def _analyse_delete(self, stmt: ast.DeleteStmt) -> None:
        schema = self._require_table(stmt.table_name, stmt.line, stmt.col)
        if schema is None:
            return
        self._report(f"Resolved DELETE target {schema.name}.")
        self._visible_tables = [stmt.table_name]
        if stmt.where is not None:
            errors_before = len(self.reporter.errors)
            self._analyse_expr(stmt.where, self._visible_tables)
            self._require_bool(stmt.where, "WHERE")
            if (
                len(self.reporter.errors) == errors_before
                and stmt.where.resolved_type in ("BOOL", "NULL")
            ):
                self._report(
                    f"WHERE condition resolves to {stmt.where.resolved_type}: "
                    f"{_expr_compact(stmt.where)}."
                )

    # ------------------------------------------------------------------
    # SELECT
    # ------------------------------------------------------------------

    def _analyse_select(self, stmt: ast.SelectStmt) -> None:
        self.catalog.clear_aliases()
        self._visible_tables = []
        self._group_by_cols  = []
        self._in_aggregate_ctx = False

        # Register FROM table and aliases
        if stmt.from_table:
            self._register_table_ref(stmt.from_table)

        # Register JOINed tables
        for join in stmt.joins:
            self._register_table_ref(join.table)

        # Check JOIN ON conditions
        for join in stmt.joins:
            if join.condition is not None:
                errors_before = len(self.reporter.errors)
                self._analyse_expr(join.condition, self._visible_tables)
                self._require_bool(join.condition, "JOIN ON")
                if (
                    len(self.reporter.errors) == errors_before
                    and join.condition.resolved_type in ("BOOL", "NULL")
                ):
                    self._report(
                        f"{join.join_type} JOIN condition resolves to "
                        f"{join.condition.resolved_type}: "
                        f"{_expr_compact(join.condition)}."
                    )

        # WHERE
        if stmt.where is not None:
            errors_before = len(self.reporter.errors)
            self._analyse_expr(stmt.where, self._visible_tables)
            self._require_bool(stmt.where, "WHERE")
            if (
                len(self.reporter.errors) == errors_before
                and stmt.where.resolved_type in ("BOOL", "NULL")
            ):
                self._report(
                    f"WHERE condition resolves to {stmt.where.resolved_type}: "
                    f"{_expr_compact(stmt.where)}."
                )

        # GROUP BY
        if stmt.group_by:
            for g in stmt.group_by:
                errors_before = len(self.reporter.errors)
                self._analyse_expr(g, self._visible_tables)
                if len(self.reporter.errors) == errors_before:
                    self._report(
                        f"GROUP BY expression resolves to "
                        f"{g.resolved_type or 'NULL'}: {_expr_compact(g)}."
                    )
                # Track group-by column names for aggregate validation
                if isinstance(g, ast.ColumnRef):
                    self._group_by_cols.append(g.column.lower())

        # HAVING — requires GROUP BY
        if stmt.having is not None:
            if not stmt.group_by:
                self.reporter.report(
                    "Semantic",
                    "HAVING clause requires a GROUP BY clause",
                    stmt.having.line, stmt.having.col,
                )
            self._in_aggregate_ctx = True
            errors_before = len(self.reporter.errors)
            self._analyse_expr(stmt.having, self._visible_tables)
            self._require_bool(stmt.having, "HAVING")
            if (
                len(self.reporter.errors) == errors_before
                and stmt.having.resolved_type in ("BOOL", "NULL")
            ):
                self._report(
                    f"HAVING condition resolves to {stmt.having.resolved_type}: "
                    f"{_expr_compact(stmt.having)}."
                )
            self._in_aggregate_ctx = False

        # SELECT list
        has_agg   = False
        has_plain = False
        self._in_aggregate_ctx = bool(stmt.group_by or stmt.having)
        self._select_aliases = {}

        for item in stmt.items:
            if isinstance(item.expr, ast.Star):
                item.expr.resolved_type = "TEXT"  # placeholder
                has_plain = True
                if item.alias:
                    self._select_aliases[item.alias.lower()] = item.expr.resolved_type
                self._report("SELECT * expands to all columns from visible tables.")
                continue
            errors_before = len(self.reporter.errors)
            self._analyse_expr(item.expr, self._visible_tables)
            if item.alias:
                self._select_aliases[item.alias.lower()] = (
                    item.expr.resolved_type or "NULL"
                )
            if len(self.reporter.errors) == errors_before:
                self._report(
                    f"SELECT expression {_expr_compact(item.expr)} resolves to "
                    f"{item.expr.resolved_type or 'NULL'}"
                    + (f" AS {item.alias}" if item.alias else "")
                    + "."
                )
            if _expr_has_aggregate(item.expr):
                has_agg = True
            else:
                has_plain = True

        # ORDER BY
        for ob in stmt.order_by:
            errors_before = len(self.reporter.errors)
            self._analyse_expr(ob.expr, self._visible_tables)
            if len(self.reporter.errors) == errors_before:
                self._report(
                    f"ORDER BY expression {_expr_compact(ob.expr)} resolves to "
                    f"{ob.expr.resolved_type or 'NULL'} ({ob.direction})."
                )

        # Aggregate coherence: if any SELECT item is an aggregate, all
        # non-aliased plain columns must appear in GROUP BY
        if has_agg and stmt.group_by:
            for item in stmt.items:
                if isinstance(item.expr, (ast.Star, ast.FunctionCall)):
                    continue
                if _expr_has_aggregate(item.expr):
                    continue
                if isinstance(item.expr, ast.ColumnRef):
                    cname = item.expr.column.lower()
                    if cname not in self._group_by_cols:
                        self.reporter.report(
                            "Semantic",
                            f"Column {item.expr.column!r} in SELECT list must appear "
                            f"in GROUP BY when aggregates are used",
                            item.expr.line, item.expr.col,
                        )

        self._in_aggregate_ctx = False
        if stmt.group_by and (has_agg or stmt.having is not None):
            self._report("Aggregate and GROUP BY coherence checks passed.")

    # ------------------------------------------------------------------
    # Expression analysis (visitor)
    # ------------------------------------------------------------------

    def _analyse_expr(
        self, expr: ast.Expression, visible: List[str]
    ) -> None:
        """Recursively analyse and type-annotate *expr*."""

        if isinstance(expr, ast.Literal):
            # Already has resolved_type from __post_init__
            return

        if isinstance(expr, ast.Star):
            expr.resolved_type = "TEXT"
            return

        if isinstance(expr, ast.ColumnRef):
            self._analyse_column_ref(expr, visible)
            return

        if isinstance(expr, ast.BinaryOp):
            self._analyse_expr(expr.left,  visible)
            self._analyse_expr(expr.right, visible)
            lt = expr.left.resolved_type  or "NULL"
            rt = expr.right.resolved_type or "NULL"
            rtype = _resolve_binary_type(expr.op, lt, rt)
            if rtype is None:
                self.reporter.report(
                    "Semantic",
                    f"Type error: cannot apply {expr.op!r} to {lt} and {rt}",
                    expr.line, expr.col,
                )
                expr.resolved_type = "NULL"
            else:
                expr.resolved_type = rtype
            return

        if isinstance(expr, ast.UnaryOp):
            self._analyse_expr(expr.operand, visible)
            ot = expr.operand.resolved_type or "NULL"
            if expr.op == "-":
                if ot not in NUMERIC_TYPES and ot != "NULL":
                    self.reporter.report(
                        "Semantic",
                        f"Unary '-' requires a numeric type, got {ot}",
                        expr.line, expr.col,
                    )
                expr.resolved_type = ot
            elif expr.op == "NOT":
                if ot not in ("BOOL", "NULL"):
                    self.reporter.report(
                        "Semantic",
                        f"NOT requires a BOOL operand, got {ot}",
                        expr.line, expr.col,
                    )
                expr.resolved_type = "BOOL"
            return

        if isinstance(expr, ast.BetweenExpr):
            self._analyse_expr(expr.expr, visible)
            self._analyse_expr(expr.lo,   visible)
            self._analyse_expr(expr.hi,   visible)
            expr.resolved_type = "BOOL"
            return

        if isinstance(expr, ast.InListExpr):
            self._analyse_expr(expr.expr, visible)
            for v in expr.values:
                self._analyse_expr(v, visible)
            expr.resolved_type = "BOOL"
            return

        if isinstance(expr, ast.LikeExpr):
            self._analyse_expr(expr.expr,    visible)
            self._analyse_expr(expr.pattern, visible)
            et = expr.expr.resolved_type or "NULL"
            if et not in ("TEXT", "NULL"):
                self.reporter.report(
                    "Semantic",
                    f"LIKE requires TEXT operand, got {et}",
                    expr.line, expr.col,
                )
            expr.resolved_type = "BOOL"
            return

        if isinstance(expr, ast.IsNullExpr):
            self._analyse_expr(expr.expr, visible)
            expr.resolved_type = "BOOL"
            return

        if isinstance(expr, ast.FunctionCall):
            self._analyse_func(expr, visible)
            return

    def _analyse_column_ref(
        self, expr: ast.ColumnRef, visible: List[str]
    ) -> None:
        tname, col_schema = self.catalog.resolve_column(
            expr.column, table=expr.table, visible=visible
        )
        if (
            col_schema is None
            and not expr.table
            and expr.column.lower() in self._select_aliases
        ):
            expr.resolved_type = self._select_aliases[expr.column.lower()]
            self._report(
                f"Resolved SELECT alias {expr.column} to output type "
                f"{expr.resolved_type}."
            )
            return

        if col_schema is None:
            if expr.table:
                # Check if table exists first
                real = self.catalog.resolve_alias(expr.table)
                tbl_schema = self.catalog.get_table(expr.table)
                if tbl_schema is None:
                    self.reporter.report(
                        "Semantic",
                        f"Unknown table {expr.table!r}",
                        expr.line, expr.col,
                    )
                else:
                    candidates = tbl_schema.column_names
                    hint = did_you_mean(expr.column, candidates)
                    self.reporter.report(
                        "Semantic",
                        f"Column {expr.column!r} does not exist in {expr.table!r}",
                        expr.line, expr.col,
                        hint=f"did you mean {hint!r}?" if hint else None,
                    )
            else:
                # Unqualified: gather all candidates from visible tables
                all_cols: List[str] = []
                for tn in (visible or self.catalog.table_names()):
                    real = self.catalog.resolve_alias(tn) or tn
                    tbl = self.catalog.get_table(real)
                    if tbl:
                        all_cols.extend(tbl.column_names)

                # Check if ambiguous
                matching = [c for c in all_cols if c.lower() == expr.column.lower()]
                if len(matching) > 1:
                    self.reporter.report(
                        "Semantic",
                        f"Ambiguous column {expr.column!r} — qualify with table name",
                        expr.line, expr.col,
                    )
                else:
                    hint = did_you_mean(expr.column, list(set(all_cols)))
                    self.reporter.report(
                        "Semantic",
                        f"Column {expr.column!r} not found in any visible table",
                        expr.line, expr.col,
                        hint=f"did you mean {hint!r}?" if hint else None,
                    )
            expr.resolved_type = "NULL"
            return

        expr.resolved_table = tname
        expr.resolved_type  = col_schema.type_name
        qualifier = f"{expr.table}." if expr.table else ""
        self._report(
            f"Resolved {qualifier}{expr.column} to "
            f"{tname}.{col_schema.name} ({col_schema.type_name})."
        )

    def _analyse_func(
        self, expr: ast.FunctionCall, visible: List[str]
    ) -> None:
        if expr.arg is not None:
            self._analyse_expr(expr.arg, visible)

        NUMERIC_AGG = {"SUM", "AVG"}
        if expr.func in NUMERIC_AGG and expr.arg is not None:
            at = expr.arg.resolved_type or "NULL"
            if at not in NUMERIC_TYPES and at != "NULL":
                self.reporter.report(
                    "Semantic",
                    f"{expr.func}() requires a numeric argument, got {at}",
                    expr.line, expr.col,
                )
            expr.resolved_type = "FLOAT" if expr.func == "AVG" else at
        elif expr.func in ("MIN", "MAX"):
            expr.resolved_type = (
                expr.arg.resolved_type if expr.arg else "NULL"
            ) or "NULL"
        else:  # COUNT
            expr.resolved_type = "INT"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _require_table(
        self, name: str, line: int, col: int
    ) -> Optional[TableSchema]:
        schema = self.catalog.get_table(name)
        if schema is None:
            hint = did_you_mean(name, self.catalog.table_names())
            self.reporter.report(
                "Semantic",
                f"Table {name!r} does not exist",
                line, col,
                hint=f"did you mean {hint!r}?" if hint else None,
            )
        return schema

    def _register_table_ref(self, tref: ast.TableRef) -> None:
        """Validate table reference and register its alias."""
        schema = self.catalog.get_table(tref.name)
        if schema is None:
            hint = did_you_mean(tref.name, self.catalog.table_names())
            self.reporter.report(
                "Semantic",
                f"Table {tref.name!r} does not exist",
                tref.line, tref.col,
                hint=f"did you mean {hint!r}?" if hint else None,
            )
            return
        self._visible_tables.append(tref.name)
        if tref.alias:
            self.catalog.set_alias(tref.alias, tref.name)
            self._visible_tables.append(tref.alias)
        alias_detail = f" AS {tref.alias}" if tref.alias else ""
        columns = ", ".join(
            f"{col.name}: {col.type_name}" for col in schema.columns
        )
        self._report(
            f"Resolved table {schema.name}{alias_detail}; columns: {columns}."
        )

    def _require_bool(self, expr: ast.Expression, ctx: str) -> None:
        """Emit an error if *expr* doesn't resolve to BOOL."""
        t = expr.resolved_type
        if t is not None and t not in ("BOOL", "NULL"):
            self.reporter.report(
                "Semantic",
                f"{ctx} condition must be BOOL but got {t}",
                expr.line, expr.col,
            )


# ---------------------------------------------------------------------------
# Helper: does an expression contain any aggregate function?
# ---------------------------------------------------------------------------

def _expr_has_aggregate(expr: ast.Expression) -> bool:
    if isinstance(expr, ast.FunctionCall):
        return True
    if isinstance(expr, ast.BinaryOp):
        return _expr_has_aggregate(expr.left) or _expr_has_aggregate(expr.right)
    if isinstance(expr, ast.UnaryOp):
        return _expr_has_aggregate(expr.operand)
    if isinstance(expr, ast.BetweenExpr):
        return any(_expr_has_aggregate(e) for e in (expr.expr, expr.lo, expr.hi))
    if isinstance(expr, ast.InListExpr):
        return _expr_has_aggregate(expr.expr) or any(_expr_has_aggregate(v) for v in expr.values)
    return False


# ---------------------------------------------------------------------------
# Public convenience function
# ---------------------------------------------------------------------------

def analyse(
    program: ast.Program,
    catalog: Catalog,
    reporter: ErrorReporter,
) -> None:
    """Run semantic analysis on *program*, updating *catalog* and *reporter*."""
    SemanticAnalyser(catalog, reporter).analyse(program)
