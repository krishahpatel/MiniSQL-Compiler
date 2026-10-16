"""test_semantic.py — Unit Tests for Phase 3: Semantic Analyzer"""
import pytest
from minisql.errors import ErrorReporter
from minisql.lexer import Lexer
from minisql.parser import Parser
from minisql.semantic import SemanticAnalyser

def analyse_sql(sql: str, catalog):
    reporter = ErrorReporter(sql)
    tokens = Lexer(sql, reporter).tokenize()
    program = Parser(tokens, reporter).parse()
    if reporter.has_errors():
        return reporter
    SemanticAnalyser(catalog, reporter).analyse(program)
    return reporter

def test_semantic_valid(catalog):
    sql = "SELECT id, name FROM students WHERE age > 20;"
    reporter = analyse_sql(sql, catalog)
    assert not reporter.has_errors()

def test_semantic_table_not_found(catalog):
    sql = "SELECT * FROM non_existent_table;"
    reporter = analyse_sql(sql, catalog)
    assert reporter.has_errors()
    assert "does not exist" in str(reporter)

def test_semantic_column_not_found(catalog):
    sql = "SELECT invalid_col FROM students;"
    reporter = analyse_sql(sql, catalog)
    assert reporter.has_errors()
    assert "not found" in str(reporter)

def test_semantic_duplicate_table_create(catalog):
    sql = "CREATE TABLE students (id INT);"
    reporter = analyse_sql(sql, catalog)
    assert reporter.has_errors()
    assert "already exists" in str(reporter)

def test_semantic_report_describes_resolved_query(catalog):
    sql = (
        "SELECT s.name AS student_name FROM students s "
        "JOIN courses c ON TRUE "
        "WHERE s.age > 20 ORDER BY student_name;"
    )
    reporter = ErrorReporter(sql)
    tokens = Lexer(sql, reporter).tokenize()
    program = Parser(tokens, reporter).parse()
    analyser = SemanticAnalyser(catalog, reporter)
    analyser.analyse(program)

    report = analyser.report()
    assert not reporter.has_errors()
    assert "Status: PASSED" in report
    assert "Resolved table students AS s" in report
    assert "Resolved table courses AS c" in report
    assert "INNER JOIN condition resolves to BOOL" in report
    assert "WHERE condition resolves to BOOL" in report
    assert "SELECT expression s.name resolves to TEXT AS student_name" in report
    assert "ORDER BY expression student_name resolves to TEXT" in report

def test_semantic_report_includes_semantic_errors(catalog):
    sql = "SELECT missing FROM students;"
    reporter = ErrorReporter(sql)
    tokens = Lexer(sql, reporter).tokenize()
    program = Parser(tokens, reporter).parse()
    analyser = SemanticAnalyser(catalog, reporter)
    analyser.analyse(program)

    report = analyser.report()
    assert reporter.has_errors()
    assert "Status: FAILED" in report
    assert "Column 'missing' not found" in report
