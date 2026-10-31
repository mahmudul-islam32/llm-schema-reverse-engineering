import pytest

from srev.datasets.base import type_family
from srev.noise import PRESETS, NoiseConfig, inject, resolve_profile
from srev.qlog import mine_joins
from srev.truth import gold_from_dataset


def test_profiles():
    assert resolve_profile(0) == PRESETS["none"]
    assert resolve_profile("L2") == PRESETS["medium"]
    assert resolve_profile("high") == PRESETS["high"]
    assert resolve_profile({"nulls": 0.1}).nulls == 0.1
    with pytest.raises(KeyError):
        resolve_profile("extreme")


def test_level_zero_changes_nothing(university):
    noisy, log = inject(university, resolve_profile(0), seed=1)
    assert noisy.checksum() == university.checksum()
    assert log.summary() == {}


def test_noise_is_reproducible_and_logged(university):
    gold = gold_from_dataset(university)
    a, log_a = inject(university, PRESETS["high"], seed=5, gold=gold)
    b, log_b = inject(university, PRESETS["high"], seed=5, gold=gold)
    c, _ = inject(university, PRESETS["high"], seed=6, gold=gold)
    assert a.checksum() == b.checksum() != c.checksum()
    assert log_a == log_b
    summary = log_a.summary()
    for kind in ("orphans", "nulls", "typos", "rename_columns", "rename_tables"):
        assert summary[kind] > 0, kind
    # the input dataset is untouched
    assert university.checksum() == university.checksum()
    assert university.tables[0].name == "department"


def test_renames_are_consistent(university):
    gold = gold_from_dataset(university)
    noisy, log = inject(university, PRESETS["high"], seed=3, gold=gold)
    renamed_gold = log.renames.gold(gold)
    schema = {t.name: set(t.column_names) for t in noisy.tables}
    for label in renamed_gold.labels:
        for ref in label.columns():
            table, col = ref.split(".")
            assert col in schema[table], ref
    # the renamed workload only references the new names and still yields joins
    stats = mine_joins(noisy.queries, noisy.schema_map())
    assert stats.n_failed == 0 and stats.pairs
    for pair in stats.pairs:
        for ref in (pair.left, pair.right):
            table, col = ref.split(".")
            assert col in schema[table]
    original_names = {c for t in university.tables for c in t.column_names}
    assert not original_names & {c for t in noisy.tables for c in t.column_names}


def test_keys_are_protected_and_lengths_kept(tpch):
    noisy, _ = inject(tpch, NoiseConfig(nulls=0.3, typos=0.3, orphans=0.2), seed=2)
    for clean_t, noisy_t in zip(tpch.tables, noisy.tables, strict=True):
        for i, col in enumerate(clean_t.columns):
            if col.name in clean_t.primary_key:
                assert all(r[i] is not None for r in noisy_t.rows)
            if type_family(col.type) == "text":
                width = max((len(str(r[i])) for r in clean_t.rows if r[i] is not None), default=0)
                assert all(r[i] is None or len(r[i]) <= width for r in noisy_t.rows)


def test_orphans_dangle(retail):
    gold = gold_from_dataset(retail)
    noisy, log = inject(retail, NoiseConfig(orphans=0.2), seed=4, gold=gold)
    cust_keys = {r[0] for r in noisy.table("tbl_cust").rows}
    orders = noisy.table("tbl_ord")
    idx = orders.index("cust_no")
    dangling = [r for r in orders.rows if r[idx] not in cust_keys]
    assert dangling
    assert any(a.kind == "orphans" and a.target == "tbl_ord.cust_no" for a in log.actions)


def test_abbreviate():
    from srev.noise import abbreviate

    assert abbreviate("customer_id") == "cstm_id"
    assert abbreviate("l_orderkey") == "l_ordr"
    assert abbreviate("id") == "id"


@pytest.mark.parametrize("style", ["names-abbreviated", "names-cryptic", "names-misleading"])
def test_name_probes_keep_data_and_gold_consistent(university, style):
    from srev.db import SQLiteConnector, materialize
    from srev.verify import Verifier

    gold = gold_from_dataset(university)
    noisy, log = inject(university, resolve_profile(style), seed=9, gold=gold)
    renamed = log.renames.gold(gold)
    # only names change: the rows are identical
    assert [t.rows for t in noisy.tables] == [t.rows for t in university.tables]
    assert log.renames.columns and log.renames.tables
    with SQLiteConnector(":memory:") as conn:
        materialize(noisy, conn)
        verifier = Verifier(conn)
        assert all(verifier.verify(label).accepted for label in renamed.labels)
    stats = mine_joins(noisy.queries, noisy.schema_map())
    assert stats.n_failed == 0 and stats.pairs


def test_misleading_names_are_a_derangement(university):
    noisy, log = inject(university, resolve_profile("names-misleading"), seed=3)
    original_tables = {t.name for t in university.tables}
    assert {t.name for t in noisy.tables} == original_tables  # same names, permuted
    assert all(old != new for old, new in log.renames.tables.items())
    for clean_t in university.tables:
        if len(clean_t.columns) < 2:
            continue
        moved = {k for k in log.renames.columns if k.startswith(clean_t.name + ".")}
        assert len(moved) == len(clean_t.columns)  # every column got another column's name
