"""
lexer.py — Phase 1: Lexical Analysis
=====================================
Hand-written DFA-based scanner for MiniSQL.

TOKEN CATEGORIES
----------------
  KEYWORD       Reserved SQL words (case-insensitive): SELECT, FROM, WHERE, ...
  IDENTIFIER    [a-zA-Z_][a-zA-Z0-9_]*  (not a keyword)
  INT_LITERAL   [0-9]+
  FLOAT_LITERAL [0-9]+ '.' [0-9]* | '.' [0-9]+
  STRING_LITERAL  '...' or "..." with escape sequences
  BOOL_LITERAL  TRUE | FALSE  (treated as keywords for simplicity)
  NULL_LITERAL  NULL
  OPERATOR      =  !=  <>  <  <=  >  >=  +  -  *  /  %
  PUNCTUATION   (  )  ,  ;  .
  EOF           End of input

REGULAR EXPRESSIONS (conceptual DFA)
--------------------------------------
  identifier    : [a-zA-Z_][a-zA-Z0-9_]*
  integer       : [0-9]+
  float         : [0-9]+ '.' [0-9]* | [0-9]* '.' [0-9]+
  string_single : ' ( [^'\\] | \\. )* '
  string_double : " ( [^"\\] | \\. )* "
  line_comment  : '--' [^\n]* '\n'
  block_comment : '/*' (.*?) '*/'   (non-greedy, handles nesting not required)
  operator      : =|!=|<>|<=|>=|<|>|+|-|*|/|%
  punctuation   : [(,);.]

DFA for identifiers/numbers is documented in docs/grammar.md.

Usage:
    from minisql.lexer import Lexer, TokenType
    tokens = Lexer(source).tokenize()
"""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum, auto
from typing import List, Optional
from .errors import ErrorReporter, Severity


# ---------------------------------------------------------------------------
# Token type enumeration
# ---------------------------------------------------------------------------

class TokenType(Enum):
    # Literals
    INT_LITERAL    = auto()
    FLOAT_LITERAL  = auto()
    STRING_LITERAL = auto()
    BOOL_LITERAL   = auto()
    NULL_LITERAL   = auto()

    # Names
    KEYWORD        = auto()
    IDENTIFIER     = auto()

    # Operators
    OPERATOR       = auto()

    # Punctuation
    PUNCTUATION    = auto()

    # Sentinel
    EOF            = auto()


# ---------------------------------------------------------------------------
# Reserved keywords (case-insensitive)
# ---------------------------------------------------------------------------

KEYWORDS: frozenset = frozenset({
    "SELECT", "DISTINCT", "FROM", "WHERE", "AND", "OR", "NOT",
    "INSERT", "INTO", "VALUES", "UPDATE", "SET", "DELETE",
    "CREATE", "TABLE", "DROP", "PRIMARY", "KEY",
    "JOIN", "INNER", "LEFT", "RIGHT", "OUTER", "FULL", "CROSS", "ON",
    "GROUP", "BY", "HAVING", "ORDER", "ASC", "DESC", "LIMIT",
    "AS", "IS", "NULL", "IN", "BETWEEN", "LIKE",
    "INT", "FLOAT", "TEXT", "BOOL",
    "COUNT", "SUM", "AVG", "MIN", "MAX",
    "TRUE", "FALSE",
})

# Keywords that represent literal values
BOOL_KEYWORDS: frozenset = frozenset({"TRUE", "FALSE"})
NULL_KEYWORDS: frozenset = frozenset({"NULL"})


# ---------------------------------------------------------------------------
# Token dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Token:
    """
    A single token produced by the lexer.

    Attributes
    ----------
    type    : TokenType
    lexeme  : str       -- exact text from source
    value   : object    -- Python-typed value (int, float, str, bool, None)
    line    : int       -- 1-indexed source line
    col     : int       -- 1-indexed column of first character
    """
    type:   TokenType
    lexeme: str
    value:  object
    line:   int
    col:    int

    def __repr__(self) -> str:
        return (
            f"Token({self.type.name}, {self.lexeme!r}, "
            f"line={self.line}, col={self.col})"
        )

    def is_keyword(self, *kws: str) -> bool:
        """Check if this token is a specific keyword (case-insensitive)."""
        return (
            self.type == TokenType.KEYWORD
            and self.lexeme.upper() in {k.upper() for k in kws}
        )

    def is_operator(self, *ops: str) -> bool:
        return self.type == TokenType.OPERATOR and self.lexeme in ops

    def is_punct(self, *chars: str) -> bool:
        return self.type == TokenType.PUNCTUATION and self.lexeme in chars


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

