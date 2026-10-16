# MiniSQL Compiler Phases — Deep Dive

---

## Phase 1: Lexical Analysis (`lexer.py`)
Translates source SQL text into a stream of structured tokens. Handles identifiers, keywords, numeric literals (int/float), strings, operators, and comments.

## Phase 2: Syntax Analysis (`parser.py`)
Transforms token streams into an Abstract Syntax Tree (AST) using LL(1) recursive-descent parsing. Includes panic-mode error recovery on `;`.

## Phase 3: Semantic Analysis (`semantic.py` & `catalog.py`)
Validates identifiers against the Catalog (symbol table), resolves table aliases, enforces type compatibility, and verifies aggregate coherence. The web visualizer's semantic report shows the tables/columns resolved and the types and clause checks completed for each statement.

## Phase 4: IR & TAC Generation (`ir.py` & `irgen.py`)
Translates AST nodes into a relational algebra plan tree (`ScanNode`, `FilterNode`, `ProjectNode`, `JoinNode`, `AggregateNode`) and Three-Address Code (TAC) quadruples.

## Phase 5: Optimization (`optimizer.py`)
Applies six optimization passes:
1. Constant Folding
2. Boolean Simplification
3. Predicate Pushdown
4. Projection Pruning
5. Join Strategy Selection (Hash vs Nested Loop)
6. Common Subexpression Elimination (CSE)

## Phase 6: Code Generation (`codegen.py`)
Lowers the optimized plan tree into stack-based bytecode instructions (`SCAN`, `NEXT_ROW`, `EVAL_EXPR`, `PROJECT`, `EMIT_ROW`, `AGG_UPDATE`, `HALT`). Plans containing joins retain the complete optimized tree in a VM execution instruction so nested joins and pushed-down filters are not lost during lowering.

## Phase 7: Virtual Machine & Storage (`vm.py` & `storage.py`)
Executes bytecode programs against in-memory table stores. The VM interprets joined plans as complete plan trees and runs other programs on the stack machine. Supports Three-Valued Logic (`TRUE`, `FALSE`, `NULL`), joins, aggregations, and JSON persistence.
