"""Python API for notebooks and pipelines.

>>> from srev import api
>>> result = api.discover_csv(["exports/"])          # folder of CSV files
>>> result.counts()
{'FK': 4, 'UCC': 8, 'FD': 0}
>>> result.to_frame()                                # pandas DataFrame of constraints

``discover_frames({"orders": df_orders, "customers": df_customers})`` does the
same for DataFrames, ``discover_database(dsn=...)`` for a live database. CSV
files and DataFrames are loaded into an in-memory SQLite database first.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from srev.datasets.base import Dataset
from srev.db import SQLiteConnector, connect, materialize
from srev.discover import DiscoverConfig, Discovery, discover
from srev.ingest import dataset_from_csv, dataset_from_frames
from srev.qlog import load_workload, mine_joins


def _discover_dataset(
    ds: Dataset, workload: str | Path | None, config: DiscoverConfig | None
) -> Discovery:
    with SQLiteConnector(":memory:") as conn:
        materialize(ds, conn)
        joins = None
        if workload:
            joins = mine_joins(load_workload(workload), ds.schema_map())
        return discover(conn, ds.name, joins=joins, config=config)


def discover_csv(
    paths: Iterable[str | Path],
    *,
    name: str = "csv",
    workload: str | Path | None = None,
    config: DiscoverConfig | None = None,
    **csv_options: Any,
) -> Discovery:
    return _discover_dataset(dataset_from_csv(paths, name, **csv_options), workload, config)


def discover_frames(
    frames: Mapping[str, Any],
    *,
    name: str = "dataframes",
    workload: str | Path | None = None,
    config: DiscoverConfig | None = None,
) -> Discovery:
    return _discover_dataset(dataset_from_frames(frames, name), workload, config)


def discover_database(
    *,
    dsn: str | None = None,
    sqlite: str | Path | None = None,
    schema: str = "public",
    workload: str | Path | None = None,
    config: DiscoverConfig | None = None,
) -> Discovery:
    with connect(dsn=dsn, sqlite=sqlite, schema=schema) as conn:
        joins = None
        if workload:
            schema_map = {t: [c.name for c in conn.list_columns(t)] for t in conn.list_tables()}
            joins = mine_joins(load_workload(workload), schema_map)
        return discover(conn, schema if dsn else Path(str(sqlite)).stem, joins=joins, config=config)


__all__ = ["DiscoverConfig", "Discovery", "discover_csv", "discover_database", "discover_frames"]
