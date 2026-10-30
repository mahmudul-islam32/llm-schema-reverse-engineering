"""Metanome baseline runner (F-07): HyFD, HyUCC and BINDER via ``metanome-cli``.

Metanome is a Java platform; this module exports the working tables to CSV,
calls the Metanome command-line client with the configured algorithm JAR, and
converts the JSON result lines into the common :class:`~srev.model.Proposal`
format. FK candidates are INDs whose referenced side is a discovered UCC.

You need Java plus ``metanome-cli.jar`` and the algorithm JARs (build them from
the HPI ``metanome-cli`` and ``metanome-algorithms`` repositories and pin the
commit in the run config). The CLI flags follow ``metanome-cli``'s documented
usage; check them against the version you build, and use the Python profilers
in :mod:`srev.baselines.profiling` when Metanome is not available.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from srev.db import Connector
from srev.model import Proposal, qualify

ALGORITHMS: dict[str, dict[str, str]] = {
    "hyfd": {
        "class": "de.metanome.algorithms.hyfd.HyFD",
        "file_key": "INPUT_GENERATOR",
        "kind": "fds",
    },
    "hyucc": {
        "class": "de.metanome.algorithms.hyucc.HyUCC",
        "file_key": "INPUT_GENERATOR",
        "kind": "uccs",
    },
    "binder": {
        "class": "de.metanome.algorithms.binder.BINDERFile",
        "file_key": "INPUT_FILES",
        "kind": "inds",
    },
}


class MetanomeConfig(BaseModel):
    cli_jar: str = "lib/metanome-cli.jar"
    algorithm_jars: dict[str, str] = Field(
        default_factory=lambda: {
            "hyfd": "lib/HyFD.jar",
            "hyucc": "lib/HyUCC.jar",
            "binder": "lib/BINDER.jar",
        }
    )
    algorithms: list[str] = Field(default_factory=lambda: ["hyfd", "hyucc", "binder"])
    java: str = "java"
    java_opts: list[str] = Field(default_factory=lambda: ["-Xmx4g"])
    timeout_s: int = 3600
    extra_args: dict[str, list[str]] = Field(default_factory=dict)


class MetanomeRun(BaseModel):
    algorithm: str
    seconds: float
    returncode: int
    max_rss_kb: int | None = None
    n_results: int = 0


class MetanomeUnavailable(RuntimeError):
    pass


def export_csv(conn: Connector, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for table in conn.list_tables():
        cols = [c.name for c in conn.list_columns(table)]
        rows = conn.query(
            f"SELECT {', '.join(conn.quote(c) for c in cols)} FROM {conn.qtable(table)}"
        )
        path = out_dir / f"{table}.csv"
        with path.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(cols)
            writer.writerows(["" if v is None else v for v in r] for r in rows)
        files.append(path)
    return files


def build_command(config: MetanomeConfig, algorithm: str, files: list[Path], tag: str) -> list[str]:
    spec = ALGORITHMS[algorithm]
    classpath = os.pathsep.join([config.cli_jar, config.algorithm_jars[algorithm]])
    return [
        config.java,
        *config.java_opts,
        "-cp",
        classpath,
        "de.metanome.cli.App",
        "--algorithm",
        spec["class"],
        "--files",
        *[str(f) for f in files],
        "--file-key",
        spec["file_key"],
        "--separator",
        ",",
        "--header",
        "--output",
        f"file:{tag}",
        *config.extra_args.get(algorithm, []),
    ]


def _table(identifier: dict[str, Any]) -> str:
    return Path(str(identifier["tableIdentifier"])).stem.lower()


def _refs(combo: dict[str, Any]) -> list[str]:
    return [
        qualify(_table(c), str(c["columnIdentifier"])) for c in combo.get("columnIdentifiers", [])
    ]


def parse_results(
    lines: list[str],
) -> tuple[list[Proposal], list[Proposal], list[tuple[list[str], list[str]]], int]:
    """Parse Metanome JSON result lines into (UCCs, FDs, INDs, n_unparsed)."""
    uccs: list[Proposal] = []
    fds: list[Proposal] = []
    inds: list[tuple[list[str], list[str]]] = []
    bad = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            kind = obj.get("type", "")
            if kind == "UniqueColumnCombination" or "columnCombination" in obj:
                uccs.append(
                    Proposal(
                        type="UCC",
                        lhs=_refs(obj["columnCombination"]),
                        source="baseline",
                        rationale="Metanome UCC",
                    )
                )
            elif kind == "FunctionalDependency" or "determinant" in obj:
                lhs = _refs(obj["determinant"])
                dep = obj["dependant"]
                rhs = qualify(_table(dep), str(dep["columnIdentifier"]))
                if lhs:  # Metanome reports {} -> A for constant columns; skip those
                    fds.append(
                        Proposal(
                            type="FD",
                            lhs=lhs,
                            rhs=[rhs],
                            source="baseline",
                            rationale="Metanome FD",
                        )
                    )
            elif kind == "InclusionDependency" or "referenced" in obj:
                inds.append((_refs(obj["dependant"]), _refs(obj["referenced"])))
            else:
                bad += 1
        except (ValueError, KeyError, TypeError):
            bad += 1
    return uccs, fds, inds, bad


def fks_from_inds(inds: list[tuple[list[str], list[str]]], uccs: list[Proposal]) -> list[Proposal]:
    keys = {tuple(sorted(u.lhs)) for u in uccs}
    out = []
    for dep, ref in inds:
        if dep and len(dep) == len(ref) and tuple(sorted(ref)) in keys and dep != ref:
            try:
                out.append(
                    Proposal(
                        type="FK",
                        lhs=dep,
                        rhs=ref,
                        source="baseline",
                        rationale="Metanome IND into UCC",
                    )
                )
            except ValueError:
                continue
    return out


def run(
    conn: Connector, work_dir: Path, config: MetanomeConfig
) -> tuple[list[Proposal], list[MetanomeRun]]:
    if shutil.which(config.java) is None:
        raise MetanomeUnavailable(f"Java executable {config.java!r} not found")
    missing = [p for p in [config.cli_jar, *config.algorithm_jars.values()] if not Path(p).exists()]
    if missing:
        raise MetanomeUnavailable(f"Metanome JARs not found: {', '.join(missing)}")
    files = export_csv(conn, work_dir / "csv")
    runs: list[MetanomeRun] = []
    uccs: list[Proposal] = []
    fds: list[Proposal] = []
    inds: list[tuple[list[str], list[str]]] = []
    for algorithm in config.algorithms:
        tag = f"srev_{algorithm}"
        cmd = build_command(config, algorithm, files, tag)
        start = time.perf_counter()
        proc = subprocess.run(
            ["/usr/bin/time", "-f", "%M", *cmd] if Path("/usr/bin/time").exists() else cmd,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=config.timeout_s,
            check=False,
        )
        seconds = time.perf_counter() - start
        rss = None
        tail = proc.stderr.strip().splitlines()[-1:] if proc.stderr else []
        if tail and tail[0].isdigit():
            rss = int(tail[0])
        result_files = sorted((work_dir / "results").glob(f"{tag}*"))
        lines = [ln for f in result_files for ln in f.read_text().splitlines()]
        u, f_, i, _ = parse_results(lines)
        uccs += u
        fds += f_
        inds += i
        runs.append(
            MetanomeRun(
                algorithm=algorithm,
                seconds=round(seconds, 3),
                returncode=proc.returncode,
                max_rss_kb=rss,
                n_results=len(u) + len(f_) + len(i),
            )
        )
    return uccs + fds + fks_from_inds(inds, uccs), runs
