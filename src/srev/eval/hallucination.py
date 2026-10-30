"""Hallucination taxonomy (F-10).

Every false positive gets exactly one category, checked in this order:

1. ``non_existent_column``   - references a table or column that does not exist
2. ``wrong_direction``       - an FK whose reverse is a gold FK
3. ``wrong_arity``           - same table(s) and overlapping columns as a gold label of
                               the same type, but a different number of columns
4. ``contradicted_by_data``  - the SQL verifier rejects it
5. ``plausible_not_in_gold`` - holds in the data but is not in the gold set
                               (candidates for the manual audit: gold may be incomplete)

The *hallucination rate* (docs/RESEARCH.md §6) is the share of all proposals
that reference non-existent columns or are contradicted by data.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence

from pydantic import BaseModel, Field

from srev.model import ConstraintKey, Proposal, Verdict, dedupe

CATEGORIES = (
    "non_existent_column",
    "wrong_direction",
    "wrong_arity",
    "contradicted_by_data",
    "plausible_not_in_gold",
)


class TaggedFP(BaseModel):
    proposal: Proposal
    category: str
    verdict: Verdict | None = None


class HallucinationReport(BaseModel):
    n_proposals: int
    n_false_positives: int
    counts: dict[str, int]
    hallucination_rate: float | None
    items: list[TaggedFP] = Field(default_factory=list)


def _wrong_arity(p: Proposal, gold: Sequence[Proposal]) -> bool:
    for g in gold:
        if g.type != p.type or len(g.lhs) == len(p.lhs):
            continue
        if p.type == "FK":
            same_tables = g.table == p.table and g.ref_table == p.ref_table
            if same_tables and set(g.lhs) & set(p.lhs):
                return True
        elif p.type == "UCC":
            if g.table == p.table and set(g.lhs) & set(p.lhs):
                return True
        elif g.rhs == p.rhs and set(g.lhs) & set(p.lhs):
            return True
    return False


def classify(
    p: Proposal,
    gold: Sequence[Proposal],
    ref_exists: Callable[[str], bool],
    verdict: Verdict | None,
) -> str:
    if verdict is not None and verdict.status == "invalid_reference":
        return "non_existent_column"
    if not all(ref_exists(r) for r in p.columns()):
        return "non_existent_column"
    if p.type == "FK":
        reverse = Proposal(type="FK", lhs=p.rhs, rhs=p.lhs).key()
        if any(g.key() == reverse for g in gold):
            return "wrong_direction"
    if _wrong_arity(p, gold):
        return "wrong_arity"
    if verdict is not None and not verdict.accepted:
        return "contradicted_by_data"
    return "plausible_not_in_gold"


def analyse(
    proposals: list[Proposal],
    gold: list[Proposal],
    ref_exists: Callable[[str], bool],
    verdicts: dict[ConstraintKey, Verdict] | None = None,
) -> HallucinationReport:
    verdicts = verdicts or {}
    props = dedupe(proposals)
    gold_keys = {g.key() for g in gold}
    items = []
    hallucinated = 0
    for p in props:
        v = verdicts.get(p.key())
        missing = not all(ref_exists(r) for r in p.columns())
        if missing or (v is not None and not v.accepted):
            hallucinated += 1
        if p.key() in gold_keys:
            continue
        items.append(TaggedFP(proposal=p, category=classify(p, gold, ref_exists, v), verdict=v))
    counts = Counter(i.category for i in items)
    return HallucinationReport(
        n_proposals=len(props),
        n_false_positives=len(items),
        counts={c: counts.get(c, 0) for c in CATEGORIES},
        hallucination_rate=round(hallucinated / len(props), 4) if props else None,
        items=items,
    )


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float | None:
    """Cohen's kappa for two annotators' labels of the same items."""
    if len(a) != len(b) or not a:
        return None
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    if expected == 1:
        return 1.0
    return round((observed - expected) / (1 - expected), 4)
