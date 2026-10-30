"""Markdown report and SVG charts generated from a run directory's stored results.

Everything is re-computable from the JSON tables, so ``srev report RUN_DIR``
regenerates the report without re-running any method.
"""

from __future__ import annotations

import json
from collections import defaultdict
from html import escape
from pathlib import Path
from typing import Any

from srev.eval.hallucination import CATEGORIES
from srev.model import CONSTRAINT_TYPES

PALETTE = [
    "#2563eb",
    "#dc2626",
    "#16a34a",
    "#9333ea",
    "#ea580c",
    "#0891b2",
    "#4d7c0f",
    "#be185d",
    "#475569",
    "#a16207",
]


def _load(run_dir: Path, name: str) -> list[dict[str, Any]]:
    path = run_dir / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else []


def _f(v: Any, digits: int = 3) -> str:
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def _ci(v: Any) -> str:
    if not v:
        return ""
    return f" [{v[0]:.2f}, {v[1]:.2f}]"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def noise_curve_svg(
    aggregates: list[dict[str, Any]], ctype: str, noise_order: list[str], width: int = 640
) -> str | None:
    """Line chart of macro F1 against noise level, one line per variant."""
    if len(noise_order) < 2:
        return None
    series: dict[str, dict[str, float]] = defaultdict(dict)
    for a in aggregates:
        if a["type"] == ctype and a["macro_f1"] is not None:
            series[a["variant"]][a["noise"]] = a["macro_f1"]
    series = {k: v for k, v in series.items() if len(v) >= 2}
    if not series:
        return None
    variants = sorted(series)[: len(PALETTE)]
    height, left, right, top, bottom = 300, 48, 220, 28, 40
    plot_w, plot_h = width - left - right, height - top - bottom

    def x(i: int) -> float:
        return left + plot_w * i / (len(noise_order) - 1)

    def y(v: float) -> float:
        return top + plot_h * (1 - v)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="sans-serif" font-size="11">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{left}" y="16" font-size="13" font-weight="bold">'
        f"{escape(ctype)}: macro F1 by noise level</text>",
    ]
    for tick in (0, 0.25, 0.5, 0.75, 1.0):
        parts.append(
            f'<line x1="{left}" x2="{left + plot_w}" y1="{y(tick):.1f}" '
            f'y2="{y(tick):.1f}" stroke="#e5e7eb"/>'
        )
        parts.append(
            f'<text x="{left - 6}" y="{y(tick) + 4:.1f}" text-anchor="end" '
            f'fill="#374151">{tick:.2f}</text>'
        )
    for i, n in enumerate(noise_order):
        parts.append(
            f'<text x="{x(i):.1f}" y="{top + plot_h + 18}" text-anchor="middle" '
            f'fill="#374151">{escape(n)}</text>'
        )
    for vi, variant in enumerate(variants):
        color = PALETTE[vi % len(PALETTE)]
        pts = [
            (x(i), y(series[variant][n])) for i, n in enumerate(noise_order) if n in series[variant]
        ]
        path = " ".join(
            f"{'M' if j == 0 else 'L'}{px:.1f},{py:.1f}" for j, (px, py) in enumerate(pts)
        )
        parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2"/>')
        parts += [f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3" fill="{color}"/>' for px, py in pts]
        ly = top + 14 * vi
        parts.append(
            f'<rect x="{left + plot_w + 12}" y="{ly}" width="10" height="10" fill="{color}"/>'
        )
        label = variant if len(variant) <= 30 else variant[:29] + "…"
        parts.append(
            f'<text x="{left + plot_w + 26}" y="{ly + 9}" fill="#111827">{escape(label)}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def write_report(run_dir: str | Path) -> Path:
    rd = Path(run_dir)
    manifest = (
        json.loads((rd / "manifest.json").read_text()) if (rd / "manifest.json").exists() else {}
    )
    metrics = _load(rd, "metrics")
    aggregates = _load(rd, "aggregates")
    costs = _load(rd, "costs")
    halluc = _load(rd, "hallucinations")
    synth = _load(rd, "synth")
    noise_order = list(dict.fromkeys(m["noise"] for m in metrics))

    lines = [f"# Run report: {manifest.get('run_id', rd.name)}", ""]
    git = manifest.get("git", {})
    lines += [
        f"- srev {manifest.get('srev_version', '?')}, git `{(git.get('sha') or 'n/a')[:12]}`"
        + (" (dirty tree)" if git.get("dirty") else ""),
        f"- started {manifest.get('started_at', '?')}, finished {manifest.get('finished_at', '?')}",
        f"- config sha256 `{manifest.get('config_sha256', '')[:16]}`, seed {manifest.get('seed')}",
    ]
    for name, info in (manifest.get("llm") or {}).items():
        lines.append(
            f"- LLM `{name}`: `{info.get('model')}` (revision {info.get('revision')}), "
            f"temperature {info.get('temperature')}, seeds {info.get('seeds')}"
        )
    hw = manifest.get("hardware", {})
    lines.append(
        f"- hardware: {hw.get('platform')}, {hw.get('cpu_count')} CPUs, "
        f"{hw.get('memory_gb')} GB RAM, GPUs: {', '.join(hw.get('gpus') or []) or 'none'}"
    )
    lines += [
        "",
        "Numbers below are produced by this run only; read them together with the "
        "threats to validity in docs/RESEARCH.md.",
        "",
    ]

    if manifest.get("datasets"):
        lines += ["## Databases", ""]
        rows = [
            [
                d["db_id"],
                d["noise"],
                str(d["tables"]),
                str(d["rows"]),
                ", ".join(f"{k} {v}" for k, v in d["gold_counts"].items()),
                ", ".join(f"{k} {v}" for k, v in d["noise_summary"].items()) or "–",
            ]
            for d in manifest["datasets"]
        ]
        lines += [
            _table(["database", "noise", "tables", "rows", "gold", "perturbations"], rows),
            "",
        ]

    lines += [
        "## Aggregate results (across databases)",
        "",
        "Micro = pooled counts; macro = mean per-database F1 with a bootstrap 95% CI; "
        "p = paired sign-flip test of per-database F1 against the reference baseline.",
        "",
    ]
    for noise in noise_order:
        for ctype in CONSTRAINT_TYPES:
            rows = [
                [
                    a["variant"],
                    _f(a["micro_precision"]),
                    _f(a["micro_recall"]),
                    _f(a["micro_f1"]),
                    _f(a["macro_f1"]) + _ci(a.get("ci_macro_f1")),
                    _f(a.get("p_vs_reference")),
                ]
                for a in aggregates
                if a["noise"] == noise and a["type"] == ctype
            ]
            if rows:
                lines += [
                    f"### {ctype} · noise {noise}",
                    "",
                    _table(
                        ["variant", "P (micro)", "R (micro)", "F1 (micro)", "F1 (macro) [CI]", "p"],
                        rows,
                    ),
                    "",
                ]

    lines += [
        "## Per-database results",
        "",
        "Exact match; CI = item-level bootstrap. Top-k cuts each type at "
        "ceil(factor × |gold|) proposals by confidence. Overlap = column-set Jaccard ≥ 0.5.",
        "",
    ]
    for db in dict.fromkeys(m["db_id"] for m in metrics):
        lines += [f"### {db}", ""]
        rows = [
            [
                m["variant"],
                m["type"],
                str(m["n_proposed"]),
                str(m["n_gold"]),
                f"{m['tp']}/{m['fp']}/{m['fn']}",
                _f(m["precision"]) + _ci(m.get("ci_precision")),
                _f(m["recall"]) + _ci(m.get("ci_recall")),
                _f(m["f1"]) + _ci(m.get("ci_f1")),
                _f(m.get("topk_f1")),
                _f(m.get("overlap_f1")),
            ]
            for m in metrics
            if m["db_id"] == db
        ]
        lines += [
            _table(
                [
                    "variant",
                    "type",
                    "proposed",
                    "gold",
                    "TP/FP/FN",
                    "precision",
                    "recall",
                    "F1",
                    "top-k F1",
                    "overlap F1",
                ],
                rows,
            ),
            "",
        ]

    if halluc:
        lines += [
            "## Hallucinations and false positives",
            "",
            "Rate = proposals with non-existent columns or contradicted by data ÷ all "
            "proposals. Each false positive has exactly one category.",
            "",
        ]
        rows = [
            [
                h["db_id"],
                h["variant"],
                str(h["n_proposals"]),
                _f(h["hallucination_rate"]),
                *[str(h.get(c, 0)) for c in CATEGORIES],
            ]
            for h in halluc
        ]
        lines += [_table(["database", "variant", "proposals", "rate", *CATEGORIES], rows), ""]

    if costs:
        lines += ["## Cost and structured-output quality", ""]
        rows = [
            [
                c["db_id"],
                c["variant"],
                _f(c["seconds"]),
                str(c["tokens"]),
                f"{c['response_failures']}/{c['responses']}",
                f"{c['item_failures']}/{c['items']}",
                str(c.get("fence_stripped", 0)),
                c.get("trimmed") or "",
            ]
            for c in costs
        ]
        lines += [
            _table(
                [
                    "database",
                    "variant",
                    "seconds",
                    "tokens",
                    "bad responses",
                    "bad items",
                    "fences stripped",
                    "prompt trimming",
                ],
                rows,
            ),
            "",
        ]

    charts = []
    for ctype in CONSTRAINT_TYPES:
        svg = noise_curve_svg(aggregates, ctype, noise_order)
        if svg:
            name = f"noise_curve_{ctype.lower()}.svg"
            (rd / name).write_text(svg)
            charts.append(name)
    if charts:
        lines += ["## Robustness to noise", ""] + [f"![{c}]({c})" for c in charts] + [""]

    if synth:
        lines += ["## Synthesised models", ""]
        rows = [
            [
                s["db_id"],
                s["variant"],
                str(s["accepted"]),
                f"[DDL](synth/{s['db_id']}.sql) · [ER](synth/{s['db_id']}.mmd) · "
                f"[migration](synth/{s['db_id']}.migration.sql)",
            ]
            for s in synth
        ]
        lines += [_table(["database", "from variant", "accepted constraints", "files"], rows), ""]

    out = rd / "report.md"
    out.write_text("\n".join(lines).rstrip() + "\n")
    return out
