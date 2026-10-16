"""
irgen.py — Phase 4: Intermediate Code Generation
=================================================
Translates the decorated AST (from semantic analysis) into:

  1. A Logical Query Plan (relational algebra tree, from ir.py)
  2. A Three-Address Code (TAC) representation for expressions

The translation is syntax-directed: each AST node maps to a specific
plan node via _gen_<NodeType> methods (like an attribute grammar where
the synthesised attribute is the plan node).

Key transformations:
  SELECT * FROM t WHERE p ORDER BY c LIMIT n
    ->
  Limit(n, Sort(tau_c, Project(pi_*, Filter(sigma_p, Scan(t)))))

  SELECT a, SUM(b) FROM t GROUP BY a HAVING SUM(b) > 100
    ->
  Filter(sigma_SUM(b)>100, Aggregate(gamma_a;SUM(b), Scan(t)))

TAC generation visits every expression node bottom-up, assigning each
sub-expression to a fresh temporary variable t0, t1, t2, ...
"""

from __future__ import annotations
from typing import List, Optional

from . import ast_nodes as ast
from .ir import (
    PlanNode, ScanNode, FilterNode, ProjectNode, JoinNode,
    AggregateNode, SortNode, LimitNode, DistinctNode,
    InsertNode, UpdateNode, DeleteNode,
    TACProgram, TACInstr, _expr_compact,
)
from .catalog import Catalog


# ---------------------------------------------------------------------------
# IR Generator
# ---------------------------------------------------------------------------

