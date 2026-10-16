# PLAN.md — MiniSQL: A SQL Query Compiler
## Course: Principles of Compiler Design (Innovative Assignment)

---

## 1. Project Overview

MiniSQL is a **real compiler** (not an interpreter) that translates a subset of SQL through
every classic compiler phase into executable bytecode, then runs that bytecode on a custom
Virtual Machine against in-memory storage.

**Source Language**: MiniSQL (subset of SQL)
**Target**: Custom stack-based bytecode executed by `vm.py`
**Bonus Target**: Generated Python source code

---

## 2. Architecture Diagram

```
Source SQL Text
      |
      v
+-----------------+
|  Phase 1: Lexer |  lexer.py -> [Token stream]
+-----------------+
      |
      v
+------------------+
| Phase 2: Parser  |  parser.py + ast_nodes.py -> [AST]
+------------------+
      |
      v
+----------------------+
| Phase 3: Semantic    |  semantic.py + catalog.py -> [Decorated AST]
|         Analysis     |
+----------------------+
      |
      v
+-------------------------+
| Phase 4: IR Generation  |  ir.py + irgen.py -> [Logical Plan + TAC]
+-------------------------+
      |
      v
+---------------------+
| Phase 5: Optimizer  |  optimizer.py -> [Optimized Plan]
+---------------------+
      |
      v
+-------------------------+
| Phase 6: Code Generator |  codegen.py -> [Bytecode Program]
+-------------------------+
      |
      v
+-------------------------+
| Phase 7: VM + Runtime   |  vm.py + storage.py -> [Results]
+-------------------------+
```

---

## 3. Folder Structure

```
MiniSQL_PCD/
|-- PLAN.md
|-- README.md
|-- requirements.txt
|-- src/
|   +-- minisql/
|       |-- __init__.py
|       |-- lexer.py          # Phase 1: Lexical Analysis
|       |-- ast_nodes.py      # AST node dataclasses
|       |-- parser.py         # Phase 2: Recursive-descent parser
|       |-- catalog.py        # Symbol table / schema catalog
|       |-- semantic.py       # Phase 3: Semantic analysis
|       |-- ir.py             # IR node definitions
|       |-- irgen.py          # Phase 4: IR generation
|       |-- optimizer.py      # Phase 5: Optimization passes
|       |-- codegen.py        # Phase 6: Bytecode code generation
|       |-- vm.py             # Phase 7: Stack-based virtual machine
|       |-- storage.py        # In-memory storage + JSON persistence
|       |-- errors.py         # Unified error reporting
|       +-- cli.py            # CLI entry point (argparse)
|-- web/
|   |-- app.py
|   |-- templates/index.html
|   +-- static/
|-- examples/
|   |-- 01_schema.sql ... 13_demo.sql
|-- tests/
|   |-- conftest.py
|   |-- test_lexer.py
|   |-- test_parser.py
|   |-- test_semantic.py
|   |-- test_optimizer.py
|   |-- test_codegen.py
|   +-- test_e2e.py
|-- docs/
|   |-- grammar.md
|   |-- architecture.md
|   |-- phases.md
|   +-- design_decisions.md
+-- report/
    |-- report.md
    +-- VIVA_QA.md
```

---

## 4. Grammar Summary (Full Grammar in docs/grammar.md)

```ebnf
program        ::= statement* EOF
statement      ::= create_stmt | insert_stmt | select_stmt
                 | update_stmt | delete_stmt | drop_stmt
create_stmt    ::= CREATE TABLE name '(' col_def (',' col_def)* ')' ';'
col_def        ::= name type_kw (PRIMARY KEY)?
type_kw        ::= INT | FLOAT | TEXT | BOOL
insert_stmt    ::= INSERT INTO name ['(' name_list ')'] VALUES val_list (',' val_list)* ';'
select_stmt    ::= SELECT [DISTINCT] select_list FROM table_ref
                   join_clause* [WHERE expr]
                   [GROUP BY name_list [HAVING expr]]
                   [ORDER BY order_item (',' order_item)*]
                   [LIMIT INT_LITERAL] ';'
update_stmt    ::= UPDATE name SET assign (',' assign)* [WHERE expr] ';'
delete_stmt    ::= DELETE FROM name [WHERE expr] ';'
drop_stmt      ::= DROP TABLE name ';'
expr           ::= or_expr
or_expr        ::= and_expr (OR and_expr)*
and_expr       ::= not_expr (AND not_expr)*
not_expr       ::= NOT not_expr | cmp_expr
cmp_expr       ::= add_expr (cmp_op add_expr | BETWEEN add_expr AND add_expr
                            | IN '(' expr_list ')' | IS [NOT] NULL | LIKE STRING)*
add_expr       ::= mul_expr (('+' | '-') mul_expr)*
mul_expr       ::= unary_expr (('*' | '/' | '%') unary_expr)*
unary_expr     ::= '-' unary_expr | primary
primary        ::= literal | name ['.' name] | '(' expr ')' | agg_call
agg_call       ::= (COUNT|SUM|AVG|MIN|MAX) '(' ('*' | expr) ')'
```

