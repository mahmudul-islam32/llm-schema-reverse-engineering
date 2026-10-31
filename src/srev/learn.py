"""Learned foreign-key classifier over partial-IND candidates (F-14).

Following the idea of Rostin, Albrecht, Bauckmann, Naumann and Leser
(*A Machine Learning Approach to Foreign Key Discovery*, WebDB 2009), FK
discovery is treated as classification: every partial inclusion dependency
into a (nearly) unique column is a candidate, described by interpretable
features, and a model decides which candidates are genuine foreign keys.

The feature set mixes data features (coverage, uniqueness, value ranges),
schema features (name similarity, key-like names) and usage features (how
often the pair is joined in the query log), so the learned weights show
which kind of evidence carries the decision. The classifier is an
L2-regularised logistic regression in plain Python: small, deterministic and
inspectable. The runner evaluates it leave-one-dataset-out, so a dataset is
never scored by a model that saw any noise level of that dataset in training.

Only unary FKs are candidates; composite FKs count as misses.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, field_validator

from srev import names
from srev.baselines.profiling import TableData, discover_unary_inds, load_tables
from srev.db import Connector
from srev.model import ConstraintKey, Proposal, qualify
from srev.qlog import JoinStats

FEATURES = (
    "row_coverage",
    "value_coverage",
    "ref_uniqueness",
    "dep_uniqueness",
    "distinct_ratio",
    "name_score",
    "dep_keyish",
    "ref_keyish",
    "ref_first_column",
    "same_table",
    "range_inside",
    "dep_null_ratio",
    "join_log",
    "ref_rows_ratio",
)


class LearnedConfig(BaseModel):
    min_coverage: float = Field(0.5, gt=0, le=1, description="row coverage of a candidate IND")
    ref_min_uniqueness: float = Field(0.9, gt=0, le=1, description="referenced side near-unique")
    threshold: float = Field(0.5, gt=0, lt=1)
    l2: float = 1.0
    epochs: int = 400
    learning_rate: float = 0.3
    balanced: bool = True
    max_rows: int | None = None
    features: list[str] | None = Field(
        None, description="feature subset for ablations (default: all of FEATURES)"
    )

    @field_validator("features")
    @classmethod
    def _known(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and (unknown := set(v) - set(FEATURES)):
            raise ValueError(f"unknown features: {sorted(unknown)}")
        return v


@dataclass
class Candidate:
    dep: str
    ref: str
    features: dict[str, float]
    label: int = 0

    def key(self) -> ConstraintKey:
        return ("FK", (self.dep,), (self.ref,))

    def proposal(self, p: float, rationale: str = "") -> Proposal:
        return Proposal(
            type="FK",
            lhs=[self.dep],
            rhs=[self.ref],
            confidence=round(p, 4),
            source="learned",
            rationale=rationale,
        )


@dataclass
class _ColStats:
    values: list[Any]
    distinct: set[Any]
    rows: int
    nulls: int
    family: str
    position: int
    table_rows: int

    @property
    def uniqueness(self) -> float:
        return len(self.distinct) / len(self.values) if self.values else 0.0


def _stats(tables: list[TableData]) -> dict[str, _ColStats]:
    out = {}
    for t in tables:
        for i, c in enumerate(t.columns):
            vals = [v for v in t.values[c] if v is not None]
            out[qualify(t.name, c)] = _ColStats(
                values=vals,
                distinct=set(vals),
                rows=t.n,
                nulls=t.n - len(vals),
                family=t.families[c],
                position=i,
                table_rows=t.n,
            )
    return out


def _range_inside(dep: _ColStats, ref: _ColStats) -> float:
    if dep.family not in ("integer", "numeric") or not dep.values or not ref.values:
        return 0.5  # not applicable
    try:
        return float(min(dep.values) >= min(ref.values) and max(dep.values) <= max(ref.values))
    except TypeError:
        return 0.5


def candidates(
    conn: Connector, joins: JoinStats | None, config: LearnedConfig | None = None
) -> list[Candidate]:
    """Partial unary INDs into near-unique columns, with their feature vectors."""
    cfg = config or LearnedConfig()
    tables = load_tables(conn, cfg.max_rows)
    stats = _stats(tables)
    join_counts = {(p.left, p.right): p.count for p in joins.pairs} if joins else {}
    out = []
    for dep, ref, row_cov in discover_unary_inds(tables, cfg.min_coverage, "rows"):
        d, r = stats[dep], stats[ref]
        if r.uniqueness < cfg.ref_min_uniqueness or r.nulls == r.rows:
            continue
        (dt, dc), (rt, rc) = dep.split(".", 1), ref.split(".", 1)
        pair = (min(dep, ref), max(dep, ref))
        features = {
            "row_coverage": row_cov,
            "value_coverage": len(d.distinct & r.distinct) / len(d.distinct),
            "ref_uniqueness": r.uniqueness,
            "dep_uniqueness": d.uniqueness,
            "distinct_ratio": min(1.0, len(d.distinct) / max(1, len(r.distinct))),
            "name_score": names.fk_name_score(dt, dc, rt, rc),
            "dep_keyish": float(names.keyish(dc)),
            "ref_keyish": float(names.keyish(rc)),
            "ref_first_column": float(r.position == 0),
            "same_table": float(dt == rt),
            "range_inside": _range_inside(d, r),
            "dep_null_ratio": d.nulls / d.rows if d.rows else 0.0,
            "join_log": math.log1p(join_counts.get(pair, 0)),
            "ref_rows_ratio": min(1.0, r.table_rows / max(1, d.table_rows)),
        }
        out.append(Candidate(dep=dep, ref=ref, features=features))
    return out


def label(cands: list[Candidate], gold: list[Proposal]) -> list[Candidate]:
    keys = {g.key() for g in gold if g.type == "FK"}
    for c in cands:
        c.label = int(c.key() in keys)
    return cands


@dataclass
class LogisticModel:
    """L2-regularised logistic regression on standardised features."""

    features: tuple[str, ...] = FEATURES
    weights: list[float] = field(default_factory=list)
    bias: float = 0.0
    mean: list[float] = field(default_factory=list)
    std: list[float] = field(default_factory=list)

    def _x(self, c: Candidate) -> list[float]:
        return [
            (c.features[f] - m) / s
            for f, m, s in zip(self.features, self.mean, self.std, strict=True)
        ]

    def fit(self, data: list[Candidate], config: LearnedConfig) -> LogisticModel:
        if not data:
            raise ValueError("no training candidates")
        cols = [[c.features[f] for c in data] for f in self.features]
        self.mean = [sum(col) / len(col) for col in cols]
        self.std = [
            math.sqrt(sum((v - m) ** 2 for v in col) / len(col)) or 1.0
            for col, m in zip(cols, self.mean, strict=True)
        ]
        xs = [self._x(c) for c in data]
        ys = [c.label for c in data]
        pos = sum(ys)
        neg = len(ys) - pos
        if config.balanced and pos and neg:
            w_pos, w_neg = len(ys) / (2 * pos), len(ys) / (2 * neg)
        else:
            w_pos = w_neg = 1.0
        sample_w = [w_pos if y else w_neg for y in ys]
        total_w = sum(sample_w)
        self.weights = [0.0] * len(self.features)
        self.bias = 0.0
        lr = config.learning_rate
        for _ in range(config.epochs):  # full-batch gradient descent: deterministic
            grad = [0.0] * len(self.weights)
            grad_b = 0.0
            for x, y, sw in zip(xs, ys, sample_w, strict=True):
                err = (self._sigmoid(x) - y) * sw
                grad_b += err
                for j, v in enumerate(x):
                    grad[j] += err * v
            for j in range(len(self.weights)):
                self.weights[j] -= lr * (grad[j] / total_w + config.l2 * self.weights[j] / len(xs))
            self.bias -= lr * grad_b / total_w
        return self

    def _sigmoid(self, x: list[float]) -> float:
        z = self.bias + sum(w * v for w, v in zip(self.weights, x, strict=True))
        return 1 / (1 + math.exp(-z)) if z >= 0 else math.exp(z) / (1 + math.exp(z))

    def predict(self, c: Candidate) -> float:
        return self._sigmoid(self._x(c))

    def explain(self, c: Candidate, top: int = 3) -> str:
        contrib = sorted(
            ((w * v, f) for w, v, f in zip(self.weights, self._x(c), self.features, strict=True)),
            reverse=True,
        )
        return "learned; strongest evidence: " + ", ".join(f for _, f in contrib[:top])

    def coefficients(self) -> dict[str, float]:
        return {f: round(w, 4) for f, w in zip(self.features, self.weights, strict=True)}


def train_and_predict(
    train: list[Candidate], test: list[Candidate], config: LearnedConfig
) -> tuple[list[Proposal], LogisticModel]:
    model = LogisticModel(features=tuple(config.features or FEATURES)).fit(train, config)
    out = []
    for c in test:
        p = model.predict(c)
        if p >= config.threshold:
            out.append(c.proposal(p, model.explain(c)))
    return out, model
