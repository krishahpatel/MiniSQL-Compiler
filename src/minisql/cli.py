"""
cli.py — Command-Line Interface
================================
Entry point for the MiniSQL compiler.

COMMANDS
--------
  minisql run <file.sql>
      Run a SQL script through all compiler phases and display results.

  minisql compile <file.sql> --show tokens|ast|plan|opt|tac|bytecode|all
      Compile without executing; show intermediate representations.

  minisql repl
      Interactive read-eval-print loop with multi-line support.

  minisql explain "<SQL>"
      Show all phases + optimization report for a single SQL statement.

  minisql describe
      Show the current catalog (table schemas).

USAGE
-----
  python -m minisql run examples/demo.sql
  python -m minisql compile examples/03_simple_select.sql --show all
  python -m minisql repl
  python -m minisql explain "SELECT * FROM students WHERE age > 20;"
"""

from __future__ import annotations
import argparse
from importlib import import_module
import os
import sys
import time
try:
    import readline   # enables history in repl (Unix/macOS)
except ImportError:
    try:
        readline = import_module("pyreadline3")
    except ImportError:
        readline = None   # fallback for Windows without pyreadline3

from .errors    import ErrorReporter, MiniSQLError
from .lexer     import Lexer, format_tokens
from .parser    import Parser
from .ast_nodes import pretty_print, Program
from .catalog   import Catalog
from .semantic  import SemanticAnalyser
from .ir        import TACProgram
from .irgen     import IRGenerator
from .optimizer import Optimizer
from .codegen   import CodeGenerator, generate_bytecode
from .vm        import VM, VMResult
from .storage   import Storage, format_result


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def _run_pipeline(
    source:   str,
    catalog:  Catalog,
    storage:  Storage,
    show:     set,
    db_path:  str = "",
    optimize: bool = True,
    opt_passes: list = None,
) -> list:
    """
    Run the full compiler pipeline on *source*.

    Returns a list of VMResult objects (one per statement).
    """
    reporter = ErrorReporter(source)
    results  = []

    # ---- Phase 1: Lexer ----
    tokens = Lexer(source, reporter).tokenize()
    if "tokens" in show or "all" in show:
        print("\n" + "="*60)
        print("PHASE 1 — TOKENS")
        print("="*60)
        print(format_tokens(tokens))

    if reporter.has_errors():
        print(str(reporter))
        return results

    # ---- Phase 2: Parser ----
    from .parser import Parser
    program = Parser(tokens, reporter).parse()
    if "ast" in show or "all" in show:
        print("\n" + "="*60)
        print("PHASE 2 — ABSTRACT SYNTAX TREE")
        print("="*60)
        print(pretty_print(program))

    if reporter.has_errors():
        print(str(reporter))
        return results

    # ---- Phase 3: Semantic Analysis ----
    SemanticAnalyser(catalog, reporter).analyse(program)
    if reporter.has_errors():
        print(str(reporter))
        return results

    # ---- Phase 4: IR Generation ----
    irgen  = IRGenerator(catalog)
    plans  = irgen.generate(program)
    tac    = irgen.tac

    if "plan" in show or "all" in show:
        print("\n" + "="*60)
        print("PHASE 4 — LOGICAL QUERY PLANS")
        print("="*60)
        for i, plan in enumerate(plans):
            print(f"\n--- Statement {i+1} ---")
            print(plan.pretty())
            print(f"  RA: {plan.ra_str()}")

    if "tac" in show or "all" in show:
        print("\n" + "="*60)
        print("PHASE 4 — THREE-ADDRESS CODE (TAC)")
        print("="*60)
        print(str(tac) if str(tac).strip() else "(no TAC for this query)")

    # ---- Phase 5: Optimization ----
    opt_results = []
    if optimize:
        opt = Optimizer(catalog, passes=opt_passes)
        opt_plans = [opt.optimize(p) for p in plans]
        opt_tac   = opt.optimize_tac(tac)
        if "opt" in show or "all" in show:
            print("\n" + "="*60)
            print("PHASE 5 — OPTIMIZED PLANS")
            print("="*60)
            for i, plan in enumerate(opt_plans):
                print(f"\n--- Statement {i+1} (after optimization) ---")
                print(plan.pretty())
            print("\n" + opt.report())
    else:
        opt_plans = plans
        opt_tac   = tac

    # ---- Phase 6: Code Generation ----
    prog_list = [generate_bytecode(p) for p in opt_plans]
    if "bytecode" in show or "all" in show:
        print("\n" + "="*60)
        print("PHASE 6 — BYTECODE")
        print("="*60)
        for i, prog in enumerate(prog_list):
            print(f"\n--- Statement {i+1} ---")
            print(prog.disassemble())

    # ---- Phase 7: VM Execution ----
    storage.sync_catalog()
    vm = VM(storage)
    prog_idx = 0

    for stmt_ast in program.statements:
        from . import ast_nodes as ast
        if isinstance(stmt_ast, ast.CreateTableStmt):
            schema = catalog.get_table(stmt_ast.table_name)
            if schema and not storage.has_table(stmt_ast.table_name):
                storage.create_table(schema)
            result = VMResult(rows=[], columns=[], rows_affected=0,
                              elapsed_ms=0, statement_type="CREATE")
        elif isinstance(stmt_ast, ast.DropTableStmt):
            storage.drop_table(stmt_ast.table_name)
            result = VMResult(rows=[], columns=[], rows_affected=0,
                              elapsed_ms=0, statement_type="DROP")
        else:
            if prog_idx < len(prog_list):
                prog = prog_list[prog_idx]
                prog_idx += 1
                result = vm.execute(prog)
            else:
                continue

        results.append((stmt_ast, result))

    # Save catalog and storage
    if db_path:
        catalog.save(db_path + ".catalog.json")
        storage.save(db_path + ".db.json")

    return results


