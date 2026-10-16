"""test_lexer.py — Unit Tests for Phase 1: Lexer"""
import pytest
from minisql.errors import ErrorReporter
from minisql.lexer import Lexer, TokenType

def test_lexer_keywords_and_identifiers():
    sql = "SELECT id, name FROM students WHERE age > 20;"
    reporter = ErrorReporter(sql)
    tokens = Lexer(sql, reporter).tokenize()
    assert not reporter.has_errors()
    
    types = [t.type for t in tokens]
    assert types[0] == TokenType.KEYWORD
    assert tokens[0].lexeme.upper() == "SELECT"
    assert types[1] == TokenType.IDENTIFIER
    assert tokens[1].lexeme == "id"
    assert types[2] == TokenType.PUNCTUATION
    assert tokens[2].lexeme == ","
    assert types[3] == TokenType.IDENTIFIER
    assert tokens[3].lexeme == "name"

def test_lexer_literals():
    sql = "INSERT INTO t VALUES (10, 3.14, 'hello', TRUE, NULL);"
    reporter = ErrorReporter(sql)
    tokens = Lexer(sql, reporter).tokenize()
    assert not reporter.has_errors()

    val_tokens = [t for t in tokens if t.type in (
        TokenType.INT_LITERAL, TokenType.FLOAT_LITERAL,
        TokenType.STRING_LITERAL, TokenType.BOOL_LITERAL,
        TokenType.NULL_LITERAL
    )]
    assert len(val_tokens) == 5
    assert val_tokens[0].value == 10
    assert val_tokens[1].value == 3.14
    assert val_tokens[2].value == "hello"
    assert val_tokens[3].value is True
    assert val_tokens[4].value is None

def test_lexer_operators():
    sql = "SELECT * FROM t WHERE a <= 10 AND b != 20 OR c <> 30;"
    reporter = ErrorReporter(sql)
    tokens = Lexer(sql, reporter).tokenize()
    assert not reporter.has_errors()
    
    ops = [t.lexeme for t in tokens if t.type == TokenType.OPERATOR]
    assert "<=" in ops
    assert "!=" in ops
    assert "<>" in ops

def test_lexer_invalid_char():
    sql = "SELECT @#$ FROM t;"
    reporter = ErrorReporter(sql)
    tokens = Lexer(sql, reporter).tokenize()
    assert reporter.has_errors()
