"""In-memory dataset representation.

A :class:`Dataset` holds a schema (with its *declared* constraints), the rows
and an optional query workload. Built-in generators produce datasets, and
:func:`dataset_from_connector` reads one from an existing database, so the
rest of the pipeline (gold extraction, noise, materialisation) works the same
way for both.
"""

from __future__ import annotations

import datetime as dt
import decimal
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ForeignKeyDef:
    columns: list[str]
    ref_table: str
    ref_columns: list[str]


@dataclass
class ColumnDef:
    name: str
    type: str
    nullable: bool = True
    comment: str | None = None


@dataclass
class TableDef:
    name: str
    columns: list[ColumnDef]
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    uniques: list[list[str]] = field(default_factory=list)
    foreign_keys: list[ForeignKeyDef] = field(default_factory=list)
    comment: str | None = None

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def column(self, name: str) -> ColumnDef:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(f"{self.name}.{name}")

    def index(self, name: str) -> int:
        return self.column_names.index(name)


@dataclass
class CuratedFD:
    """A hand-labelled *semantic* functional dependency (gold source ``curated``)."""

    table: str
    lhs: list[str]
    rhs: str
    note: str = ""


@dataclass
class CuratedFK:
    """A relationship that exists semantically but is not declared in the schema."""

    table: str
    columns: list[str]
    ref_table: str
    ref_columns: list[str]
    note: str = ""


@dataclass
class Dataset:
    name: str
    tables: list[TableDef]
    description: str = ""
    licence: str = ""
    curated_fds: list[CuratedFD] = field(default_factory=list)
    curated_fks: list[CuratedFK] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)

    def table(self, name: str) -> TableDef:
        for t in self.tables:
            if t.name == name:
                return t
        raise KeyError(name)

    def schema_map(self) -> dict[str, list[str]]:
        return {t.name: t.column_names for t in self.tables}

    def checksum(self) -> str:
        """SHA-256 over schema, constraints and rows: identifies the exact data used."""
        h = hashlib.sha256()
        for t in sorted(self.tables, key=lambda t: t.name):
            header = {
                "name": t.name,
                "columns": [(c.name, c.type, c.nullable) for c in t.columns],
                "pk": t.primary_key,
                "uniques": t.uniques,
                "fks": [(f.columns, f.ref_table, f.ref_columns) for f in t.foreign_keys],
            }
            h.update(json.dumps(header, sort_keys=True).encode())
            for row in t.rows:
                h.update(json.dumps([to_jsonable(v) for v in row]).encode())
                h.update(b"\n")
        for q in self.queries:
            h.update(q.encode())
        return h.hexdigest()

    @property
    def n_rows(self) -> int:
        return sum(len(t.rows) for t in self.tables)


def to_jsonable(value: Any) -> Any:
    """Convert database values to JSON-safe primitives."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value) if value != value.to_integral_value() else int(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    return str(value)


def type_family(sql_type: str) -> str:
    """Coarse type family used for IND compatibility and prompts."""
    t = sql_type.lower()
    if any(k in t for k in ("int", "serial")):
        return "integer"
    if any(k in t for k in ("numeric", "decimal", "real", "double", "float")):
        return "numeric"
    if "timestamp" in t or "date" in t or "time" in t:
        return "temporal"
    if "bool" in t:
        return "boolean"
    return "text"
