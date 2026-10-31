"""Noise injector (F-03): seeded perturbations that simulate legacy databases.

Every perturbation has a level, is recorded in a :class:`NoiseLog`, and is
reproducible from the seed. The injector works on an in-memory
:class:`~srev.datasets.base.Dataset` copy; gold labels and the query workload
are renamed consistently so evaluation happens on the noisy names.
"""

from __future__ import annotations

import copy
import random
import string
from typing import Any, Literal

import sqlglot
from pydantic import BaseModel, Field

from srev import sqlutil
from srev.datasets.base import Dataset, TableDef, type_family
from srev.model import Proposal, qualify, split_ref
from srev.truth import GoldSet


class NoiseConfig(BaseModel):
    """Perturbation levels; fractions are in ``[0, 1]``."""

    rename_columns: float = Field(0.0, ge=0, le=1, description="share of columns renamed")
    rename_tables: float = Field(0.0, ge=0, le=1, description="share of tables renamed")
    nulls: float = Field(0.0, ge=0, le=1, description="share of cells set to NULL")
    typos: float = Field(0.0, ge=0, le=1, description="share of text cells with a typo")
    orphans: float = Field(0.0, ge=0, le=1, description="share of FK values made dangling")
    drop_comments: bool = False
    protect_keys: bool = Field(True, description="never null/typo PK and UNIQUE columns")
    rename_style: Literal["cryptic", "abbreviate", "misleading"] = Field(
        "cryptic",
        description="cryptic: c_k1-style codes; abbreviate: vowel-dropped legacy names "
        "(customer_id -> cstm_id); misleading: names permuted among the renamed "
        "columns/tables, so names point at the wrong data",
    )


PRESETS: dict[str, NoiseConfig] = {
    "none": NoiseConfig(),
    "low": NoiseConfig(
        rename_columns=0.25, nulls=0.02, typos=0.01, orphans=0.01, drop_comments=True
    ),
    "medium": NoiseConfig(
        rename_columns=0.6,
        rename_tables=0.3,
        nulls=0.05,
        typos=0.03,
        orphans=0.03,
        drop_comments=True,
    ),
    "high": NoiseConfig(
        rename_columns=1.0,
        rename_tables=1.0,
        nulls=0.10,
        typos=0.05,
        orphans=0.05,
        drop_comments=True,
    ),
}
# Name-only probes: identical data, only the names change. Comparing them with
# "none" isolates how much a method relies on names (and on memorised schemas).
PRESETS.update(
    {
        "names-abbreviated": NoiseConfig(
            rename_columns=1.0, rename_tables=1.0, rename_style="abbreviate", drop_comments=True
        ),
        "names-cryptic": NoiseConfig(
            rename_columns=1.0, rename_tables=1.0, rename_style="cryptic", drop_comments=True
        ),
        "names-misleading": NoiseConfig(
            rename_columns=1.0, rename_tables=1.0, rename_style="misleading", drop_comments=True
        ),
    }
)
LEVELS = {0: "none", 1: "low", 2: "medium", 3: "high"}


def resolve_profile(profile: str | int | NoiseConfig | dict[str, Any]) -> NoiseConfig:
    if isinstance(profile, NoiseConfig):
        return profile
    if isinstance(profile, dict):
        return NoiseConfig.model_validate(profile)
    key = str(profile).lower()
    if key.startswith("l") and key[1:].isdigit():
        key = key[1:]
    if key.isdigit():
        key = LEVELS[int(key)]
    if key not in PRESETS:
        raise KeyError(f"unknown noise profile {profile!r}; use one of {sorted(PRESETS)} or 0-3")
    return PRESETS[key]


class RenameMap(BaseModel):
    tables: dict[str, str] = Field(default_factory=dict)
    columns: dict[str, str] = Field(default_factory=dict, description="'table.col' -> new col")

    def ref(self, ref: str) -> str:
        table, column = split_ref(ref)
        new_col = self.columns.get(ref, column)
        return qualify(self.tables.get(table, table), new_col)

    def proposal(self, p: Proposal) -> Proposal:
        return p.model_copy(
            update={"lhs": [self.ref(r) for r in p.lhs], "rhs": [self.ref(r) for r in p.rhs]}
        )

    def gold(self, gold: GoldSet) -> GoldSet:
        return gold.model_copy(update={"labels": [self.proposal(p) for p in gold.labels]})


class NoiseAction(BaseModel):
    kind: str
    target: str
    count: int = 0
    detail: str = ""


class NoiseLog(BaseModel):
    config: NoiseConfig
    seed: int
    renames: RenameMap = Field(default_factory=RenameMap)
    actions: list[NoiseAction] = Field(default_factory=list)
    dropped_queries: int = 0

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for a in self.actions:
            if a.count:
                out[a.kind] = out.get(a.kind, 0) + a.count
        return out


