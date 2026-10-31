"""One-shot constraint discovery for an unknown database (F-19).

``srev discover`` is the practitioner's entry point: point it at a database,
a folder of CSV files or (via :mod:`srev.api`) a set of DataFrames and get
the likely keys, foreign keys and functional dependencies, each verified on
the data and annotated with its evidence.

Pipeline:

1. **Approximate profiling** (:mod:`srev.baselines.profiling`) enumerates UCCs,
   FDs and binary FK candidates while tolerating a little dirty data.
2. **Learned FK classifier** (:mod:`srev.learn`) scores unary FK candidates. It
   is trained on the built-in generated benchmarks at several noise levels and
   with cryptic names, so no labelled data from the target database is needed.
   The query-log feature is used only when a workload is supplied.
3. **Selection**: UCCs are kept when they look like keys (small, NULL-free,
   key-like names) with at least one key per table; FDs are kept when they are
   not implied by a key and explain real redundancy.
4. **SQL verification** (:mod:`srev.verify`) of every candidate; only accepted
   constraints are returned as ``constraints``, the rest as ``rejected``.

The output file is a list of verified proposals, the same format ``srev
verify`` writes, so it feeds ``srev synth``, ``srev check`` and the exports.
"""

from __future__ import annotations

import functools
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from srev.baselines.profiling import ProfilingConfig, profile
from srev.datasets import load_dataset
from srev.db import Connector, SQLiteConnector, materialize
from srev.learn import FEATURES, LearnedConfig, LogisticModel, candidates, label
from srev.model import Proposal, VerifiedProposal, dedupe
from srev.noise import inject, resolve_profile
from srev.qlog import JoinStats, mine_joins
from srev.truth import gold_from_dataset
from srev.verify import Verifier, VerifyConfig


class DiscoverConfig(BaseModel):
    profiling: ProfilingConfig = Field(
        default_factory=lambda: ProfilingConfig(
            ucc_max_arity=2, fd_max_lhs=1, ucc_max_error=0.0, fd_max_error=0.02,
            ind_min_coverage=0.9, ind_coverage="rows",
        )
    )  # fmt: skip
    fk_threshold: float = Field(0.5, gt=0, lt=1)
    review_threshold: float = Field(
        0.2, ge=0, lt=1, description="verified FK candidates scoring in [review, fk) need review"
    )
    ucc_min_score: float = 0.5
    fd_min_redundancy: float = 0.5
    fd_min_lhs_distinct: int = 3
    verify: VerifyConfig = Field(
        default_factory=lambda: VerifyConfig(fk_min_coverage=0.9, fd_max_g3=0.02)
    )
    training_datasets: list[str] = Field(
        default_factory=lambda: ["tpch_mini", "university", "retail_legacy", "airline"]
    )
    training_noise: list[str] = Field(
        default_factory=lambda: ["none", "low", "medium", "names-cryptic"]
    )
    training_scale: float = 0.3
    seed: int = 42


