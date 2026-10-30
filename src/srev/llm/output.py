"""Strict parsing of structured LLM output.

Malformed output is *counted*, never silently repaired: a response that is
not valid JSON is a response failure, and every proposal that fails schema
validation is an item failure. The only normalisation applied is removing a
surrounding markdown code fence, and that is counted too.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from srev.model import Proposal

_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n(.*)\n```\s*$", re.DOTALL)


class ParseStats(BaseModel):
    responses: int = 0
    response_failures: int = 0
    items: int = 0
    item_failures: int = 0
    fence_stripped: int = 0
    errors: list[str] = Field(default_factory=list)

    @property
    def response_failure_rate(self) -> float:
        return self.response_failures / self.responses if self.responses else 0.0

    @property
    def item_failure_rate(self) -> float:
        return self.item_failures / self.items if self.items else 0.0

    def merge(self, other: ParseStats) -> ParseStats:
        return ParseStats(
            responses=self.responses + other.responses,
            response_failures=self.response_failures + other.response_failures,
            items=self.items + other.items,
            item_failures=self.item_failures + other.item_failures,
            fence_stripped=self.fence_stripped + other.fence_stripped,
            errors=(self.errors + other.errors)[:50],
        )

    def _error(self, msg: str) -> None:
        if len(self.errors) < 50:
            self.errors.append(msg[:300])


def _load_json(text: str, stats: ParseStats) -> Any:
    stats.responses += 1
    body = text.strip()
    match = _FENCE.match(body)
    if match:
        stats.fence_stripped += 1
        body = match.group(1).strip()
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        stats.response_failures += 1
        stats._error(f"invalid JSON: {exc}: {body[:120]!r}")
        return None


def parse_proposals(text: str, source: str = "llm") -> tuple[list[Proposal], ParseStats]:
    stats = ParseStats()
    data = _load_json(text, stats)
    if data is None:
        return [], stats
    if not isinstance(data, dict) or not isinstance(data.get("proposals"), list):
        stats.response_failures += 1
        stats._error("top level must be an object with a 'proposals' array")
        return [], stats
    out = []
    for item in data["proposals"]:
        stats.items += 1
        if not isinstance(item, dict):
            stats.item_failures += 1
            stats._error(f"proposal is not an object: {item!r}")
            continue
        try:
            out.append(
                Proposal(
                    type=item.get("type"),
                    lhs=item.get("lhs"),
                    rhs=item.get("rhs", []),
                    confidence=item.get("confidence", 1.0),
                    rationale=str(item.get("rationale", "")),
                    source=source,
                )
            )
        except (ValidationError, ValueError, TypeError) as exc:
            stats.item_failures += 1
            stats._error(f"invalid proposal {json.dumps(item)[:150]}: {exc}".replace("\n", " "))
    return out, stats


def parse_ranking(text: str, n_candidates: int) -> tuple[dict[int, float], ParseStats]:
    stats = ParseStats()
    data = _load_json(text, stats)
    if data is None:
        return {}, stats
    if not isinstance(data, dict) or not isinstance(data.get("ranking"), list):
        stats.response_failures += 1
        stats._error("top level must be an object with a 'ranking' array")
        return {}, stats
    scores: dict[int, float] = {}
    for item in data["ranking"]:
        stats.items += 1
        try:
            idx = int(item["id"])
            conf = float(item["confidence"])
            if not 0 <= idx < n_candidates or not 0.0 <= conf <= 1.0:
                raise ValueError("id or confidence out of range")
            scores[idx] = max(conf, scores.get(idx, 0.0))
        except (KeyError, TypeError, ValueError) as exc:
            stats.item_failures += 1
            stats._error(f"invalid ranking item {item!r}: {exc}")
    return scores, stats
