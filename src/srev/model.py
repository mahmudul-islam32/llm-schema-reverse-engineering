"""Common data model shared by every pipeline stage.

All methods (LLM, heuristic, statistical baselines, hybrid) emit
:class:`Proposal` objects in the same format, and gold labels use it as well,
so the evaluator never needs to know where a constraint came from.

Column references are always qualified and lower-cased: ``"table.column"``.

* ``FK``  - ``lhs`` are the referencing columns, ``rhs`` the referenced ones
  (pairwise, same length). Direction matters.
* ``UCC`` - ``lhs`` is the column set, ``rhs`` is empty.
* ``FD``  - ``lhs`` determines the single column in ``rhs`` (same table).
  Multi-column right-hand sides are split by :func:`split_fds`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ConstraintType = Literal["FK", "UCC", "FD"]
CONSTRAINT_TYPES: tuple[ConstraintType, ...] = ("FK", "UCC", "FD")

ConstraintKey = tuple[str, tuple[str, ...], tuple[str, ...]]


def qualify(table: str, column: str) -> str:
    return f"{table.lower()}.{column.lower()}"


def split_ref(ref: str) -> tuple[str, str]:
    """Split ``"table.column"`` into its parts (the column may not contain dots)."""
    table, _, column = ref.rpartition(".")
    if not table or not column:
        raise ValueError(f"column reference must look like 'table.column', got {ref!r}")
    return table, column


class Proposal(BaseModel):
    """A candidate constraint, from any method, or a gold label."""

    type: ConstraintType
    lhs: list[str]
    rhs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: str = "unknown"
    rationale: str = ""

    @field_validator("lhs", "rhs")
    @classmethod
    def _normalise(cls, refs: list[str]) -> list[str]:
        out = []
        for ref in refs:
            ref = ref.strip().replace('"', "").lower()
            split_ref(ref)
            out.append(ref)
        return out

    @model_validator(mode="after")
    def _check_shape(self) -> Proposal:
        if not self.lhs:
            raise ValueError("lhs must not be empty")
        if self.type == "FK":
            if len(self.lhs) != len(self.rhs):
                raise ValueError("FK lhs and rhs must have the same length")
            if len({split_ref(r)[0] for r in self.lhs}) != 1:
                raise ValueError("FK lhs columns must come from one table")
            if len({split_ref(r)[0] for r in self.rhs}) != 1:
                raise ValueError("FK rhs columns must come from one table")
            if self.lhs == self.rhs:
                raise ValueError("FK must not reference itself")
        elif self.type == "UCC":
            if self.rhs:
                raise ValueError("UCC must have an empty rhs")
            if len({split_ref(r)[0] for r in self.lhs}) != 1:
                raise ValueError("UCC columns must come from one table")
            if len(set(self.lhs)) != len(self.lhs):
                raise ValueError("UCC columns must be distinct")
        elif self.type == "FD":
            if not self.rhs:
                raise ValueError("FD needs a non-empty rhs")
            tables = {split_ref(r)[0] for r in self.lhs + self.rhs}
            if len(tables) != 1:
                raise ValueError("FD columns must come from one table")
            if set(self.rhs) & set(self.lhs):
                raise ValueError("FD rhs must not overlap lhs (trivial FD)")
        return self

    # -- identity -------------------------------------------------------
    def key(self) -> ConstraintKey:
        """Canonical identity used for exact-match evaluation."""
        if self.type == "FK":
            pairs = sorted(zip(self.lhs, self.rhs, strict=True))
            return ("FK", tuple(p[0] for p in pairs), tuple(p[1] for p in pairs))
        if self.type == "UCC":
            return ("UCC", tuple(sorted(self.lhs)), ())
        return ("FD", tuple(sorted(self.lhs)), tuple(sorted(self.rhs)))

    @property
    def table(self) -> str:
        return split_ref(self.lhs[0])[0]

    @property
    def ref_table(self) -> str | None:
        return split_ref(self.rhs[0])[0] if self.type == "FK" else None

    def columns(self) -> list[str]:
        return list(dict.fromkeys(self.lhs + self.rhs))

    def ident(self) -> str:
        """Short stable id, handy for traceability in reports and DDL comments."""
        raw = json.dumps(self.key())
        return hashlib.sha1(raw.encode()).hexdigest()[:10]

    def describe(self) -> str:
        if self.type == "FK":
            return f"FK ({', '.join(self.lhs)}) -> ({', '.join(self.rhs)})"
        if self.type == "UCC":
            return f"UCC ({', '.join(self.lhs)})"
        return f"FD ({', '.join(self.lhs)}) -> {', '.join(self.rhs)}"


def split_fds(proposals: list[Proposal]) -> list[Proposal]:
    """Turn every FD ``X -> AB`` into ``X -> A`` and ``X -> B``."""
    out: list[Proposal] = []
    for p in proposals:
        if p.type == "FD" and len(p.rhs) > 1:
            out.extend(p.model_copy(update={"rhs": [r]}) for r in p.rhs)
        else:
            out.append(p)
    return out


def dedupe(proposals: list[Proposal]) -> list[Proposal]:
    """Drop duplicates by canonical key, keeping the highest-confidence copy."""
    best: dict[ConstraintKey, Proposal] = {}
    for p in split_fds(proposals):
        k = p.key()
        if k not in best or p.confidence > best[k].confidence:
            best[k] = p
    return list(best.values())


class Verdict(BaseModel):
    """Outcome of checking one proposal against the data."""

    accepted: bool
    status: Literal["ok", "rejected", "invalid_reference", "error", "empty"]
    statistic: str = ""
    value: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class VerifiedProposal(BaseModel):
    proposal: Proposal
    verdict: Verdict