class Lexer:
    """
    DFA-based hand-written lexical analyser for MiniSQL.

    The scanner works character by character, dispatching to sub-scanners
    for each token category based on the current character (lookahead = 1).

    State transitions are implicit in the code structure:
      - Letter/underscore  -> scan_identifier_or_keyword()
      - Digit              -> scan_number()
      - Quote              -> scan_string()
      - '-' followed by '-' -> scan_line_comment()
      - '/' followed by '*' -> scan_block_comment()
      - '<', '>', '!', '=' -> scan_operator() (two-char lookahead)
      - Other operator/punct -> single character token
      - Whitespace         -> skip
    """

    def __init__(self, source: str, reporter: Optional[ErrorReporter] = None):
        self.source   = source
        self.reporter = reporter or ErrorReporter(source)
        self._pos     = 0          # current index into source
        self._line    = 1          # current line (1-indexed)
        self._col     = 1          # current column (1-indexed)
        self._tokens: List[Token] = []

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def tokenize(self) -> List[Token]:
        """Scan the entire source and return the token list (including EOF)."""
        while not self._at_end():
            self._skip_whitespace_and_comments()
            if self._at_end():
                break
            tok = self._next_token()
            if tok is not None:
                self._tokens.append(tok)

        # Append sentinel EOF
        self._tokens.append(Token(TokenType.EOF, "", None, self._line, self._col))
        return self._tokens

    # ------------------------------------------------------------------
    # Character-level helpers
    # ------------------------------------------------------------------

    def _at_end(self) -> bool:
        return self._pos >= len(self.source)

    def _peek(self, offset: int = 0) -> str:
        """Return character at current position + offset without consuming."""
        idx = self._pos + offset
        return self.source[idx] if idx < len(self.source) else "\0"

    def _advance(self) -> str:
        """Consume and return the current character, updating line/col."""
        ch = self.source[self._pos]
        self._pos += 1
        if ch == "\n":
            self._line += 1
            self._col  = 1
        else:
            self._col += 1
        return ch

    def _match(self, expected: str) -> bool:
        """Consume the next character if it equals *expected*."""
        if self._at_end() or self._peek() != expected:
            return False
        self._advance()
        return True

    # ------------------------------------------------------------------
    # Whitespace and comment skipping
    # ------------------------------------------------------------------

    def _skip_whitespace_and_comments(self) -> None:
        """Skip all whitespace, line comments (--), and block comments (/* */)."""
        while not self._at_end():
            ch = self._peek()
            if ch in " \t\r\n":
                self._advance()
            elif ch == "-" and self._peek(1) == "-":
                self._skip_line_comment()
            elif ch == "/" and self._peek(1) == "*":
                self._skip_block_comment()
            else:
                break

    def _skip_line_comment(self) -> None:
        """Skip from '--' to end of line (regex: --[^\n]*)."""
        self._advance()  # '-'
        self._advance()  # '-'
        while not self._at_end() and self._peek() != "\n":
            self._advance()

    def _skip_block_comment(self) -> None:
        """Skip from '/*' to '*/' (regex: /\*.*?\*/ non-greedy)."""
        start_line, start_col = self._line, self._col
        self._advance()  # '/'
        self._advance()  # '*'
        while not self._at_end():
            if self._peek() == "*" and self._peek(1) == "/":
                self._advance()  # '*'
                self._advance()  # '/'
                return
            self._advance()
        # Unterminated block comment
        self.reporter.report(
            "Lexer", "Unterminated block comment",
            start_line, start_col
        )

    # ------------------------------------------------------------------
    # Main dispatch
    # ------------------------------------------------------------------

    def _next_token(self) -> Optional[Token]:
        """Identify and return the next token starting at self._pos."""
        start_line = self._line
        start_col  = self._col
        ch = self._peek()

        # --- Identifiers and keywords ---
        if ch.isalpha() or ch == "_":
            return self._scan_identifier(start_line, start_col)

        # --- Numbers ---
        if ch.isdigit() or (ch == "." and self._peek(1).isdigit()):
            return self._scan_number(start_line, start_col)

        # --- Strings ---
        if ch in ("'", '"'):
            return self._scan_string(start_line, start_col)

        # --- Two-character operators: !=  <>  <=  >= ---
        if ch == "!" and self._peek(1) == "=":
            self._advance(); self._advance()
            return Token(TokenType.OPERATOR, "!=", "!=", start_line, start_col)
        if ch == "<" and self._peek(1) == ">":
            self._advance(); self._advance()
            return Token(TokenType.OPERATOR, "<>", "<>", start_line, start_col)
        if ch == "<" and self._peek(1) == "=":
            self._advance(); self._advance()
            return Token(TokenType.OPERATOR, "<=", "<=", start_line, start_col)
        if ch == ">" and self._peek(1) == "=":
            self._advance(); self._advance()
            return Token(TokenType.OPERATOR, ">=", ">=", start_line, start_col)

        # --- Single-character operators ---
        if ch in "=<>+-*/%":
            self._advance()
            return Token(TokenType.OPERATOR, ch, ch, start_line, start_col)

        # --- Punctuation ---
        if ch in "(),;.":
            self._advance()
            return Token(TokenType.PUNCTUATION, ch, ch, start_line, start_col)

        # --- Unknown character (lexical error) ---
        self._advance()
        self.reporter.report(
            "Lexer",
            f"Unexpected character {ch!r}",
            start_line, start_col,
        )
        return None  # skip it; scanning continues

    # ------------------------------------------------------------------
    # Sub-scanners
    # ------------------------------------------------------------------

    def _scan_identifier(self, line: int, col: int) -> Token:
        """
        DFA for identifiers:
          State 0 (start): accept [a-zA-Z_]  -> State 1
          State 1 (body) : accept [a-zA-Z0-9_] -> State 1  |  else -> accept

        Regular expression: [a-zA-Z_][a-zA-Z0-9_]*
        """
        start = self._pos
        while not self._at_end() and (self._peek().isalnum() or self._peek() == "_"):
            self._advance()
        lexeme = self.source[start:self._pos]
        upper  = lexeme.upper()

        if upper in BOOL_KEYWORDS:
            val = (upper == "TRUE")
            return Token(TokenType.BOOL_LITERAL, lexeme, val, line, col)
        if upper in NULL_KEYWORDS:
            return Token(TokenType.NULL_LITERAL, lexeme, None, line, col)
        if upper in KEYWORDS:
            return Token(TokenType.KEYWORD, lexeme, upper, line, col)
        return Token(TokenType.IDENTIFIER, lexeme, lexeme, line, col)

    def _scan_number(self, line: int, col: int) -> Token:
        """
        DFA for numeric literals:
          State 0       : digit -> State 1 | '.' digit -> State 2
          State 1 (int) : digit -> State 1 | '.' -> State 2 | else -> accept INT
          State 2 (frac): digit -> State 2 | else -> accept FLOAT

        Regular expressions:
          INT   : [0-9]+
          FLOAT : [0-9]+ '.' [0-9]* | [0-9]* '.' [0-9]+
        """
        start    = self._pos
        is_float = False

        # Scan integer part
        while not self._at_end() and self._peek().isdigit():
            self._advance()

        # Optional fractional part
        if not self._at_end() and self._peek() == "." and self._peek(1).isdigit():
            is_float = True
            self._advance()  # consume '.'
            while not self._at_end() and self._peek().isdigit():
                self._advance()

        lexeme = self.source[start:self._pos]
        if is_float:
            return Token(TokenType.FLOAT_LITERAL, lexeme, float(lexeme), line, col)
        return Token(TokenType.INT_LITERAL, lexeme, int(lexeme), line, col)

    def _scan_string(self, line: int, col: int) -> Token:
        """
        DFA for string literals (single or double quoted).
        Supports escape sequences: \\, \', \", \n, \t, \r

        State 0 (start) : quote -> State 1
        State 1 (body)  : '\\' -> State 2 | matching_quote -> accept | other -> State 1
        State 2 (escape): any -> State 1

        Regular expression (single-quoted): ' ( [^'\\] | \\. )* '
        """
        quote = self._advance()  # consume opening quote
        chars: List[str] = []
        ESCAPES = {"\\": "\\", "'": "'", '"': '"', "n": "\n", "t": "\t", "r": "\r"}

        while not self._at_end():
            ch = self._advance()
            if ch == "\\":
                esc = self._advance() if not self._at_end() else ""
                chars.append(ESCAPES.get(esc, esc))
            elif ch == quote:
                lexeme = quote + "".join(chars) + quote
                return Token(TokenType.STRING_LITERAL, lexeme, "".join(chars), line, col)
            else:
                chars.append(ch)

        # Unterminated string
        self.reporter.report(
            "Lexer", "Unterminated string literal", line, col
        )
        lexeme = quote + "".join(chars)
        return Token(TokenType.STRING_LITERAL, lexeme, "".join(chars), line, col)


# ---------------------------------------------------------------------------
# Pretty-print helper
# ---------------------------------------------------------------------------

def format_tokens(tokens: List[Token]) -> str:
    """Return a human-readable table of the token list (for --show tokens)."""
    lines = [
        f"{'#':<5} {'TYPE':<20} {'LEXEME':<25} {'VALUE':<20} {'LINE':<6} {'COL'}",
        "-" * 85,
    ]
    for i, tok in enumerate(tokens):
        val = repr(tok.value) if tok.value is not None else "NULL"
        lines.append(
            f"{i:<5} {tok.type.name:<20} {tok.lexeme:<25} {val:<20} "
            f"{tok.line:<6} {tok.col}"
        )
    return "\n".join(lines)
