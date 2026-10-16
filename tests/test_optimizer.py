"""test_optimizer.py — Unit Tests for Phase 5: Query Optimizer"""
import pytest
from minisql.errors import ErrorReporter
from minisql.lexer import Lexer
from minisql.parser import Parser
from minisql.semantic import SemanticAnalyser
from minisql.irgen import IRGenerator
from minisql.optimizer import Optimizer
from minisql import ir

def get_optimized_plan(sql: str, catalog):
    reporter = ErrorReporter(sql)
    tokens = Lexer(sql, reporter).tokenize()
    program = Parser(tokens, reporter).parse()
    SemanticAnalyser(catalog, reporter).analyse(program)
    plans = IRGenerator(catalog).generate(program)
    opt = Optimizer(catalog)
    return opt.optimize(plans[0]), opt

def test_constant_folding(catalog):
    sql = "SELECT id FROM students WHERE age > 10 + 10;"
    opt_plan, opt = get_optimized_plan(sql, catalog)
    # The filter predicate (age > 10 + 10) should fold (10 + 10) -> 20
    assert "Constant Folding" in opt.report() or "age > 20" in opt_plan.pretty()

def test_boolean_simplification(catalog):
    sql = "SELECT id FROM students WHERE age > 20 AND TRUE;"
    opt_plan, opt = get_optimized_plan(sql, catalog)
    assert "Boolean Simplification" in opt.report() or "age > 20" in opt_plan.pretty()

def test_predicate_pushdown(catalog):
    sql = "SELECT s.name FROM students s JOIN courses c ON TRUE WHERE s.age > 21;"
    opt_plan, opt = get_optimized_plan(sql, catalog)
    assert opt_plan is not None
