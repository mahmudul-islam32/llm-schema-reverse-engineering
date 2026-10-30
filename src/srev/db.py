"""Thin database connectors for PostgreSQL (primary target) and SQLite.

SQLite lets the whole pipeline run with no infrastructure (tests, CI smoke
runs, laptops). PostgreSQL is the research target: evidence extraction, the
verifier and the ground-truth export all work against it.
"""

from __future__ import annotations

import os
import re
import sqlite3
from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from srev.datasets.base import ColumnDef, Dataset, ForeignKeyDef, TableDef

_SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_$]*$")


class DatabaseError(RuntimeError):
    """Raised for query failures, independent of the driver."""


@dataclass
class ColumnInfo:
    name: str
    type: str
    nullable: bool
    comment: str | None = None


@dataclass
class DeclaredConstraints:
    primary_keys: dict[str, list[str]] = field(default_factory=dict)
    uniques: dict[str, list[list[str]]] = field(default_factory=dict)
    foreign_keys: dict[str, list[ForeignKeyDef]] = field(default_factory=dict)


class Connector(ABC):
    dialect: str
    placeholder: str

    def quote(self, name: str) -> str:
        if _SAFE_IDENT.match(name):
            return name
        return '"' + name.replace('"', '""') + '"'

    def qtable(self, table: str) -> str:
        return self.quote(table)

    @abstractmethod
    def query(self, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]: ...

    @abstractmethod
    def execute(self, sql: str, params: Sequence[Any] = ()) -> None: ...

    @abstractmethod
    def insert_rows(self, table: str, columns: list[str], rows: list[tuple[Any, ...]]) -> None: ...

    @abstractmethod
    def list_tables(self) -> list[str]: ...

    @abstractmethod
    def list_columns(self, table: str) -> list[ColumnInfo]: ...

    @abstractmethod
    def declared_constraints(self) -> DeclaredConstraints: ...

    @abstractmethod
    def reset(self) -> None:
        """Remove every table in the connector's namespace."""

    def table_comment(self, table: str) -> str | None:
        return None

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> Connector:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def describe(self) -> str:
        return self.dialect


class SQLiteConnector(Connector):
    dialect = "sqlite"
    placeholder = "?"

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA foreign_keys = OFF")

    def describe(self) -> str:
        return f"sqlite:{self.path}"

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
        try:
            return list(self.conn.execute(sql, tuple(params)).fetchall())
        except sqlite3.Error as exc:
            raise DatabaseError(f"{exc} -- {sql}") from exc

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        try:
            self.conn.execute(sql, tuple(params))
            self.conn.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(f"{exc} -- {sql}") from exc

    def insert_rows(self, table: str, columns: list[str], rows: list[tuple[Any, ...]]) -> None:
        cols = ", ".join(self.quote(c) for c in columns)
        marks = ", ".join("?" for _ in columns)
        self.conn.executemany(
            f"INSERT INTO {self.qtable(table)} ({cols}) VALUES ({marks})",
            [tuple(_sqlite_value(v) for v in r) for r in rows],
        )
        self.conn.commit()

    def list_tables(self) -> list[str]:
        rows = self.query(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        return [r[0] for r in rows]

    def list_columns(self, table: str) -> list[ColumnInfo]:
        rows = self.query(f"PRAGMA table_info({self.quote(table)})")
        return [ColumnInfo(name=r[1], type=r[2] or "TEXT", nullable=not r[3]) for r in rows]

    def declared_constraints(self) -> DeclaredConstraints:
        out = DeclaredConstraints()
        for t in self.list_tables():
            info = self.query(f"PRAGMA table_info({self.quote(t)})")
            pk = [r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5])]
            if pk:
                out.primary_keys[t] = pk
            for idx in self.query(f"PRAGMA index_list({self.quote(t)})"):
                # (seq, name, unique, origin, partial); origin 'u' = UNIQUE constraint
                if idx[2] and idx[3] == "u":
                    cols = [r[2] for r in self.query(f"PRAGMA index_info({self.quote(idx[1])})")]
                    out.uniques.setdefault(t, []).append(cols)
            fks: dict[int, ForeignKeyDef] = {}
            for r in self.query(f"PRAGMA foreign_key_list({self.quote(t)})"):
                fk = fks.setdefault(r[0], ForeignKeyDef([], r[2], []))
                fk.columns.append(r[3])
                fk.ref_columns.append(r[4])
            if fks:
                out.foreign_keys[t] = list(fks.values())
        return out

    def reset(self) -> None:
        for t in self.list_tables():
            self.execute(f"DROP TABLE {self.qtable(t)}")

    def close(self) -> None:
        self.conn.close()