class Discovery(BaseModel):
    database: str
    constraints: list[VerifiedProposal] = Field(default_factory=list)
    rejected: list[VerifiedProposal] = Field(default_factory=list)
    review: list[VerifiedProposal] = Field(
        default_factory=list,
        description="FK candidates that hold in the data but scored below the threshold",
    )
    model: dict[str, Any] = Field(default_factory=dict)
    seconds: float = 0.0

    def accepted(self) -> list[Proposal]:
        return [v.proposal for v in self.constraints]

    def counts(self) -> dict[str, int]:
        out = {"FK": 0, "UCC": 0, "FD": 0}
        for p in self.accepted():
            out[p.type] += 1
        return out

    def save(self, path: str | Path) -> None:
        """Write the accepted constraints in the ``srev verify`` output format."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        import json

        Path(path).write_text(json.dumps([v.model_dump() for v in self.constraints], indent=2))

    def to_frame(self) -> Any:
        """Accepted constraints as a pandas DataFrame (requires pandas)."""
        import pandas as pd

        return pd.DataFrame(
            [
                {
                    "type": v.proposal.type,
                    "lhs": ", ".join(v.proposal.lhs),
                    "rhs": ", ".join(v.proposal.rhs),
                    "confidence": v.proposal.confidence,
                    "statistic": v.verdict.statistic,
                    "value": v.verdict.value,
                    "source": v.proposal.source,
                    "rationale": v.proposal.rationale,
                }
                for v in self.constraints
            ]
        )


@functools.lru_cache(maxsize=4)
def _trained_model(cfg_json: str, use_logs: bool) -> tuple[LogisticModel, int, int]:
    cfg = DiscoverConfig.model_validate_json(cfg_json)
    feats = tuple(f for f in FEATURES if use_logs or f != "join_log")
    learned = LearnedConfig(features=list(feats))
    pool = []
    for name in cfg.training_datasets:
        ds = load_dataset(name, seed=cfg.seed, scale=cfg.training_scale)
        gold = gold_from_dataset(ds)
        for level in cfg.training_noise:
            noisy, log = inject(ds, resolve_profile(level), seed=cfg.seed, gold=gold)
            with SQLiteConnector(":memory:") as conn:
                materialize(noisy, conn)
                joins = mine_joins(noisy.queries, noisy.schema_map()) if use_logs else None
                pool += label(candidates(conn, joins, learned), log.renames.gold(gold).labels)
    model = LogisticModel(features=feats).fit(pool, learned)
    return model, len(pool), sum(c.label for c in pool)


def train_fk_model(config: DiscoverConfig | None = None, use_logs: bool = False) -> LogisticModel:
    """The FK classifier ``discover`` uses (cached per configuration)."""
    cfg = config or DiscoverConfig()
    return _trained_model(cfg.model_dump_json(), use_logs)[0]


def _select_uccs(props: list[Proposal], min_score: float) -> list[Proposal]:
    by_table: dict[str, list[Proposal]] = {}
    for p in props:
        by_table.setdefault(p.table, []).append(p)
    out = []
    for group in by_table.values():
        group.sort(key=lambda p: (-p.confidence, len(p.lhs)))
        keep = [p for p in group if p.confidence >= min_score] or group[:1]
        out += keep
    return out


def discover(
    conn: Connector,
    database: str = "database",
    *,
    joins: JoinStats | None = None,
    config: DiscoverConfig | None = None,
) -> Discovery:
    cfg = config or DiscoverConfig()
    start = time.perf_counter()
    use_logs = bool(joins and joins.pairs)
    model, n_train, n_pos = _trained_model(cfg.model_dump_json(), use_logs)

    prof = profile(conn, cfg.profiling)
    uccs = _select_uccs([p for p in prof.proposals if p.type == "UCC"], cfg.ucc_min_score)
    key_sets = [set(u.lhs) for u in uccs]
    distinct = {
        f"{t}.{c.name}".lower(): c.distinct_count
        for t in conn.list_tables()
        for c in _column_counts(conn, t)
    }
    fds = [
        p.model_copy(update={"source": "discover"})
        for p in prof.proposals
        if p.type == "FD"
        and p.confidence >= cfg.fd_min_redundancy
        and not any(k <= set(p.lhs) for k in key_sets)
        and all(distinct.get(r, 0) >= cfg.fd_min_lhs_distinct for r in p.lhs)
    ]
    composite_fks = [p for p in prof.proposals if p.type == "FK" and len(p.lhs) > 1]

    learned = LearnedConfig(features=list(model.features))
    fks, maybe = [], []
    for c in candidates(conn, joins if use_logs else None, learned):
        p = model.predict(c)
        if p >= cfg.fk_threshold:
            fks.append(c.proposal(p, model.explain(c)))
        elif p >= cfg.review_threshold:
            maybe.append(c.proposal(p, model.explain(c)).model_copy(update={"source": "discover"}))

    props = dedupe(
        [u.model_copy(update={"source": "discover"}) for u in uccs]
        + fds
        + [f.model_copy(update={"source": "discover"}) for f in fks + composite_fks]
    )
    verifier = Verifier(conn, cfg.verify)
    verified = verifier.verify_all(props)
    review = [v for v in verifier.verify_all(dedupe(maybe)) if v.verdict.accepted]
    return Discovery(
        database=database,
        constraints=[v for v in verified if v.verdict.accepted],
        rejected=[v for v in verified if not v.verdict.accepted],
        review=sorted(review, key=lambda v: -v.proposal.confidence),
        model={
            "coefficients": model.coefficients(),
            "training_candidates": n_train,
            "training_positives": n_pos,
            "uses_query_log": use_logs,
        },
        seconds=round(time.perf_counter() - start, 3),
    )


class _Count(BaseModel):
    name: str
    distinct_count: int


def _column_counts(conn: Connector, table: str) -> list[_Count]:
    cols = conn.list_columns(table)
    if not cols:
        return []
    sql = ", ".join(f"COUNT(DISTINCT {conn.quote(c.name)})" for c in cols)
    row = conn.query(f"SELECT {sql} FROM {conn.qtable(table)}")[0]
    return [_Count(name=c.name, distinct_count=int(n)) for c, n in zip(cols, row, strict=True)]
