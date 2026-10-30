from srev.model import Proposal
from srev.verify import Verifier, VerifyConfig


def _setup(conn):
    conn.execute("CREATE TABLE parent (id INTEGER, code TEXT)")
    conn.insert_rows("parent", ["id", "code"], [(1, "a"), (2, "b"), (3, "c"), (3, "d")])
    conn.execute("CREATE TABLE child (pid INTEGER, k TEXT, v TEXT)")
    rows = [(1, "x", "1"), (2, "x", "1"), (2, "y", "2"), (9, "y", "3"), (None, "z", "4")]
    conn.insert_rows("child", ["pid", "k", "v"], rows)


def test_fk_coverage(sqlite_conn):
    _setup(sqlite_conn)
    v = Verifier(sqlite_conn, VerifyConfig(fk_min_coverage=0.6, ucc_min_uniqueness=0.7))
    verdict = v.verify(Proposal(type="FK", lhs=["child.pid"], rhs=["parent.id"]))
    # non-null child rows 1, 2, 2, 9: three of four match; distinct values 2 of 3
    assert verdict.statistic == "coverage" and verdict.value == 0.75
    assert abs(verdict.details["value_coverage"] - 2 / 3) < 1e-5
    assert verdict.details["rows"] == 4 and verdict.details["distinct_values"] == 3
    assert verdict.details["referenced_uniqueness"] == 0.75
    assert verdict.accepted
    strict = Verifier(sqlite_conn).verify(Proposal(type="FK", lhs=["child.pid"], rhs=["parent.id"]))
    assert not strict.accepted and strict.status == "rejected"


def test_uniqueness_and_g3(sqlite_conn):
    _setup(sqlite_conn)
    v = Verifier(sqlite_conn)
    u = v.verify(Proposal(type="UCC", lhs=["parent.code"]))
    assert u.accepted and u.value == 1.0
    assert v.verify(Proposal(type="UCC", lhs=["parent.id"])).value == 0.75
    # k -> v: group x has v {1,1}, y has {2,3}: one row must go, g3 = 1/5
    fd = v.verify(Proposal(type="FD", lhs=["child.k"], rhs=["child.v"]))
    assert fd.statistic == "g3" and abs(fd.value - 0.2) < 1e-9 and not fd.accepted
    assert v.verify(Proposal(type="FD", lhs=["child.v"], rhs=["child.k"])).value == 0.0


def test_invalid_reference(sqlite_conn):
    _setup(sqlite_conn)
    verdict = Verifier(sqlite_conn).verify(Proposal(type="UCC", lhs=["parent.ghost"]))
    assert verdict.status == "invalid_reference" and not verdict.accepted
    assert "parent.ghost" in verdict.message
    verdict = Verifier(sqlite_conn).verify(
        Proposal(type="FK", lhs=["child.pid"], rhs=["nowhere.id"])
    )
    assert verdict.status == "invalid_reference"


def test_empty_tables(sqlite_conn):
    sqlite_conn.execute("CREATE TABLE e (a INTEGER, b INTEGER)")
    v = Verifier(sqlite_conn)
    assert v.verify(Proposal(type="UCC", lhs=["e.a"])).status == "empty"
    assert v.verify(Proposal(type="FD", lhs=["e.a"], rhs=["e.b"])).status == "empty"
