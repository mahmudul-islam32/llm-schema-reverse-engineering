"""Proposers: turn evidence into constraint proposals.

* :class:`LLMProposer` - asks an OpenAI-compatible model (vLLM) for proposals.
* :class:`HeuristicProposer` - a transparent, offline *name/statistics
  heuristic*. It is not an LLM; it is the "semantic cues without a model"
  reference point and lets the whole pipeline run without a GPU.
* :class:`HybridRanker` - RQ5: baseline candidates re-ranked by an LLM (or by
  the heuristic scorer) and cut at a confidence threshold.
"""

from __future__ import annotations

import itertools
import time
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from srev import names
from srev.extract import Evidence, TableEvidence, is_unique_column
from srev.llm.client import ChatClient, Completion
from srev.llm.output import ParseStats, parse_proposals, parse_ranking
from srev.llm.prompt import (
    OUTPUT_SCHEMA,
    Prompt,
    PromptConfig,
    build_prompt,
    build_ranking_prompt,
)
from srev.model import Proposal, dedupe, qualify, split_ref
from srev.qlog import JoinStats


class ProposerOutput(BaseModel):
    proposals: list[Proposal] = Field(default_factory=list)
    parse: ParseStats = Field(default_factory=ParseStats)
    prompts: list[Prompt] = Field(default_factory=list)
    completions: list[Completion] = Field(default_factory=list)
    seconds: float = 0.0

    @property
    def tokens(self) -> int:
        return sum((c.prompt_tokens or 0) + (c.completion_tokens or 0) for c in self.completions)


class Proposer(Protocol):
    def propose(self, evidence: Evidence, joins: JoinStats | None) -> ProposerOutput: ...


class LLMProposer:
    def __init__(self, client: ChatClient, prompt_config: PromptConfig | None = None) -> None:
        self.client = client
        self.prompt_config = prompt_config or PromptConfig()

    def propose(self, evidence: Evidence, joins: JoinStats | None) -> ProposerOutput:
        start = time.perf_counter()
        prompt = build_prompt(evidence, joins, self.prompt_config)
        completion = self.client.complete(prompt, OUTPUT_SCHEMA)
        proposals, stats = parse_proposals(completion.content, source="llm")
        return ProposerOutput(
            proposals=dedupe(proposals),
            parse=stats,
            prompts=[prompt],
            completions=[completion],
            seconds=round(time.perf_counter() - start, 3),
        )


# --------------------------------------------------------------------------
# heuristic proposer
# --------------------------------------------------------------------------


def _key_column(t: TableEvidence, use_stats: bool) -> str | None:
    """Best single-column key guess for a table."""
    stem = names.table_stem(t.name)
    scored = []
    for i, c in enumerate(t.columns):
        unique = is_unique_column(t, c) if use_stats else True
        if not unique:
            continue
        score = 0.0
        if names.keyish(c.name):
            score += 0.5
        if names.similarity(names.core(c.name, t.name), stem) > 0.6:
            score += 0.3
        if i == 0:
            score += 0.2
        if use_stats:
            score += 0.2
        scored.append((score, -i, c.name))
    scored.sort(reverse=True)
    if scored and scored[0][0] >= (0.4 if use_stats else 0.5):
        return scored[0][2]
    return None


def _same_family(a: str, b: str) -> bool:
    numeric = {"integer", "numeric"}
    return a == b or (a in numeric and b in numeric)


class HeuristicProposer:
    """Name- and statistics-based rules; honours the same evidence channels."""

    def __init__(self, channels: list[str] | None = None, fk_threshold: float = 0.75) -> None:
        self.channels = set(channels or ["names", "stats", "samples", "logs"])
        self.fk_threshold = fk_threshold

    def propose(self, evidence: Evidence, joins: JoinStats | None) -> ProposerOutput:
        start = time.perf_counter()
        use_stats = "stats" in self.channels
        use_samples = "samples" in self.channels
        join_pairs = (
            {p.as_tuple() for p in joins.pairs} if joins and "logs" in self.channels else set()
        )
        out: list[Proposal] = []
        keys: dict[str, str] = {}
        for t in evidence.tables:
            key = _key_column(t, use_stats)
            if key:
                keys[t.name] = key
                out.append(
                    Proposal(
                        type="UCC",
                        lhs=[qualify(t.name, key)],
                        confidence=0.8,
                        source="heuristic",
                        rationale="key-like unique column",
                    )
                )
            else:
                keyish = [c.name for c in t.columns if names.keyish(c.name)][:2]
                if len(keyish) == 2:
                    out.append(
                        Proposal(
                            type="UCC",
                            lhs=[qualify(t.name, c) for c in keyish],
                            confidence=0.55,
                            source="heuristic",
                            rationale="composite of key-like columns",
                        )
                    )

        for t in evidence.tables:
            for c in t.columns:
                if keys.get(t.name) == c.name and not names.keyish(c.name):
                    continue
                best: tuple[float, str, str, bool] | None = None
                for r in evidence.tables:
                    rkey = keys.get(r.name)
                    if not rkey or (r.name == t.name and rkey == c.name):
                        continue
                    kcol = r.column(rkey)
                    score = names.fk_name_score(t.name, c.name, r.name, rkey)
                    if use_stats:
                        if not _same_family(c.family, kcol.family):
                            continue
                        # dirty data adds a few orphan values: tolerate small excesses
                        if c.distinct_count > 1.1 * kcol.distinct_count + 1:
                            continue
                        if c.family in ("integer", "numeric") and None not in (c.min, kcol.min):
                            try:
                                if c.min < kcol.min:
                                    score -= 0.15
                            except TypeError:
                                pass
                    pair = tuple(sorted((qualify(t.name, c.name), qualify(r.name, rkey))))
                    logged = pair in join_pairs
                    if logged:
                        score = max(score, 0.9)
                    if best is None or score > best[0]:
                        best = (score, r.name, rkey, logged)
                if best and best[0] >= self.fk_threshold:
                    why = "name similarity" + (" + join log" if best[3] else "")
                    out.append(
                        Proposal(
                            type="FK",
                            lhs=[qualify(t.name, c.name)],
                            rhs=[qualify(best[1], best[2])],
                            confidence=round(min(1.0, best[0]), 3),
                            source="heuristic",
                            rationale=why,
                        )
                    )

            out.extend(self._fds(t, keys.get(t.name), use_samples))
        return ProposerOutput(proposals=dedupe(out), seconds=round(time.perf_counter() - start, 3))

    @staticmethod
    def _fds(t: TableEvidence, key: str | None, use_samples: bool) -> list[Proposal]:
        out = []
        for a, b in itertools.permutations(t.columns, 2):
            if a.name == key or b.name == key or not names.keyish(a.name):
                continue
            a_core = names.core(a.name, t.name)
            if not a_core or a_core not in names.tokens(names.strip_column_prefix(b.name, t.name)):
                continue
            if names.keyish(b.name):
                continue
            if use_samples and t.sample_rows:
                ia, ib = t.column_names.index(a.name), t.column_names.index(b.name)
                seen: dict[object, object] = {}
                if any(seen.setdefault(r[ia], r[ib]) != r[ib] for r in t.sample_rows):
                    continue
            out.append(
                Proposal(
                    type="FD",
                    lhs=[qualify(t.name, a.name)],
                    rhs=[qualify(t.name, b.name)],
                    confidence=0.6,
                    source="heuristic",
                    rationale="code column determines its description",
                )
            )
        return out


