"""
storage.py — In-Memory Storage Layer
=====================================
Provides a simple in-memory table store that persists to / from JSON files.

Design
------
  Storage
    .tables : dict[str, Table]

  Table
    .schema  : TableSchema (from catalog.py)
    .rows    : list of dict[str, Any]   -- each row is a Python dict

Rows are plain Python dicts so the VM can work with them directly
without any ORM overhead.

NULL values are stored as Python None.

Three-valued logic (for NULL handling):
  A = None: A = B is NULL (not TRUE or FALSE)
  We implement this in sql_equal() etc. below.
"""

from __future__ import annotations
import json
import os
import re
from typing import Any, Dict, List, Optional, Iterator

from .catalog import Catalog, TableSchema, ColumnSchema


# ---------------------------------------------------------------------------
# Python value coercion helpers
# ---------------------------------------------------------------------------

def coerce(value: Any, type_name: str) -> Any:
    """
    Coerce a Python value to the MiniSQL column type.
    Returns None (NULL) if coercion fails.
    """
    if value is None:
        return None
    try:
        if type_name == "INT":
            return int(value)
        if type_name == "FLOAT":
            return float(value)
        if type_name == "TEXT":
            return str(value)
        if type_name == "BOOL":
            if isinstance(value, bool):
                return value
            if isinstance(value, int):
                return bool(value)
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes")
    except (ValueError, TypeError):
        pass
    return None


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------

class Table:
    """
    An in-memory table with a schema and a list of row dicts.

    Each row is:  { col_name: python_value, ... }
    """

    def __init__(self, schema: TableSchema):
        self.schema: TableSchema      = schema
        self.rows:   List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def insert(self, row: Dict[str, Any]) -> None:
        """Insert a row dict.  Missing columns are filled with None."""
        complete: Dict[str, Any] = {}
        for col in self.schema.columns:
            val = row.get(col.name)
            if val is None:
                val = row.get(col.name.lower())
            complete[col.name] = coerce(val, col.type_name)
        self.rows.append(complete)

    def scan(self) -> Iterator[Dict[str, Any]]:
        """Yield all rows."""
        yield from self.rows

    def delete_where(self, predicate) -> int:
        """
        Delete rows satisfying *predicate* (a Python callable row->bool).
        Returns number of deleted rows.
        """
        before   = len(self.rows)
        self.rows = [r for r in self.rows if not _truthy(predicate(r))]
        return before - len(self.rows)

    def update_where(self, predicate, updates: Dict[str, Any]) -> int:
        """
        Update rows satisfying *predicate*.
        Returns number of updated rows.
        """
        count = 0
        for row in self.rows:
            if _truthy(predicate(row)):
                for col, val in updates.items():
                    # Find the column schema to get the type
                    col_schema = self.schema.get_column(col)
                    tname = col_schema.type_name if col_schema else "TEXT"
                    row[col] = coerce(val, tname)
                count += 1
        return count

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {"schema": self.schema.to_dict(), "rows": self.rows}

    @staticmethod
    def from_dict(d: dict) -> "Table":
        schema = TableSchema.from_dict(d["schema"])
        tbl    = Table(schema)
        tbl.rows = d["rows"]
        return tbl

    def __len__(self) -> int:
        return len(self.rows)

    def __repr__(self) -> str:
        return f"Table({self.schema.name}, {len(self.rows)} rows)"


# ---------------------------------------------------------------------------
# Storage engine
# ---------------------------------------------------------------------------

