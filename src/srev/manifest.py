"""Reproducible run manifests (F-12).

A manifest records everything needed to re-run a result: git commit (and
whether the tree was dirty), the resolved config and its hash, dataset
checksums, model id and revision, seeds, hardware and package versions.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from srev import __version__

PACKAGES = ("pydantic", "sqlglot", "httpx", "PyYAML", "psycopg")


def _cmd(args: list[str], cwd: Path | None = None) -> str | None:
    try:
        out = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=10, check=True)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def git_info(cwd: Path | None = None) -> dict[str, Any]:
    sha = _cmd(["git", "rev-parse", "HEAD"], cwd)
    if sha is None:
        return {"sha": None}
    status = _cmd(["git", "status", "--porcelain", "--untracked-files=no"], cwd) or ""
    return {
        "sha": sha,
        "branch": _cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd),
        "dirty": bool(status),
    }


def hardware_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "cpu_count": os.cpu_count(),
        "python": sys.version.split()[0],
    }
    try:
        pages, size = os.sysconf("SC_PHYS_PAGES"), os.sysconf("SC_PAGE_SIZE")
        info["memory_gb"] = round(pages * size / 1e9, 1)
    except (ValueError, OSError, AttributeError):
        info["memory_gb"] = None
    if shutil.which("nvidia-smi"):
        gpus = _cmd(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"]
        )
        info["gpus"] = gpus.splitlines() if gpus else []
    else:
        info["gpus"] = []
    return info


def package_versions() -> dict[str, str | None]:
    out: dict[str, str | None] = {"srev": __version__}
    for pkg in PACKAGES:
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            out[pkg] = None
    return out


class DatasetRecord(BaseModel):
    db_id: str
    dataset: str
    noise: str
    clean_checksum: str
    noisy_checksum: str
    rows: int
    tables: int
    gold_counts: dict[str, int]
    noise_summary: dict[str, int] = Field(default_factory=dict)


class Manifest(BaseModel):
    srev_version: str = __version__
    run_id: str
    started_at: str
    finished_at: str | None = None
    command: list[str] = Field(default_factory=lambda: list(sys.argv))
    git: dict[str, Any] = Field(default_factory=git_info)
    config: dict[str, Any]
    config_sha256: str
    seed: int
    datasets: list[DatasetRecord] = Field(default_factory=list)
    llm: dict[str, Any] = Field(default_factory=dict)
    hardware: dict[str, Any] = Field(default_factory=hardware_info)
    packages: dict[str, str | None] = Field(default_factory=package_versions)
    notes: list[str] = Field(default_factory=list)

    @classmethod
    def start(cls, run_id: str, config: dict[str, Any], seed: int) -> Manifest:
        blob = json.dumps(config, sort_keys=True, default=str).encode()
        return cls(
            run_id=run_id,
            started_at=dt.datetime.now(dt.UTC).isoformat(),
            config=config,
            config_sha256=hashlib.sha256(blob).hexdigest(),
            seed=seed,
        )

    def finish(self) -> None:
        self.finished_at = dt.datetime.now(dt.UTC).isoformat()

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2))

    @classmethod
    def load(cls, path: str | Path) -> Manifest:
        return cls.model_validate_json(Path(path).read_text())
