import pytest

from srev.eval import aggregate, analyse, bootstrap_ci, cohen_kappa, paired_permutation_test, score
from srev.eval.metrics import overlaps, prf, top_k_cut
from srev.model import Proposal, Verdict

G = [
    Proposal(type="FK", lhs=["o.c"], rhs=["c.id"]),
    Proposal(type="FK", lhs=["l.p", "l.s"], rhs=["ps.p", "ps.s"]),
    Proposal(type="UCC", lhs=["c.id"]),
    Proposal(type="FD", lhs=["c.zip"], rhs=["c.city"]),
]


def test_prf():
    assert prf(0, 0, 0) == (None, None, None)
    assert prf(0, 3, 0) == (0.0, None, None)
    assert prf(1, 1, 1) == (0.5, 0.5, 0.5)
    assert prf(0, 1, 1) == (0.0, 0.0, 0.0)


def test_exact_and_overlap_scores():
    props = [
        Proposal(type="FK", lhs=["o.c"], rhs=["c.id"], confidence=0.9),
        Proposal(type="FK", lhs=["l.p"], rhs=["ps.p"], confidence=0.8),  # wrong arity
        Proposal(type="FK", lhs=["c.id"], rhs=["o.c"], confidence=0.1),  # wrong direction
        Proposal(type="UCC", lhs=["c.id"]),
        Proposal(type="FD", lhs=["c.zip"], rhs=["c.city", "c.state"]),
    ]
    card = score(props, G, n_boot=200, seed=1)
    fk = card.scores["FK"]
    assert (fk.tp, fk.fp, fk.fn) == (1, 2, 1)
    assert fk.precision == pytest.approx(1 / 3, abs=1e-4) and fk.recall == 0.5
    assert fk.ci_f1 is not None and fk.ci_f1[0] <= fk.f1 <= fk.ci_f1[1]
    assert fk.overlap_recall == 1.0  # the partial FK overlaps the binary gold FK
    fd = card.scores["FD"]
    assert (fd.tp, fd.fp, fd.fn) == (1, 1, 0)  # FDs are split per rhs
    assert card.scores["UCC"].f1 == 1.0
    # top-k keeps ceil(1.0 * |gold FK|) = 2 FKs, the two most confident ones
    assert card.top_k["FK"].n_proposed == 2 and card.top_k["FK"].tp == 1


def test_overlap_requires_same_orientation():
    assert overlaps(Proposal(type="FK", lhs=["l.p"], rhs=["ps.p"]), G[1])
    assert not overlaps(Proposal(type="FK", lhs=["ps.p"], rhs=["l.p"]), G[1])
    assert not overlaps(Proposal(type="FD", lhs=["c.zip"], rhs=["c.state"]), G[3])


def test_top_k_cut_orders_by_confidence():
    props = [Proposal(type="UCC", lhs=[f"t.c{i}"], confidence=i / 10) for i in range(5)]
    cut = top_k_cut(props, [Proposal(type="UCC", lhs=["t.c0"])] * 2, factor=1.0)
    assert [p.lhs[0] for p in cut] == ["t.c4", "t.c3"]


def test_bootstrap_and_aggregate():
    ci = bootstrap_ci(["tp"] * 8 + ["fp"] * 2, n_boot=500, seed=0)
    lo, hi = ci["precision"]
    assert 0.5 <= lo <= 0.8 <= hi <= 1.0
    assert ci["recall"] == (1.0, 1.0)
    assert bootstrap_ci([], 100)["f1"] is None
    a = score([G[0]], G, n_boot=0).scores["FK"]
    b = score(G, G, n_boot=0).scores["FK"]
    agg = aggregate([a, b], n_boot=200)
    assert agg.n_databases == 2
    assert agg.micro_precision == 1.0 and agg.micro_recall == 0.75
    assert agg.macro_f1 == pytest.approx((2 / 3 + 1) / 2, abs=1e-3)
    assert agg.ci_macro_f1 is not None


def test_paired_permutation_test():
    assert paired_permutation_test([1, 1, 1, 1], [0, 0, 0, 0]) == 0.125
    assert paired_permutation_test([0.5, 0.5], [0.5, 0.5]) == 1.0
    assert paired_permutation_test([], []) is None
    p = paired_permutation_test([1.0] * 20, [0.0] * 20, n_perm=2000)
    assert p is not None and p < 0.01


def test_hallucination_taxonomy():
    schema = {"o.c", "c.id", "c.zip", "c.city", "l.p", "l.s", "ps.p", "ps.s", "c.name"}
    props = [
        G[0],
        Proposal(type="FK", lhs=["o.ghost"], rhs=["c.id"]),
        Proposal(type="FK", lhs=["c.id"], rhs=["o.c"]),
        Proposal(type="FK", lhs=["l.p"], rhs=["ps.p"]),
        Proposal(type="UCC", lhs=["c.name"]),
        Proposal(type="FD", lhs=["c.name"], rhs=["c.zip"]),
    ]
    verdicts = {
        props[4].key(): Verdict(accepted=False, status="rejected", statistic="uniqueness"),
        props[5].key(): Verdict(accepted=True, status="ok", statistic="g3", value=0.0),
    }
    rep = analyse(props, G, schema.__contains__, verdicts)
    cats = {i.proposal.describe(): i.category for i in rep.items}
    assert cats == {
        "FK (o.ghost) -> (c.id)": "non_existent_column",
        "FK (c.id) -> (o.c)": "wrong_direction",
        "FK (l.p) -> (ps.p)": "wrong_arity",
        "UCC (c.name)": "contradicted_by_data",
        "FD (c.name) -> c.zip": "plausible_not_in_gold",
    }
    assert rep.n_false_positives == 5 and sum(rep.counts.values()) == 5
    assert rep.hallucination_rate == pytest.approx(2 / 6, abs=1e-4)


def test_cohen_kappa():
    assert cohen_kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]) == 1.0
    assert cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == 0.0
    assert cohen_kappa(["a"], ["a", "b"]) is None
    assert cohen_kappa(["a", "a"], ["a", "a"]) == 1.0
