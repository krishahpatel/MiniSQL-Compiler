# MiniSQL Context-Free Grammar (BNF / EBNF)

---

## 1. Formal Grammar Specification

```ebnf
program        ::= statement* EOF
statement      ::= create_stmt | insert_stmt | select_stmt
                 | update_stmt | delete_stmt | drop_stmt

create_stmt    ::= CREATE TABLE identifier '(' col_def (',' col_def)* ')' ';'
col_def        ::= identifier type_keyword [PRIMARY KEY]
type_keyword   ::= INT | FLOAT | TEXT | BOOL

insert_stmt    ::= INSERT INTO identifier ['(' ident_list ')'] VALUES val_tuple (',' val_tuple)* ';'
ident_list     ::= identifier (',' identifier)*
val_tuple      ::= '(' expr (',' expr)* ')'

select_stmt    ::= SELECT [DISTINCT] select_list FROM table_ref
                   (join_clause)*
                   [WHERE expr]
                   [GROUP BY expr (',' expr)*]
                   [HAVING expr]
                   [ORDER BY order_item (',' order_item)*]
                   [LIMIT INT_LITERAL] ';'

table_ref      ::= identifier [identifier]
join_clause    ::= [INNER | LEFT | RIGHT | FULL | CROSS] [JOIN] table_ref [ON expr]

select_list    ::= '*' | select_item (',' select_item)*
select_item    ::= expr [AS identifier]

order_item     ::= expr [ASC | DESC]

update_stmt    ::= UPDATE identifier SET assignment (',' assignment)* [WHERE expr] ';'
assignment     ::= identifier '=' expr

delete_stmt    ::= DELETE FROM identifier [WHERE expr] ';'
drop_stmt      ::= DROP TABLE identifier ';'

/* Expression Grammar (Precedence: Lowest to Highest) */
expr           ::= or_expr
or_expr        ::= and_expr (OR and_expr)*
and_expr       ::= not_expr (AND not_expr)*
not_expr       ::= NOT not_expr | cmp_expr
cmp_expr       ::= add_expr [ cmp_op add_expr
                            | [NOT] BETWEEN add_expr AND add_expr
                            | [NOT] IN '(' expr_list ')'
                            | IS [NOT] NULL
                            | [NOT] LIKE STRING_LITERAL ]
cmp_op         ::= '=' | '!=' | '<>' | '<' | '<=' | '>' | '>='
add_expr       ::= mul_expr (('+' | '-') mul_expr)*
mul_expr       ::= unary_expr (('*' | '/' | '%') unary_expr)*
unary_expr     ::= '-' unary_expr | primary
primary        ::= literal | col_ref | '(' expr ')' | agg_call
agg_call       ::= (COUNT | SUM | AVG | MIN | MAX) '(' ('*' | expr) ')'
col_ref        ::= [identifier '.'] identifier
literal        ::= INT_LITERAL | FLOAT_LITERAL | STRING_LITERAL | BOOL_LITERAL | NULL
```

---

## 2. Operator Precedence Table

| Precedence | Level | Operators / Expression | Associativity |
|------------|-------|-----------------------|---------------|
| 1 (Lowest) | Logical OR | `OR` | Left-to-right |
| 2 | Logical AND | `AND` | Left-to-right |
| 3 | Logical NOT | `NOT` | Right-to-left |
| 4 | Relational / Pattern | `=`, `!=`, `<>`, `<`, `<=`, `>`, `>=`, `BETWEEN`, `IN`, `IS NULL`, `LIKE` | Non-associative |
| 5 | Term | `+`, `-` | Left-to-right |
| 6 | Factor | `*`, `/`, `%` | Left-to-right |
| 7 | Unary | `-` (negation) | Right-to-left |
| 8 (Highest)| Primary / Literals | Literals, `ColumnRef`, `( Expr )`, Aggregates | N/A |

---

## 3. FIRST and FOLLOW Sets

### 3.1 Non-Terminal FIRST Sets

- **FIRST(program)** = { `CREATE`, `INSERT`, `SELECT`, `UPDATE`, `DELETE`, `DROP`, `EOF` }
- **FIRST(statement)** = { `CREATE`, `INSERT`, `SELECT`, `UPDATE`, `DELETE`, `DROP` }
- **FIRST(expr)** = { `NOT`, `INT`, `FLOAT`, `STRING`, `BOOL`, `NULL`, `IDENTIFIER`, `(`, `-`, `COUNT`, `SUM`, `AVG`, `MIN`, `MAX` }

### 3.2 Non-Terminal FOLLOW Sets

- **FOLLOW(statement)** = { `CREATE`, `INSERT`, `SELECT`, `UPDATE`, `DELETE`, `DROP`, `EOF` }
- **FOLLOW(expr)** = { `,`, `;`, `)`, `AS`, `ON`, `AND`, `OR`, `WHERE`, `GROUP`, `HAVING`, `ORDER`, `LIMIT` }
