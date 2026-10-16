"""test_codegen.py — Unit Tests for Phase 6: Bytecode Code Generator"""
import pytest
from minisql.errors import ErrorReporter
from minisql.lexer import Lexer
from minisql.parser import Parser
from minisql.semantic import SemanticAnalyser
from minisql.irgen import IRGenerator
from minisql.optimizer import Optimizer
from minisql.codegen import generate_bytecode

def test_codegen_select(catalog):
    sql = "SELECT name, age FROM students WHERE age > 20;"
    reporter = ErrorReporter(sql)
    tokens = Lexer(sql, reporter).tokenize()
    program = Parser(tokens, reporter).parse()
    SemanticAnalyser(catalog, reporter).analyse(program)
    plans = IRGenerator(catalog).generate(program)
    opt_plan = Optimizer(catalog).optimize(plans[0])
    
    prog = generate_bytecode(opt_plan)
    assert len(prog.instructions) > 0
    opcodes = [i.opcode for i in prog.instructions]
    assert "SCAN" in opcodes
    assert "NEXT_ROW" in opcodes
    assert "PROJECT" in opcodes
    assert "EMIT_ROW" in opcodes