class Storage:
    """
    The runtime storage engine: a dict of Table objects.

    The VM uses this to execute SCAN, INSERT_ROW, UPDATE_ROW, DELETE_ROW.
    """

    def __init__(self, catalog: Catalog, db_path: Optional[str] = None):
        self.catalog  = catalog
        self.db_path  = db_path
        self._tables: Dict[str, Table] = {}
        # Auto-load if db_path exists
        if db_path and os.path.exists(db_path):
            self.load(db_path)

    # ------------------------------------------------------------------
    # Table access
    # ------------------------------------------------------------------

    def get_table(self, name: str) -> Optional[Table]:
        return self._tables.get(name.lower())

    def create_table(self, schema: TableSchema) -> Table:
        """Create a new (empty) table from a TableSchema."""
        tbl = Table(schema)
        self._tables[schema.name.lower()] = tbl
        return tbl

    def drop_table(self, name: str) -> None:
        self._tables.pop(name.lower(), None)

    def has_table(self, name: str) -> bool:
        return name.lower() in self._tables

    def table_names(self) -> List[str]:
        return [t.schema.name for t in self._tables.values()]

    # ------------------------------------------------------------------
    # Sync with catalog
    # ------------------------------------------------------------------

    def sync_catalog(self) -> None:
        """
        Ensure a Table object exists for every table in the catalog.
        Called after loading a script that uses CREATE TABLE.
        """
        for tname in self.catalog.table_names():
            if not self.has_table(tname):
                schema = self.catalog.get_table(tname)
                if schema:
                    self.create_table(schema)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Optional[str] = None) -> None:
        """Persist all tables to a JSON file."""
        path = path or self.db_path
        if not path:
            return
        data = {name: tbl.to_dict() for name, tbl in self._tables.items()}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

    def load(self, path: str) -> None:
        """Load tables from a JSON file (merges into current storage)."""
        if not os.path.exists(path):
            return
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for name, tbl_data in data.items():
            tbl = Table.from_dict(tbl_data)
            self._tables[name] = tbl
            # Sync schema back into catalog
            if not self.catalog.has_table(tbl.schema.name):
                self.catalog.add_table(tbl.schema)
            self.catalog.set_row_count(tbl.schema.name, len(tbl))

    def update_row_counts(self) -> None:
        """Update catalog row count estimates from actual storage."""
        for name, tbl in self._tables.items():
            self.catalog.set_row_count(tbl.schema.name, len(tbl))

    # ------------------------------------------------------------------
    # Result formatting
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        tbls = ", ".join(f"{n}({len(t)} rows)" for n, t in self._tables.items())
        return f"Storage([{tbls}])"


# ---------------------------------------------------------------------------
# NULL-safe comparison helpers (three-valued logic)
# ---------------------------------------------------------------------------

def sql_equal(a: Any, b: Any) -> Optional[bool]:
    """Return True/False/None for SQL a = b (NULL-safe)."""
    if a is None or b is None:
        return None
    return a == b


def sql_compare(op: str, a: Any, b: Any) -> Optional[bool]:
    """Evaluate a comparison operator with NULL semantics."""
    if a is None or b is None:
        return None
    try:
        if op in ("=",):  return a == b
        if op in ("!=", "<>"): return a != b
        if op == "<":   return a < b
        if op == "<=":  return a <= b
        if op == ">":   return a > b
        if op == ">=":  return a >= b
    except TypeError:
        return None
    return None


def sql_like(text: Optional[str], pattern: Optional[str]) -> Optional[bool]:
    """Evaluate SQL LIKE with % (any chars) and _ (single char)."""
    if text is None or pattern is None:
        return None
    # Convert SQL pattern to Python regex
    regex = re.escape(pattern).replace(r"\%", ".*").replace(r"\_", ".")
    return bool(re.fullmatch(regex, text, re.DOTALL))


def _truthy(v: Any) -> bool:
    """SQL truthiness: None (NULL) -> False, else bool(v)."""
    if v is None:
        return False
    return bool(v)


# ---------------------------------------------------------------------------
# Pretty-print result rows as a table
# ---------------------------------------------------------------------------

def format_result(
    rows: List[Dict[str, Any]],
    columns: Optional[List[str]] = None,
    row_count: Optional[int] = None,
    elapsed_ms: Optional[float] = None,
) -> str:
    """
    Format query results like a psql-style table.

    Example output:
      +----+----------+--------+
      | id | name     | salary |
      +----+----------+--------+
      |  1 | Alice    |  75000 |
      |  2 | Bob      |  60000 |
      +----+----------+--------+
      2 rows (1.23 ms)
    """
    if not rows:
        n   = row_count if row_count is not None else 0
        msg = f"(0 rows)"
        if elapsed_ms is not None:
            msg += f" ({elapsed_ms:.2f} ms)"
        return msg

    # Determine column order
    if columns is None:
        columns = list(rows[0].keys())

    # Compute column widths
    widths = {col: len(str(col)) for col in columns}
    for row in rows:
        for col in columns:
            val = row.get(col)
            widths[col] = max(widths[col], len("NULL" if val is None else str(val)))

    def sep_line():
        return "+" + "+".join("-" * (w + 2) for w in [widths[c] for c in columns]) + "+"

    def data_line(values):
        cells = []
        for col, val in zip(columns, values):
            s = "NULL" if val is None else str(val)
            # Right-align numbers
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                cells.append(f" {s:>{widths[col]}} ")
            else:
                cells.append(f" {s:<{widths[col]}} ")
        return "|" + "|".join(cells) + "|"

    lines = [sep_line(), data_line(columns), sep_line()]
    for row in rows:
        lines.append(data_line([row.get(c) for c in columns]))
    lines.append(sep_line())

    n   = row_count if row_count is not None else len(rows)
    footer = f"{n} row{'s' if n != 1 else ''}"
    if elapsed_ms is not None:
        footer += f" ({elapsed_ms:.2f} ms)"
    lines.append(footer)
    return "\n".join(lines)
