import json
from pathlib import Path

import pytest
import yaml

from srev.cli import compare_metrics, main
from srev.config import ExperimentConfig, expand_env, load_config
from srev.runner import run_experiment

from .fake_vllm import FakeVLLM

LLM_OUTPUT = {
    "proposals": [
        {
            "type": "FK",
            "lhs": ["takes.student_id"],
            "rhs": ["student.student_id"],
            "confidence": 0.9,
            "rationale": "r",
        },
        {
            "type": "FK",
            "lhs": ["student.student_id"],
            "rhs": ["takes.student_id"],
            "confidence": 0.4,
            "rationale": "reverse",
        },
        {"type": "UCC", "lhs": ["student.ghost"], "rhs": [], "confidence": 0.2, "rationale": "x"},
        {
            "type": "FD",
            "lhs": ["course.dept_id"],
            "rhs": ["course.dept_name"],
            "confidence": 0.8,
            "rationale": "denormalised",
        },
        {"type": "FK"},
    ]
}


def _config(tmp_path: Path, **overrides) -> ExperimentConfig:
    raw = {
        "name": "t",
        "output_dir": str(tmp_path / "runs"),
        "datasets": [
            {"name": "university", "params": {"scale": 0.3}},
            {"name": "retail_legacy", "params": {"scale": 0.3}},
        ],
        "noise": [0, "high"],
        "methods": [
            {"name": "profiling", "kind": "baseline", "verify": False},
            {"name": "heur", "kind": "heuristic", "evidence": [["names"], ["names", "stats"]]},
            {"name": "hyb", "kind": "hybrid"},
            {"name": "meta", "kind": "metanome"},
        ],
        "eval": {"bootstrap": 50},
    }
    raw.update(overrides)
    return ExperimentConfig.model_validate(raw)


def test_offline_run_writes_all_artifacts(tmp_path):
    run_dir = run_experiment(_config(tmp_path))
    for name in (
        "manifest.json",
        "config.yaml",
        "metrics.csv",
        "aggregates.csv",
        "costs.csv",
        "hallucinations.csv",
        "proposals.jsonl",
        "false_positives.jsonl",
        "report.md",
        "noise_curve_fk.svg",
        "gold/university_L0.json",
        "evidence/retail_legacy_high.json",
        "evidence/university_L0.joins.json",
        "noise/university_high.json",
        "synth/university_L0.sql",
        "synth/university_L0.mmd",
        "db/university_L0.sqlite",
    ):
        assert (run_dir / name).exists(), name
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert len(manifest["datasets"]) == 4
    assert manifest["config_sha256"] and manifest["hardware"]["cpu_count"]
    assert manifest["datasets"][1]["noise_summary"]["rename_columns"] > 0
    metrics = json.loads((run_dir / "metrics.json").read_text())
    variants = {m["variant"] for m in metrics}
    assert {
        "profiling",
        "heur[names]",
        "heur[names]+verify",
        "heur[names+stats]",
        "hyb",
        "hyb+verify",
    } <= variants
    assert not any(v.startswith("meta") for v in variants)  # skipped without Java JARs
    base = next(
        m
        for m in metrics
        if m["variant"] == "profiling" and m["type"] == "FK" and m["db_id"] == "university_L0"
    )
    assert base["recall"] == 1.0
    report = (run_dir / "report.md").read_text()
    assert "## Aggregate results" in report and "university_high" in report
    synth = json.loads((run_dir / "synth.json").read_text())
    assert all(not s["variant"].startswith("profiling") for s in synth)


def test_llm_run_and_offline_replay(tmp_path):
    cfg = _config(
        tmp_path,
        datasets=[{"name": "university", "params": {"scale": 0.3}}],
        noise=[0],
        methods=[
            {
                "name": "llm",
                "kind": "llm",
                "evidence": [["names"], ["names", "stats", "samples", "logs"]],
            }
        ],
        llm={"model": "fake/model-7b", "retries": 0},
    )
    with FakeVLLM(LLM_OUTPUT) as server:
        cfg.llm.base_url = server.base_url
        run_dir = run_experiment(cfg, tmp_path / "run")
        assert len(server.requests) == 2
    costs = json.loads((run_dir / "costs.json").read_text())
    raw = next(c for c in costs if c["variant"] == "llm[names]")
    assert raw["item_failures"] == 1 and raw["items"] == 5 and raw["tokens"] == 120
    halluc = {h["variant"]: h for h in json.loads((run_dir / "hallucinations.json").read_text())}
    h = halluc["llm[names]"]
    assert h["non_existent_column"] == 1 and h["wrong_direction"] == 1
    assert halluc["llm[names]+verify"]["n_proposals"] == 2
    metrics = json.loads((run_dir / "metrics.json").read_text())
    fd = next(m for m in metrics if m["variant"] == "llm[names]" and m["type"] == "FD")
    assert fd["tp"] == 1
    prompts = list((run_dir / "prompts").glob("*.txt"))
    assert len(prompts) == 2
    assert len(list((run_dir / "llm_cache").glob("*.json"))) == 2
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["llm"]["llm"]["model"] == "fake/model-7b"
    # server is gone: --replay must reproduce every number from the response cache
    code = main(["reproduce", str(run_dir), "--replay", "-o", str(tmp_path / "repro")])
    assert code == 0
    assert compare_metrics(run_dir, tmp_path / "repro", 0.0) == []