def _protected(table: TableDef, protect: bool) -> set[str]:
    if not protect:
        return set()
    cols = set(table.primary_key)
    for u in table.uniques:
        cols.update(u)
    return cols


def _typo(rng: random.Random, value: str) -> str:
    """Perturb a string without making it longer (VARCHAR(n) limits must still hold)."""
    if not value:
        return value
    i = rng.randrange(len(value))
    op = rng.randrange(3) if len(value) >= 2 else 2
    if op == 0 and i < len(value) - 1 and value[i] != value[i + 1]:  # swap neighbours
        return value[:i] + value[i + 1] + value[i] + value[i + 2 :]
    if op == 1:  # drop a character
        return value[:i] + value[i + 1 :]
    letter = rng.choice([c for c in string.ascii_lowercase if c != value[i].lower()])
    return value[:i] + letter + value[i + 1 :]  # replace a character


def _orphan_value(sample: Any, i: int) -> Any:
    if isinstance(sample, bool):
        return sample
    if isinstance(sample, int):
        return 10**9 + i
    if isinstance(sample, str):
        # same length as the original so VARCHAR(n) still fits; '~' never occurs in keys
        code = "~" + format(i, "x")
        if len(code) > len(sample):
            return "~" * max(1, len(sample))
        return sample[: len(sample) - len(code)] + code
    return sample


_VOWELS = set("aeiou")


def abbreviate(name: str, max_len: int = 4) -> str:
    """Legacy-style abbreviation per token: ``customer_id`` -> ``cstm_id``."""
    out = []
    for tok in name.lower().split("_"):
        if len(tok) <= 3:
            out.append(tok)
            continue
        short = tok[0] + "".join(ch for ch in tok[1:] if ch not in _VOWELS)
        out.append(short[:max_len])
    return "_".join(t for t in out if t) or name


def _unique(name: str, taken: set[str]) -> str:
    candidate, i = name, 2
    while candidate in taken:
        candidate, i = f"{name}{i}", i + 1
    taken.add(candidate)
    return candidate


def _derangement(rng: random.Random, items: list[str]) -> list[str]:
    """A permutation that moves every element (identity only for fewer than 2 items)."""
    if len(items) < 2:
        return list(items)
    while True:
        perm = items[:]
        rng.shuffle(perm)
        if all(a != b for a, b in zip(items, perm, strict=True)):
            return perm


def _cryptic(rng: random.Random, table: str, idx: int, taken: set[str]) -> str:
    while True:
        name = f"{table[:1] or 'x'}_{rng.choice(string.ascii_lowercase)}{idx}"
        if rng.random() < 0.5:
            name += rng.choice(string.ascii_lowercase)
        if name not in taken:
            taken.add(name)
            return name


