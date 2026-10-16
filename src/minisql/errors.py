"""
errors.py — Unified Error Reporting Module
==========================================
All compiler phases use this module to emit consistent, user-friendly
error messages with source location and a caret (^) pointing to the
exact position of the problem.

Error format:
  [Phase] Error at line L, col C: message
      source line here
                   ^--- caret
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional


# ---------------------------------------------------------------------------
# Error severity levels
# ---------------------------------------------------------------------------

class Severity:
    ERROR   = "Error"
    WARNING = "Warning"
    NOTE    = "Note"


# ---------------------------------------------------------------------------
# Structured error record
# ---------------------------------------------------------------------------

@dataclass
class CompileError:
    """A single compiler diagnostic message."""
    phase:   str            # e.g. "Lexer", "Parser", "Semantic", "Runtime"
    message: str
    line:    int  = 0       # 1-indexed
    col:     int  = 0       # 1-indexed
    source_line: str = ""   # the raw source line for context
    severity: str = Severity.ERROR
    hint:    Optional[str] = None   # "did you mean X?"

    def __str__(self) -> str:
        header = (
            f"[{self.phase}] {self.severity} at line {self.line}, "
            f"col {self.col}: {self.message}"
        )
        lines = [header]
        if self.source_line:
            lines.append(f"  {self.source_line}")
            # Build caret line; col is 1-based, prefix has 2 spaces
            caret_pos = max(0, self.col - 1)
            lines.append(f"  {' ' * caret_pos}^")
        if self.hint:
            lines.append(f"  Hint: {self.hint}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Error collector — gathers errors across a compilation run
# ---------------------------------------------------------------------------

class ErrorReporter:
    """
    Collects all errors during compilation so we can report them all
    at once rather than stopping at the first problem.
    """

    def __init__(self, source: str = ""):
        self.errors: List[CompileError] = []
        # Split source into lines for context display (1-indexed via offset 0)
        self._lines: List[str] = source.splitlines() if source else []

    def report(
        self,
        phase:   str,
        message: str,
        line:    int = 0,
        col:     int = 0,
        severity: str = Severity.ERROR,
        hint:    Optional[str] = None,
    ) -> CompileError:
        """Record and return a CompileError."""
        src_line = ""
        if line > 0 and line <= len(self._lines):
            src_line = self._lines[line - 1]

        err = CompileError(
            phase=phase,
            message=message,
            line=line,
            col=col,
            source_line=src_line,
            severity=severity,
            hint=hint,
        )
        self.errors.append(err)
        return err

    def has_errors(self) -> bool:
        return any(e.severity == Severity.ERROR for e in self.errors)

    def raise_if_errors(self) -> None:
        """Raise a MiniSQLError if any errors were recorded."""
        if self.has_errors():
            raise MiniSQLError(self)

    def __len__(self) -> int:
        return len(self.errors)

    def __bool__(self) -> bool:
        return True

    def __str__(self) -> str:
        return "\n\n".join(str(e) for e in self.errors)


# ---------------------------------------------------------------------------
# Exception wrapper
# ---------------------------------------------------------------------------

class MiniSQLError(Exception):
    """Raised when the compiler encounters one or more fatal errors."""

    def __init__(self, reporter: ErrorReporter):
        self.reporter = reporter
        super().__init__(str(reporter))


# ---------------------------------------------------------------------------
# Utility: Levenshtein edit distance for "did you mean?" suggestions
# ---------------------------------------------------------------------------

def edit_distance(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between strings a and b."""
    m, n = len(a), len(b)
    # dp[i][j] = edit distance between a[:i] and b[:j]
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[:]
        dp[0] = i
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                dp[j] = prev[j - 1]
            else:
                dp[j] = 1 + min(prev[j], dp[j - 1], prev[j - 1])
    return dp[n]


def did_you_mean(name: str, candidates: List[str], max_dist: int = 2) -> Optional[str]:
    """
    Return the closest candidate to *name* within *max_dist* edit distance,
    or None if no good match exists.
    """
    best, best_dist = None, max_dist + 1
    for c in candidates:
        d = edit_distance(name.lower(), c.lower())
        if d < best_dist:
            best, best_dist = c, d
    return best
