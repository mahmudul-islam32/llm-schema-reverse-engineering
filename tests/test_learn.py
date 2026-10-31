import json

import pytest

from srev.config import ExperimentConfig
from srev.db import SQLiteConnector, materialize
from srev.learn import FEATURES, Candidate, LearnedConfig, LogisticModel, candidates, label
from srev.qlog import mine_joins
from srev.runner import run_experiment
from srev.truth import gold_from_dataset


def test_candidates_have_features_and_labels(university):
    gold = gold_from_dataset(university)
    joins = mine_joins(university.queries, university.schema_map())
    with SQLiteConnector(":memory:") as conn:
        materialize(university, conn)
        cands = label(candidates(conn, joins), gold.labels)
    assert cands and all(set(c.features) == set(FEATURES) for c in cands)
    positives = {(c.dep, c.ref) for c in cands if c.label}
    unary_gold = {(g.lhs[0], g.rhs[0]) for g in gold.of_type("FK") if len(g.lhs) == 1}
    assert positives == unary_gold  # every unary gold FK is a candidate on clean data
    takes = next(c for c in cands if (c.dep, c.ref) == ("takes.student_id", "student.student_id"))
    assert takes.features["row_coverage"] == 1.0 and takes.features["join_log"] > 0


def _cand(x: float, y: int) -> Candidate:
    feats = {f: 0.0 for f in FEATURES}
    feats["row_coverage"] = x
    return Candidate(dep="a.x", ref="b.y", features=feats, label=y)


def test_logistic_model_learns_and_explains():
    data = [_cand(v / 10, int(v >= 5)) for v in range(10)] * 3
    model = LogisticModel().fit(data, LearnedConfig(epochs=300))
    assert model.predict(_cand(0.95, 1)) > 0.8 > 0.2 > model.predict(_cand(0.05, 0))
    assert model.coefficients()["row_coverage"] > 0
    assert "row_coverage" in model.explain(_cand(0.9, 1), top=1)
    with pytest.raises(ValueError):
        LogisticModel().fit([], LearnedConfig())
    with pytest.raises(ValueError):
        LearnedConfig(features=["vibes"])
    sub = LogisticModel(features=("row_coverage",)).fit(data, LearnedConfig())
    assert list(sub.coefficients()) == ["row_coverage"]


def test_leave_one_dataset_out_in_runner(tmp_path):
    cfg = ExperimentConfig.model_validate(
        {
            "name": "lodo",
            "output_dir": str(tmp_path),
            "datasets": [
                {"name": "university", "params": {"scale": 0.3}},
                {"name": "retail_legacy", "params": {"scale": 0.3}},
            ],
            "noise": [0, 1],
            "methods": [
                {"name": "lr", "kind": "learned"},
                {
                    "name": "lr-nologs",
                    "kind": "learned",
                    "learned": {"features": [f for f in FEATURES if f != "join_log"]},
                },
            ],
            "eval": {"bootstrap": 20},
            "synth": {"enabled": False},
        }
    )
    run_dir = run_experiment(cfg, tmp_path / "run")
    rows = json.loads((run_dir / "learned.json").read_text())
    folds = {(r["method"], r["held_out"]) for r in rows}
    assert folds == {(m, d) for m in ("lr", "lr-nologs") for d in ("university", "retail_legacy")}
    # each fold trains only on the *other* dataset (both of its noise levels)
    fold = next(r for r in rows if r["method"] == "lr" and r["held_out"] == "university")
    assert fold["train_positives"] == 2 * 6  # retail_legacy has 6 curated FKs, 2 noise levels
    assert not any(r["feature"] == "join_log" for r in rows if r["method"] == "lr-nologs")
    metrics = json.loads((run_dir / "metrics.json").read_text())
    variants = {m["variant"] for m in metrics if m["type"] == "FK"}
    assert {"lr", "lr+verify", "lr-nologs", "lr-nologs+verify"} <= variants
    assert "## Learned FK model" in (run_dir / "report.md").read_text()
    props = [json.loads(line) for line in (run_dir / "proposals.jsonl").open()]
    assert all(p["proposal"]["source"] == "learned" for p in props)


def test_single_dataset_is_reported_not_crashed(tmp_path):
    cfg = ExperimentConfig.model_validate(
        {
            "name": "one",
            "output_dir": str(tmp_path),
            "datasets": [{"name": "retail_legacy", "params": {"scale": 0.2}}],
            "methods": [{"name": "lr", "kind": "learned"}],
            "eval": {"bootstrap": 0},
        }
    )
    run_dir = run_experiment(cfg, tmp_path / "run")
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert any("at least two datasets" in n for n in manifest["notes"])
