# MiniSQL Key Design Decisions

---

## 1. Why a Custom Stack-Based Virtual Machine?
Translating SQL queries directly to a custom stack-based bytecode VM (rather than executing via interpreter AST walkers) provides a faithful demonstration of code generation and execution in compiler theory. The VM instructions maintain an execution stack and program counter, cleanly separating compilation from runtime execution.

## 2. Three-Valued Logic (SQL Standard NULLs)
MiniSQL implements ANSI SQL three-valued logic (`TRUE`, `FALSE`, `NULL`):
- `NULL AND FALSE` -> `FALSE`
- `NULL OR TRUE` -> `TRUE`
- `NULL = NULL` -> `NULL` (unknown)
- Arithmetic operations involving `NULL` yield `NULL`.

## 3. Panic-Mode Error Recovery
When a syntax error occurs during parsing, the parser records the error, enters panic mode, and skips tokens until encountering the statement terminator `;`. This enables reporting multiple syntax errors in a single compilation run.

## 4. Hash Join vs. Nested Loop Join
The Optimizer selects between Hash Join and Nested Loop Join strategies based on table statistics in the Catalog:
- Hash Join is chosen when one table is significantly smaller or when equi-joins are present.
- Nested Loop Join is used for general predicate conditions or non-equi joins.
