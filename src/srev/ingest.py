"""Ingest CSV files and pandas DataFrames as datasets (F-18).

Data often arrives as a folder of CSV exports or as DataFrames in a notebook,
not as a database. This module turns either into a :class:`Dataset` (with
inferred SQL types and no constraints), which can then be materialised into
SQLite/PostgreSQL and profiled like any other database.

Type inference is conservative: integers with leading zeros (postal codes,
account numbers) stay text, ISO dates become ``DATE``, empty strings become
NULL.
"""

from __future__ import annotations

import csv
import datetime as dt
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from srev.datasets.base import ColumnDef, Dataset, TableDef

_INT = re.compile(r"^[+-]?(0|[1-9]\d*)$")
_FLOAT = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")
_ZERO_PADDED = re.compile(r"^[+-]?0\d")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BOOL = {"true", "false", "t", "f", "yes", "no"}
NULL_TOKENS = {"", "null", "NULL", "None", "NA", "N/A", "nan", "NaN"}


def sanitize(name: str, taken: set[str] | None = None) -> str:
    """SQL-friendly lower-case identifier; deduplicated against ``taken``."""
    out = re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_") or "col"
    if out[0].isdigit():
        out = f"c_{out}"
    if taken is not None:
        base, i = out, 2
        while out in taken:
            out, i = f"{base}_{i}", i + 1
        taken.add(out)
    return out


def _is_date(v: str) -> bool:
    if not _DATE.match(v):
        return False
    try:
        dt.date.fromisoformat(v)
        return True
    except ValueError:
        return False


def infer_type(values: Iterable[str | None]) -> str:
    present = [v for v in values if v is not None]
    if not present:
        return "TEXT"
    if any(_ZERO_PADDED.match(v) for v in present):
        return "TEXT"  # codes such as postal codes or account numbers
    if all(_INT.match(v) for v in present):
        return "BIGINT" if any(abs(int(v)) > 2**31 - 1 for v in present) else "INTEGER"
    if all(_FLOAT.match(v) for v in present):
        return "DOUBLE PRECISION"
    if all(_is_date(v) for v in present):
        return "DATE"
    if all(v.lower() in _BOOL for v in present):
        return "BOOLEAN"
    return "TEXT"


def convert(value: str | None, sql_type: str) -> Any:
    if value is None:
        return None
    if sql_type in ("INTEGER", "BIGINT"):
        return int(value)
    if sql_type == "DOUBLE PRECISION":
        return float(value)
    if sql_type == "DATE":
        return dt.date.fromisoformat(value)
    if sql_type == "BOOLEAN":
        return value.lower() in ("true", "t", "yes")
    return value


def table_from_rows(
    name: str, header: list[str], rows: list[list[str | None]], *, sanitize_names: bool = True
) -> TableDef:
    taken: set[str] = set()
    names = [sanitize(h, taken) if sanitize_names else h for h in header]
    types = [infer_type(r[i] if i < len(r) else None for r in rows) for i in range(len(header))]
    data = [
        tuple(convert(r[i] if i < len(r) else None, types[i]) for i in range(len(header)))
        for r in rows
    ]
    return TableDef(
        name=sanitize(name) if sanitize_names else name,
        columns=[ColumnDef(n, t) for n, t in zip(names, types, strict=True)],
        rows=data,
    )


def read_csv(
    path: str | Path,
    *,
    delimiter: str | None = None,
    encoding: str = "utf-8",
    max_rows: int | None = None,
    table: str | None = None,
) -> TableDef:
    path = Path(path)
    with path.open(newline="", encoding=encoding) as fh:
        sample = fh.read(65536)
        fh.seek(0)
        if delimiter is None:
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
            except csv.Error:
                delimiter = ","
        reader = csv.reader(fh, delimiter=delimiter)
        header = next(reader, None)
        if not header:
            raise ValueError(f"{path}: empty file or missing header")
        rows: list[list[str | None]] = []
        for i, raw in enumerate(reader):
            if max_rows is not None and i >= max_rows:
                break
            if not raw:
                continue
            rows.append([None if v.strip() in NULL_TOKENS else v.strip() for v in raw])
    return table_from_rows(table or path.stem, header, rows)


def dataset_from_csv(
    paths: Iterable[str | Path],
    name: str = "csv",
    *,
    delimiter: str | None = None,
    encoding: str = "utf-8",
    max_rows: int | None = None,
) -> Dataset:
    """One table per CSV file; a directory argument expands to its ``*.csv`` files."""
    files: list[Path] = []
    for p in map(Path, paths):
        if not p.exists():
            raise FileNotFoundError(f"no such file or folder: {p}")
        files += sorted(p.glob("*.csv")) if p.is_dir() else [p]
    if not files:
        raise ValueError("no CSV files found")
    tables = [read_csv(f, delimiter=delimiter, encoding=encoding, max_rows=max_rows) for f in files]
    taken: set[str] = set()
    for t in tables:  # two files may sanitise to the same table name
        t.name = sanitize(t.name, taken)
    return Dataset(name=name, tables=tables, description=f"{len(files)} CSV file(s)")


def dataset_from_frames(frames: Mapping[str, Any], name: str = "dataframes") -> Dataset:
    """Build a dataset from ``{table_name: pandas.DataFrame}`` (pandas required)."""
    tables = []
    taken: set[str] = set()
    for table_name, df in frames.items():
        header = [str(c) for c in df.columns]
        rows: list[list[str | None]] = []
        for record in df.itertuples(index=False, name=None):
            rows.append([_frame_cell(v) for v in record])
        t = table_from_rows(table_name, header, rows)
        t.name = sanitize(t.name, taken)
        tables.append(t)
    return Dataset(name=name, tables=tables, description=f"{len(tables)} DataFrame(s)")


def _frame_cell(v: Any) -> str | None:
    if v is None:
        return None
    try:
        if v != v:  # NaN / NaT
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, dt.datetime):
        return v.date().isoformat() if v.time() == dt.time() else v.isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, bool):
        return "true" if v else "false"
    if hasattr(v, "item"):  # numpy scalar
        v = v.item()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    text = str(v)
    return None if text in NULL_TOKENS else text
