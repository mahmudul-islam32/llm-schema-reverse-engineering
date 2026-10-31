"""Data-quality checks and drift detection from discovered constraints (F-21).

Once constraints are discovered (``srev discover``) they can serve as a
lightweight data contract. ``srev check`` re-measures every constraint on the
current data and compares it with the value stored at discovery time:

* ``pass``    - still holds and did not get worse
* ``warn``    - still accepted, but the statistic drifted by more than the tolerance
* ``fail``    - no longer holds (verifier rejects it)
* ``missing`` - a referenced table or column no longer exists (schema drift)
* ``error``   - the check itself failed (e.g. permissions)

With a baseline evidence file it also reports schema drift: added/removed
tables and columns, type changes and row-count changes. The exit code makes
the command usable as a CI or orchestration step.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

from srev.db import Connector
from srev.extract import Evidence
from srev.model import Proposal, VerifiedProposal
from srev.verify import Verifier, VerifyConfig

Status = Literal["pass", "warn", "fail", "missing", "error"]
_ORDER = {"pass": 0, "warn": 1, "fail": 2, "missing": 2, "error": 2}


class CheckResult(BaseModel):
    constraint: Proposal
    status: Status
    statistic: str = ""
    value: float | None = None
    baseline: float | None = None
    delta: float | None = None
    message: str = ""


class SchemaChange(BaseModel):
    kind: Literal[
        "table_added", "table_removed", "column_added", "column_removed", "type_changed",
        "row_count_changed",
    ]  # fmt: skip
    target: str
    detail: str = ""


class QualityReport(BaseModel):
    database: str
    checked_at: str = Field(default_factory=lambda: dt.datetime.now(dt.UTC).isoformat())
    results: list[CheckResult] = Field(default_factory=list)
    schema_changes: list[SchemaChange] = Field(default_factory=list)

    def summary(self) -> dict[str, int]:
        out = {s: 0 for s in ("pass", "warn", "fail", "missing", "error")}
        for r in self.results:
            out[r.status] += 1
        return out

    def exit_code(self, fail_on: Literal["fail", "warn"] = "fail") -> int:
        """0 = healthy, 1 = threshold reached (failures, or warnings with fail_on=warn)."""
        worst = max((_ORDER[r.status] for r in self.results), default=0)
        if self.schema_changes and any(
            c.kind in ("table_removed", "column_removed", "type_changed")
            for c in self.schema_changes
        ):
            worst = max(worst, 2)
        limit = 1 if fail_on == "warn" else 2
        return int(worst >= limit)


def _worse(statistic: str, value: float, baseline: float, tolerance: float) -> bool:
    if statistic == "g3":  # an error: higher is worse
        return value - baseline > tolerance
    return baseline - value > tolerance  # coverage / uniqueness: lower is worse


def run_checks(
    conn: Connector,
    constraints: list[VerifiedProposal],
    *,
    database: str = "database",
    config: VerifyConfig | None = None,
    tolerance: float = 0.01,
    baseline_evidence: Evidence | None = None,
) -> QualityReport:
    verifier = Verifier(conn, config)
    report = QualityReport(database=database)
    for vp in constraints:
        p, old = vp.proposal, vp.verdict
        new = verifier.verify(p)
        result = CheckResult(
            constraint=p, status="pass", statistic=new.statistic, value=new.value,
            baseline=old.value, message=new.message,
        )  # fmt: skip
        if new.value is not None and old.value is not None:
            result.delta = round(new.value - old.value, 6)
        if new.status == "invalid_reference":
            result.status = "missing"
        elif new.status == "error":
            result.status = "error"
        elif not new.accepted:
            result.status = "fail"
        elif (
            new.value is not None
            and old.value is not None
            and old.statistic == new.statistic
            and _worse(new.statistic, new.value, old.value, tolerance)
        ):
            result.status = "warn"
            result.message = f"{new.statistic} drifted from {old.value} to {new.value}"
        report.results.append(result)
    if baseline_evidence is not None:
        report.schema_changes = schema_drift(baseline_evidence, conn)
    return report


def schema_drift(
    baseline: Evidence, conn: Connector, row_change: float = 0.2
) -> list[SchemaChange]:
    """Compare the baseline evidence's schema (and row counts) with the live database."""
    changes: list[SchemaChange] = []
    old = {t.name: t for t in baseline.tables}
    live = set(conn.list_tables())
    for name in sorted(live - old.keys()):
        changes.append(SchemaChange(kind="table_added", target=name))
    for name in sorted(old.keys() - live):
        changes.append(SchemaChange(kind="table_removed", target=name))
    for name in sorted(live & old.keys()):
        old_cols = {c.name: c.type for c in old[name].columns}
        new_cols = {c.name: c.type for c in conn.list_columns(name)}
        for c in sorted(new_cols.keys() - old_cols.keys()):
            changes.append(SchemaChange(kind="column_added", target=f"{name}.{c}"))
        for c in sorted(old_cols.keys() - new_cols.keys()):
            changes.append(SchemaChange(kind="column_removed", target=f"{name}.{c}"))
        for c in sorted(old_cols.keys() & new_cols.keys()):
            if old_cols[c].lower() != new_cols[c].lower():
                changes.append(
                    SchemaChange(kind="type_changed", target=f"{name}.{c}",
                                 detail=f"{old_cols[c]} -> {new_cols[c]}")
                )  # fmt: skip
        rows = conn.query(f"SELECT COUNT(*) FROM {conn.qtable(name)}")[0][0]
        before = old[name].row_count
        if before and abs(rows - before) / before > row_change:
            changes.append(
                SchemaChange(kind="row_count_changed", target=name, detail=f"{before} -> {rows}")
            )
    return changes


_ICON = {"pass": "✅", "warn": "⚠️", "fail": "❌", "missing": "🚫", "error": "💥"}


def to_markdown(report: QualityReport) -> str:
    s = report.summary()
    lines = [
        f"# Data-quality check: {report.database}",
        "",
        f"Checked at {report.checked_at}. "
        + ", ".join(f"{_ICON[k]} {k} {v}" for k, v in s.items() if v),
        "",
    ]
    order = sorted(report.results, key=lambda r: (-_ORDER[r.status], r.constraint.describe()))
    lines += [
        "| status | constraint | statistic | now | at discovery | Δ |",
        "|---|---|---|---|---|---|",
    ]
    for r in order:
        lines.append(
            f"| {_ICON[r.status]} {r.status} | {r.constraint.describe()} | {r.statistic or '–'} | "
            f"{_fmt(r.value)} | {_fmt(r.baseline)} | {_fmt(r.delta)} |"
        )
    if report.schema_changes:
        lines += ["", "## Schema drift", "", "| change | target | detail |", "|---|---|---|"]
        lines += [f"| {c.kind} | {c.target} | {c.detail or ''} |" for c in report.schema_changes]
    return "\n".join(lines) + "\n"


def _fmt(v: float | None) -> str:
    return "–" if v is None else f"{v:.4g}"
