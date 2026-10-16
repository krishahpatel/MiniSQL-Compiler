# MiniSQL Architecture & Subsystem Design

---

## 1. System Overview

MiniSQL is designed following classic compiler architecture principles:

```
                  +-------------------+
                  |   Source SQL      |
                  +-------------------+
                            |
                            v
                  +-------------------+
                  |  Phase 1: Lexer   |  lexer.py
                  +-------------------+
                            |  [Tokens]
                            v
                  +-------------------+
                  |  Phase 2: Parser  |  parser.py + ast_nodes.py
                  +-------------------+
                            |  [AST]
                            v
                  +-------------------+
                  | Phase 3: Semantic |  semantic.py + catalog.py
                  +-------------------+
                            |  [Decorated AST]
                            v
                  +-------------------+
                  |  Phase 4: IR Gen  |  irgen.py + ir.py
                  +-------------------+
                            |  [Logical Plan + TAC]
                            v
                  +-------------------+
                  | Phase 5: Optimizer|  optimizer.py
                  +-------------------+
                            |  [Optimized Plan]
                            v
                  +-------------------+
                  | Phase 6: Codegen  |  codegen.py
                  +-------------------+
                            |  [Bytecode]
                            v
                  +-------------------+
                  |   Phase 7: VM     |  vm.py + storage.py
                  +-------------------+
                            |
                            v
                  +-------------------+
                  |   QueryResult     |
                  +-------------------+
```

---

## 2. Core Modules & Data Structures

| Module | Subsystem | Core Data Structures | Responsibilities |
|--------|-----------|----------------------|------------------|
| `lexer.py` | Lexical Analysis | `Token`, `TokenType` | DFA state transitions scanning characters into token stream |
| `ast_nodes.py` | AST Representations | `Program`, `SelectStmt`, `BinaryOp`, etc. | Dataclasses forming abstract syntax trees |
| `parser.py` | Syntax Analysis | `Parser` | LL(1) recursive descent parser with panic-mode recovery |
| `catalog.py` | Symbol Table | `Catalog`, `TableSchema`, `ColumnSchema` | Symbol table managing schema declarations, types, and column resolutions |
| `semantic.py` | Contextual Analysis | `SemanticAnalyser` | Type checking, scoping, qualification, and aggregate validation |
| `ir.py` / `irgen.py` | Intermediate Representation | `PlanNode`, `ScanNode`, `TACProgram` | Logical relational algebra tree + 3-Address Code quadruples |
| `optimizer.py` | Code Optimization | `Optimizer` | Constant folding, boolean simplification, predicate pushdown |
| `codegen.py` | Target Code Generation | `BytecodeProgram`, `Instruction` | Emitting VM bytecode instructions from relational plans |
| `vm.py` | Execution Engine | `VM`, `ExprEvaluator`, `VMResult` | Stack-based Virtual Machine executing bytecode against storage |
| `storage.py` | Physical Storage | `Storage`, `Table` | In-memory table representation with JSON persistence |
