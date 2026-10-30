from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest

from srev.datasets import load_dataset
from srev.datasets.base import Dataset
from srev.db import Connector, PostgresConnector, SQLiteConnector, materialize
from srev.truth import GoldSet, gold_from_dataset


@pytest.fixture(scope="session")
def university() -> Dataset:
    return load_dataset("university", seed=7, scale=0.5)


@pytest.fixture(scope="session")
def tpch() -> Dataset:
    return load_dataset("tpch_mini", seed=7, scale=0.3)


@pytest.fixture(scope="session")
def retail() -> Dataset:
    return load_dataset("retail_legacy", seed=7, scale=0.5)


@pytest.fixture
def university_gold(university: Dataset) -> GoldSet:
    return gold_from_dataset(university)


@pytest.fixture
def university_db(university: Dataset) -> Iterator[Connector]:
    conn = SQLiteConnector(":memory:")
    materialize(university, conn)
    yield conn
    conn.close()


@pytest.fixture
def sqlite_conn() -> Iterator[SQLiteConnector]:
    conn = SQLiteConnector(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def pg_conn() -> Iterator[PostgresConnector]:
    dsn = os.environ.get("SREV_TEST_DSN")
    if not dsn:
        pytest.skip("set SREV_TEST_DSN to run PostgreSQL tests")
    schema = f"srev_test_{uuid.uuid4().hex[:8]}"
    conn = PostgresConnector(dsn, schema=schema)
    conn.reset()
    yield conn
    conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    conn.execute(f"DROP SCHEMA IF EXISTS {schema}_stripped CASCADE")
    conn.close()
