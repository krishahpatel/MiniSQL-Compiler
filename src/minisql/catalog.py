"""
catalog.py — Symbol Table / Schema Catalog
==========================================
The Catalog is the compiler's symbol table for MiniSQL.
It stores:
  - Table definitions (name, columns, types, primary keys)
  - Column -> table mappings for qualified/unqualified lookup
  - Alias -> real-table mappings (per query, set by semantic analysis)

The Catalog is populated by CREATE TABLE statements and persisted to a
JSON file so that multi-file scripts share a schema.

Data structures
---------------
  Catalog.tables : dict[str, TableSchema]
  TableSchema    : name, columns: list[ColumnSchema], primary_key: str|None
  ColumnSchema   : name, type_name ('INT'|'FLOAT'|'TEXT'|'BOOL'), position

This is analogous to a compiler's global symbol table where every
identifier (table/column) is resolved to its declaration.
"""

from __future__ import annotations
import json
import os
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Type constants
# ---------------------------------------------------------------------------

VALID_TYPES: frozenset = frozenset({"INT", "FLOAT", "TEXT", "BOOL", "NULL"})

# Type compatibility for arithmetic / comparison
# Implicit coercion: INT -> FLOAT when one operand is FLOAT
TYPE_HIERARCHY: Dict[str, int] = {"NULL": 0, "BOOL": 1, "INT": 2, "FLOAT": 3, "TEXT": 4}


def wider_numeric_type(a: str, b: str) -> Optional[str]:
    """
    Return the wider numeric type if a and b are both numeric (INT/FLOAT),
    otherwise return None (indicating a type error).

    Examples:
      INT  + INT   -> INT
      INT  + FLOAT -> FLOAT
      TEXT + INT   -> None  (error)
    """
    numeric = {"INT", "FLOAT"}
    if a in numeric and b in numeric:
        return "FLOAT" if "FLOAT" in (a, b) else "INT"
    return None


# ---------------------------------------------------------------------------
# Schema dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ColumnSchema:
    """Metadata for a single column in a table."""
    name:       str
    type_name:  str   # 'INT' | 'FLOAT' | 'TEXT' | 'BOOL'
    position:   int   # 0-indexed ordinal position
    primary_key: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ColumnSchema":
        return ColumnSchema(**d)


@dataclass
class TableSchema:
    """Metadata for a single table."""
    name:    str
    columns: List[ColumnSchema] = field(default_factory=list)

    @property
    def primary_key(self) -> Optional[str]:
        for c in self.columns:
            if c.primary_key:
                return c.name
        return None

    @property
    def column_names(self) -> List[str]:
        return [c.name for c in self.columns]

    def get_column(self, name: str) -> Optional[ColumnSchema]:
        """Case-insensitive column lookup."""
        lower = name.lower()
        for c in self.columns:
            if c.name.lower() == lower:
                return c
        return None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "columns": [c.to_dict() for c in self.columns],
        }

    @staticmethod
    def from_dict(d: dict) -> "TableSchema":
        cols = [ColumnSchema.from_dict(c) for c in d.get("columns", [])]
        return TableSchema(name=d["name"], columns=cols)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

