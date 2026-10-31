import json

import pytest

from srev.cli import main
from srev.summarize import summarize, to_latex


def _run(tmp_path, name, f1):
    d = tmp_path / name
    d.mkdir()
    rows = [
        {
            "type": "FK",
            "variant": "a",
            "noise": "L0",
            "macro_f1": f1,
            "micro_precision": 1.0,
            "micro_recall": f1,
            "micro_f1": f1,
        },
        {
            "type": "FK",
            "variant": "a",
            "noise": "L1",
            "macro_f1": None,
            "micro_precision": None,
            "micro_recall": 0.0,
            "micro_f1": None,
        },
        {
            "type": "UCC",
            "variant": "b",
            "noise": "L0",
            "macro_f1": None,
            "micro_precision": None,
            "micro_recall": None,
            "micro_f1": None,
        },
    ]
    (d / "aggregates.json").write_text(json.dumps(rows))
    return d


def test_summarize_mean_sd(tmp_path):
    runs = [_run(tmp_path, f"s{i}", f1) for i, f1 in enumerate([0.5, 0.7, 0.9])]
    rows = summarize(runs)
    a0 = next(r for r in rows if r["variant"] == "a" and r["noise"] == "L0")
    assert a0["macro_f1_mean"] == pytest.approx(0.7) and a0["macro_f1_sd"] == pytest.approx(0.2)
    assert a0["macro_f1_n"] == 3 and a0["runs"] == 3
    a1 = next(r for r in rows if r["variant"] == "a" and r["noise"] == "L1")
    assert a1["macro_f1_mean"] is None and a1["micro_recall_mean"] == 0.0
    assert not any(r["variant"] == "b" for r in rows)  # never defined -> dropped
    tex = to_latex(rows)
    assert r"\textbf{0.70{\scriptsize$\pm$0.20}}" in tex
    out = tmp_path / "out"
    assert main(["summarize", *map(str, runs), "-o", str(out)]) == 0
    md = (out / "summary.md").read_text()
    assert "0.70 ± 0.20" in md and (out / "summary.csv").exists()
