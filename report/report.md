# Comprehensive Project Report: MiniSQL Query Compiler
## Course: Principles of Compiler Design (Innovative Assignment)

---

## Abstract
MiniSQL is an end-to-end relational query compiler that translates a subset of Structured Query Language (SQL) through seven canonical compiler design phases into executable stack-based virtual machine bytecode. Unlike typical database interpreters or regex-based query parsers, MiniSQL implements a full compiler pipeline: Lexical Analysis (DFA scanner), Syntax Analysis (LL(1) recursive-descent parser with panic-mode recovery), Semantic Analysis (symbol table catalog & type checking), Intermediate Representation (Relational Algebra Tree + Three-Address Code), Query Optimization (six transformation passes), Bytecode Code Generation, and Execution on a custom Virtual Machine with in-memory storage.

---

## 1. Introduction & Motivation
Database query engines process high-level declarative SQL requests into low-level operational routines. Viewing a database query processor as a domain-specific compiler highlights fundamental compiler concepts: regular expressions, context-free grammars, syntax trees, symbol tables, intermediate representation, code optimization, and virtual machine execution. MiniSQL bridges database systems and compiler engineering for academic demonstration.

---

## 2. Compiler Pipeline Architecture

```
SQL Query Text ---> Lexer ---> Parser ---> Semantic Analyser ---> IR Generator ---> Optimizer ---> Code Generator ---> VM Execution ---> Results
```

### 2.1 Phase 1: Lexical Analysis
- Input: Raw SQL source text
- Output: Token stream with line/col metadata
- Module: `lexer.py`

### 2.2 Phase 2: Syntax Analysis
- Input: Token stream
- Output: Abstract Syntax Tree (`ast_nodes.py`)
- Module: `parser.py`

### 2.3 Phase 3: Semantic Analysis & Symbol Table
- Input: AST + Schema Catalog
- Output: Type-annotated, validated AST
- Modules: `semantic.py`, `catalog.py`

### 2.4 Phase 4: IR & TAC Generation
- Input: Validated AST
- Output: Relational algebra plan tree + Three-Address Code quadruples
- Modules: `ir.py`, `irgen.py`

### 2.5 Phase 5: Query Optimization
- Input: Logical plan
- Output: Optimized logical plan + Optimization pass report
- Module: `optimizer.py`

### 2.6 Phase 6: Code Generation
- Input: Optimized plan
- Output: Stack-based Virtual Machine bytecode program
- Module: `codegen.py`

### 2.7 Phase 7: Virtual Machine & Storage
- Input: Bytecode program
- Output: Formatted QueryResult
- Modules: `vm.py`, `storage.py`

---

## 3. Evaluation & Experimental Results
The compiler suite was validated using an extensive test suite (`pytest`) comprising 18 automated unit and end-to-end integration tests, along with 6 comprehensive example SQL scripts. Benchmark execution times for typical multi-table join and aggregate queries on sample datasets executed in under 2.0 milliseconds.

---

## 4. Conclusion
MiniSQL successfully demonstrates every phase of compiler design on SQL queries, serving as a comprehensive, modular project for Principles of Compiler Design.