class PostgresConnector(Connector):
    dialect = "postgres"
    placeholder = "%s"

    def __init__(self, dsn: str, schema: str = "public") -> None:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - depends on extras
            raise ImportError("PostgreSQL support needs: pip install 'srev[postgres]'") from exc
        self._psycopg = psycopg
        self.dsn = dsn
        self.schema = schema
        self.conn = psycopg.connect(dsn, autocommit=True)

    def describe(self) -> str:
        return f"postgres:{_redact(self.dsn)}#{self.schema}"

    def qtable(self, table: str) -> str:
        return f"{self.quote(self.schema)}.{self.quote(table)}"

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, tuple(params) or None)
                return list(cur.fetchall()) if cur.description else []
        except self._psycopg.Error as exc:
            raise DatabaseError(f"{exc} -- {sql}") from exc

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        self.query(sql, params)

    def insert_rows(self, table: str, columns: list[str], rows: list[tuple[Any, ...]]) -> None:
        cols = ", ".join(self.quote(c) for c in columns)
        with (
            self.conn.cursor() as cur,
            cur.copy(f"COPY {self.qtable(table)} ({cols}) FROM STDIN") as copy,
        ):
            for row in rows:
                copy.write_row(row)

    def list_tables(self) -> list[str]:
        rows = self.query(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY table_name",
            [self.schema],
        )
        return [r[0] for r in rows]

    def list_columns(self, table: str) -> list[ColumnInfo]:
        rows = self.query(
            """
            SELECT a.attname, format_type(a.atttypid, a.atttypmod), NOT a.attnotnull,
                   col_description(a.attrelid, a.attnum)
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
            """,
            [self.schema, table],
        )
        return [ColumnInfo(name=r[0], type=r[1], nullable=r[2], comment=r[3]) for r in rows]

    def table_comment(self, table: str) -> str | None:
        rows = self.query(
            "SELECT obj_description(c.oid, 'pg_class') FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relname = %s",
            [self.schema, table],
        )
        return rows[0][0] if rows else None

    def declared_constraints(self) -> DeclaredConstraints:
        rows = self.query(
            """
            SELECT con.contype, rel.relname, con.conname,
                   ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(n, i)
                         JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.n
                         ORDER BY k.i),
                   frel.relname,
                   ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(n, i)
                         JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.n
                         ORDER BY k.i)
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = rel.relnamespace
            LEFT JOIN pg_class frel ON frel.oid = con.confrelid
            WHERE n.nspname = %s AND con.contype IN ('p', 'u', 'f')
            ORDER BY rel.relname, con.conname
            """,
            [self.schema],
        )
        out = DeclaredConstraints()
        for contype, table, _name, cols, ftable, fcols in rows:
            if contype == "p":
                out.primary_keys[table] = list(cols)
            elif contype == "u":
                out.uniques.setdefault(table, []).append(list(cols))
            else:
                out.foreign_keys.setdefault(table, []).append(
                    ForeignKeyDef(list(cols), ftable, list(fcols))
                )
        return out

    def reset(self) -> None:
        self.execute(f"DROP SCHEMA IF EXISTS {self.quote(self.schema)} CASCADE")
        self.execute(f"CREATE SCHEMA {self.quote(self.schema)}")

    def close(self) -> None:
        self.conn.close()


def _redact(dsn: str) -> str:
    return re.sub(r"(://[^:/@]+:)[^@]+@", r"\1***@", dsn)


def _sqlite_value(v: Any) -> Any:
    import datetime as dt
    import decimal

    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, dt.date | dt.datetime):
        return v.isoformat()
    return v