---

## 5. IR Design

### 5.1 Logical Query Plan (Relational Algebra Tree)

Nodes: ScanNode, FilterNode, ProjectNode, JoinNode, AggregateNode,
       SortNode, LimitNode, DistinctNode, InsertNode, UpdateNode, DeleteNode

### 5.2 Three-Address Code (TAC)

Quadruple format: (op, arg1, arg2, result)

Example:
  t1 = salary * 1.1
  t2 = t1 > 50000
  t3 = t2 AND active = TRUE

---

## 6. Bytecode Instruction Set

| Opcode       | Args      | Description                      |
|--------------|-----------|----------------------------------|
| SCAN         | table     | Push full table iterator         |
| LOAD_COL     | col_name  | Load column value from row       |
| PUSH_CONST   | value     | Push literal constant            |
| ADD/SUB/MUL  | -         | Arithmetic on top 2 stack items  |
| DIV/MOD      | -         | Division / modulo                |
| NEG          | -         | Negate top of stack              |
| CMP_EQ/NEQ   | -         | Equality comparisons             |
| CMP_LT/LE    | -         | Less than / less-or-equal        |
| CMP_GT/GE    | -         | Greater than / greater-or-equal  |
| AND/OR/NOT   | -         | Boolean operators (3-valued)     |
| JMP_IF_FALSE | offset    | Conditional jump                 |
| JMP          | offset    | Unconditional jump               |
| EMIT_ROW     | -         | Output current result row        |
| HASH_BUILD   | -         | Build hash table for join        |
| HASH_PROBE   | -         | Probe hash table                 |
| AGG_INIT     | agg_type  | Initialize accumulator           |
| AGG_UPDATE   | -         | Update aggregate accumulator     |
| AGG_EMIT     | -         | Emit aggregate result            |
| SORT         | cols,dirs | Sort result set                  |
| LIMIT        | n         | Limit rows emitted               |
| DISTINCT     | -         | Deduplicate rows                 |
| INSERT_ROW   | table     | Insert a row into storage        |
| UPDATE_ROW   | table     | Update matching rows             |
| DELETE_ROW   | table     | Delete matching rows             |
| HALT         | -         | Stop execution                   |

---

## 7. Optimization Passes

1. Constant Folding         - Evaluate constant expressions at compile time
2. Boolean Simplification   - x AND TRUE -> x, x OR FALSE -> x, etc.
3. Predicate Pushdown       - Move filters below joins for early filtering
4. Projection Pruning       - Only read needed columns
5. Join Strategy Selection  - Hash join vs nested-loop by table size stats
6. Common Subexpr Elim(CSE) - Reuse repeated TAC temporaries

---

## 8. Milestones

| # | Milestone                        | Files                                      |
|---|----------------------------------|--------------------------------------------|
| 1 | Lexer + tests                    | lexer.py, errors.py, test_lexer.py         |
| 2 | AST nodes + Parser + tests       | ast_nodes.py, parser.py, test_parser.py    |
| 3 | Catalog + Semantic + tests       | catalog.py, semantic.py, test_semantic.py  |
| 4 | IR + IR Gen + TAC                | ir.py, irgen.py                            |
| 5 | Optimizer + tests                | optimizer.py, test_optimizer.py            |
| 6 | Code Gen + VM + Storage + tests  | codegen.py, vm.py, storage.py, test_codegen.py |
| 7 | CLI + Examples + E2E tests       | cli.py, examples/, test_e2e.py             |
| 8 | Web UI                           | web/                                       |
| 9 | Docs + Report + VIVA             | docs/, report/                             |

---

## 9. Error Handling Strategy

All errors flow through errors.py:
  [Phase] Error at line L, col C: message
     source line here
                  ^--- caret points to offending token

- Lexer:    Illegal character errors
- Parser:   Panic-mode recovery (sync on ';')
- Semantic: Type errors, undefined symbols, "did you mean X?"
- Runtime:  Division by zero, type mismatch

---

## 10. Concept Mapping Table

| Compiler Concept          | Where in MiniSQL                      |
|---------------------------|---------------------------------------|
| DFA / Regular Expression  | lexer.py -- token scanning            |
| Context-Free Grammar      | docs/grammar.md -- BNF grammar        |
| LL(1) Parsing             | parser.py -- recursive descent        |
| FIRST / FOLLOW Sets       | docs/grammar.md -- documented sets    |
| Abstract Syntax Tree      | ast_nodes.py, parser.py               |
| Symbol Table              | catalog.py                            |
| Type Checking             | semantic.py                           |
| Syntax-Directed Transl.   | irgen.py -- AST to relational plan    |
| Three-Address Code        | irgen.py -- TAC generation            |
| Optimization              | optimizer.py -- 6 passes              |
| Code Generation           | codegen.py -- bytecode emission       |
| Error Recovery            | parser.py -- panic mode on ';'        |
| Virtual Machine           | vm.py -- stack-based execution        |
