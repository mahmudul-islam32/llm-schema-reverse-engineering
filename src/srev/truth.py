"""Ground-truth builder (F-02).

Gold labels come from *declared* constraints (PRIMARY KEY and UNIQUE become
UCCs, FOREIGN KEY becomes FK) and from *curated* labels shipped with a
dataset (semantic FDs, undeclared relationships). Every label records its
source. Gold files are written separately from the evidence so methods never
see them.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from srev.datasets.base import Dataset
from srev.db import Connector, DeclaredConstraints
from srev.model import CONSTRAINT_TYPES, ConstraintType, Proposal, qualify


class GoldSet(BaseModel):
    database: str
    dataset_checksum: str = ""
    labels: list[Proposal] = Field(default_factory=list)

    def of_type(self, ctype: ConstraintType) -> list[Proposal]:
        return [label for label in self.labels if label.type == ctype]

    def counts(self) -> dict[str, int]:
        return {t: len(self.of_type(t)) for t in CONSTRAINT_TYPES}

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(self.model_dump_json(indent=2))

    @classmethod
    def load(cls, path: str | Path) -> GoldSet:
        return cls.model_validate_json(Path(path).read_text())


def _declared_labels(declared: DeclaredConstraints) -> list[Proposal]:
    labels: list[Proposal] = []
    for table, pk in declared.primary_keys.items():
        labels.append(
            Proposal(
                type="UCC",
                lhs=[qualify(table, c) for c in pk],
                source="declared",
                rationale="PRIMARY KEY",
            )
        )
    for table, uniques in declared.uniques.items():
        for cols in uniques:
            labels.append(
                Proposal(
                    type="UCC",
                    lhs=[qualify(table, c) for c in cols],
                    source="declared",
                    rationale="UNIQUE",
                )
            )
    for table, fks in declared.foreign_keys.items():
        for fk in fks:
            labels.append(
                Proposal(
                    type="FK",
                    lhs=[qualify(table, c) for c in fk.columns],
                    rhs=[qualify(fk.ref_table, c) for c in fk.ref_columns],
                    source="declared",
                    rationale="FOREIGN KEY",
                )
            )
    return labels


def _unique(labels: list[Proposal]) -> list[Proposal]:
    seen, out = set(), []
    for label in labels:
        if label.key() not in seen:
            seen.add(label.key())
            out.append(label)
    return out


def gold_from_dataset(dataset: Dataset) -> GoldSet:
    declared = DeclaredConstraints(
        primary_keys={t.name: t.primary_key for t in dataset.tables if t.primary_key},
        uniques={t.name: t.uniques for t in dataset.tables if t.uniques},
        foreign_keys={t.name: t.foreign_keys for t in dataset.tables if t.foreign_keys},
    )
    labels = _declared_labels(declared)
    for fk in dataset.curated_fks:
        labels.append(
            Proposal(
                type="FK",
                lhs=[qualify(fk.table, c) for c in fk.columns],
                rhs=[qualify(fk.ref_table, c) for c in fk.ref_columns],
                source="curated",
                rationale=fk.note,
            )
        )
    for fd in dataset.curated_fds:
        labels.append(
            Proposal(
                type="FD",
                lhs=[qualify(fd.table, c) for c in fd.lhs],
                rhs=[qualify(fd.table, fd.rhs)],
                source="curated",
                rationale=fd.note,
            )
        )
    return GoldSet(
        database=dataset.name, dataset_checksum=dataset.checksum(), labels=_unique(labels)
    )


def gold_from_connector(conn: Connector, database: str) -> GoldSet:
    """Export the declared constraints of a live database as gold labels."""
    return GoldSet(database=database, labels=_unique(_declared_labels(conn.declared_constraints())))


def strip_constraints(conn: Connector, target_schema: str) -> list[str]:
    """Copy every table of ``conn``'s schema into ``target_schema`` without constraints.

    ``CREATE TABLE ... AS SELECT`` copies data and column types but no keys,
    UNIQUE, FK or NOT NULL constraints. PostgreSQL only; the source is untouched.
    """
    if conn.dialect != "postgres":
        raise ValueError("strip_constraints needs PostgreSQL; for SQLite re-materialise instead")
    tables = conn.list_tables()
    target = conn.quote(target_schema)
    conn.execute(f"DROP SCHEMA IF EXISTS {target} CASCADE")
    conn.execute(f"CREATE SCHEMA {target}")
    for t in tables:
        conn.execute(f"CREATE TABLE {target}.{conn.quote(t)} AS SELECT * FROM {conn.qtable(t)}")
    return tables
