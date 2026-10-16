"""
parser.py — Phase 2: Recursive-Descent (LL(1)) Parser
======================================================
Converts a flat token stream (from lexer.py) into an AST (ast_nodes.py).

DESIGN
------
* Pure recursive descent — one method per grammar non-terminal.
* Expressions use a precedence-climbing approach (one method per level)
  rather than a single expression parser with operator tables, so the
  grammar structure is explicit and explainable.
* Panic-mode error recovery: when an unexpected token is found, the
  parser reports the error and synchronises by skipping tokens until it
  finds ';' (or EOF), then continues parsing the next statement.  This
  allows multiple syntax errors to be reported in a single run.

GRAMMAR (abbreviated — full BNF in docs/grammar.md)
----------------------------------------------------
  program      -> statement* EOF
  statement    -> create_stmt | insert_stmt | select_stmt
               |  update_stmt | delete_stmt | drop_stmt
  expr (8 levels, lowest to highest):
    or_expr   -> and_expr (OR and_expr)*
    and_expr  -> not_expr (AND not_expr)*
    not_expr  -> NOT not_expr | cmp_expr
    cmp_expr  -> add_expr ( cmp_op add_expr
                           | BETWEEN add_expr AND add_expr
                           | IN '(' expr_list ')'
                           | IS [NOT] NULL
                           | [NOT] LIKE STRING )*
    add_expr  -> mul_expr (('+' | '-') mul_expr)*
    mul_expr  -> unary ((  '*' | '/' | '%') unary)*
    unary     -> '-' unary | primary
    primary   -> literal | col_ref | '(' expr ')' | agg_call

FIRST / FOLLOW sets are documented in docs/grammar.md.
"""

from __future__ import annotations
from typing import List, Optional, Tuple