def test_config_env_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("SREV_X", "postgresql://u@h/db")
    assert expand_env("dsn: ${SREV_X}") == "dsn: postgresql://u@h/db"
    assert expand_env("${SREV_MISSING:-fallback}") == "fallback"
    with pytest.raises(KeyError):
        expand_env("${SREV_MISSING}")
    for name in ("offline.yaml", "experiment.yaml"):
        load_config(Path(__file__).parents[1] / "configs" / name)
    with pytest.raises(ValueError):
        _config(tmp_path, methods=[{"name": "a", "kind": "heuristic", "evidence": [["x"]]}])


def test_cli_pipeline(tmp_path, capsys):
    db = str(tmp_path / "u.sqlite")
    gold = str(tmp_path / "gold.json")
    wl = str(tmp_path / "w.sql")
    assert main(["datasets", "list"]) == 0
    assert "university" in capsys.readouterr().out
    assert (
        main(
            [
                "datasets",
                "load",
                "university",
                "--sqlite",
                db,
                "--scale",
                "0.3",
                "--noise",
                "low",
                "--gold",
                gold,
                "--workload",
                wl,
            ]
        )
        == 0
    )
    ev, joins = str(tmp_path / "ev.json"), str(tmp_path / "joins.json")
    assert main(["extract", "--sqlite", db, "--name", "u", "--rows", "5", "-o", ev]) == 0
    assert main(["qlog", wl, "--evidence", ev, "-o", joins]) == 0
    props, ver = str(tmp_path / "p.json"), str(tmp_path / "v.json")
    assert main(["propose", "--evidence", ev, "--joins", joins, "--heuristic", "-o", props]) == 0
    assert main(["verify", "--proposals", props, "--sqlite", db, "-o", ver]) == 0
    out = str(tmp_path / "eval.json")
    assert (
        main(
            [
                "evaluate",
                "--proposals",
                ver,
                "--accepted-only",
                "--gold",
                gold,
                "--evidence",
                ev,
                "--bootstrap",
                "50",
                "-o",
                out,
            ]
        )
        == 0
    )
    result = json.loads(Path(out).read_text())
    assert result["scores"]["FK"]["precision"] == 1.0
    assert "hallucinations" in result
    assert main(["synth", "--evidence", ev, "--proposals", ver, "-o", str(tmp_path / "synth")]) == 0
    assert (tmp_path / "synth" / "model.sql").read_text().startswith("-- Generated by srev")
    prof = str(tmp_path / "prof.json")
    assert main(["profile", "--sqlite", db, "--fd-max-lhs", "1", "-o", prof]) == 0
    assert json.loads(Path(prof).read_text())["stats"]["tables"] == 8
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    a.write_text("id,category\n1,x\n2,y\n3,x\n")
    b.write_text("id,category\n1,x\n2,y\n3,y\n")
    capsys.readouterr()
    assert main(["audit", "kappa", str(a), str(b)]) == 0
    assert json.loads(capsys.readouterr().out)["items"] == 3


def test_cli_truth_export_sqlite(tmp_path):
    db = str(tmp_path / "c.sqlite")
    assert (
        main(["datasets", "load", "retail", "--sqlite", db, "--scale", "0.2", "--with-constraints"])
        == 0
    )
    out = tmp_path / "gold.json"
    assert main(["truth", "export", "--sqlite", db, "-o", str(out)]) == 0
    labels = json.loads(out.read_text())["labels"]
    assert {(lbl["type"], lbl["source"]) for lbl in labels} == {("UCC", "declared")}


def test_report_regeneration(tmp_path):
    run_dir = run_experiment(
        _config(tmp_path, noise=[0], methods=[{"name": "heur", "kind": "heuristic"}])
    )
    (run_dir / "report.md").unlink()
    assert main(["report", str(run_dir)]) == 0
    assert (run_dir / "report.md").exists()
    cfg = yaml.safe_load((run_dir / "config.yaml").read_text())
    assert cfg["methods"][0]["name"] == "heur"


def test_latex_export(tmp_path):
    from srev.export import escape, latex_tables

    run_dir = run_experiment(
        _config(tmp_path, noise=[0, 1], methods=[{"name": "heur_x", "kind": "heuristic"}])
    )
    tex = latex_tables(run_dir, with_ci=True)
    assert tex.count(r"\begin{table}") == 3 and r"\toprule" in tex
    assert r"heur\_x[names+stats+samples+logs]+verify" in tex
    assert r"\textbf{" in tex and "L0 & L1" in tex
    only_fk = latex_tables(run_dir, types=("FK",), metric="micro_recall")
    assert only_fk.count(r"\begin{table}") == 1 and "micro recall" in only_fk
    assert escape("a_b%c&d") == r"a\_b\%c\&d"
    with pytest.raises(ValueError):
        latex_tables(run_dir, metric="accuracy")
    out = tmp_path / "t.tex"
    assert main(["export", "latex", str(run_dir), "--types", "fk", "-o", str(out)]) == 0
    assert out.read_text().startswith("% Generated by srev")