# --------------------------------------------------------------------------
# hybrid ranking (RQ5)
# --------------------------------------------------------------------------


def heuristic_score(p: Proposal) -> float:
    if p.type == "FK":
        scores = [
            names.fk_name_score(*split_ref(a), *split_ref(b))
            for a, b in zip(p.lhs, p.rhs, strict=True)
        ]
        return round(sum(scores) / len(scores), 3)
    if p.type == "UCC":
        table = p.table
        keyish = sum(names.keyish(split_ref(r)[1]) for r in p.lhs) / len(p.lhs)
        stem = names.table_stem(table)
        named = max(names.similarity(names.core(split_ref(r)[1], table), stem) for r in p.lhs)
        return round(0.5 * keyish + 0.3 * named + 0.2 / len(p.lhs), 3)
    table = p.table
    lhs_cores = {names.core(split_ref(r)[1], table) for r in p.lhs}
    rhs_tokens = set(names.tokens(names.strip_column_prefix(split_ref(p.rhs[0])[1], table)))
    shared = bool(lhs_cores & rhs_tokens)
    keyish = all(names.keyish(split_ref(r)[1]) for r in p.lhs)
    return round(0.5 * shared + 0.3 * keyish + 0.2 * p.confidence, 3)


class HybridRanker:
    """Re-rank baseline candidates and keep those scored above ``threshold``."""

    def __init__(
        self,
        scorer: Literal["llm", "heuristic"] = "heuristic",
        client: ChatClient | None = None,
        prompt_config: PromptConfig | None = None,
        threshold: float = 0.5,
        max_candidates: int = 150,
    ) -> None:
        if scorer == "llm" and client is None:
            raise ValueError("an LLM scorer needs a client")
        self.scorer = scorer
        self.client = client
        self.prompt_config = prompt_config or PromptConfig()
        self.threshold = threshold
        self.max_candidates = max_candidates

    def shortlist(self, candidates: list[Proposal]) -> list[Proposal]:
        """Drop key-implied FDs and keep the top candidates per type by baseline score."""
        cands = [p for p in dedupe(candidates) if not (p.type == "FD" and p.confidence <= 0)]
        per_type = max(1, self.max_candidates // 3)
        out: list[Proposal] = []
        for ctype in ("FK", "UCC", "FD"):
            typed = sorted((p for p in cands if p.type == ctype), key=lambda p: -p.confidence)
            out.extend(typed[:per_type])
        return out

    def rank(
        self, evidence: Evidence, joins: JoinStats | None, candidates: list[Proposal]
    ) -> ProposerOutput:
        start = time.perf_counter()
        shortlist = self.shortlist(candidates)
        output = ProposerOutput()
        if self.scorer == "heuristic":
            scores = {i: heuristic_score(p) for i, p in enumerate(shortlist)}
        else:
            assert self.client is not None
            prompt = build_ranking_prompt(
                evidence, [p.describe() for p in shortlist], joins, self.prompt_config
            )
            completion = self.client.complete(prompt, None)
            scores, output.parse = parse_ranking(completion.content, len(shortlist))
            output.prompts.append(prompt)
            output.completions.append(completion)
        output.proposals = [
            p.model_copy(
                update={
                    "confidence": scores[i],
                    "source": "hybrid",
                    "rationale": f"{p.rationale}; ranked by {self.scorer}",
                }
            )
            for i, p in enumerate(shortlist)
            if scores.get(i, 0.0) >= self.threshold
        ]
        output.seconds = round(time.perf_counter() - start, 3)
        return output
