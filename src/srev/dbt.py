"""Export discovered constraints as a dbt project (F-20).

Turns verified constraints into data tests a team can run in its pipeline:

* single-column keys   -> ``unique`` + ``not_null`` column tests
* unary foreign keys   -> ``relationships`` tests against the parent source
* composite keys, composite FKs and FDs -> singular SQL tests in ``tests/``
  (no extra dbt packages needed)

Constraints that hold only approximately (coverage < 1, uniqueness < 1,
g3 > 0) get ``severity: warn`` so known dirty data does not break builds.
Every test description names the srev evidence it came from. Uses the
``data_tests`` key introduced in dbt 1.8 (``tests_key="tests"`` for older)
and nests generic-test arguments under ``arguments:`` as dbt 1.10+ expects
(``legacy_test_args=True`` writes them inline for older versions).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from srev.extract import Evidence
from srev.model import Proposal, Verdict, VerifiedProposal, split_ref


def _approximate(v: Verdict | None) -> bool:
    if v is None or v.value is None:
        return False
    if v.statistic == "g3":
        return v.value > 0
    return v.value < 1


def _q(name: str) -> str:
    return name if name.replace("_", "").isalnum() and name == name.lower() else f'"{name}"'


class _Names:
    """Map lower-case proposal references back to the database's spelling."""

    def __init__(self, evidence: Evidence) -> None:
        self.tables = {t.name.lower(): t.name for t in evidence.tables}
        self.columns = {
            (t.name.lower(), c.name.lower()): c.name for t in evidence.tables for c in t.columns
        }

    def ref(self, ref: str) -> tuple[str, str]:
        t, c = split_ref(ref)
        return self.tables.get(t, t), self.columns.get((t, c), c)


