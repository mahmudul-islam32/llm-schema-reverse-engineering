"""Exact data-profiling baselines implemented in Python.

These are faithful but simple level-wise algorithms (in the spirit of TANE
and apriori-style UCC/IND discovery), not re-implementations of HyFD, HyUCC
or BINDER. They return the *complete* set of minimal dependencies up to the
configured arity, which is exactly the behaviour the research protocol wants
to contrast with LLM proposals. For the Metanome algorithms themselves see
:mod:`srev.baselines.metanome`.

Semantics: NULL is treated as an ordinary value (NULL = NULL), as in most
profiling tools. Inclusion dependencies ignore NULLs on the dependent side.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from srev import names
from srev.datasets.base import type_family
from srev.db import Connector
from srev.model import Proposal, qualify


class ProfilingConfig(BaseModel):
    ucc_max_arity: int = 3
    fd_max_lhs: int = 2
    ind_min_coverage: float = 1.0
    nary_fks: bool = True
    max_rows: int | None = None


@dataclass
class TableData:
    name: str
    columns: list[str]
    families: dict[str, str]
    values: dict[str, list[Any]]
    n: int


@dataclass
class ProfileResult:
    proposals: list[Proposal]
    seconds: float
    stats: dict[str, int] = field(default_factory=dict)


def load_tables(conn: Connector, max_rows: int | None = None) -> list[TableData]:
    out = []
    for table in conn.list_tables():
        cols = conn.list_columns(table)
        col_sql = ", ".join(conn.quote(c.name) for c in cols)
        limit = f" LIMIT {int(max_rows)}" if max_rows else ""
        rows = conn.query(f"SELECT {col_sql} FROM {conn.qtable(table)}{limit}")
        values = {c.name: [r[i] for r in rows] for i, c in enumerate(cols)}
        out.append(
            TableData(
                name=table,
                columns=[c.name for c in cols],
                families={c.name: type_family(c.type) for c in cols},
                values=values,
                n=len(rows),
            )
        )
    return out


# --------------------------------------------------------------------------
# partitions
# --------------------------------------------------------------------------


def _ids(values: list[Any]) -> tuple[list[int], int]:
    index: dict[Any, int] = {}
    ids = [index.setdefault(v, len(index)) for v in values]
    return ids, len(index)


def _combine(a: list[int], b: list[int]) -> tuple[list[int], int]:
    index: dict[tuple[int, int], int] = {}
    ids = [index.setdefault(pair, len(index)) for pair in zip(a, b, strict=True)]
    return ids, len(index)


class _Partitions:
    """Caches group ids for column combinations (stripped-partition-lite)."""

    def __init__(self, table: TableData) -> None:
        self.table = table
        self.cache: dict[tuple[str, ...], tuple[list[int], int]] = {}
        for c in table.columns:
            self.cache[(c,)] = _ids(table.values[c])

    def get(self, cols: tuple[str, ...]) -> tuple[list[int], int]:
        cols = tuple(sorted(cols))
        if cols not in self.cache:
            head = self.get(cols[:-1])
            self.cache[cols] = _combine(head[0], self.cache[(cols[-1],)][0])
        return self.cache[cols]

    def groups(self, cols: tuple[str, ...]) -> int:
        return self.get(cols)[1]


# --------------------------------------------------------------------------
# UCCs
# --------------------------------------------------------------------------


def discover_uccs(table: TableData, max_arity: int, parts: _Partitions) -> list[tuple[str, ...]]:
    """All minimal unique column combinations up to ``max_arity``."""
    if table.n == 0:
        return []
    found: list[tuple[str, ...]] = []
    cols = sorted(table.columns)
    for k in range(1, max_arity + 1):
        for combo in itertools.combinations(cols, k):
            if any(set(u) <= set(combo) for u in found):
                continue
            if parts.groups(combo) == table.n:
                found.append(combo)
    return found


def _ucc_score(combo: tuple[str, ...], table: TableData) -> float:
    key_bonus = sum(names.keyish(c) for c in combo) / len(combo)
    null_free = all(v is not None for c in combo for v in table.values[c])
    return round(0.5 / len(combo) + 0.3 * key_bonus + 0.2 * null_free, 3)


# --------------------------------------------------------------------------
# FDs
# --------------------------------------------------------------------------


def discover_fds(
    table: TableData, max_lhs: int, parts: _Partitions, uccs: list[tuple[str, ...]]
) -> list[tuple[tuple[str, ...], str, float]]:
    """Minimal non-trivial FDs ``X -> A`` with ``1 <= |X| <= max_lhs``.

    Returns ``(lhs, rhs, redundancy)`` where redundancy = 1 - groups(X)/n is
    0 for key-based FDs and grows with how much repetition the FD explains.
    Constant columns are skipped as right-hand sides (``{} -> A``).
    """
    if table.n == 0:
        return []
    cols = sorted(table.columns)
    constant = {c for c in cols if parts.groups((c,)) <= 1}
    minimal: dict[str, list[frozenset[str]]] = {c: [] for c in cols}
    out = []
    for k in range(1, max_lhs + 1):
        for lhs in itertools.combinations([c for c in cols if c not in constant], k):
            lhs_set = frozenset(lhs)
            # supersets of a key determine everything, but cannot be minimal
            if any(set(u) < lhs_set for u in uccs):
                continue
            g_lhs = parts.groups(lhs)
            for rhs in cols:
                if rhs in lhs_set or rhs in constant:
                    continue
                if any(m <= lhs_set for m in minimal[rhs]):
                    continue
                if parts.groups((*lhs, rhs)) == g_lhs:
                    minimal[rhs].append(lhs_set)
                    out.append((lhs, rhs, round(1 - g_lhs / table.n, 4)))
    return out


# --------------------------------------------------------------------------
# INDs and FK candidates
# --------------------------------------------------------------------------


def _compatible(a: str, b: str) -> bool:
    numeric = {"integer", "numeric"}
    return a == b or (a in numeric and b in numeric)


def discover_unary_inds(
    tables: list[TableData], min_coverage: float = 1.0
) -> list[tuple[str, str, float]]:
    """Unary INDs ``dep ⊆ ref`` as ``(dep_ref, ref_ref, coverage)``."""
    sets: dict[str, tuple[set[Any], str]] = {}
    for t in tables:
        for c in t.columns:
            sets[qualify(t.name, c)] = ({v for v in t.values[c] if v is not None}, t.families[c])
    out = []
    for dep, (dvals, dfam) in sets.items():
        if not dvals or dfam == "boolean":
            continue
        for ref, (rvals, rfam) in sets.items():
            if dep == ref or not rvals or not _compatible(dfam, rfam):
                continue
            if len(rvals) * 1.0 < len(dvals) * min_coverage:
                continue
            coverage = len(dvals & rvals) / len(dvals)
            if coverage >= min_coverage:
                out.append((dep, ref, round(coverage, 4)))
    return out


def profile(conn: Connector, config: ProfilingConfig | None = None) -> ProfileResult:
    """Run UCC, FD and IND discovery and derive FK candidates."""
    config = config or ProfilingConfig()
    start = time.perf_counter()
    tables = load_tables(conn, config.max_rows)
    by_name = {t.name: t for t in tables}
    proposals: list[Proposal] = []
    uccs: dict[str, list[tuple[str, ...]]] = {}
    n_fds = 0
    for t in tables:
        parts = _Partitions(t)
        uccs[t.name] = discover_uccs(t, config.ucc_max_arity, parts)
        for combo in uccs[t.name]:
            proposals.append(
                Proposal(
                    type="UCC",
                    lhs=[qualify(t.name, c) for c in combo],
                    confidence=_ucc_score(combo, t),
                    source="baseline",
                    rationale="exact uniqueness over all rows",
                )
            )
        for lhs, rhs, redundancy in discover_fds(t, config.fd_max_lhs, parts, uccs[t.name]):
            n_fds += 1
            proposals.append(
                Proposal(
                    type="FD",
                    lhs=[qualify(t.name, c) for c in lhs],
                    rhs=[qualify(t.name, rhs)],
                    confidence=redundancy,
                    source="baseline",
                    rationale=f"exact FD; redundancy {redundancy:.2f}",
                )
            )

    unary_keys = {qualify(t, u[0]) for t, us in uccs.items() for u in us if len(u) == 1}
    inds = discover_unary_inds(tables, config.ind_min_coverage)
    distinct = {
        qualify(t.name, c): len({v for v in t.values[c] if v is not None})
        for t in tables
        for c in t.columns
    }
    n_fk = 0
    for dep, ref, coverage in inds:
        if ref not in unary_keys:
            continue
        (dt, dc), (rt, rc) = dep.split(".", 1), ref.split(".", 1)
        name_score = names.fk_name_score(dt, dc, rt, rc)
        ref_cov = min(1.0, distinct[dep] / max(1, distinct[ref]))
        proposals.append(
            Proposal(
                type="FK",
                lhs=[dep],
                rhs=[ref],
                confidence=round(0.5 * name_score + 0.5 * ref_cov, 3),
                source="baseline",
                rationale=f"IND coverage {coverage:.2f}; referenced column is unique",
            )
        )
        n_fk += 1

    if config.nary_fks:
        ind_set = {(d, r) for d, r, _ in inds}
        for rt, combos in uccs.items():
            for combo in (c for c in combos if len(c) == 2):
                r1, r2 = qualify(rt, combo[0]), qualify(rt, combo[1])
                ref_tuples = set(
                    zip(by_name[rt].values[combo[0]], by_name[rt].values[combo[1]], strict=True)
                )
                for t in tables:
                    for a1, a2 in itertools.permutations(t.columns, 2):
                        d1, d2 = qualify(t.name, a1), qualify(t.name, a2)
                        if (d1, r1) not in ind_set or (d2, r2) not in ind_set:
                            continue
                        if t.name == rt and {a1, a2} == set(combo):
                            continue
                        dep_tuples = {
                            (x, y)
                            for x, y in zip(t.values[a1], t.values[a2], strict=True)
                            if x is not None and y is not None
                        }
                        if dep_tuples and dep_tuples <= ref_tuples:
                            score = (
                                names.fk_name_score(t.name, a1, rt, combo[0])
                                + names.fk_name_score(t.name, a2, rt, combo[1])
                            ) / 2
                            proposals.append(
                                Proposal(
                                    type="FK",
                                    lhs=[d1, d2],
                                    rhs=[r1, r2],
                                    confidence=round(score, 3),
                                    source="baseline",
                                    rationale="binary IND into a 2-column UCC",
                                )
                            )
                            n_fk += 1
    return ProfileResult(
        proposals=proposals,
        seconds=round(time.perf_counter() - start, 3),
        stats={
            "tables": len(tables),
            "uccs": sum(len(u) for u in uccs.values()),
            "fds": n_fds,
            "unary_inds": len(inds),
            "fk_candidates": n_fk,
        },
    )
