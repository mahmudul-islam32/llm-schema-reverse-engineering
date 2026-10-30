"""SQL verifier for hybrid mode (F-08).

Each proposal is checked against the data with SQL and gets a
:class:`~srev.model.Verdict` that stores the measured statistic:

* FK  - *inclusion coverage*: share of non-NULL referencing rows whose values
  exist on the referenced side (the distinct-value share is recorded too); the
  referenced side's uniqueness must pass the UCC threshold as well.
* UCC - *uniqueness*: distinct tuples / rows (rows with NULLs excluded, as in
  SQL ``UNIQUE``).
* FD  - *g3 error*: the minimum share of rows to delete so the FD holds
  exactly (Kivinen & Mannila). 0 means the FD holds.

References to tables or columns that do not exist are rejected before any
SQL runs (``invalid_reference``), which is the main hallucination signal.
"""

from __future__ import annotations

from pydantic import BaseModel

from srev.db import Connector, DatabaseError
from srev.model import Proposal, Verdict, VerifiedProposal, split_ref


class VerifyConfig(BaseModel):
    fk_min_coverage: float = 0.95
    ucc_min_uniqueness: float = 0.99
    fd_max_g3: float = 0.05
    min_rows: int = 1


class Verifier:
    def __init__(self, conn: Connector, config: VerifyConfig | None = None) -> None:
        self.conn = conn
        self.config = config or VerifyConfig()
        self.schema = {t: {c.name for c in conn.list_columns(t)} for t in conn.list_tables()}

    # -- helpers ---------------------------------------------------------
    def missing_refs(self, p: Proposal) -> list[str]:
        missing = []
        for ref in p.columns():
            table, column = split_ref(ref)
            if table not in self.schema or column not in self.schema[table]:
                missing.append(ref)
        return missing

    def _cols(self, refs: list[str]) -> list[str]:
        return [self.conn.quote(split_ref(r)[1]) for r in refs]

    def _scalar(self, sql: str) -> float:
        value = self.conn.query(sql)[0][0]
        return float(value or 0)

    # -- checks ----------------------------------------------------------
    def uniqueness(self, refs: list[str]) -> tuple[float, int]:
        table = self.conn.qtable(split_ref(refs[0])[0])
        cols = self._cols(refs)
        not_null = " AND ".join(f"{c} IS NOT NULL" for c in cols)
        rows = int(self._scalar(f"SELECT COUNT(*) FROM {table} WHERE {not_null}"))
        if rows == 0:
            return 0.0, 0
        distinct = self._scalar(
            f"SELECT COUNT(*) FROM (SELECT DISTINCT {', '.join(cols)} FROM {table} "
            f"WHERE {not_null}) d"
        )
        return distinct / rows, rows

    def fk_coverage(self, p: Proposal, cast: bool = False) -> dict[str, float]:
        """Row- and value-level inclusion of the referencing columns in the referenced ones.

        ``rows``/``row_coverage``: non-NULL referencing rows and the share that find a
        match. ``values``/``value_coverage``: the same over distinct referencing tuples,
        which is much more sensitive to a handful of unique orphan values.
        """
        child = self.conn.qtable(split_ref(p.lhs[0])[0])
        parent = self.conn.qtable(split_ref(p.rhs[0])[0])
        lcols, rcols = self._cols(p.lhs), self._cols(p.rhs)

        def c(expr: str) -> str:
            return f"CAST({expr} AS TEXT)" if cast else expr

        not_null = " AND ".join(f"{x} IS NOT NULL" for x in lcols)
        cond = " AND ".join(
            f"{c('d.' + x)} = {c('p.' + y)}" for x, y in zip(lcols, rcols, strict=True)
        )
        exists = f"EXISTS (SELECT 1 FROM {parent} p WHERE {cond})"
        rows_sql = f"SELECT {', '.join(lcols)} FROM {child} WHERE {not_null}"
        dist_sql = f"SELECT DISTINCT {', '.join(lcols)} FROM {child} WHERE {not_null}"
        rows = self._scalar(f"SELECT COUNT(*) FROM ({rows_sql}) d")
        if rows == 0:
            return {"rows": 0, "values": 0, "row_coverage": 0.0, "value_coverage": 0.0}
        values = self._scalar(f"SELECT COUNT(*) FROM ({dist_sql}) d")
        rows_ok = self._scalar(f"SELECT COUNT(*) FROM ({rows_sql}) d WHERE {exists}")
        values_ok = self._scalar(f"SELECT COUNT(*) FROM ({dist_sql}) d WHERE {exists}")
        return {
            "rows": rows,
            "values": values,
            "row_coverage": rows_ok / rows,
            "value_coverage": values_ok / values,
        }

    def g3(self, p: Proposal) -> tuple[float, int]:
        table = self.conn.qtable(p.table)
        lcols, rcol = self._cols(p.lhs), self._cols(p.rhs)[0]
        rows = int(self._scalar(f"SELECT COUNT(*) FROM {table}"))
        if rows == 0:
            return 1.0, 0
        group = ", ".join(lcols)
        kept = self._scalar(
            f"SELECT SUM(mx) FROM (SELECT MAX(n) AS mx FROM (SELECT {group}, {rcol}, COUNT(*) AS n "
            f"FROM {table} GROUP BY {group}, {rcol}) x GROUP BY {group}) y"
        )
        return (rows - kept) / rows, rows

    # -- entry points ------------------------------------------------------
    def verify(self, p: Proposal) -> Verdict:
        missing = self.missing_refs(p)
        if missing:
            return Verdict(
                accepted=False,
                status="invalid_reference",
                message=f"unknown columns: {', '.join(missing)}",
            )
        cfg = self.config
        try:
            if p.type == "UCC":
                value, rows = self.uniqueness(p.lhs)
                if rows < cfg.min_rows:
                    return Verdict(accepted=False, status="empty", statistic="uniqueness")
                ok = value >= cfg.ucc_min_uniqueness
                return Verdict(
                    accepted=ok,
                    status="ok" if ok else "rejected",
                    statistic="uniqueness",
                    value=round(value, 6),
                    details={"rows": rows},
                )
            if p.type == "FK":
                try:
                    cov = self.fk_coverage(p)
                except DatabaseError:
                    # type mismatch (e.g. integer = text in PostgreSQL): compare as text
                    cov = self.fk_coverage(p, cast=True)
                if cov["rows"] < cfg.min_rows:
                    return Verdict(accepted=False, status="empty", statistic="coverage")
                ref_unique, _ = self.uniqueness(p.rhs)
                value = cov["row_coverage"]
                ok = value >= cfg.fk_min_coverage and ref_unique >= cfg.ucc_min_uniqueness
                return Verdict(
                    accepted=ok,
                    status="ok" if ok else "rejected",
                    statistic="coverage",
                    value=round(value, 6),
                    details={
                        "rows": int(cov["rows"]),
                        "distinct_values": int(cov["values"]),
                        "value_coverage": round(cov["value_coverage"], 6),
                        "referenced_uniqueness": round(ref_unique, 6),
                    },
                )
            value, rows = self.g3(p)
            if rows < cfg.min_rows:
                return Verdict(accepted=False, status="empty", statistic="g3")
            ok = value <= cfg.fd_max_g3
            return Verdict(
                accepted=ok,
                status="ok" if ok else "rejected",
                statistic="g3",
                value=round(value, 6),
                details={"rows": rows},
            )
        except DatabaseError as exc:
            return Verdict(accepted=False, status="error", message=str(exc)[:300])

    def verify_all(self, proposals: list[Proposal]) -> list[VerifiedProposal]:
        return [VerifiedProposal(proposal=p, verdict=self.verify(p)) for p in proposals]


def accepted(verified: list[VerifiedProposal]) -> list[Proposal]:
    return [v.proposal for v in verified if v.verdict.accepted]