from .lexer import Lexer, Token, TokenType
from .errors import ErrorReporter, MiniSQLError
from . import ast_nodes as ast


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class Parser:
    """
    LL(1) recursive-descent parser for MiniSQL.

    Attributes
    ----------
    tokens   : flat list of Token objects (including EOF sentinel)
    pos      : current index into tokens
    reporter : ErrorReporter used to collect parse errors
    """

    def __init__(self, tokens: List[Token], reporter: ErrorReporter):
        self.tokens   = tokens
        self.pos      = 0
        self.reporter = reporter
        self._source_lines: List[str] = reporter._lines

    # ------------------------------------------------------------------
    # Token navigation helpers
    # ------------------------------------------------------------------

    def _peek(self, offset: int = 0) -> Token:
        idx = self.pos + offset
        if idx < len(self.tokens):
            return self.tokens[idx]
        return self.tokens[-1]   # EOF

    def _advance(self) -> Token:
        tok = self.tokens[self.pos]
        if self.pos < len(self.tokens) - 1:
            self.pos += 1
        return tok

    def _at_end(self) -> bool:
        return self._peek().type == TokenType.EOF

    def _check(self, ttype: TokenType, *lexemes: str) -> bool:
        """Return True if current token matches type (and optionally any of the lexemes)."""
        tok = self._peek()
        if tok.type != ttype:
            return False
        if lexemes:
            return tok.lexeme.upper() in {l.upper() for l in lexemes}
        return True

    def _match_keyword(self, *kws: str) -> Optional[Token]:
        """Consume and return current token if it is one of the given keywords."""
        tok = self._peek()
        if tok.type in (TokenType.KEYWORD, TokenType.NULL_LITERAL, TokenType.BOOL_LITERAL) and tok.lexeme.upper() in {k.upper() for k in kws}:
            return self._advance()
        return None

    def _match_operator(self, *ops: str) -> Optional[Token]:
        """Consume and return current token if it is one of the given operators."""
        tok = self._peek()
        if tok.type == TokenType.OPERATOR and tok.lexeme in ops:
            return self._advance()
        return None

    def _match_punct(self, *chars: str) -> Optional[Token]:
        """Consume and return current token if it is one of the given punctuation chars."""
        tok = self._peek()
        if tok.type == TokenType.PUNCTUATION and tok.lexeme in chars:
            return self._advance()
        return None

    def _expect_keyword(self, *kws: str) -> Token:
        """Consume a keyword or report an error and raise."""
        tok = self._match_keyword(*kws)
        if tok is None:
            self._error(f"expected {' or '.join(kws)!r} but found {self._peek().lexeme!r}")
        return tok  # type: ignore

    def _expect_punct(self, char: str) -> Token:
        """Consume a punctuation character or report an error."""
        tok = self._match_punct(char)
        if tok is None:
            cur = self._peek()
            self._error(f"expected '{char}' but found {cur.lexeme!r}")
        return tok  # type: ignore

    def _expect_operator(self, *ops: str) -> Token:
        """Consume an operator or report an error."""
        tok = self._match_operator(*ops)
        if tok is None:
            self._error(f"expected operator {ops} but found {self._peek().lexeme!r}")
        return tok  # type: ignore

    def _expect_identifier(self) -> Token:
        """Consume an identifier token or report an error."""
        tok = self._peek()
        if tok.type == TokenType.IDENTIFIER:
            return self._advance()
        # Allow keywords to serve as bare identifiers (unquoted column aliases etc.)
        if tok.type == TokenType.KEYWORD:
            return self._advance()
        self._error(f"expected identifier but found {tok.lexeme!r}")
        # unreachable; _error raises
        raise AssertionError

    # ------------------------------------------------------------------
    # Error handling / panic-mode recovery
    # ------------------------------------------------------------------

    def _error(self, message: str) -> None:
        """Report a parse error at the current token position and raise."""
        tok = self._peek()
        self.reporter.report("Parser", message, tok.line, tok.col)
        raise _SyncError()

    def _synchronize(self) -> None:
        """
        Panic-mode recovery: skip tokens until we find ';' or EOF.
        This lets the parser continue and find more errors after the bad point.
        """
        while not self._at_end():
            if self._peek().type == TokenType.PUNCTUATION and self._peek().lexeme == ";":
                self._advance()  # consume the ';'
                return
            self._advance()

    # ------------------------------------------------------------------
    # Top-level entry point
    # ------------------------------------------------------------------

    def parse(self) -> ast.Program:
        """Parse the full token stream and return the AST root Program node."""
        stmts: List[ast.Statement] = []
        while not self._at_end():
            try:
                stmt = self._parse_statement()
                if stmt is not None:
                    stmts.append(stmt)
            except _SyncError:
                self._synchronize()
        return ast.Program(statements=stmts)

    # ------------------------------------------------------------------
    # Statements
    # ------------------------------------------------------------------

    def _parse_statement(self) -> Optional[ast.Statement]:
        """Dispatch to the correct statement parser based on the first keyword."""
        tok = self._peek()
        upper = tok.lexeme.upper() if tok.type == TokenType.KEYWORD else ""

        if upper == "CREATE":
            return self._parse_create()
        elif upper == "INSERT":
            return self._parse_insert()
        elif upper == "SELECT":
            return self._parse_select()
        elif upper == "UPDATE":
            return self._parse_update()
        elif upper == "DELETE":
            return self._parse_delete()
        elif upper == "DROP":
            return self._parse_drop()
        else:
            self._error(
                f"expected a statement keyword (SELECT, INSERT, …) "
                f"but found {tok.lexeme!r}"
            )
            return None

    # -- CREATE TABLE --

    def _parse_create(self) -> ast.CreateTableStmt:
        line, col = self._peek().line, self._peek().col
        self._expect_keyword("CREATE")
        self._expect_keyword("TABLE")
        name_tok = self._expect_identifier()
        self._expect_punct("(")
        cols = self._parse_column_defs()
        self._expect_punct(")")
        self._expect_punct(";")
        return ast.CreateTableStmt(
            table_name=name_tok.lexeme,
            columns=cols,
            line=line, col=col,
        )

    def _parse_column_defs(self) -> List[ast.ColumnDef]:
        cols: List[ast.ColumnDef] = []
        while True:
            col = self._parse_column_def()
            cols.append(col)
            if not self._match_punct(","):
                break
        return cols

    def _parse_column_def(self) -> ast.ColumnDef:
        line, col_pos = self._peek().line, self._peek().col
        name_tok = self._expect_identifier()
        type_tok = self._expect_keyword("INT", "FLOAT", "TEXT", "BOOL")
        pk = False
        if self._match_keyword("PRIMARY"):
            self._expect_keyword("KEY")
            pk = True
        return ast.ColumnDef(
            name=name_tok.lexeme,
            type_name=type_tok.lexeme.upper(),
            primary_key=pk,
            line=line, col=col_pos,
        )

    # -- INSERT --

    def _parse_insert(self) -> ast.InsertStmt:
        line, col = self._peek().line, self._peek().col
        self._expect_keyword("INSERT")
        self._expect_keyword("INTO")
        name_tok = self._expect_identifier()
        cols: Optional[List[str]] = None
        if self._match_punct("("):
            cols = self._parse_name_list()
            self._expect_punct(")")
        self._expect_keyword("VALUES")
        rows: List[List[ast.Expression]] = []
        rows.append(self._parse_value_row())
        while self._match_punct(","):
            rows.append(self._parse_value_row())
        self._expect_punct(";")
        return ast.InsertStmt(
            table_name=name_tok.lexeme,
            columns=cols,
            value_rows=rows,
            line=line, col=col,
        )

    def _parse_name_list(self) -> List[str]:
        names = [self._expect_identifier().lexeme]
        while self._match_punct(","):
            names.append(self._expect_identifier().lexeme)
        return names

    def _parse_value_row(self) -> List[ast.Expression]:
        self._expect_punct("(")
        vals = [self._parse_expr()]
        while self._match_punct(","):
            vals.append(self._parse_expr())
        self._expect_punct(")")
        return vals

    # -- SELECT --

    def _parse_select(self) -> ast.SelectStmt:
        line, col = self._peek().line, self._peek().col
        self._expect_keyword("SELECT")
        distinct = bool(self._match_keyword("DISTINCT"))
        items    = self._parse_select_list()
        self._expect_keyword("FROM")
        from_tbl = self._parse_table_ref()
        joins    = self._parse_join_clauses()
        where    = None
        if self._match_keyword("WHERE"):
            where = self._parse_expr()
        group_by: List[ast.Expression] = []
        having: Optional[ast.Expression] = None
        if self._match_keyword("GROUP"):
            self._expect_keyword("BY")
            group_by = self._parse_expr_list()
            if self._match_keyword("HAVING"):
                having = self._parse_expr()
        order_by: List[ast.OrderByItem] = []
        if self._match_keyword("ORDER"):
            self._expect_keyword("BY")
            order_by = self._parse_order_by()
        limit: Optional[int] = None
        if self._match_keyword("LIMIT"):
            tok = self._peek()
            if tok.type == TokenType.INT_LITERAL:
                limit = tok.value
                self._advance()
            else:
                self._error("LIMIT requires an integer literal")
        self._expect_punct(";")
        return ast.SelectStmt(
            distinct=distinct,
            items=items,
            from_table=from_tbl,
            joins=joins,
            where=where,
            group_by=group_by,
            having=having,
            order_by=order_by,
            limit=limit,
            line=line, col=col,
        )

    def _parse_select_list(self) -> List[ast.SelectItem]:
        items = [self._parse_select_item()]
        while self._match_punct(","):
            items.append(self._parse_select_item())
        return items

    def _parse_select_item(self) -> ast.SelectItem:
        line, col = self._peek().line, self._peek().col
        # bare '*'
        if self._check(TokenType.OPERATOR, "*"):
            self._advance()
            return ast.SelectItem(expr=ast.Star(line=line, col=col), line=line, col=col)
        expr = self._parse_expr()
        alias: Optional[str] = None
        if self._match_keyword("AS"):
            alias = self._expect_identifier().lexeme
        elif self._peek().type == TokenType.IDENTIFIER:
            # implicit alias (no AS)
            alias = self._advance().lexeme
        return ast.SelectItem(expr=expr, alias=alias, line=line, col=col)

    def _parse_table_ref(self) -> ast.TableRef:
        line, col = self._peek().line, self._peek().col
        name_tok = self._expect_identifier()
        alias: Optional[str] = None
        if self._match_keyword("AS"):
            alias = self._expect_identifier().lexeme
        elif (self._peek().type == TokenType.IDENTIFIER
              and self._peek().lexeme.upper() not in {
                  "WHERE", "GROUP", "ORDER", "HAVING", "LIMIT",
                  "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "CROSS",
                  "ON", "SET",
              }):
            alias = self._advance().lexeme
        return ast.TableRef(name=name_tok.lexeme, alias=alias, line=line, col=col)

    def _parse_join_clauses(self) -> List[ast.JoinClause]:
        joins: List[ast.JoinClause] = []
        JOIN_TYPES = {"JOIN", "INNER", "LEFT", "RIGHT", "FULL", "CROSS"}
        while self._peek().type == TokenType.KEYWORD and self._peek().lexeme.upper() in JOIN_TYPES:
            line, col = self._peek().line, self._peek().col
            jtype = "INNER"
            tok = self._peek()
            upper = tok.lexeme.upper()
            if upper in {"LEFT", "RIGHT", "FULL", "CROSS", "INNER"}:
                jtype = upper
                self._advance()
                self._match_keyword("OUTER")   # optional OUTER keyword
                self._match_keyword("JOIN")
            else:
                self._advance()   # consume JOIN

            table = self._parse_table_ref()
            cond: Optional[ast.Expression] = None
            if jtype != "CROSS" and self._match_keyword("ON"):
                cond = self._parse_expr()
            joins.append(ast.JoinClause(join_type=jtype, table=table, condition=cond, line=line, col=col))
        return joins

    def _parse_order_by(self) -> List[ast.OrderByItem]:
        items: List[ast.OrderByItem] = []
        while True:
            line, col = self._peek().line, self._peek().col
            expr = self._parse_expr()
            direction = "ASC"
            if self._match_keyword("ASC"):
                direction = "ASC"
            elif self._match_keyword("DESC"):
                direction = "DESC"
            items.append(ast.OrderByItem(expr=expr, direction=direction, line=line, col=col))
            if not self._match_punct(","):
                break
        return items

    # -- UPDATE --

    def _parse_update(self) -> ast.UpdateStmt:
        line, col = self._peek().line, self._peek().col
        self._expect_keyword("UPDATE")
        name_tok = self._expect_identifier()
        self._expect_keyword("SET")
        assigns = self._parse_assignments()
        where: Optional[ast.Expression] = None
        if self._match_keyword("WHERE"):
            where = self._parse_expr()
        self._expect_punct(";")
        return ast.UpdateStmt(
            table_name=name_tok.lexeme,
            assignments=assigns,
            where=where,
            line=line, col=col,
        )

    def _parse_assignments(self) -> List[ast.Assignment]:
        assigns: List[ast.Assignment] = []
        while True:
            line, col = self._peek().line, self._peek().col
            col_tok = self._expect_identifier()
            self._expect_operator("=")
            val = self._parse_expr()
            assigns.append(ast.Assignment(column=col_tok.lexeme, value=val, line=line, col=col))
            if not self._match_punct(","):
                break
        return assigns

    # -- DELETE --

    def _parse_delete(self) -> ast.DeleteStmt:
        line, col = self._peek().line, self._peek().col
        self._expect_keyword("DELETE")
        self._expect_keyword("FROM")
        name_tok = self._expect_identifier()
        where: Optional[ast.Expression] = None
        if self._match_keyword("WHERE"):
            where = self._parse_expr()
        self._expect_punct(";")
        return ast.DeleteStmt(table_name=name_tok.lexeme, where=where, line=line, col=col)

    # -- DROP --

    def _parse_drop(self) -> ast.DropTableStmt:
        line, col = self._peek().line, self._peek().col
        self._expect_keyword("DROP")
        self._expect_keyword("TABLE")
        name_tok = self._expect_identifier()
        self._expect_punct(";")
        return ast.DropTableStmt(table_name=name_tok.lexeme, line=line, col=col)

    # ------------------------------------------------------------------
    # Expression grammar (8 precedence levels)
    # ------------------------------------------------------------------

    def _parse_expr(self) -> ast.Expression:
        return self._parse_or()

    def _parse_expr_list(self) -> List[ast.Expression]:
        exprs = [self._parse_expr()]
        while self._match_punct(","):
            exprs.append(self._parse_expr())
        return exprs

    # Level 1 (lowest): OR
    def _parse_or(self) -> ast.Expression:
        left = self._parse_and()
        while self._match_keyword("OR"):
            line, col = self._peek().line, self._peek().col
            right = self._parse_and()
            left  = ast.BinaryOp(op="OR", left=left, right=right, line=line, col=col)
        return left

    # Level 2: AND
    def _parse_and(self) -> ast.Expression:
        left = self._parse_not()
        while self._match_keyword("AND"):
            line, col = self._peek().line, self._peek().col
            right = self._parse_not()
            left  = ast.BinaryOp(op="AND", left=left, right=right, line=line, col=col)
        return left

    # Level 3: NOT
    def _parse_not(self) -> ast.Expression:
        if self._match_keyword("NOT"):
            line, col = self._peek().line, self._peek().col
            operand = self._parse_not()
            return ast.UnaryOp(op="NOT", operand=operand, line=line, col=col)
        return self._parse_comparison()

    # Level 4: comparisons, BETWEEN, IN, IS NULL, LIKE
    def _parse_comparison(self) -> ast.Expression:
        left = self._parse_add()
        CMP_OPS = {"=", "!=", "<>", "<", "<=", ">", ">="}

        while True:
            tok = self._peek()

            # Standard comparison operators
            if tok.type == TokenType.OPERATOR and tok.lexeme in CMP_OPS:
                op = self._advance().lexeme
                if op == "<>":
                    op = "!="   # normalise
                right = self._parse_add()
                left  = ast.BinaryOp(op=op, left=left, right=right,
                                     line=tok.line, col=tok.col)
                continue

            # NOT BETWEEN / NOT IN / NOT LIKE
            negated = False
            if tok.type == TokenType.KEYWORD and tok.lexeme.upper() == "NOT":
                # peek further to decide what kind of expression
                next2 = self._peek(1)
                if next2.type == TokenType.KEYWORD and next2.lexeme.upper() in {
                    "BETWEEN", "IN", "LIKE"
                }:
                    self._advance()  # consume NOT
                    negated = True
                    tok = self._peek()

            if tok.type == TokenType.KEYWORD and tok.lexeme.upper() == "BETWEEN":
                self._advance()
                lo  = self._parse_add()
                self._expect_keyword("AND")
                hi  = self._parse_add()
                left = ast.BetweenExpr(expr=left, lo=lo, hi=hi, negated=negated,
                                       line=tok.line, col=tok.col)
                continue

            if tok.type == TokenType.KEYWORD and tok.lexeme.upper() == "IN":
                self._advance()
                self._expect_punct("(")
                values = self._parse_expr_list()
                self._expect_punct(")")
                left = ast.InListExpr(expr=left, values=values, negated=negated,
                                      line=tok.line, col=tok.col)
                continue

            if tok.type == TokenType.KEYWORD and tok.lexeme.upper() == "LIKE":
                self._advance()
                pattern = self._parse_primary()
                left = ast.LikeExpr(expr=left, pattern=pattern, negated=negated,
                                    line=tok.line, col=tok.col)
                continue

            # IS [NOT] NULL  (negated = False here; check separately)
            if tok.type == TokenType.KEYWORD and tok.lexeme.upper() == "IS":
                self._advance()
                is_not = bool(self._match_keyword("NOT"))
                self._expect_keyword("NULL")
                left = ast.IsNullExpr(expr=left, negated=is_not,
                                      line=tok.line, col=tok.col)
                continue

            break
        return left

    # Level 5: addition / subtraction
    def _parse_add(self) -> ast.Expression:
        left = self._parse_mul()
        while True:
            tok = self._peek()
            if tok.type == TokenType.OPERATOR and tok.lexeme in ("+", "-"):
                op = self._advance().lexeme
                right = self._parse_mul()
                left  = ast.BinaryOp(op=op, left=left, right=right,
                                     line=tok.line, col=tok.col)
            else:
                break
        return left

    # Level 6: multiplication / division / modulo
    def _parse_mul(self) -> ast.Expression:
        left = self._parse_unary()
        while True:
            tok = self._peek()
            if tok.type == TokenType.OPERATOR and tok.lexeme in ("*", "/", "%"):
                op = self._advance().lexeme
                right = self._parse_unary()
                left  = ast.BinaryOp(op=op, left=left, right=right,
                                     line=tok.line, col=tok.col)
            else:
                break
        return left

    # Level 7: unary minus / NOT (NOT is handled at level 3)
    def _parse_unary(self) -> ast.Expression:
        tok = self._peek()
        if tok.type == TokenType.OPERATOR and tok.lexeme == "-":
            self._advance()
            operand = self._parse_unary()
            return ast.UnaryOp(op="-", operand=operand, line=tok.line, col=tok.col)
        return self._parse_primary()

    # Level 8 (highest): literals, identifiers, function calls, parentheses
    def _parse_primary(self) -> ast.Expression:
        tok = self._peek()
        line, col = tok.line, tok.col

        # Integer literal
        if tok.type == TokenType.INT_LITERAL:
            self._advance()
            return ast.Literal(value=tok.value, kind="INT", line=line, col=col)

        # Float literal
        if tok.type == TokenType.FLOAT_LITERAL:
            self._advance()
            return ast.Literal(value=tok.value, kind="FLOAT", line=line, col=col)

        # String literal
        if tok.type == TokenType.STRING_LITERAL:
            self._advance()
            return ast.Literal(value=tok.value, kind="TEXT", line=line, col=col)

        # Boolean literal
        if tok.type == TokenType.BOOL_LITERAL:
            self._advance()
            return ast.Literal(value=tok.value, kind="BOOL", line=line, col=col)

        # NULL literal
        if tok.type == TokenType.NULL_LITERAL:
            self._advance()
            return ast.Literal(value=None, kind="NULL", line=line, col=col)

        # Aggregate functions: COUNT, SUM, AVG, MIN, MAX
        if (tok.type == TokenType.KEYWORD
                and tok.lexeme.upper() in {"COUNT", "SUM", "AVG", "MIN", "MAX"}):
            return self._parse_aggregate(line, col)

        # Parenthesised expression
        if tok.type == TokenType.PUNCTUATION and tok.lexeme == "(":
            self._advance()
            expr = self._parse_expr()
            self._expect_punct(")")
            return expr

        # table.column  or  column
        if tok.type in (TokenType.IDENTIFIER, TokenType.KEYWORD):
            name = self._advance().lexeme
            if self._match_punct("."):
                col_name = self._expect_identifier().lexeme
                return ast.ColumnRef(table=name, column=col_name, line=line, col=col)
            return ast.ColumnRef(column=name, line=line, col=col)

        # Bare '*' in aggregate context (handled by caller mostly)
        if tok.type == TokenType.OPERATOR and tok.lexeme == "*":
            self._advance()
            return ast.Star(line=line, col=col)

        self._error(f"unexpected token {tok.lexeme!r} in expression")
        raise AssertionError  # unreachable

    def _parse_aggregate(self, line: int, col: int) -> ast.FunctionCall:
        func_tok = self._advance()   # consume function name
        func     = func_tok.lexeme.upper()
        self._expect_punct("(")
        if func == "COUNT" and self._check(TokenType.OPERATOR, "*"):
            self._advance()          # consume '*'
            arg = None               # COUNT(*)
        else:
            arg = self._parse_expr()
        self._expect_punct(")")
        return ast.FunctionCall(func=func, arg=arg, line=line, col=col)


# ---------------------------------------------------------------------------
# Sentinel exception for panic-mode recovery (internal use only)
# ---------------------------------------------------------------------------

class _SyncError(Exception):
    """Raised internally when a parse error is found; caught by parse()."""
    pass


# ---------------------------------------------------------------------------
# Public convenience function
# ---------------------------------------------------------------------------

def parse(source: str, reporter: Optional[ErrorReporter] = None) -> ast.Program:
    """
    Lex + parse a MiniSQL source string, returning the AST root.

    Raises MiniSQLError if any errors were found.
    """
    from .lexer import Lexer
    rep    = reporter or ErrorReporter(source)
    tokens = Lexer(source, rep).tokenize()
    prog   = Parser(tokens, rep).parse()
    if reporter is None:
        rep.raise_if_errors()
    return prog
