"""Loader for Spider/BIRD-style text-to-SQL benchmarks (F-16).

Both benchmarks ship many small SQLite databases plus a ``tables.json`` with
schema annotations (``primary_keys``, ``foreign_keys``) and gold SQL queries,
which gives the breadth the research protocol asks for: hundreds of databases
from different domains, annotated keys, and a realistic query workload.

Expected layout (Spider defaults; BIRD works by passing its file names)::

    <root>/tables.json                    # or e.g. dev_tables.json for BIRD
    <root>/database/<db_id>/<db_id>.sqlite  # or e.g. dev_databases/ for BIRD
    <root>/dev.json                       # optional gold SQL: {"db_id", "query"|"SQL"}

The annotations replace whatever the SQLite files declare. Spider lists one
column pair per FK entry, so every annotated pair becomes a unary FK; primary
keys given as nested lists (newer releases) become composite keys. The
benchmark data is not redistributed here: download it from the official sites
and check the licences.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from srev.datasets.base import Dataset, ForeignKeyDef
from srev.db import SQLiteConnector, dataset_from_connector


def read_tables_json(path: str | Path) -> dict[str, dict[str, Any]]:
    entries = json.loads(Path(path).read_text())
    return {e["db_id"]: e for e in entries}


def _tables_path(root: Path, tables_json: str | None) -> Path:
    return root / (tables_json or "tables.json")


def list_databases(root: str | Path, tables_json: str | None = None) -> list[dict[str, Any]]:
    """Summaries (db_id, tables, annotated PKs and FKs) of every database in tables.json."""
    out = []
    for db_id, e in sorted(read_tables_json(_tables_path(Path(root), tables_json)).items()):
        out.append(
            {
                "db_id": db_id,
                "tables": len(e["table_names_original"]),
                "primary_keys": len(e.get("primary_keys", [])),
                "foreign_keys": len(e.get("foreign_keys", [])),
            }
        )
    return out


def _queries(root: Path, queries_json: str | None, db_id: str) -> list[str]:
    if not queries_json:
        return []
    items = json.loads((root / queries_json).read_text())
    out = []
    for item in items:
        if item.get("db_id") == db_id:
            sql = item.get("query") or item.get("SQL")
            if sql and sql not in out:
                out.append(sql)
    return out


def load_spider(
    root: str | Path,
    db_id: str,
    *,
    tables_json: str | None = None,
    db_dir: str = "database",
    queries_json: str | None = None,
    max_rows: int | None = None,
) -> Dataset:
    root = Path(root)
    entry = read_tables_json(_tables_path(root, tables_json))[db_id]
    path = root / db_dir / db_id / f"{db_id}.sqlite"
    if not path.exists():
        raise FileNotFoundError(f"database file not found: {path}")
    with SQLiteConnector(path) as conn:
        ds = dataset_from_connector(conn, db_id, max_rows=max_rows)

    # map annotation names onto the actual (case-preserving) names in the file
    tables_by_lower = {t.name.lower(): t for t in ds.tables}
    ann_tables = entry["table_names_original"]
    columns = entry["column_names_original"]  # [[table_index, column_name], ...]

    def resolve(col_index: int) -> tuple[str, str] | None:
        t_idx, col_name = columns[col_index]
        if t_idx < 0:
            return None
        table = tables_by_lower.get(ann_tables[t_idx].lower())
        if table is None:
            return None
        for c in table.column_names:
            if c.lower() == str(col_name).lower():
                return table.name, c
        return None

    for t in ds.tables:
        t.primary_key, t.uniques, t.foreign_keys = [], [], []
    pks: dict[str, list[str]] = defaultdict(list)
    for pk in entry.get("primary_keys", []):
        for idx in pk if isinstance(pk, list) else [pk]:
            resolved = resolve(idx)
            if resolved:
                pks[resolved[0]].append(resolved[1])
    for table_name, cols in pks.items():
        tables_by_lower[table_name.lower()].primary_key = cols
    for src_idx, ref_idx in entry.get("foreign_keys", []):
        src, ref = resolve(src_idx), resolve(ref_idx)
        if src and ref and src != ref:
            tables_by_lower[src[0].lower()].foreign_keys.append(
                ForeignKeyDef([src[1]], ref[0], [ref[1]])
            )
    ds.queries = _queries(root, queries_json, db_id)
    ds.description = (
        f"{db_id} from {root.name} (annotations from {_tables_path(root, tables_json).name})"
    )
    ds.licence = "see the benchmark's licence"
    ds.params = {"root": str(root), "db_id": db_id}
    return ds