def build_project(
    evidence: Evidence,
    constraints: list[VerifiedProposal],
    out_dir: str | Path,
    *,
    project: str | None = None,
    source: str = "raw",
    schema: str = "public",
    tests_key: str = "data_tests",
    legacy_test_args: bool = False,
    staging: bool = True,
) -> dict[str, int]:
    out = Path(out_dir)
    (out / "models" / "staging").mkdir(parents=True, exist_ok=True)
    (out / "tests").mkdir(parents=True, exist_ok=True)
    names = _Names(evidence)
    project = project or f"srev_{evidence.database}".lower().replace("-", "_")
    counts = {"column_tests": 0, "singular_tests": 0, "warn": 0}

    columns: dict[str, dict[str, list[Any]]] = {t.name: {} for t in evidence.tables}

    def col_test(table: str, column: str, test: Any, p: Proposal, v: Verdict | None) -> None:
        if _approximate(v):
            if isinstance(test, str):
                test = {test: {"config": {"severity": "warn"}}}
            else:
                key = next(iter(test))
                test = {key: {**test[key], "config": {"severity": "warn"}}}
            counts["warn"] += 1
        columns[table].setdefault(column, []).append(test)
        counts["column_tests"] += 1

    def singular(name: str, sql: str, p: Proposal, v: Verdict | None) -> None:
        stat = f" ({v.statistic}={v.value})" if v and v.value is not None else ""
        severity = "warn" if _approximate(v) else "error"
        if severity == "warn":
            counts["warn"] += 1
        header = (
            f"-- srev {p.describe()}{stat}\n-- evidence {p.ident()} [{p.source}]: "
            f"{p.rationale[:100]}\n{{{{ config(severity='{severity}') }}}}\n"
        )
        (out / "tests" / f"{name}.sql").write_text(header + sql.strip() + "\n")
        counts["singular_tests"] += 1

    def src(table: str) -> str:
        return f"{{{{ source('{source}', '{table}') }}}}"

    for vp in constraints:
        p, v = vp.proposal, vp.verdict
        tag = f"{p.ident()}"
        if p.type == "UCC" and len(p.lhs) == 1:
            t, c = names.ref(p.lhs[0])
            col_test(t, c, "unique", p, v)
            nulls = next(
                (col.null_count for tt in evidence.tables if tt.name == t for col in tt.columns
                 if col.name == c),
                0,
            )  # fmt: skip
            if nulls == 0:
                col_test(t, c, "not_null", p, v)
        elif p.type == "UCC":
            refs = [names.ref(r) for r in p.lhs]
            t = refs[0][0]
            cols = ", ".join(_q(c) for _, c in refs)
            singular(
                f"unique_{t}_{'_'.join(c for _, c in refs)}".lower()[:80] + f"_{tag}",
                f"select {cols}, count(*) as n\nfrom {src(t)}\n"
                f"where {' and '.join(f'{_q(c)} is not null' for _, c in refs)}\n"
                f"group by {cols}\nhaving count(*) > 1",
                p, v,
            )  # fmt: skip
        elif p.type == "FK" and len(p.lhs) == 1:
            (ct, cc), (pt, pc) = names.ref(p.lhs[0]), names.ref(p.rhs[0])
            rel_args = {"to": f"source('{source}', '{pt}')", "field": pc}
            body = rel_args if legacy_test_args else {"arguments": rel_args}
            col_test(ct, cc, {"relationships": body}, p, v)
        elif p.type == "FK":
            child = [names.ref(r) for r in p.lhs]
            parent = [names.ref(r) for r in p.rhs]
            ct, pt = child[0][0], parent[0][0]
            on = " and ".join(
                f"c.{_q(a)} = p.{_q(b)}" for (_, a), (_, b) in zip(child, parent, strict=True)
            )
            singular(
                f"fk_{ct}_{pt}".lower() + f"_{tag}",
                f"select c.*\nfrom {src(ct)} as c\nleft join {src(pt)} as p\n  on {on}\n"
                f"where {' and '.join(f'c.{_q(a)} is not null' for _, a in child)}\n"
                f"  and p.{_q(parent[0][1])} is null",
                p, v,
            )  # fmt: skip
        elif p.type == "FD":
            lhs = [names.ref(r) for r in p.lhs]
            t, rc = names.ref(p.rhs[0])
            cols = ", ".join(_q(c) for _, c in lhs)
            singular(
                f"fd_{t}_{'_'.join(c for _, c in lhs)}_{rc}".lower()[:80] + f"_{tag}",
                f"-- every {cols} must determine one {rc}\n"
                f"select {cols}, count(distinct {_q(rc)}) as variants\nfrom {src(t)}\n"
                f"group by {cols}\nhaving count(distinct {_q(rc)}) > 1",
                p, v,
            )  # fmt: skip

    sources = {
        "version": 2,
        "sources": [
            {
                "name": source,
                "schema": schema,
                "description": f"Tables profiled by srev ({evidence.database}).",
                "tables": [
                    {
                        "name": t.name,
                        "description": f"{t.row_count} rows at discovery time.",
                        "columns": [
                            {"name": c, tests_key: tests} for c, tests in columns[t.name].items()
                        ],
                    }
                    for t in evidence.tables
                ],
            }
        ],
    }
    (out / "models" / "sources.yml").write_text(yaml.safe_dump(sources, sort_keys=False))
    if staging:
        for table in evidence.tables:
            cols = ",\n    ".join(_q(c.name) for c in table.columns)
            (out / "models" / "staging" / f"stg_{table.name.lower()}.sql").write_text(
                f"-- staging model generated by srev\nselect\n    {cols}\nfrom {src(table.name)}\n"
            )
    (out / "dbt_project.yml").write_text(
        yaml.safe_dump(
            {
                "name": project,
                "version": "1.0.0",
                "config-version": 2,
                "profile": project,
                "model-paths": ["models"],
                "test-paths": ["tests"],
                "models": {project: {"staging": {"+materialized": "view"}}},
            },
            sort_keys=False,
        )
    )
    (out / "README.md").write_text(
        f"# {project}\n\nGenerated by `srev` from verified constraints of "
        f"`{evidence.database}`.\n\n"
        f"- {counts['column_tests']} column tests in `models/sources.yml`\n"
        f"- {counts['singular_tests']} singular tests in `tests/`\n"
        f"- {counts['warn']} tests with `severity: warn` (constraints that hold only "
        "approximately)\n\nAdd a profile named "
        f"`{project}` to `~/.dbt/profiles.yml`, then run `dbt test`.\n"
    )
    return counts
