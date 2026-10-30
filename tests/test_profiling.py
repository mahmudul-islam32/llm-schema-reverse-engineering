from srev.baselines.profiling import ProfilingConfig, profile
from srev.db import SQLiteConnector, materialize
from srev.truth import gold_from_dataset


def _keys(props, ctype):
    return {p.key() for p in props if p.type == ctype}


def test_small_table(sqlite_conn):
    sqlite_conn.execute("CREATE TABLE p (id INTEGER, zip TEXT, city TEXT, n INTEGER)")
    sqlite_conn.insert_rows(
        "p",
        ["id", "zip", "city", "n"],
        [(1, "10", "A", 5), (2, "10", "A", 6), (3, "20", "B", 5), (4, "30", "B", 7)],
    )
    sqlite_conn.execute("CREATE TABLE c (pid INTEGER, k INTEGER)")
    sqlite_conn.insert_rows("c", ["pid", "k"], [(1, 1), (1, 2), (3, 1), (None, 3)])
    res = profile(sqlite_conn, ProfilingConfig(ucc_max_arity=2, fd_max_lhs=2))
    uccs = _keys(res.proposals, "UCC")
    assert ("UCC", ("p.id",), ()) in uccs
    assert ("UCC", ("p.n", "p.zip"), ()) in uccs  # minimal composite key
    assert not any(set(k[1]) > {"p.id"} for k in uccs)  # minimality
    fds = {p.key(): p for p in res.proposals if p.type == "FD"}
    assert ("FD", ("p.zip",), ("p.city",)) in fds
    assert fds[("FD", ("p.zip",), ("p.city",))].confidence > 0  # redundancy > 0
    assert ("FD", ("p.city",), ("p.zip",)) not in fds  # does not hold
    assert ("FK", ("c.pid",), ("p.id",)) in _keys(res.proposals, "FK")
    assert res.stats["tables"] == 2


def test_recovers_all_tpch_foreign_keys(tpch):
    with SQLiteConnector(":memory:") as conn:
        materialize(tpch, conn)
        res = profile(conn)
    gold = gold_from_dataset(tpch)
    found = {p.key() for p in res.proposals}
    missing = [g.describe() for g in gold.labels if g.key() not in found]
    assert missing == []
    # raw profiling output is much larger than the gold set
    assert len(res.proposals) > 5 * len(gold.labels)
