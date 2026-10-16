"""
app.py — Flask Web UI for MiniSQL Compiler Visualiser
=====================================================
Interactive web interface allowing users to run SQL queries and inspect
each phase of the compiler (Lexer, Parser, Semantic, IR, Optimizer, Codegen, VM).
"""

from __future__ import annotations
import os
import sys
from typing import Dict, Any, List
from flask import Flask, render_template, request, jsonify

# Add src to python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from minisql.errors import ErrorReporter
from minisql.lexer import Lexer, format_tokens
from minisql.parser import Parser
from minisql.ast_nodes import pretty_print
from minisql.catalog import Catalog
from minisql.semantic import SemanticAnalyser
from minisql.irgen import IRGenerator
from minisql.optimizer import Optimizer
from minisql.codegen import generate_bytecode
from minisql.vm import VM, VMResult
from minisql.storage import Storage

app = Flask(__name__)

# Global persistent database session for the Web UI
catalog = Catalog()
storage = Storage(catalog)

def _init_demo_db():
    global catalog, storage
    catalog = Catalog()
    storage = Storage(catalog)
    
    schema_sql = """
    CREATE TABLE students (id INT PRIMARY KEY, name TEXT, age INT, gpa FLOAT, active BOOL);
    CREATE TABLE courses (course_id TEXT PRIMARY KEY, title TEXT, credits INT, department TEXT);
    CREATE TABLE enrollments (student_id INT, course_id TEXT, grade TEXT, semester TEXT);
    """
    
    reporter = ErrorReporter(schema_sql)
    tokens = Lexer(schema_sql, reporter).tokenize()
    program = Parser(tokens, reporter).parse()
    SemanticAnalyser(catalog, reporter).analyse(program)
    
    for stmt in program.statements:
        schema = catalog.get_table(stmt.table_name)
        if schema:
            storage.create_table(schema)

    # Insert initial sample data
    tbl_s = storage.get_table("students")
    tbl_c = storage.get_table("courses")
    tbl_e = storage.get_table("enrollments")
    
    if tbl_s:
        tbl_s.insert({"id": 1, "name": "Alice Johnson", "age": 20, "gpa": 3.8, "active": True})
        tbl_s.insert({"id": 2, "name": "Bob Smith", "age": 22, "gpa": 3.2, "active": True})
        tbl_s.insert({"id": 3, "name": "Carol White", "age": 21, "gpa": 3.9, "active": False})
        tbl_s.insert({"id": 4, "name": "David Brown", "age": 23, "gpa": 2.7, "active": True})

    if tbl_c:
        tbl_c.insert({"course_id": "CS101", "title": "Compiler Design", "credits": 4, "department": "CS"})
        tbl_c.insert({"course_id": "CS102", "title": "Database Systems", "credits": 3, "department": "CS"})
        tbl_c.insert({"course_id": "EE201", "title": "Circuit Analysis", "credits": 4, "department": "EE"})

    if tbl_e:
        tbl_e.insert({"student_id": 1, "course_id": "CS101", "grade": "A", "semester": "Fall 2026"})
        tbl_e.insert({"student_id": 1, "course_id": "CS102", "grade": "A-", "semester": "Fall 2026"})
        tbl_e.insert({"student_id": 2, "course_id": "CS101", "grade": "B", "semester": "Fall 2026"})
        tbl_e.insert({"student_id": 3, "course_id": "EE201", "grade": "A", "semester": "Fall 2026"})

    storage.update_row_counts()

_init_demo_db()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/schema", methods=["GET"])
def get_schema():
    tables = []
    for tname in catalog.table_names():
        schema = catalog.get_table(tname)
        if schema:
            cols = [{"name": c.name, "type": c.type_name, "pk": c.primary_key} for c in schema.columns]
            rc = catalog.get_row_count(tname)
            tables.append({"name": schema.name, "columns": cols, "row_count": rc})
    return jsonify({"tables": tables})

@app.route("/api/reset", methods=["POST"])
def reset_db():
    _init_demo_db()
    return jsonify({"status": "ok", "message": "Database reset to initial demo state"})