class IRGenerator:
    """
    Translates a type-annotated AST into a logical plan tree + TAC.

    Usage:
        irgen = IRGenerator(catalog)
        plan  = irgen.generate(program)
        tac   = irgen.tac   # TACProgram for last query's expressions
    """

    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        self.tac     = TACProgram()
        self._plans: List[PlanNode] = []

    def generate(self, program: ast.Program) -> List[PlanNode]:
        """Generate one plan per statement in the program."""
        plans = []
        for stmt in program.statements:
            self.tac = TACProgram()   # fresh TAC per statement
            plan = self._gen_stmt(stmt)
            if plan is not None:
                plans.append(plan)
                self._plans.append(plan)
        return plans

    # ------------------------------------------------------------------
    # Statement dispatch
    # ------------------------------------------------------------------

    def _gen_stmt(self, stmt: ast.Statement) -> Optional[PlanNode]:
        if isinstance(stmt, ast.SelectStmt):
            return self._gen_select(stmt)
        if isinstance(stmt, ast.InsertStmt):
            return self._gen_insert(stmt)
        if isinstance(stmt, ast.UpdateStmt):
            return self._gen_update(stmt)
        if isinstance(stmt, ast.DeleteStmt):
            return self._gen_delete(stmt)
        # CREATE / DROP: no runtime plan needed (handled by semantic/storage)
        return None

    # ------------------------------------------------------------------
    # SELECT
    # ------------------------------------------------------------------

    def _gen_select(self, stmt: ast.SelectStmt) -> PlanNode:
        """
        Build the logical plan bottom-up:
          Scan -> (Join)* -> Filter -> Aggregate -> Project -> Distinct -> Sort -> Limit
        """
        # 1. Base scan
        if stmt.from_table is None:
            # SELECT without FROM (e.g., SELECT 1+1) — use a dummy single-row scan
            plan: PlanNode = _SingleRowNode()
        else:
            plan = ScanNode(
                table=stmt.from_table.name,
                alias=stmt.from_table.alias,
                estimated_rows=self.catalog.get_row_count(stmt.from_table.name),
            )

        # 2. Joins (left-deep tree)
        for join in stmt.joins:
            right = ScanNode(
                table=join.table.name,
                alias=join.table.alias,
                estimated_rows=self.catalog.get_row_count(join.table.name),
            )
            plan = JoinNode(
                left=plan,
                right=right,
                condition=join.condition,
                join_type=join.join_type,
            )

        # 3. WHERE filter
        if stmt.where is not None:
            self._gen_expr_tac(stmt.where)   # emit TAC for predicate
            plan = FilterNode(child=plan, predicate=stmt.where)

        # 4. Aggregation (GROUP BY / aggregate functions)
        agg_items   = [i for i in stmt.items if _has_agg(i.expr)]
        plain_items = [i for i in stmt.items if not _has_agg(i.expr)]

        if stmt.group_by or agg_items:
            plan = AggregateNode(
                child=plan,
                group_by=stmt.group_by,
                aggregates=agg_items,
                having=stmt.having,
            )
            # TAC for HAVING predicate
            if stmt.having:
                self._gen_expr_tac(stmt.having)

        # 5. Projection
        # If SELECT * we keep a wildcard project; else we project named cols
        if not (len(stmt.items) == 1 and isinstance(stmt.items[0].expr, ast.Star)):
            plan = ProjectNode(child=plan, columns=stmt.items)

        # 6. DISTINCT
        if stmt.distinct:
            plan = DistinctNode(child=plan)

        # 7. ORDER BY
        if stmt.order_by:
            plan = SortNode(child=plan, order_by=stmt.order_by)

        # 8. LIMIT
        if stmt.limit is not None:
            plan = LimitNode(child=plan, n=stmt.limit)

        return plan

    # ------------------------------------------------------------------
    # INSERT / UPDATE / DELETE
    # ------------------------------------------------------------------

    def _gen_insert(self, stmt: ast.InsertStmt) -> InsertNode:
        # Generate TAC for each value expression
        for row in stmt.value_rows:
            for expr in row:
                self._gen_expr_tac(expr)
        return InsertNode(
            table=stmt.table_name,
            columns=stmt.columns,
            value_rows=stmt.value_rows,
        )

    def _gen_update(self, stmt: ast.UpdateStmt) -> UpdateNode:
        for assign in stmt.assignments:
            self._gen_expr_tac(assign.value)
        if stmt.where:
            self._gen_expr_tac(stmt.where)
        return UpdateNode(
            table=stmt.table_name,
            assignments=stmt.assignments,
            predicate=stmt.where,
        )

    def _gen_delete(self, stmt: ast.DeleteStmt) -> DeleteNode:
        if stmt.where:
            self._gen_expr_tac(stmt.where)
        return DeleteNode(
            table=stmt.table_name,
            predicate=stmt.where,
        )

    # ------------------------------------------------------------------
    # TAC generation for expressions
    # ------------------------------------------------------------------

    def _gen_expr_tac(self, expr: ast.Expression) -> str:
        """
        Recursively emit TAC instructions for *expr*.
        Returns the name of the temporary holding the result.

        TAC representation (quadruples):
          (op, arg1, arg2, result)
          e.g. salary * 1.1  -> t0 = salary * 1.1
               t0 > 50000    -> t1 = t0 > 50000
        """
        if isinstance(expr, ast.Literal):
            t = self.tac.new_temp()
            self.tac.emit("COPY", arg1=repr(expr.value), result=t)
            return t

        if isinstance(expr, ast.ColumnRef):
            t = self.tac.new_temp()
            col = f"{expr.table}.{expr.column}" if expr.table else expr.column
            self.tac.emit("LOAD", arg1=col, result=t)
            return t

        if isinstance(expr, ast.BinaryOp):
            l = self._gen_expr_tac(expr.left)
            r = self._gen_expr_tac(expr.right)
            t = self.tac.new_temp()
            self.tac.emit(expr.op, arg1=l, arg2=r, result=t)
            return t

        if isinstance(expr, ast.UnaryOp):
            o = self._gen_expr_tac(expr.operand)
            t = self.tac.new_temp()
            self.tac.emit(f"UNARY_{expr.op}", arg1=o, result=t)
            return t

        if isinstance(expr, ast.BetweenExpr):
            # x BETWEEN lo AND hi  -> (x >= lo) AND (x <= hi)
            ex = self._gen_expr_tac(expr.expr)
            lo = self._gen_expr_tac(expr.lo)
            hi = self._gen_expr_tac(expr.hi)
            t1 = self.tac.new_temp()
            t2 = self.tac.new_temp()
            t3 = self.tac.new_temp()
            self.tac.emit(">=", arg1=ex, arg2=lo, result=t1)
            self.tac.emit("<=", arg1=ex, arg2=hi, result=t2)
            op = "AND" if not expr.negated else "OR"   # NOT BETWEEN
            self.tac.emit(op, arg1=t1, arg2=t2, result=t3)
            return t3

        if isinstance(expr, ast.InListExpr):
            ex = self._gen_expr_tac(expr.expr)
            prev = None
            for val in expr.values:
                v  = self._gen_expr_tac(val)
                eq = self.tac.new_temp()
                self.tac.emit("=", arg1=ex, arg2=v, result=eq)
                if prev is None:
                    prev = eq
                else:
                    nxt = self.tac.new_temp()
                    self.tac.emit("OR", arg1=prev, arg2=eq, result=nxt)
                    prev = nxt
            if prev is None:
                t = self.tac.new_temp()
                self.tac.emit("COPY", arg1="False", result=t)
                prev = t
            if expr.negated:
                nt = self.tac.new_temp()
                self.tac.emit("UNARY_NOT", arg1=prev, result=nt)
                prev = nt
            return prev

        if isinstance(expr, ast.LikeExpr):
            ex  = self._gen_expr_tac(expr.expr)
            pat = self._gen_expr_tac(expr.pattern)
            t   = self.tac.new_temp()
            op  = "NOT_LIKE" if expr.negated else "LIKE"
            self.tac.emit(op, arg1=ex, arg2=pat, result=t)
            return t

        if isinstance(expr, ast.IsNullExpr):
            ex = self._gen_expr_tac(expr.expr)
            t  = self.tac.new_temp()
            op = "IS_NOT_NULL" if expr.negated else "IS_NULL"
            self.tac.emit(op, arg1=ex, result=t)
            return t

        if isinstance(expr, ast.FunctionCall):
            arg_t = None
            if expr.arg is not None:
                arg_t = self._gen_expr_tac(expr.arg)
            t = self.tac.new_temp()
            self.tac.emit(f"AGG_{expr.func}", arg1=arg_t or "*", result=t)
            return t

        if isinstance(expr, ast.Star):
            t = self.tac.new_temp()
            self.tac.emit("LOAD", arg1="*", result=t)
            return t

        t = self.tac.new_temp()
        self.tac.emit("NOP", result=t)
        return t


# ---------------------------------------------------------------------------
# Dummy single-row plan node (for SELECT without FROM)
# ---------------------------------------------------------------------------

class _SingleRowNode(PlanNode):
    def pretty(self, indent: int = 0) -> str:
        return "  " * indent + "SingleRow()"

    def ra_str(self) -> str:
        return "SingleRow()"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _has_agg(expr: ast.Expression) -> bool:
    """Return True if the expression contains an aggregate function call."""
    if isinstance(expr, ast.FunctionCall):
        return True
    if isinstance(expr, ast.BinaryOp):
        return _has_agg(expr.left) or _has_agg(expr.right)
    if isinstance(expr, ast.UnaryOp):
        return _has_agg(expr.operand)
    return False


# ---------------------------------------------------------------------------
# Public convenience
# ---------------------------------------------------------------------------

def generate_ir(
    program: ast.Program,
    catalog: Catalog,
) -> tuple:
    """
    Generate the logical plan tree and TAC for all statements.

    Returns
    -------
    (plans: List[PlanNode], tac: TACProgram)
    """
    gen   = IRGenerator(catalog)
    plans = gen.generate(program)
    return plans, gen.tac
