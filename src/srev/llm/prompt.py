"""Prompt builder with evidence ablation (F-05).

Evidence channels:

* ``names``   - table and column names with types (always included)
* ``stats``   - column statistics (distinct/null counts, min/max, top values)
* ``samples`` - sampled rows
* ``logs``    - equi-join pairs mined from the query workload

Prompts are trimmed to a token budget (samples first, then top-k values, then
statistics) and identified by a SHA-256 hash so every stored result can be
traced back to the exact prompt text. ``paraphrase`` selects one of several
instruction wordings to measure prompt sensitivity.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field

from srev.extract import Evidence, TableEvidence
from srev.qlog import JoinStats

CHANNELS = ("names", "stats", "samples", "logs")

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["FK", "UCC", "FD"]},
                    "lhs": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "rhs": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string"},
                },
                "required": ["type", "lhs", "rhs", "confidence", "rationale"],
            },
        }
    },
    "required": ["proposals"],
}

_FORMAT = """Return ONLY a JSON object of this form, with no markdown and no extra text:
{"proposals": [
  {"type": "FK", "lhs": ["child_table.col"], "rhs": ["parent_table.col"], "confidence": 0.9, "rationale": "..."},
  {"type": "UCC", "lhs": ["table.col"], "rhs": [], "confidence": 0.8, "rationale": "..."},
  {"type": "FD", "lhs": ["table.col_a"], "rhs": ["table.col_b"], "confidence": 0.7, "rationale": "..."}
]}
Rules: always write columns as "table.column" using the exact names given. FK lhs = referencing
columns, rhs = referenced (key) columns, same length and order. UCC rhs is []. FD lhs and rhs must
be in the same table, rhs has one column, and do not list FDs whose lhs is a unique key of the
table (those are implied). Confidence is your probability (0..1) that the constraint is intended
by the schema designer, not merely true by accident in this data."""  # noqa: E501

INSTRUCTIONS = [
    (
        "You are an expert database reverse engineer. The database below has no declared "
        "constraints. Infer its intended primary keys and other unique column combinations "
        "(UCC), foreign keys (FK) and non-key functional dependencies (FD)."
    ),
    (
        "You are helping to document a legacy relational database whose keys and relationships "
        "were never declared. From the evidence, identify unique column combinations (UCC, "
        "including primary keys), foreign-key references (FK) and functional dependencies (FD) "
        "between non-key columns that indicate denormalisation."
    ),
    (
        "Task: recover the implicit relational schema. List every unique column combination "
        "(UCC), every foreign key (FK) and every semantically meaningful functional "
        "dependency (FD) that the designer most likely intended."
    ),
]

RANK_INSTRUCTIONS = (
    "You are an expert database reverse engineer. A statistical profiler found the candidate "
    "constraints listed below; all of them hold in the data, but many hold only by accident. "
    "For each candidate, estimate the probability that it is an intended constraint of the "
    "schema (a real key, a real foreign-key relationship, or a semantic dependency)."
)
RANK_FORMAT = """Return ONLY a JSON object: {"ranking": [{"id": 0, "confidence": 0.9}, ...]}
with one entry per candidate id you consider (omitted ids count as confidence 0)."""


class PromptConfig(BaseModel):
    channels: list[str] = Field(default_factory=lambda: ["names", "stats", "samples", "logs"])
    token_budget: int = 8000
    paraphrase: int = 0
    max_sample_rows: int = 10
    max_join_pairs: int = 60
    max_value_chars: int = 40


class Prompt(BaseModel):
    system: str
    user: str
    channels: list[str]
    paraphrase: int
    sha256: str
    est_tokens: int
    trimmed: list[str] = Field(default_factory=list)

    def messages(self) -> list[dict[str, str]]:
        return [{"role": "system", "content": self.system}, {"role": "user", "content": self.user}]


def estimate_tokens(text: str) -> int:
    """Rough token count (~4 characters per token) used for budgeting only."""
    return max(1, len(text) // 4)


def _fmt_value(v: Any, width: int) -> str:
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s if len(s) <= width else s[: width - 1] + "…"


def _render_table(
    t: TableEvidence, channels: set[str], sample_rows: int, top_k: int, width: int
) -> str:
    lines = [f"TABLE {t.name} ({t.row_count} rows)"]
    if t.comment:
        lines.append(f"  -- {t.comment}")
    for c in t.columns:
        line = f"  - {c.name} {c.type}"
        if c.comment:
            line += f"  -- {c.comment}"
        if "stats" in channels:
            parts = [f"distinct={c.distinct_count}", f"nulls={c.null_count}"]
            if c.min is not None:
                parts.append(f"min={_fmt_value(c.min, width)}")
                parts.append(f"max={_fmt_value(c.max, width)}")
            if top_k and c.top_k:
                tops = ", ".join(f"{_fmt_value(v, width)}×{n}" for v, n in c.top_k[:top_k])
                parts.append(f"top=[{tops}]")
            line += "  [" + "; ".join(parts) + "]"
        lines.append(line)
    if "samples" in channels and sample_rows and t.sample_rows:
        lines.append("  sample rows (" + ", ".join(t.column_names) + "):")
        for row in t.sample_rows[:sample_rows]:
            lines.append("    (" + ", ".join(_fmt_value(v, width) for v in row) + ")")
    return "\n".join(lines)


def _render(
    evidence: Evidence,
    joins: JoinStats | None,
    channels: set[str],
    sample_rows: int,
    top_k: int,
    join_pairs: int,
    width: int,
) -> str:
    parts = [f"DATABASE {evidence.database}"]
    parts += [_render_table(t, channels, sample_rows, top_k, width) for t in evidence.tables]
    if "logs" in channels and joins and joins.pairs and join_pairs:
        lines = ["JOIN PREDICATES OBSERVED IN THE QUERY LOG (column = column: frequency):"]
        lines += [f"  {p.left} = {p.right}: {p.count}" for p in joins.pairs[:join_pairs]]
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _fit(
    render: Any, budget: int, cfg: PromptConfig, channels: set[str], overhead: int
) -> tuple[str, list[str]]:
    sample_rows, top_k, join_pairs = cfg.max_sample_rows, 5, cfg.max_join_pairs
    trimmed: list[str] = []
    text = render(channels, sample_rows, top_k, join_pairs)
    while estimate_tokens(text) + overhead > budget:
        if "samples" in channels and sample_rows > 1:
            sample_rows = max(1, sample_rows // 2)
            trimmed.append(f"samples->{sample_rows}")
        elif "samples" in channels:
            channels = channels - {"samples"}
            trimmed.append("samples dropped")
        elif "stats" in channels and top_k > 0:
            top_k = top_k - 2 if top_k > 2 else 0
            trimmed.append(f"top_k->{top_k}")
        elif "logs" in channels and join_pairs > 10:
            join_pairs //= 2
            trimmed.append(f"join_pairs->{join_pairs}")
        elif "stats" in channels:
            channels = channels - {"stats"}
            trimmed.append("stats dropped")
        else:
            trimmed.append("over budget with names only")
            break
        text = render(channels, sample_rows, top_k, join_pairs)
    return text, trimmed


def build_prompt(
    evidence: Evidence, joins: JoinStats | None = None, config: PromptConfig | None = None
) -> Prompt:
    cfg = config or PromptConfig()
    unknown = set(cfg.channels) - set(CHANNELS)
    if unknown:
        raise ValueError(f"unknown evidence channels: {sorted(unknown)}")
    channels = set(cfg.channels) | {"names"}
    system = INSTRUCTIONS[cfg.paraphrase % len(INSTRUCTIONS)] + "\n\n" + _FORMAT

    def render(ch: set[str], rows: int, top_k: int, pairs: int) -> str:
        return _render(evidence, joins, ch, rows, top_k, pairs, cfg.max_value_chars)

    body, trimmed = _fit(render, cfg.token_budget, cfg, channels, estimate_tokens(system))
    user = body + "\n\nList the constraints as JSON."
    digest = hashlib.sha256((system + "\x00" + user).encode()).hexdigest()
    return Prompt(
        system=system,
        user=user,
        channels=sorted(channels, key=CHANNELS.index),
        paraphrase=cfg.paraphrase % len(INSTRUCTIONS),
        sha256=digest,
        est_tokens=estimate_tokens(system) + estimate_tokens(user),
        trimmed=trimmed,
    )


def build_ranking_prompt(
    evidence: Evidence,
    candidates: list[str],
    joins: JoinStats | None = None,
    config: PromptConfig | None = None,
) -> Prompt:
    """Prompt for hybrid mode (RQ5): the LLM scores baseline candidates by id."""
    cfg = config or PromptConfig()
    channels = set(cfg.channels) | {"names"}
    listing = "\n".join(f"  [{i}] {c}" for i, c in enumerate(candidates))
    system = RANK_INSTRUCTIONS + "\n\n" + RANK_FORMAT
    overhead = estimate_tokens(system) + estimate_tokens(listing)

    def render(ch: set[str], rows: int, top_k: int, pairs: int) -> str:
        return _render(evidence, joins, ch, rows, top_k, pairs, cfg.max_value_chars)

    body, trimmed = _fit(render, cfg.token_budget, cfg, channels, overhead)
    user = body + "\n\nCANDIDATES:\n" + listing + "\n\nScore the candidates as JSON."
    digest = hashlib.sha256((system + "\x00" + user).encode()).hexdigest()
    return Prompt(
        system=system,
        user=user,
        channels=sorted(channels, key=CHANNELS.index),
        paraphrase=0,
        sha256=digest,
        est_tokens=estimate_tokens(system) + estimate_tokens(user),
        trimmed=trimmed,
    )
