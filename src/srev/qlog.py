"""Query-log miner (F-04).

Parses a SQL workload and extracts equi-join predicates (``a.x = b.y`` in
``WHERE`` or ``JOIN ... ON``) as relationship evidence. Join pairs are counted
(weighted by ``calls`` when the log provides it) and parser failures are
counted and reported, never hidden.

Supported inputs: ``.sql`` files (statements separated by ``;``), CSV exports
of ``pg_stat_statements`` (columns ``query`` and optionally ``calls``) and JSONL
(``{"query": ..., "calls": ...}``).
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp

from srev import sqlutil
from srev.model import qualify


class WorkloadEntry(BaseModel):
    query: str
    calls: int = 1


class JoinPair(BaseModel):
    left: str
    right: str
    count: int

    def as_tuple(self) -> tuple[str, str]:
        return (self.left, self.right)


class JoinStats(BaseModel):
    pairs: list[JoinPair] = Field(default_factory=list)
    n_queries: int = 0
    n_parsed: int = 0
    n_failed: int = 0
    n_unresolved_columns: int = 0
    failures: list[str] = Field(default_factory=list, description="first few failing statements")

    @property
    def failure_rate(self) -> float:
        return self.n_failed / self.n_queries if self.n_queries else 0.0

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(self.model_dump_json(indent=2))


def split_sql(text: str) -> list[str]:
    """Split a script on ``;`` outside of quotes and comments."""
    out, buf = [], []
    quote: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif text.startswith("--", i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    stmt = "".join(buf).strip()
    if stmt:
        out.append(stmt)
    return out


def load_workload(path: str | Path) -> list[WorkloadEntry]:
    path = Path(path)
    if path.suffix == ".csv":
        with path.open(newline="") as fh:
            return [
                WorkloadEntry(query=row["query"], calls=int(row.get("calls") or 1))
                for row in csv.DictReader(fh)
                if row.get("query")
            ]
    if path.suffix in (".jsonl", ".ndjson"):
        return [
            WorkloadEntry.model_validate(json.loads(line))
            for line in path.read_text().splitlines()
            if line.strip()
        ]
    return [WorkloadEntry(query=q) for q in split_sql(path.read_text())]


def _equi_joins(
    expression: exp.Expression, schema: sqlutil.Schema | None
) -> tuple[list[tuple[str, str]], int]:
    aliases = sqlutil.table_aliases(expression)
    pairs: list[tuple[str, str]] = []
    unresolved = 0
    for eq in expression.find_all(exp.EQ):
        left, right = eq.this, eq.expression
        if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
            continue
        lt = sqlutil.resolve_column(left, aliases, schema)
        rt = sqlutil.resolve_column(right, aliases, schema)
        if lt is None or rt is None:
            unresolved += 1
            continue
        a, b = qualify(lt, left.name), qualify(rt, right.name)
        if a != b:
            pairs.append((min(a, b), max(a, b)))
    return pairs, unresolved


def mine_joins(
    workload: Iterable[WorkloadEntry | str],
    schema: sqlutil.Schema | None = None,
    dialect: str = "postgres",
) -> JoinStats:
    """Count equi-join column pairs (unordered) across a workload."""
    counter: Counter[tuple[str, str]] = Counter()
    stats = JoinStats()
    lower_schema = (
        {t.lower(): {c.lower() for c in cols} for t, cols in schema.items()} if schema else None
    )
    for entry in workload:
        if isinstance(entry, str):
            entry = WorkloadEntry(query=entry)
        stats.n_queries += 1
        try:
            expression = sqlutil.parse(entry.query, dialect)
        except sqlglot.errors.SqlglotError:
            stats.n_failed += 1
            if len(stats.failures) < 10:
                stats.failures.append(entry.query[:200])
            continue
        stats.n_parsed += 1
        pairs, unresolved = _equi_joins(expression, lower_schema)
        stats.n_unresolved_columns += unresolved
        for pair in pairs:
            counter[pair] += entry.calls
    stats.pairs = [
        JoinPair(left=a, right=b, count=n)
        for (a, b), n in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return stats
