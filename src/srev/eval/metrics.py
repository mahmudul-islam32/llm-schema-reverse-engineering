"""Evaluation metrics (F-09), following docs/RESEARCH.md §6.

* Exact match on the canonical constraint key (FK direction matters; FDs are
  split to single right-hand sides; UCC column order is irrelevant).
* Column-set overlap as a secondary measure: a proposal overlaps a gold
  label of the same type and orientation when the Jaccard similarity of their
  column sets is at least ``OVERLAP_JACCARD``.
* Precision, recall and F1 per constraint type, micro- and macro-averaged
  across databases, with percentile bootstrap confidence intervals.
* Top-k evaluation: proposals ranked by confidence and cut at
  ``ceil(factor * |gold of that type|)``, so methods that emit every valid
  dependency can be compared at the same budget.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence

from pydantic import BaseModel, Field

from srev.model import CONSTRAINT_TYPES, ConstraintType, Proposal, dedupe

OVERLAP_JACCARD = 0.5


class TypeScore(BaseModel):
    type: str
    n_proposed: int
    n_gold: int
    tp: int
    fp: int
    fn: int
    precision: float | None
    recall: float | None
    f1: float | None
    ci_precision: tuple[float, float] | None = None
    ci_recall: tuple[float, float] | None = None
    ci_f1: tuple[float, float] | None = None
    overlap_precision: float | None = None
    overlap_recall: float | None = None
    overlap_f1: float | None = None


class ScoreCard(BaseModel):
    scores: dict[str, TypeScore] = Field(default_factory=dict)
    top_k: dict[str, TypeScore] = Field(default_factory=dict)


def prf(tp: int, fp: int, fn: int) -> tuple[float | None, float | None, float | None]:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = None if p is None or r is None else (2 * p * r / (p + r) if p + r else 0.0)
    return p, r, f


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def overlaps(p: Proposal, g: Proposal, threshold: float = OVERLAP_JACCARD) -> bool:
    if p.type != g.type:
        return False
    if p.type == "FK":
        if p.table != g.table or p.ref_table != g.ref_table:
            return False
        return _jaccard(set(p.lhs) | set(p.rhs), set(g.lhs) | set(g.rhs)) >= threshold
    if p.type == "UCC":
        return p.table == g.table and _jaccard(set(p.lhs), set(g.lhs)) >= threshold
    return p.rhs == g.rhs and _jaccard(set(p.lhs), set(g.lhs)) >= threshold


def bootstrap_ci(
    outcomes: Sequence[str], n_boot: int = 1000, seed: int = 0, alpha: float = 0.05
) -> dict[str, tuple[float, float] | None]:
    """Percentile bootstrap over item outcomes (``'tp'``, ``'fp'``, ``'fn'``)."""
    if not outcomes or n_boot <= 0:
        return {"precision": None, "recall": None, "f1": None}
    rng = random.Random(seed)
    samples: dict[str, list[float]] = {"precision": [], "recall": [], "f1": []}
    items = list(outcomes)
    for _ in range(n_boot):
        draw = rng.choices(items, k=len(items))
        p, r, f = prf(draw.count("tp"), draw.count("fp"), draw.count("fn"))
        for name, v in (("precision", p), ("recall", r), ("f1", f)):
            if v is not None:
                samples[name].append(v)
    return {name: _percentile_ci(vals, alpha) for name, vals in samples.items()}


def _percentile_ci(values: list[float], alpha: float) -> tuple[float, float] | None:
    if not values:
        return None
    values = sorted(values)
    lo = values[max(0, math.floor(alpha / 2 * len(values)))]
    hi = values[min(len(values) - 1, math.ceil((1 - alpha / 2) * len(values)) - 1)]
    return (round(lo, 4), round(hi, 4))


def score_type(
    proposals: list[Proposal],
    gold: list[Proposal],
    ctype: ConstraintType,
    n_boot: int = 0,
    seed: int = 0,
) -> TypeScore:
    props = [p for p in proposals if p.type == ctype]
    golds = [g for g in gold if g.type == ctype]
    gold_keys = {g.key() for g in golds}
    prop_keys = {p.key() for p in props}
    tp = len(prop_keys & gold_keys)
    fp = len(prop_keys - gold_keys)
    fn = len(gold_keys - prop_keys)
    p, r, f = prf(tp, fp, fn)
    o_tp_p = sum(any(overlaps(x, g) for g in golds) for x in props)
    o_tp_r = sum(any(overlaps(x, g) for x in props) for g in golds)
    op = o_tp_p / len(props) if props else None
    orr = o_tp_r / len(golds) if golds else None
    of = (
        (2 * op * orr / (op + orr) if op + orr else 0.0)
        if op is not None and orr is not None
        else None
    )
    ci = bootstrap_ci(["tp"] * tp + ["fp"] * fp + ["fn"] * fn, n_boot, seed) if n_boot else {}
    return TypeScore(
        type=ctype,
        n_proposed=len(prop_keys),
        n_gold=len(gold_keys),
        tp=tp,
        fp=fp,
        fn=fn,
        precision=_r(p),
        recall=_r(r),
        f1=_r(f),
        ci_precision=ci.get("precision"),
        ci_recall=ci.get("recall"),
        ci_f1=ci.get("f1"),
        overlap_precision=_r(op),
        overlap_recall=_r(orr),
        overlap_f1=_r(of),
    )


def _r(v: float | None) -> float | None:
    return None if v is None else round(v, 4)


def top_k_cut(proposals: list[Proposal], gold: list[Proposal], factor: float) -> list[Proposal]:
    out = []
    for ctype in CONSTRAINT_TYPES:
        n_gold = sum(g.type == ctype for g in gold)
        k = math.ceil(factor * n_gold)
        typed = sorted(
            (p for p in proposals if p.type == ctype), key=lambda p: (-p.confidence, p.key())
        )
        out.extend(typed[:k])
    return out


def score(
    proposals: list[Proposal],
    gold: list[Proposal],
    *,
    n_boot: int = 1000,
    seed: int = 0,
    top_k_factor: float | None = 1.0,
) -> ScoreCard:
    props = dedupe(proposals)
    card = ScoreCard()
    for ctype in CONSTRAINT_TYPES:
        card.scores[ctype] = score_type(props, gold, ctype, n_boot, seed)
    if top_k_factor:
        cut = top_k_cut(props, gold, top_k_factor)
        for ctype in CONSTRAINT_TYPES:
            card.top_k[ctype] = score_type(cut, gold, ctype, 0, seed)
    return card


class Aggregate(BaseModel):
    type: str
    n_databases: int
    micro_precision: float | None
    micro_recall: float | None
    micro_f1: float | None
    macro_f1: float | None
    ci_macro_f1: tuple[float, float] | None = None


def aggregate(
    cards: Sequence[TypeScore], n_boot: int = 1000, seed: int = 0, alpha: float = 0.05
) -> Aggregate:
    """Micro (pooled counts) and macro (mean of per-database F1) averages for one type."""
    tp = sum(c.tp for c in cards)
    fp = sum(c.fp for c in cards)
    fn = sum(c.fn for c in cards)
    p, r, f = prf(tp, fp, fn)
    f1s = [c.f1 for c in cards if c.f1 is not None]
    macro = sum(f1s) / len(f1s) if f1s else None
    ci = None
    if len(f1s) >= 2 and n_boot:
        rng = random.Random(seed)
        means = [sum(rng.choices(f1s, k=len(f1s))) / len(f1s) for _ in range(n_boot)]
        ci = _percentile_ci(means, alpha)
    return Aggregate(
        type=cards[0].type if cards else "",
        n_databases=len(cards),
        micro_precision=_r(p),
        micro_recall=_r(r),
        micro_f1=_r(f),
        macro_f1=_r(macro),
        ci_macro_f1=ci,
    )


def paired_permutation_test(
    a: Sequence[float], b: Sequence[float], n_perm: int = 10000, seed: int = 0
) -> float | None:
    """Two-sided paired sign-flip permutation test on per-database scores."""
    if len(a) != len(b) or not a:
        return None
    diffs = [x - y for x, y in zip(a, b, strict=True)]
    observed = abs(sum(diffs))
    if len(diffs) <= 16:  # exact enumeration
        total = hits = 0
        for mask in range(1 << len(diffs)):
            s = sum(d if (mask >> i) & 1 else -d for i, d in enumerate(diffs))
            total += 1
            hits += abs(s) >= observed - 1e-12
        return round(hits / total, 6)
    rng = random.Random(seed)
    hits = sum(
        abs(sum(d if rng.random() < 0.5 else -d for d in diffs)) >= observed - 1e-12
        for _ in range(n_perm)
    )
    return round((hits + 1) / (n_perm + 1), 6)