def inject(
    dataset: Dataset, config: NoiseConfig, seed: int = 42, gold: GoldSet | None = None
) -> tuple[Dataset, NoiseLog]:
    """Return a perturbed deep copy of ``dataset`` and the log of what changed.

    Orphans are injected for declared and curated FKs (taken from ``gold`` if
    given, else from the dataset's declared FKs).
    """
    rng = random.Random(seed)
    ds = copy.deepcopy(dataset)
    log = NoiseLog(config=config, seed=seed)
    original_schema = {t.name: set(t.column_names) for t in ds.tables}

    # 1. orphan FK values ---------------------------------------------------
    if config.orphans > 0:
        fks: list[tuple[str, list[str]]] = []
        if gold is not None:
            fks = [(p.table, [split_ref(r)[1] for r in p.lhs]) for p in gold.of_type("FK")]
        else:
            fks = [(t.name, fk.columns) for t in ds.tables for fk in t.foreign_keys]
        counter = 0
        for tname, cols in sorted(fks):
            table = ds.table(tname)
            ci = table.index(cols[0])
            changed = 0
            for r, row in enumerate(table.rows):
                if row[ci] is not None and rng.random() < config.orphans:
                    counter += 1
                    lst = list(row)
                    lst[ci] = _orphan_value(row[ci], counter)
                    table.rows[r] = tuple(lst)
                    changed += 1
            log.actions.append(
                NoiseAction(kind="orphans", target=f"{tname}.{cols[0]}", count=changed)
            )

    # 2. NULLs and 3. typos ----------------------------------------------------
    for table in ds.tables:
        protected = _protected(table, config.protect_keys)
        for ci, col in enumerate(table.columns):
            if col.name in protected:
                continue
            n_null = n_typo = 0
            is_text = type_family(col.type) == "text"
            for r, row in enumerate(table.rows):
                value = row[ci]
                if value is None:
                    continue
                if config.nulls and rng.random() < config.nulls:
                    value, n_null = None, n_null + 1
                elif is_text and config.typos and rng.random() < config.typos:
                    value, n_typo = _typo(rng, str(value)), n_typo + 1
                if value is not row[ci]:
                    lst = list(row)
                    lst[ci] = value
                    table.rows[r] = tuple(lst)
            if n_null:
                log.actions.append(
                    NoiseAction(kind="nulls", target=f"{table.name}.{col.name}", count=n_null)
                )
            if n_typo:
                log.actions.append(
                    NoiseAction(kind="typos", target=f"{table.name}.{col.name}", count=n_typo)
                )
            if n_null or n_typo:
                col.nullable = True

    # 4. comments ------------------------------------------------------------
    if config.drop_comments:
        n = 0
        for table in ds.tables:
            n += table.comment is not None
            table.comment = None
            for col in table.columns:
                n += col.comment is not None
                col.comment = None
        log.actions.append(NoiseAction(kind="drop_comments", target="*", count=n))

    # 5. renames ---------------------------------------------------------------
    renames = log.renames
    style = config.rename_style
    if config.rename_columns > 0:
        for table in ds.tables:
            chosen = [
                (idx, col.name)
                for idx, col in enumerate(table.columns, start=1)
                if rng.random() < config.rename_columns
            ]
            if style == "misleading":
                originals = [name for _, name in chosen]
                for old, new in zip(originals, _derangement(rng, originals), strict=True):
                    if old != new:
                        renames.columns[qualify(table.name, old)] = new
                continue
            keep = {c for c in table.column_names if c not in {n for _, n in chosen}}
            taken = set(keep)
            for idx, name in chosen:
                new = (
                    _unique(abbreviate(name), taken)
                    if style == "abbreviate"
                    else _cryptic(rng, table.name, idx, taken)
                )
                if new != name:
                    renames.columns[qualify(table.name, name)] = new
    if config.rename_tables > 0:
        chosen_t = [
            (idx, t.name)
            for idx, t in enumerate(ds.tables, start=1)
            if rng.random() < config.rename_tables
        ]
        if style == "misleading":
            originals = [name for _, name in chosen_t]
            for old, new in zip(originals, _derangement(rng, originals), strict=True):
                if old != new:
                    renames.tables[old] = new
        else:
            taken = {t.name for t in ds.tables} - {n for _, n in chosen_t}
            for idx, name in chosen_t:
                if style == "abbreviate":
                    new = _unique(abbreviate(name), taken)
                else:
                    while True:
                        new = f"t{idx:02d}_{''.join(rng.choices(string.ascii_lowercase, k=2))}"
                        if new not in taken:
                            taken.add(new)
                            break
                if new != name:
                    renames.tables[name] = new
    if renames.columns or renames.tables:
        _apply_renames(ds, renames, original_schema, log)
        log.actions.append(
            NoiseAction(kind="rename_columns", target="*", count=len(renames.columns))
        )
        log.actions.append(NoiseAction(kind="rename_tables", target="*", count=len(renames.tables)))
    ds.params = {**ds.params, "noise_seed": seed}
    return ds, log


def _apply_renames(
    ds: Dataset, renames: RenameMap, original_schema: dict[str, set[str]], log: NoiseLog
) -> None:
    col_map = {tuple(k.split(".", 1)): v for k, v in renames.columns.items()}

    def new_col(table: str, col: str) -> str:
        return renames.columns.get(qualify(table, col), col)

    for table in ds.tables:
        old = table.name
        for col in table.columns:
            col.name = new_col(old, col.name)
        table.primary_key = [new_col(old, c) for c in table.primary_key]
        table.uniques = [[new_col(old, c) for c in u] for u in table.uniques]
        for fk in table.foreign_keys:
            fk.columns = [new_col(old, c) for c in fk.columns]
            fk.ref_columns = [new_col(fk.ref_table, c) for c in fk.ref_columns]
            fk.ref_table = renames.tables.get(fk.ref_table, fk.ref_table)
        table.name = renames.tables.get(old, old)
    for fd in ds.curated_fds:
        fd.lhs = [new_col(fd.table, c) for c in fd.lhs]
        fd.rhs = new_col(fd.table, fd.rhs)
        fd.table = renames.tables.get(fd.table, fd.table)
    for cfk in ds.curated_fks:
        cfk.columns = [new_col(cfk.table, c) for c in cfk.columns]
        cfk.ref_columns = [new_col(cfk.ref_table, c) for c in cfk.ref_columns]
        cfk.table = renames.tables.get(cfk.table, cfk.table)
        cfk.ref_table = renames.tables.get(cfk.ref_table, cfk.ref_table)

    queries = []
    for q in ds.queries:
        try:
            queries.append(
                sqlutil.rename_identifiers(q, renames.tables, col_map, original_schema)  # type: ignore[arg-type]
            )
        except sqlglot.errors.SqlglotError:
            # Unparseable statements carry no usable join evidence; drop them so
            # original names cannot leak into the renamed workload.
            log.dropped_queries += 1
    ds.queries = queries
