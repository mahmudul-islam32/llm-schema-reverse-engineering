import pytest
from pydantic import ValidationError

from srev.model import Proposal, dedupe, split_fds, split_ref


def test_references_are_normalised():
    p = Proposal(type="UCC", lhs=['"Orders".O_OrderKey'])
    assert p.lhs == ["orders.o_orderkey"]
    assert split_ref("a.b") == ("a", "b")
    with pytest.raises(ValueError):
        split_ref("nodot")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"type": "FK", "lhs": ["a.x"], "rhs": ["b.y", "b.z"]},  # arity mismatch
        {"type": "FK", "lhs": ["a.x", "c.y"], "rhs": ["b.x", "b.y"]},  # two child tables
        {"type": "FK", "lhs": ["a.x"], "rhs": ["a.x"]},  # self reference
        {"type": "UCC", "lhs": ["a.x"], "rhs": ["a.y"]},
        {"type": "UCC", "lhs": ["a.x", "b.y"]},
        {"type": "FD", "lhs": ["a.x"], "rhs": []},
        {"type": "FD", "lhs": ["a.x"], "rhs": ["b.y"]},
        {"type": "FD", "lhs": ["a.x"], "rhs": ["a.x"]},
        {"type": "FK", "lhs": [], "rhs": []},
        {"type": "PK", "lhs": ["a.x"]},
        {"type": "UCC", "lhs": ["a.x"], "confidence": 1.5},
    ],
)
def test_invalid_shapes_are_rejected(kwargs):
    with pytest.raises(ValidationError):
        Proposal(**kwargs)


def test_keys_are_canonical():
    a = Proposal(type="UCC", lhs=["t.b", "t.a"])
    b = Proposal(type="UCC", lhs=["t.a", "t.b"])
    assert a.key() == b.key()
    fk1 = Proposal(type="FK", lhs=["l.p", "l.s"], rhs=["ps.p", "ps.s"])
    fk2 = Proposal(type="FK", lhs=["l.s", "l.p"], rhs=["ps.s", "ps.p"])
    assert fk1.key() == fk2.key()
    reverse = Proposal(type="FK", lhs=["ps.p", "ps.s"], rhs=["l.p", "l.s"])
    assert reverse.key() != fk1.key()
    assert fk1.table == "l" and fk1.ref_table == "ps"
    assert len(fk1.ident()) == 10


def test_split_and_dedupe():
    fd = Proposal(type="FD", lhs=["t.a"], rhs=["t.b", "t.c"], confidence=0.4)
    assert [p.rhs for p in split_fds([fd])] == [["t.b"], ["t.c"]]
    dup = Proposal(type="FD", lhs=["t.a"], rhs=["t.b"], confidence=0.9)
    out = dedupe([fd, dup])
    assert len(out) == 2
    assert next(p for p in out if p.rhs == ["t.b"]).confidence == 0.9
