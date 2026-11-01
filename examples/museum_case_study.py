"""Case study: from an undocumented museum collection to a verified ontology.

The museum generator is *not* among the databases the discovery model is
trained on, so this is a held-out domain. For each condition (clean, noise
L1/L2, cryptic and misleading names) the script runs ``srev discover``,
scores the verified constraints against the gold labels (renamed to match the
condition) and extracts the OWL ontology and R2RML mapping.

    python examples/museum_case_study.py results/museum-case-study
"""

from __future__ import annotations

import sys
from pathlib import Path

from srev.datasets import load_dataset
from srev.db import SQLiteConnector, materialize
from srev.discover import discover
from srev.eval.metrics import score
from srev.extract import extract
from srev.htmlreport import write as write_html
from srev.noise import inject, resolve_profile
from srev.ontology import build_plan, to_owl, to_r2rml
from srev.truth import gold_from_dataset

CONDITIONS = ["none", "low", "medium", "names-cryptic", "names-misleading"]
SEEDS = [1, 2, 3]


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for seed in SEEDS:
        clean = load_dataset("museum", seed=seed)
        gold = gold_from_dataset(clean)
        for cond in CONDITIONS:
            noisy, log = inject(clean, resolve_profile(cond), seed=seed, gold=gold)
            labels = log.renames.gold(gold).labels
            with SQLiteConnector(":memory:") as conn:
                materialize(noisy, conn)
                result = discover(conn, f"museum_{cond}")
                card = score(result.accepted(), labels, n_boot=0, top_k_factor=None)
                plan = build_plan(extract(conn, f"museum_{cond}", rows_per_table=0),
                                  result.constraints)  # fmt: skip
                if seed == SEEDS[0] and cond in ("none", "low"):
                    ev = extract(conn, f"museum_{cond}", rows_per_table=10)
                    d = out / cond
                    d.mkdir(exist_ok=True)
                    (d / "ontology.ttl").write_text(to_owl(build_plan(ev, result.constraints)))
                    (d / "mapping.r2rml.ttl").write_text(
                        to_r2rml(build_plan(ev, result.constraints))
                    )
                    write_html(d / "report.html", ev, result)
            rows.append({
                "seed": seed, "condition": cond,
                **{f"{t}_{m}": getattr(card.scores[t], m) for t in ("FK", "UCC", "FD")
                   for m in ("precision", "recall", "f1")},
                "review": len(result.review), "classes": len(plan.classes),
                "bridges": len(plan.bridges), "candidates": len(plan.candidates),
            })  # fmt: skip
            print(seed, cond, rows[-1]["FK_f1"], rows[-1]["UCC_f1"], rows[-1]["FD_f1"], flush=True)

    def mean(cond: str, key: str) -> str:
        vals = [r[key] for r in rows if r["condition"] == cond and r[key] is not None]
        return f"{sum(vals) / len(vals):.2f}" if vals else "–"

    lines = [
        "# Museum case study (held-out domain)",
        "",
        f"`srev discover` on the museum generator, mean over seeds {SEEDS}. The discovery model "
        "was trained on tpch_mini, university, retail_legacy and airline only.",
        "",
        "| condition | FK P | FK R | FK F1 | UCC F1 | FD F1 | FD recall | FKs to review "
        "| classes | many-to-many | candidate concepts |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for cond in CONDITIONS:
        lines.append(
            f"| {cond} | {mean(cond, 'FK_precision')} | {mean(cond, 'FK_recall')} | "
            f"{mean(cond, 'FK_f1')} | {mean(cond, 'UCC_f1')} | {mean(cond, 'FD_f1')} | "
            f"{mean(cond, 'FD_recall')} | "
            f"{mean(cond, 'review')} | {mean(cond, 'classes')} | {mean(cond, 'bridges')} | "
            f"{mean(cond, 'candidates')} |"
        )
    lines += [
        "",
        "FD F1 is undefined ('–') when discovery proposes no FD; under noise the curated FDs "
        "exceed the 0.02 g3 threshold that `discover` uses, so FD recall drops to 0.",
        "",
        "Ontology, R2RML mapping and HTML report for seed 1: [`none/`](none/) and [`low/`](low/).",
    ]  # fmt: skip
    (out / "README.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "results/museum-case-study")
