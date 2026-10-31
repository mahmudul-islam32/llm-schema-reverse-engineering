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


def test_approximate_dependencies(sqlite_conn):
    from srev.baselines.profiling import _Partitions, discover_unary_inds, load_tables

    rows = [(i, f"z{i % 4}", f"c{i % 4}", i % 7) for i in range(40)]
    rows[3] = (3, "z3", "WRONG", 3)  # one dirty value breaks zip -> city exactly
    rows[25] = (24, "z1", "c1", 4)  # one duplicate id (outside the referenced range)
    sqlite_conn.execute("CREATE TABLE p (id INTEGER, zip TEXT, city TEXT, n INTEGER)")
    sqlite_conn.insert_rows("p", ["id", "zip", "city", "n"], rows)
    sqlite_conn.execute("CREATE TABLE c (pid INTEGER)")
    sqlite_conn.insert_rows("c", ["pid"], [(i % 10,) for i in range(50)] + [(999,)])
    t = next(t for t in load_tables(sqlite_conn) if t.name == "p")
    parts = _Partitions(t)
    assert parts.g3(("zip",), "city") == 1 / 40
    assert parts.g3(("city",), "zip") == 0.0
    assert parts.ucc_error(("id",)) == 1 / 40

    exact = profile(sqlite_conn)
    approx = profile(
        sqlite_conn,
        ProfilingConfig(
            ucc_max_error=0.05, fd_max_error=0.05, ind_min_coverage=0.9, ind_coverage="rows"
        ),
    )
    assert ("FD", ("p.zip",), ("p.city",)) not in _keys(exact.proposals, "FD")
    assert ("FD", ("p.zip",), ("p.city",)) in _keys(approx.proposals, "FD")
    assert ("UCC", ("p.id",), ()) in _keys(approx.proposals, "UCC")
    assert ("FK", ("c.pid",), ("p.id",)) not in _keys(exact.proposals, "FK")
    assert ("FK", ("c.pid",), ("p.id",)) in _keys(approx.proposals, "FK")
    fk = next(p for p in approx.proposals if p.key() == ("FK", ("c.pid",), ("p.id",)))
    assert "approximate" in fk.rationale

    # a single orphan value costs 1/11 of the distinct values but only 1/51 of the rows
    tables = load_tables(sqlite_conn)
    by_values = {(d, r): c for d, r, c in discover_unary_inds(tables, 0.5, "values")}
    by_rows = {(d, r): c for d, r, c in discover_unary_inds(tables, 0.5, "rows")}
    assert by_values[("c.pid", "p.id")] == round(10 / 11, 4)
    assert by_rows[("c.pid", "p.id")] == round(50 / 51, 4)
