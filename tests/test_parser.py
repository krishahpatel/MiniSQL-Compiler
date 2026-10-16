"""test_parser.py — Unit Tests for Phase 2: Recursive-Descent Parser"""
import pytest
from minisql.errors import ErrorReporter
from minisql.lexer import Lexer
from minisql.parser import Parser
from minisql import ast_nodes as ast

def parse_sql(sql: str):
    reporter = ErrorReporter(sql)
    tokens = Lexer(sql, reporter).tokenize()
    assert not reporter.has_errors(), f"Lexer error: {reporter}"
    program = Parser(tokens, reporter).parse()
    assert not reporter.has_errors(), f"Parser error: {reporter}"
    return program

def test_parse_create_table():
    sql = "CREATE TABLE users (id INT PRIMARY KEY, name TEXT, age INT);"
    prog = parse_sql(sql)
    assert len(prog.statements) == 1
    stmt = prog.statements[0]
    assert isinstance(stmt, ast.CreateTableStmt)
    assert stmt.table_name == "users"
    assert len(stmt.columns) == 3
    assert stmt.columns[0].primary_key is True

def test_parse_select_complex():
    sql = """
    SELECT s.name, COUNT(*) AS cnt
    FROM students s
    JOIN enrollments e ON s.id = e.student_id
    WHERE s.age >= 20
    GROUP BY s.name
    HAVING COUNT(*) > 1
    ORDER BY cnt DESC
    LIMIT 5;
    """
    prog = parse_sql(sql)
    assert len(prog.statements) == 1
    stmt = prog.statements[0]
    assert isinstance(stmt, ast.SelectStmt)
    assert len(stmt.joins) == 1
    assert stmt.where is not None
    assert len(stmt.group_by) == 1
    assert stmt.having is not None
    assert len(stmt.order_by) == 1
    assert stmt.limit == 5

def test_pretty_print_renders_ast_as_a_tree():
    program = parse_sql(
        "SELECT s.name FROM students s WHERE s.age >= 20 AND s.active = TRUE;"
    )

    tree = ast.pretty_print(program)

    assert tree.startswith("Program\n└── Statement 1: Select")
    assert "├── Projection: Select List" in tree
    assert "│   └── Item 1: SelectItem" in tree
    assert "└── Where: BinaryOp(AND)" in tree
    assert "BinaryOp(>=)" in tree
    assert "BinaryOp(=)" in tree

def test_parse_insert_values():
    sql = "INSERT INTO students (id, name) VALUES (1, 'Alice'), (2, 'Bob');"
    prog = parse_sql(sql)
    assert len(prog.statements) == 1
    stmt = prog.statements[0]
    assert isinstance(stmt, ast.InsertStmt)
    assert stmt.table_name == "students"
    assert len(stmt.value_rows) == 2

def test_parse_syntax_error():
    sql = "SELECT FROM WHERE;"
    reporter = ErrorReporter(sql)
    tokens = Lexer(sql, reporter).tokenize()
    Parser(tokens, reporter).parse()
    assert reporter.has_errors()
