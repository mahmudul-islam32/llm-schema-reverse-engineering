"""Experiment configuration (YAML), validated with Pydantic.

``${VAR}`` and ``${VAR:-default}`` in the YAML are replaced from the
environment before parsing, so DSNs and endpoints need not be committed.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from srev.baselines.metanome import MetanomeConfig
from srev.baselines.profiling import ProfilingConfig
from srev.learn import LearnedConfig
from srev.llm.client import LLMConfig
from srev.llm.prompt import CHANNELS, PromptConfig
from srev.noise import NoiseConfig
from srev.verify import VerifyConfig

_NO_NOISE: list[str | int | NoiseConfig] = ["none"]
_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class DatasetSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    source: Literal["builtin", "postgres", "sqlite", "spider"] = "builtin"
    generator: str | None = Field(None, description="built-in generator (defaults to name)")
    params: dict[str, Any] = Field(default_factory=dict)
    dsn: str | None = None
    db_schema: str = Field("public", alias="schema")
    path: str | None = Field(
        None, description="SQLite file (source: sqlite) or benchmark root (source: spider)"
    )
    workload: str | None = Field(None, description=".sql/.csv/.jsonl query log")
    curated_gold: str | None = Field(None, description="extra curated gold labels (JSON)")
    max_rows: int | None = None
    # source: spider (Spider/BIRD layout); one spec expands to one dataset per db_id
    db_ids: list[str] | None = Field(None, description="databases to use (default: all)")
    limit: int | None = Field(None, description="first N databases in db_id order")
    tables_json: str | None = Field(None, description="annotation file (default tables.json)")
    db_dir: str = Field("database", description="directory holding <db_id>/<db_id>.sqlite")
    queries_json: str | None = Field(None, description="gold SQL file used as the workload")
    db_id: str | None = Field(None, description="set by the expansion; do not use directly")

    def expand(self) -> list[DatasetSpec]:
        if self.source != "spider" or self.db_id:
            return [self]
        if not self.path:
            raise ValueError(f"dataset {self.name}: source spider needs path")
        from srev.datasets.spider import list_databases

        ids = self.db_ids or [d["db_id"] for d in list_databases(self.path, self.tables_json)]
        if self.limit:
            ids = ids[: self.limit]
        return [
            self.model_copy(update={"db_id": db_id, "name": f"{self.name}_{db_id}"})
            for db_id in ids
        ]


class DatabaseSpec(BaseModel):
    backend: Literal["sqlite", "postgres"] = "sqlite"
    dsn: str | None = None
    schema_prefix: str = "srev"


class MethodSpec(BaseModel):
    name: str
    kind: Literal["llm", "heuristic", "baseline", "metanome", "hybrid", "learned"]
    evidence: list[list[str]] = Field(
        default_factory=lambda: [["names", "stats", "samples", "logs"]]
    )
    paraphrases: list[int] = Field(default_factory=lambda: [0])
    seeds: list[int] | None = Field(None, description="LLM seeds (defaults to llm.seed)")
    verify: bool = True
    llm: LLMConfig | None = None
    profiling: ProfilingConfig = Field(default_factory=ProfilingConfig)
    metanome: MetanomeConfig = Field(default_factory=MetanomeConfig)
    learned: LearnedConfig = Field(default_factory=LearnedConfig)
    scorer: Literal["llm", "heuristic"] = "heuristic"
    threshold: float = 0.5
    max_candidates: int = 150

    @model_validator(mode="after")
    def _check(self) -> MethodSpec:
        for combo in self.evidence:
            bad = set(combo) - set(CHANNELS)
            if bad:
                raise ValueError(f"method {self.name}: unknown evidence channels {sorted(bad)}")
        return self


class SampleSpec(BaseModel):
    rows_per_table: int = 50
    top_k: int = 5


class EvalSpec(BaseModel):
    bootstrap: int = 1000
    top_k_factor: float = 1.0
    fd_gold: Literal["curated", "exact", "both"] = "curated"
    exact_fd_max_lhs: int = 2


class SynthSpec(BaseModel):
    enabled: bool = True
    variant: str | None = Field(
        None,
        description="variant to synthesise from "
        "(default: first verified variant of the richest method kind)",
    )
    normal_form: Literal["bcnf", "none"] = "bcnf"


class ExperimentConfig(BaseModel):
    name: str = "experiment"
    seed: int = 42
    output_dir: str = "runs"
    database: DatabaseSpec = Field(default_factory=DatabaseSpec)
    datasets: list[DatasetSpec]
    noise: list[str | int | NoiseConfig] = Field(default_factory=lambda: _NO_NOISE.copy())
    methods: list[MethodSpec]
    llm: LLMConfig = Field(default_factory=LLMConfig)
    sample: SampleSpec = Field(default_factory=SampleSpec)
    prompt: PromptConfig = Field(default_factory=PromptConfig)
    verify: VerifyConfig = Field(default_factory=VerifyConfig)
    eval: EvalSpec = Field(default_factory=EvalSpec)
    synth: SynthSpec = Field(default_factory=SynthSpec)

    @model_validator(mode="after")
    def _unique_names(self) -> ExperimentConfig:
        names = [m.name for m in self.methods]
        if len(names) != len(set(names)):
            raise ValueError("method names must be unique")
        return self


def expand_env(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        value = os.environ.get(m.group(1))
        if value is None:
            if m.group(2) is None:
                raise KeyError(f"environment variable {m.group(1)} is not set")
            return m.group(2)
        return value

    # comment lines are left alone so commented-out examples need no variables
    return "".join(
        line if line.lstrip().startswith("#") else _ENV.sub(repl, line)
        for line in text.splitlines(keepends=True)
    )


def load_config(path: str | Path) -> ExperimentConfig:
    raw = yaml.safe_load(expand_env(Path(path).read_text()))
    return ExperimentConfig.model_validate(raw)