def _print_results(results: list, show_plan: bool = False) -> None:
    """Pretty-print VMResult objects."""
    for i, (stmt, result) in enumerate(results):
        from . import ast_nodes as ast
        stype = result.statement_type

        if stype == "SELECT":
            if result.rows:
                print(format_result(result.rows, result.columns,
                                    len(result.rows), result.elapsed_ms))
            else:
                print(f"(0 rows) ({result.elapsed_ms:.2f} ms)")
        elif stype in ("INSERT", "UPDATE", "DELETE"):
            print(f"{stype}: {result.rows_affected} row(s) affected "
                  f"({result.elapsed_ms:.2f} ms)")
        elif stype in ("CREATE", "DROP"):
            tname = getattr(stmt, "table_name", "")
            print(f"{stype} TABLE {tname}: OK")


# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------

def cmd_run(args) -> None:
    """Run a SQL file through all compiler phases."""
    path = args.file
    if not os.path.exists(path):
        print(f"Error: file not found: {path}")
        sys.exit(1)

    with open(path, encoding="utf-8") as f:
        source = f.read()

    db_path = args.db or (os.path.splitext(path)[0])
    catalog = Catalog()
    storage = Storage(catalog, db_path + ".db.json")
    if os.path.exists(db_path + ".catalog.json"):
        catalog.load(db_path + ".catalog.json")
        storage.sync_catalog()

    show = set(args.show.split(",")) if args.show else set()
    results = _run_pipeline(source, catalog, storage, show, db_path,
                            optimize=not args.no_optimize)
    _print_results(results)


def cmd_compile(args) -> None:
    """Compile a SQL file and show intermediate representations."""
    path = args.file
    if not os.path.exists(path):
        print(f"Error: file not found: {path}")
        sys.exit(1)

    with open(path, encoding="utf-8") as f:
        source = f.read()

    show_arg = args.show or "all"
    show     = set(s.strip() for s in show_arg.split(","))

    db_path = getattr(args, "db", "")
    catalog = Catalog()
    storage = Storage(catalog)
    if db_path and os.path.exists(db_path + ".catalog.json"):
        catalog.load(db_path + ".catalog.json")
        storage.sync_catalog()

    _run_pipeline(source, catalog, storage, show,
                  optimize=not args.no_optimize)


