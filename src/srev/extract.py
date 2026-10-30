"""Schema, statistics and sample extractor (F-01).

Produces one versioned :class:`Evidence` document per database. Every method
sees the same evidence. Declared constraints (keys, UNIQUE, FK, NOT NULL) are
deliberately *not* read, so they cannot leak into prompts.
"""

from __future__ import annotations

import datetime as dt
import random
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from srev.datasets.base import to_jsonable, type_family
from srev.db import Connector

EVIDENCE_VERSION = "srev.evidence/1"


class ColumnEvidence(BaseModel):
    name: str
    type: str
    family: str
    comment: str | None = None
    distinct_count: int = 0
    null_count: int = 0
    null_ratio: float = 0.0
    min: Any = None
    max: Any = None
    avg_length: float | None = None
    top_k: list[tuple[Any, int]] = Field(default_factory=list)


class TableEvidence(BaseModel):
    name: str
    row_count: int
    comment: str | None = None
    columns: list[ColumnEvidence]
    sample_rows: list[list[Any]] = Field(default_factory=list)

    def column(self, name: str) -> ColumnEvidence:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(f"{self.name}.{name}")

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


class Evidence(BaseModel):
    version: str = EVIDENCE_VERSION
    database: str
    dialect: str
    seed: int
    rows_per_table: int
    extracted_at: str = Field(default_factory=lambda: dt.datetime.now(dt.UTC).isoformat())
    tables: list[TableEvidence]

    def table(self, name: str) -> TableEvidence:
        for t in self.tables:
            if t.name == name:
                return t
        raise KeyError(name)

    def schema_map(self) -> dict[str, list[str]]:
        return {t.name: t.column_names for t in self.tables}

    def has_ref(self, ref: str) -> bool:
        table, _, column = ref.rpartition(".")
        return any(t.name == table and column in t.column_names for t in self.tables)

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(self.model_dump_json(indent=2))

    @classmethod
    def load(cls, path: str | Path) -> Evidence:
        return cls.model_validate_json(Path(path).read_text())


def _sort_key(row: tuple[Any, ...]) -> tuple[str, ...]:
    return tuple("" if v is None else repr(to_jsonable(v)) for v in row)


def _column_stats(
    conn: Connector, table: str, column: str, family: str, top_k: int
) -> dict[str, Any]:
    qt, qc = conn.qtable(table), conn.quote(column)
    # PostgreSQL has no MIN/MAX for booleans
    minmax = "NULL, NULL" if family == "boolean" else f"MIN({qc}), MAX({qc})"
    total, non_null, distinct, mn, mx = conn.query(
        f"SELECT COUNT(*), COUNT({qc}), COUNT(DISTINCT {qc}), {minmax} FROM {qt}"
    )[0]
    avg_len = conn.query(
        f"SELECT AVG(LENGTH(CAST({qc} AS TEXT))) FROM {qt} WHERE {qc} IS NOT NULL"
    )[0][0]
    top = conn.query(
        f"SELECT {qc}, COUNT(*) AS n FROM {qt} WHERE {qc} IS NOT NULL "
        f"GROUP BY {qc} ORDER BY n DESC, {qc} LIMIT {int(top_k)}"
    )
    return {
        "rows": int(total),
        "distinct_count": int(distinct),
        "null_count": int(total - non_null),
        "null_ratio": round((total - non_null) / total, 4) if total else 0.0,
        "min": to_jsonable(mn),
        "max": to_jsonable(mx),
        "avg_length": round(float(avg_len), 2) if avg_len is not None else None,
        "top_k": [(to_jsonable(v), int(n)) for v, n in top],
    }


def sample_rows(
    conn: Connector, table: str, columns: list[str], n: int, seed: int, max_scan: int = 200_000
) -> list[list[Any]]:
    """Deterministic sample of ``n`` rows for a given seed.

    Rows are fetched, put into a canonical order and sampled with a seeded RNG,
    so the sample does not depend on the physical row order. Large PostgreSQL
    tables are pre-sampled with ``TABLESAMPLE BERNOULLI ... REPEATABLE(seed)``.
    """
    if n <= 0:
        return []
    cols = ", ".join(conn.quote(c) for c in columns)
    qt = conn.qtable(table)
    total = conn.query(f"SELECT COUNT(*) FROM {qt}")[0][0]
    if conn.dialect == "postgres" and total > max_scan:
        pct = min(100.0, 100.0 * max_scan / total)
        rows = conn.query(
            f"SELECT {cols} FROM {qt} TABLESAMPLE BERNOULLI ({pct:.6f}) REPEATABLE ({int(seed)})"
        )
    else:
        rows = conn.query(f"SELECT {cols} FROM {qt}")
    rows.sort(key=_sort_key)
    rng = random.Random(f"{seed}:{table}")
    picked = rng.sample(rows, min(n, len(rows)))
    return [[to_jsonable(v) for v in r] for r in picked]


def extract(
    conn: Connector,
    database: str,
    *,
    rows_per_table: int = 50,
    top_k: int = 5,
    seed: int = 42,
    include_comments: bool = True,
) -> Evidence:
    tables = []
    for tname in conn.list_tables():
        cols = conn.list_columns(tname)
        col_ev = []
        row_count = 0
        for c in cols:
            family = type_family(c.type)
            stats = _column_stats(conn, tname, c.name, family, top_k)
            row_count = stats.pop("rows")
            col_ev.append(
                ColumnEvidence(
                    name=c.name,
                    type=c.type,
                    family=family,
                    comment=c.comment if include_comments else None,
                    **stats,
                )
            )
        tables.append(
            TableEvidence(
                name=tname,
                row_count=row_count,
                comment=conn.table_comment(tname) if include_comments else None,
                columns=col_ev,
                sample_rows=sample_rows(conn, tname, [c.name for c in cols], rows_per_table, seed),
            )
        )
    return Evidence(
        database=database,
        dialect=conn.dialect,
        seed=seed,
        rows_per_table=rows_per_table,
        tables=tables,
    )


def is_unique_column(table: TableEvidence, column: ColumnEvidence) -> bool:
    return (
        table.row_count > 0 and column.null_count == 0 and column.distinct_count == table.row_count
    )