@app.route("/api/compile", methods=["POST"])
def compile_sql():
    data = request.json or {}
    source = data.get("sql", "").strip()
    optimize_flag = data.get("optimize", True)

    if not source:
        return jsonify({"error": "SQL source query cannot be empty"}), 400

    reporter = ErrorReporter(source)

    # Phase 1: Lexer
    tokens = Lexer(source, reporter).tokenize()
    token_list = []
    for t in tokens:
        token_list.append({
            "type": t.type.name,
            "lexeme": t.lexeme,
            "value": str(t.value) if t.value is not None else "",
            "line": t.line,
            "col": t.col,
        })

    if reporter.has_errors():
        return jsonify({
            "phase": "Lexer",
            "errors": [str(e) for e in reporter.errors],
            "tokens": token_list,
        })

    # Phase 2: Parser
    program = Parser(tokens, reporter).parse()
    ast_dump = pretty_print(program)

    if reporter.has_errors():
        return jsonify({
            "phase": "Parser",
            "errors": [str(e) for e in reporter.errors],
            "tokens": token_list,
            "ast": ast_dump,
        })

    # Phase 3: Semantic Analysis
    semantic_analyser = SemanticAnalyser(catalog, reporter)
    semantic_analyser.analyse(program)
    semantic_output = semantic_analyser.report()
    if reporter.has_errors():
        return jsonify({
            "phase": "Semantic",
            "errors": [str(e) for e in reporter.errors],
            "semantic": semantic_output,
            "tokens": token_list,
            "ast": ast_dump,
        })

    # Phase 4: IR Generation
    irgen = IRGenerator(catalog)
    plans = irgen.generate(program)
    tac = str(irgen.tac)

    unopt_plans_str = "\n\n".join([f"--- Plan {i+1} ---\n" + p.pretty() for i, p in enumerate(plans)])
    ra_str = "\n".join([p.ra_str() for p in plans])

    # Phase 5: Optimizer
    opt_report_str = ""
    if optimize_flag:
        opt = Optimizer(catalog)
        opt_plans = [opt.optimize(p) for p in plans]
        opt_plans_str = "\n\n".join([f"--- Optimized Plan {i+1} ---\n" + p.pretty() for i, p in enumerate(opt_plans)])
        opt_report_str = opt.report()
    else:
        opt_plans = plans
        opt_plans_str = unopt_plans_str

    # Phase 6: Code Generation
    prog_list = [generate_bytecode(p) for p in opt_plans]
    bytecode_disasm = "\n\n".join([f"--- Bytecode {i+1} ---\n" + prog.disassemble() for i, prog in enumerate(prog_list)])

    # Phase 7: VM Execution
    storage.sync_catalog()
    vm = VM(storage)
    exec_results = []

    prog_idx = 0
    from minisql import ast_nodes as ast
    for stmt_ast in program.statements:
        if isinstance(stmt_ast, ast.CreateTableStmt):
            schema = catalog.get_table(stmt_ast.table_name)
            if schema and not storage.has_table(stmt_ast.table_name):
                storage.create_table(schema)
            res = VMResult(rows=[], columns=[], rows_affected=0, elapsed_ms=0, statement_type="CREATE")
        elif isinstance(stmt_ast, ast.DropTableStmt):
            storage.drop_table(stmt_ast.table_name)
            res = VMResult(rows=[], columns=[], rows_affected=0, elapsed_ms=0, statement_type="DROP")
        else:
            if prog_idx < len(prog_list):
                prog = prog_list[prog_idx]
                prog_idx += 1
                res = vm.execute(prog)
            else:
                continue

        exec_results.append({
            "type": res.statement_type,
            "columns": res.columns,
            "rows": res.rows,
            "rows_affected": res.rows_affected,
            "elapsed_ms": round(res.elapsed_ms, 3),
        })

    return jsonify({
        "status": "success",
        "tokens": token_list,
        "ast": ast_dump,
        "semantic": semantic_output,
        "ir_plans": unopt_plans_str,
        "relational_algebra": ra_str,
        "tac": tac,
        "opt_plans": opt_plans_str,
        "opt_report": opt_report_str,
        "bytecode": bytecode_disasm,
        "execution": exec_results,
    })

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