def cmd_explain(args) -> None:
    """Show all phases for a single SQL statement."""
    source  = args.sql
    db_path = getattr(args, "db", "")
    catalog = Catalog()
    storage = Storage(catalog)
    if db_path and os.path.exists(db_path + ".catalog.json"):
        catalog.load(db_path + ".catalog.json")
        storage.sync_catalog()

    show    = {"tokens", "ast", "plan", "opt", "tac", "bytecode"}
    results = _run_pipeline(source, catalog, storage, show)
    _print_results(results)


def cmd_repl(args) -> None:
    """Interactive REPL with multi-line input and history."""
    db_path = args.db or "minisql_repl"
    catalog = Catalog()
    storage = Storage(catalog, db_path + ".db.json")

    if os.path.exists(db_path + ".catalog.json"):
        catalog.load(db_path + ".catalog.json")
        storage.sync_catalog()

    print("MiniSQL Interactive Shell  (type 'quit' or Ctrl-D to exit)")
    print("Multi-line input: end a statement with ';' on its own line")
    print(f"DB: {db_path}")
    print()

    buffer: list = []
    try:
        while True:
            prompt = "msql> " if not buffer else "  ... "
            try:
                line = input(prompt)
            except EOFError:
                print()
                break

            if line.strip().lower() in ("quit", "exit", "\\q"):
                break

            if line.strip() == "\\catalog":
                print(catalog.describe())
                continue

            buffer.append(line)
            source = "\n".join(buffer)

            # Execute when we see a complete statement (ends with ;)
            if source.rstrip().endswith(";"):
                try:
                    results = _run_pipeline(
                        source, catalog, storage, set(), db_path,
                        optimize=True,
                    )
                    _print_results(results)
                except Exception as exc:
                    print(f"Error: {exc}")
                buffer = []
            # else keep buffering

    except KeyboardInterrupt:
        print()

    # Save on exit
    catalog.save(db_path + ".catalog.json")
    storage.save(db_path + ".db.json")
    print("Bye!")


def cmd_describe(args) -> None:
    """Show the catalog (table schemas)."""
    db_path = args.db or "minisql_repl"
    catalog = Catalog()
    if os.path.exists(db_path + ".catalog.json"):
        catalog.load(db_path + ".catalog.json")
    print(catalog.describe())


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="minisql",
        description="MiniSQL — SQL Query Compiler (Principles of Compiler Design project)",
    )
    ap.add_argument("--db", default="", help="database file prefix (default: minisql_db)")
    sub = ap.add_subparsers(dest="command", required=True)

    # run
    p_run = sub.add_parser("run", help="Run a SQL file")
    p_run.add_argument("file", help="Path to .sql file")
    p_run.add_argument("--db", default="", help="database file prefix")
    p_run.add_argument("--show", default="",
                       help="Comma-separated: tokens,ast,plan,opt,tac,bytecode,all")
    p_run.add_argument("--no-optimize", action="store_true",
                       help="Disable optimizer")
    p_run.set_defaults(func=cmd_run)

    # compile
    p_compile = sub.add_parser("compile", help="Compile and show IR without running")
    p_compile.add_argument("file", help="Path to .sql file")
    p_compile.add_argument("--db", default="", help="database file prefix")
    p_compile.add_argument("--show", default="all",
                           help="Comma-separated: tokens,ast,plan,opt,tac,bytecode,all")
    p_compile.add_argument("--no-optimize", action="store_true")
    p_compile.set_defaults(func=cmd_compile)

    # explain
    p_explain = sub.add_parser("explain", help="Show all phases for a SQL string")
    p_explain.add_argument("sql", help="SQL statement string")
    p_explain.add_argument("--db", default="", help="database file prefix")
    p_explain.set_defaults(func=cmd_explain)

    # repl
    p_repl = sub.add_parser("repl", help="Interactive SQL shell")
    p_repl.add_argument("--db", default="", help="database file prefix")
    p_repl.set_defaults(func=cmd_repl)

    # describe
    p_desc = sub.add_parser("describe", help="Show catalog / table schemas")
    p_desc.add_argument("--db", default="", help="database file prefix")
    p_desc.set_defaults(func=cmd_describe)

    return ap


def main() -> None:
    ap = build_parser()
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
