import pytest

from srev.datasets import available, load_dataset
from srev.db import SQLiteConnector, materialize
from srev.truth import gold_from_connector, gold_from_dataset
from srev.verify import Verifier


def test_registry():
    assert set(available()) == {"tpch_mini", "university", "retail_legacy", "airline"}
    with pytest.raises(KeyError):
        load_dataset("nope")
    assert load_dataset("tpch", scale=0.1).name == "tpch_mini"


@pytest.mark.parametrize("name", ["tpch_mini", "university", "retail_legacy", "airline"])
def test_generation_is_deterministic(name):
    a = load_dataset(name, seed=1, scale=0.3)
    b = load_dataset(name, seed=1, scale=0.3)
    c = load_dataset(name, seed=2, scale=0.3)
    assert a.checksum() == b.checksum()
    assert a.checksum() != c.checksum()


@pytest.mark.parametrize("name", ["tpch_mini", "university", "retail_legacy", "airline"])
def test_gold_constraints_hold_on_clean_data(name):
    ds = load_dataset(name, seed=3, scale=0.3)
    gold = gold_from_dataset(ds)
    assert gold.counts()["FK"] > 0 and gold.counts()["UCC"] > 0
    with SQLiteConnector(":memory:") as conn:
        materialize(ds, conn)
        verifier = Verifier(conn)
        for label in gold.labels:
            verdict = verifier.verify(label)
            assert verdict.accepted, (label.describe(), verdict)
            if label.type == "FK":
                assert verdict.value == 1.0
            if label.type == "FD":
                assert verdict.value == 0.0


def test_gold_records_sources(retail):
    gold = gold_from_dataset(retail)
    sources = {(g.type, g.source) for g in gold.labels}
    assert ("FK", "curated") in sources  # no declared FKs in the legacy schema
    assert ("UCC", "declared") in sources
    assert ("FD", "curated") in sources
    assert ("FK", "declared") not in sources


def test_materialize_with_constraints_roundtrips_gold(university):
    with SQLiteConnector(":memory:") as conn:
        materialize(university, conn, with_constraints=True)
        exported = gold_from_connector(conn, "university")
    declared = {g.key() for g in gold_from_dataset(university).labels if g.source == "declared"}
    assert {g.key() for g in exported.labels} == declared
    # and nothing leaks when materialised without constraints
    with SQLiteConnector(":memory:") as conn:
        materialize(university, conn)
        assert gold_from_connector(conn, "u").labels == []