def connect(
    *, dsn: str | None = None, sqlite: str | Path | None = None, schema: str = "public"
) -> Connector:
    """Open a connector from a DSN or a SQLite path (``SREV_DSN`` as fallback)."""
    if sqlite is not None:
        return SQLiteConnector(sqlite)
    dsn = dsn or os.environ.get("SREV_DSN")
    if dsn:
        if dsn.startswith("sqlite:///"):
            return SQLiteConnector(dsn.removeprefix("sqlite:///"))
        return PostgresConnector(dsn, schema=schema)
    raise ValueError("no database given: pass a PostgreSQL DSN or a SQLite path")


@contextmanager
def connected(**kwargs: Any) -> Iterator[Connector]:
    conn = connect(**kwargs)
    try:
        yield conn
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Materialisation and reading datasets
# --------------------------------------------------------------------------


def _column_sql(conn: Connector, col: ColumnDef, with_constraints: bool) -> str:
    sql = f"{conn.quote(col.name)} {col.type}"
    if with_constraints and not col.nullable:
        sql += " NOT NULL"
    return sql


def materialize(dataset: Dataset, conn: Connector, *, with_constraints: bool = False) -> None:
    """Create the dataset's tables in ``conn`` and load the rows.

    With ``with_constraints=False`` (the default, used for experiments) no
    PK/UNIQUE/FK/NOT NULL constraints and no comments-as-documentation leak into
    the working copy beyond what the dataset carries in its column comments.
    """
    conn.reset()
    for t in dataset.tables:
        parts = [_column_sql(conn, c, with_constraints) for c in t.columns]
        if with_constraints:
            if t.primary_key:
                parts.append(f"PRIMARY KEY ({', '.join(conn.quote(c) for c in t.primary_key)})")
            for u in t.uniques:
                parts.append(f"UNIQUE ({', '.join(conn.quote(c) for c in u)})")
            if conn.dialect == "sqlite":
                parts.extend(_fk_clause(conn, fk) for fk in t.foreign_keys)
        conn.execute(f"CREATE TABLE {conn.qtable(t.name)} (\n  " + ",\n  ".join(parts) + "\n)")
        if t.rows:
            conn.insert_rows(t.name, t.column_names, t.rows)
        if conn.dialect == "postgres":
            if t.comment:
                conn.execute(f"COMMENT ON TABLE {conn.qtable(t.name)} IS %s", [t.comment])
            for c in t.columns:
                if c.comment:
                    conn.execute(
                        f"COMMENT ON COLUMN {conn.qtable(t.name)}.{conn.quote(c.name)} IS %s",
                        [c.comment],
                    )
    if with_constraints and conn.dialect == "postgres":
        for t in dataset.tables:
            for fk in t.foreign_keys:
                conn.execute(f"ALTER TABLE {conn.qtable(t.name)} ADD {_fk_clause(conn, fk)}")


def _fk_clause(conn: Connector, fk: ForeignKeyDef) -> str:
    cols = ", ".join(conn.quote(c) for c in fk.columns)
    refs = ", ".join(conn.quote(c) for c in fk.ref_columns)
    return f"FOREIGN KEY ({cols}) REFERENCES {conn.qtable(fk.ref_table)} ({refs})"


def dataset_from_connector(
    conn: Connector, name: str, *, queries: list[str] | None = None, max_rows: int | None = None
) -> Dataset:
    """Read an existing database (schema, declared constraints, rows) into memory."""
    declared = conn.declared_constraints()
    tables = []
    for tname in conn.list_tables():
        cols = conn.list_columns(tname)
        col_sql = ", ".join(conn.quote(c.name) for c in cols)
        limit = f" LIMIT {int(max_rows)}" if max_rows else ""
        rows = conn.query(f"SELECT {col_sql} FROM {conn.qtable(tname)}{limit}")
        tables.append(
            TableDef(
                name=tname,
                columns=[ColumnDef(c.name, c.type, c.nullable, c.comment) for c in cols],
                rows=rows,
                primary_key=declared.primary_keys.get(tname, []),
                uniques=declared.uniques.get(tname, []),
                foreign_keys=declared.foreign_keys.get(tname, []),
                comment=conn.table_comment(tname),
            )
        )
    return Dataset(
        name=name,
        tables=tables,
        description=f"read from {conn.describe()}",
        queries=list(queries or []),
    )
