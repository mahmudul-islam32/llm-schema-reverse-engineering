"""Normalised model and DDL synthesiser (F-11).

Turns accepted constraints into a reviewable target model:

1. pick a primary key per table from the accepted UCCs (smallest, NULL-free,
   key-like names first);
2. decompose tables into BCNF using the accepted FDs (``normal_form="bcnf"``);
3. attach accepted FKs and remaining UCCs (as UNIQUE) to the resulting tables;
4. emit PostgreSQL DDL, a data-migration script and a Mermaid ER diagram.

Every generated constraint carries a ``-- evidence:`` comment naming the
proposal it came from (id, source, rationale and verifier statistic).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import sqlglot
from pydantic import BaseModel, Field

from srev import names
from srev.extract import Evidence
from srev.model import ConstraintKey, Proposal, Verdict, dedupe, split_ref

_IDENT_OK = set("abcdefghijklmnopqrstuvwxyz0123456789_")


def _q(name: str) -> str:
    if name and name[0].isalpha() and set(name) <= _IDENT_OK:
        return name
    return '"' + name.replace('"', '""') + '"'


@dataclass
class Relation:
    name: str
    source: str
    columns: list[str]
    pk: list[str] = field(default_factory=list)
    pk_evidence: Proposal | None = None
    uniques: list[tuple[list[str], Proposal]] = field(default_factory=list)
    derived_from_fd: Proposal | None = None
    distinct: bool = False  # rows are SELECT DISTINCT from the source


@dataclass
class ForeignKey:
    table: str
    columns: list[str]
    ref_table: str
    ref_columns: list[str]
    evidence: Proposal | None


class TraceEntry(BaseModel):
    constraint: str
    evidence_id: str | None
    source: str
    rationale: str = ""
    statistic: str | None = None
    value: float | None = None


class SynthResult(BaseModel):
    ddl: str
    migration: str
    mermaid: str
    trace: list[TraceEntry] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)


def _choose_pk(table: str, uccs: list[Proposal], evidence: Evidence) -> Proposal | None:
    if not uccs:
        return None
    t = evidence.table(table)

    stem = names.table_stem(table)
    position = {c.lower(): i for i, c in enumerate(t.column_names)}

    def rank(p: Proposal) -> tuple[int, int, float, float, float, float]:
        cols = [split_ref(r)[1] for r in p.lhs]
        nulls = sum(t.column(c).null_count for c in cols if c in t.column_names)
        keyish = sum(names.keyish(c) for c in cols) / len(cols)
        # tie-breakers: the key named after its table (s_suppkey in supplier), then position
        named = max(names.similarity(names.core(c, table), stem) for c in cols)
        first = min(position.get(c.lower(), 99) for c in cols)
        return (len(cols), int(nulls > 0), -keyish, -round(named, 1), first, -p.confidence)

    return sorted(uccs, key=rank)[0]


def _closure(attrs: set[str], fds: list[tuple[frozenset[str], str]]) -> set[str]:
    result = set(attrs)
    changed = True
    while changed:
        changed = False
        for lhs, rhs in fds:
            if lhs <= result and rhs not in result:
                result.add(rhs)
                changed = True
    return result


def _bcnf(
    rel: Relation, fds: list[tuple[frozenset[str], str, Proposal]], fks: list[ForeignKey]
) -> list[Relation]:
    out: list[Relation] = []
    work = [rel]
    guard = 0
    while work and guard < 50:
        guard += 1
        r = work.pop()
        attrs = set(r.columns)
        local = [(lhs, rhs) for lhs, rhs, _ in fds if lhs <= attrs and rhs in attrs]
        if r.pk:
            local += [(frozenset(r.pk), a) for a in attrs if a not in r.pk]
        violation = None
        for lhs, rhs, prop in fds:
            if not (lhs <= attrs and rhs in attrs):
                continue
            if _closure(set(lhs), local) >= attrs:
                continue  # lhs is a superkey of r
            violation = (lhs, prop)
            break
        if violation is None:
            out.append(r)
            continue
        lhs, prop = violation
        determined = (_closure(set(lhs), local) & attrs) - set(lhs) - set(r.pk)
        if not determined:
            out.append(r)
            continue
        lhs_cols = [c for c in r.columns if c in lhs]
        new_name = f"{r.name}_{'_'.join(lhs_cols)}"
        r1 = Relation(
            name=new_name,
            source=r.source,
            columns=lhs_cols + [c for c in r.columns if c in determined],
            pk=lhs_cols,
            pk_evidence=prop,
            derived_from_fd=prop,
            distinct=True,
        )
        r2 = Relation(
            name=r.name,
            source=r.source,
            columns=[c for c in r.columns if c not in determined],
            pk=r.pk,
            pk_evidence=r.pk_evidence,
            uniques=r.uniques,
            derived_from_fd=r.derived_from_fd,
            distinct=r.distinct,
        )
        fks.append(ForeignKey(r2.name, lhs_cols, r1.name, lhs_cols, prop))
        work += [r1, r2]
    return out + work


def synthesize(
    evidence: Evidence,
    accepted: list[Proposal],
    verdicts: dict[ConstraintKey, Verdict] | None = None,
    *,
    normal_form: Literal["bcnf", "none"] = "bcnf",
    source_schema: str = "public",
    target_schema: str = "normalized",
) -> SynthResult:
    verdicts = verdicts or {}
    props = [p for p in dedupe(accepted) if all(evidence.has_ref(r) for r in p.columns())]
    types = {(t.name, c.name): c.type for t in evidence.tables for c in t.columns}
    relations: list[Relation] = []
    fks: list[ForeignKey] = []
    skipped: list[str] = []

    for t in evidence.tables:
        uccs = [p for p in props if p.type == "UCC" and p.table == t.name]
        pk_prop = _choose_pk(t.name, uccs, evidence)
        rel = Relation(name=t.name, source=t.name, columns=t.column_names)
        if pk_prop:
            rel.pk = [split_ref(r)[1] for r in pk_prop.lhs]
            rel.pk_evidence = pk_prop
        for u in uccs:
            if u is not pk_prop:
                rel.uniques.append(([split_ref(r)[1] for r in u.lhs], u))
        if normal_form == "bcnf":
            fds = [
                (frozenset(split_ref(r)[1] for r in p.lhs), split_ref(p.rhs[0])[1], p)
                for p in props
                if p.type == "FD" and p.table == t.name
            ]
            relations += _bcnf(rel, fds, fks)
        else:
            relations.append(rel)

    by_name = {r.name: r for r in relations}

    def home(table: str, cols: list[str], *, keyed: bool) -> Relation | None:
        cands = [r for r in relations if r.source == table and set(cols) <= set(r.columns)]
        if keyed:
            cands = [
                r
                for r in cands
                if set(r.pk) == set(cols) or any(set(u) == set(cols) for u, _ in r.uniques)
            ] or cands
        cands.sort(key=lambda r: (r.name != table, len(r.columns)))
        return cands[0] if cands else None

    for p in props:
        if p.type != "FK":
            continue
        lt, rt = p.table, p.ref_table or ""
        lcols = [split_ref(r)[1] for r in p.lhs]
        rcols = [split_ref(r)[1] for r in p.rhs]
        child, parent = home(lt, lcols, keyed=False), home(rt, rcols, keyed=True)
        if child is None or parent is None:
            skipped.append(f"{p.describe()}: columns no longer live in one table")
            continue
        if set(parent.pk) != set(rcols) and not any(
            set(u) == set(rcols) for u, _ in parent.uniques
        ):
            parent.uniques.append((rcols, p))
        fks.append(ForeignKey(child.name, lcols, parent.name, rcols, p))

    trace: list[TraceEntry] = []

    def evidence_comment(label: str, p: Proposal | None) -> str:
        if p is None:
            return ""
        v = verdicts.get(p.key())
        entry = TraceEntry(
            constraint=label,
            evidence_id=p.ident(),
            source=p.source,
            rationale=p.rationale[:120],
            statistic=v.statistic if v else None,
            value=v.value if v else None,
        )
        trace.append(entry)
        stat = f"; {v.statistic}={v.value}" if v and v.value is not None else ""
        why = f": {p.rationale[:80]}" if p.rationale else ""
        return f"  -- evidence: {p.ident()} [{p.source}]{why}{stat}"

    ts = _q(target_schema)
    ddl = [
        f"-- Generated by srev from {evidence.database}. Review before use.",
        f"CREATE SCHEMA IF NOT EXISTS {ts};",
        "",
    ]
    for r in relations:
        lines = []
        for c in r.columns:
            lines.append(
                f"  {_q(c)} {types.get((r.source, c), 'TEXT')}" + (" NOT NULL" if c in r.pk else "")
            )
        constraint_lines = []
        if r.pk:
            label = f"{r.name}_pkey"
            constraint_lines.append(
                (
                    f"  CONSTRAINT {_q(label)} PRIMARY KEY ({', '.join(map(_q, r.pk))})",
                    evidence_comment(f"{r.name} PRIMARY KEY ({', '.join(r.pk)})", r.pk_evidence),
                )
            )
        for i, (cols, p) in enumerate(r.uniques, start=1):
            label = f"{r.name}_uq{i}"
            constraint_lines.append(
                (
                    f"  CONSTRAINT {_q(label)} UNIQUE ({', '.join(map(_q, cols))})",
                    evidence_comment(f"{r.name} UNIQUE ({', '.join(cols)})", p),
                )
            )
        body = [(ln, "") for ln in lines] + constraint_lines
        header = f"CREATE TABLE {ts}.{_q(r.name)} ("
        if r.derived_from_fd is not None and r.distinct:
            header += f"  -- split off by BCNF on {r.derived_from_fd.describe()}"
        ddl.append(header)
        for i, (ln, comment) in enumerate(body):
            sep = "," if i < len(body) - 1 else ""
            ddl.append(f"{ln}{sep}{comment}")
        ddl.append(");\n")
    seen_fk = set()
    for i, fk in enumerate(fks, start=1):
        sig = (fk.table, tuple(fk.columns), fk.ref_table, tuple(fk.ref_columns))
        if sig in seen_fk or fk.table not in by_name or fk.ref_table not in by_name:
            continue
        seen_fk.add(sig)
        label = f"{fk.table}_fk{i}"
        ddl.append(
            f"ALTER TABLE {ts}.{_q(fk.table)} ADD CONSTRAINT {_q(label)} FOREIGN KEY "
            f"({', '.join(map(_q, fk.columns))}) REFERENCES {ts}.{_q(fk.ref_table)} "
            f"({', '.join(map(_q, fk.ref_columns))});"
            + evidence_comment(
                f"{fk.table}({', '.join(fk.columns)}) -> "
                f"{fk.ref_table}({', '.join(fk.ref_columns)})",
                fk.evidence,
            )
        )
    if skipped:
        ddl.append("")
        ddl += [f"-- skipped: {s}" for s in skipped]
    ddl_text = "\n".join(ddl).rstrip() + "\n"
    _check_parses(ddl_text)

    ss = _q(source_schema)
    migration = [
        f"-- Copy data from {source_schema} into {target_schema}, referenced tables first.",
        "-- Rows that violate an accepted constraint (orphans, duplicates) must be cleaned first.",
    ]
    for r in _load_order(relations, fks):
        col_list = ", ".join(map(_q, r.columns))
        not_null = " AND ".join(f"{_q(c)} IS NOT NULL" for c in r.pk) or "TRUE"
        distinct = "DISTINCT " if r.distinct else ""
        migration.append(
            f"INSERT INTO {ts}.{_q(r.name)} ({col_list})\n  SELECT {distinct}{col_list} "
            f"FROM {ss}.{_q(r.source)} WHERE {not_null};"
        )
    return SynthResult(
        ddl=ddl_text,
        migration="\n".join(migration) + "\n",
        mermaid=_mermaid(relations, fks, types),
        trace=trace,
        skipped=skipped,
    )


def _load_order(relations: list[Relation], fks: list[ForeignKey]) -> list[Relation]:
    """Topological order so referenced tables are filled before referencing ones."""
    deps: dict[str, set[str]] = {r.name: set() for r in relations}
    for fk in fks:
        if fk.table in deps and fk.ref_table in deps and fk.table != fk.ref_table:
            deps[fk.table].add(fk.ref_table)
    order: list[Relation] = []
    remaining = {r.name: r for r in relations}
    while remaining:
        ready = sorted(n for n in remaining if not (deps[n] & remaining.keys()))
        if not ready:  # cycle: fall back to name order for the rest
            ready = sorted(remaining)
        for n in ready:
            order.append(remaining.pop(n))
    return order


def _mermaid(
    relations: list[Relation], fks: list[ForeignKey], types: dict[tuple[str, str], str]
) -> str:
    fk_cols = {(fk.table, c) for fk in fks for c in fk.columns}
    lines = ["erDiagram"]
    for r in relations:
        lines.append(f"    {r.name} {{")
        for c in r.columns:
            t = types.get((r.source, c), "TEXT").split("(")[0].replace(" ", "_")
            keys = [k for k, cond in (("PK", c in r.pk), ("FK", (r.name, c) in fk_cols)) if cond]
            lines.append(f"        {t} {c}" + (f" {','.join(keys)}" if keys else ""))
        lines.append("    }")
    seen = set()
    for fk in fks:
        sig = (fk.table, fk.ref_table, tuple(fk.columns))
        if sig in seen:
            continue
        seen.add(sig)
        label = ", ".join(fk.columns)
        lines.append(f'    {fk.ref_table} ||--o{{ {fk.table} : "{label}"')
    return "\n".join(lines) + "\n"


def _check_parses(ddl: str) -> None:
    """Fail loudly if sqlglot cannot parse the generated PostgreSQL DDL."""
    for stmt in sqlglot.parse(ddl, read="postgres"):
        if stmt is None:
            continue
        if isinstance(stmt, sqlglot.exp.Command):
            raise ValueError(f"generated DDL did not parse: {stmt.sql()[:120]}")


__all__ = ["SynthResult", "synthesize"]