class Catalog:
    """
    Global symbol table: maps table names to their TableSchema.

    The catalog is the single source of truth used by:
      - Semantic analyser (Phase 3) for type checking
      - IR generator (Phase 4) for column resolution
      - VM / Storage (Phase 7) for data operations

    It can be persisted to / loaded from a JSON file.
    """

    def __init__(self):
        # Main table registry: lowercase name -> TableSchema
        self._tables: Dict[str, TableSchema] = {}
        # Per-query alias map: alias -> real table name (cleared per query)
        self._aliases: Dict[str, str] = {}
        # Known row counts, updated by storage operations.
        self._row_counts: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # Table registration
    # ------------------------------------------------------------------

    def add_table(self, schema: TableSchema) -> None:
        """Register a new table.  Raises ValueError if it already exists."""
        key = schema.name.lower()
        if key in self._tables:
            raise ValueError(f"Table {schema.name!r} already exists")
        self._tables[key] = schema
        self._row_counts[key] = 0

    def drop_table(self, name: str) -> None:
        """Remove a table.  Raises KeyError if it doesn't exist."""
        key = name.lower()
        if key not in self._tables:
            raise KeyError(f"Table {name!r} does not exist")
        del self._tables[key]
        self._row_counts.pop(key, None)

    def has_table(self, name: str) -> bool:
        return name.lower() in self._tables

    def get_table(self, name: str) -> Optional[TableSchema]:
        """Lookup by real name or alias.  Returns None if not found."""
        key = name.lower()
        if key in self._aliases:
            key = self._aliases[key]
        return self._tables.get(key)

    def table_names(self) -> List[str]:
        return [s.name for s in self._tables.values()]

    # ------------------------------------------------------------------
    # Alias management
    # ------------------------------------------------------------------

    def set_alias(self, alias: str, real_name: str) -> None:
        self._aliases[alias.lower()] = real_name.lower()

    def clear_aliases(self) -> None:
        self._aliases.clear()

    def resolve_alias(self, alias: str) -> Optional[str]:
        """Return the real table name for an alias, or None."""
        key = alias.lower()
        real = self._aliases.get(key)
        if real:
            tbl = self._tables.get(real)
            return tbl.name if tbl else None
        # Maybe it's a real table name
        tbl = self._tables.get(key)
        return tbl.name if tbl else None

    # ------------------------------------------------------------------
    # Column lookup
    # ------------------------------------------------------------------

    def resolve_column(
        self,
        column:  str,
        table:   Optional[str] = None,
        visible: Optional[List[str]] = None,
    ) -> Tuple[Optional[str], Optional[ColumnSchema]]:
        """
        Resolve a (possibly unqualified) column reference.

        Parameters
        ----------
        column  : column name to look up
        table   : optional table/alias qualifier
        visible : list of table names visible in current query scope
                  (None means all tables)

        Returns
        -------
        (resolved_table_name, ColumnSchema) or (None, None) if not found.

        If multiple tables have a column with the same name and no qualifier
        is given, returns (None, None) signalling an ambiguous reference.
        """
        col_lower = column.lower()

        # Qualified reference: look up exact table
        if table:
            real = self.resolve_alias(table)
            if not real:
                return None, None
            schema = self._tables.get(real.lower())
            if not schema:
                return None, None
            col = schema.get_column(col_lower)
            return (schema.name, col) if col else (None, None)

        # Unqualified: search visible tables
        search = visible if visible is not None else list(self._tables.keys())
        matches: List[Tuple[str, ColumnSchema]] = []
        for tname in search:
            real = self.resolve_alias(tname) or tname
            schema = self._tables.get(real.lower())
            if schema:
                col = schema.get_column(col_lower)
                if col:
                    matches.append((schema.name, col))

        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None, None   # ambiguous
        return None, None

    # ------------------------------------------------------------------
    # Row count (for optimizer)
    # ------------------------------------------------------------------

    def set_row_count(self, table: str, count: int) -> None:
        self._row_counts[table.lower()] = count

    def get_row_count(self, table: str) -> int:
        return self._row_counts.get(table.lower(), 0)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        """Serialise the catalog to a JSON file."""
        data = {
            "tables": [s.to_dict() for s in self._tables.values()],
            "row_counts": self._row_counts,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load(self, path: str) -> None:
        """Load catalog from a JSON file (merges into current catalog)."""
        if not os.path.exists(path):
            return
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for t in data.get("tables", []):
            schema = TableSchema.from_dict(t)
            key    = schema.name.lower()
            self._tables[key] = schema   # overwrite if exists
        self._row_counts.update(data.get("row_counts", {}))

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def describe(self) -> str:
        """Return a human-readable summary of all tables."""
        if not self._tables:
            return "(catalog is empty)"
        lines = []
        for schema in self._tables.values():
            lines.append(f"TABLE {schema.name}")
            lines.append(f"  {'Column':<20} {'Type':<10} {'PK'}")
            lines.append(f"  {'-'*40}")
            for col in schema.columns:
                pk = " *" if col.primary_key else ""
                lines.append(f"  {col.name:<20} {col.type_name:<10}{pk}")
            lines.append("")
        return "\n".join(lines)

    def __repr__(self) -> str:
        names = ", ".join(self.table_names())
        return f"Catalog([{names}])"
