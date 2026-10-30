"""Generate the SVG infographics used in the README and docs.

Run from the repository root:  python docs/media/tools/infographics.py
Numbers shown (noise presets, verifier thresholds, categories) are read from the
package itself so the pictures cannot drift from the code.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

from srev.eval.hallucination import CATEGORIES
from srev.noise import PRESETS
from srev.verify import VerifyConfig

OUT = Path(__file__).resolve().parents[1]
FONT = "Inter, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "'JetBrains Mono', 'DejaVu Sans Mono', Menlo, monospace"
INK, MUTED, LINE, BG = "#0f172a", "#475569", "#cbd5e1", "#ffffff"
C = {
    "indigo": ("#4f46e5", "#eef2ff"),
    "teal": ("#0f766e", "#f0fdfa"),
    "amber": ("#b45309", "#fffbeb"),
    "rose": ("#be123c", "#fff1f2"),
    "sky": ("#0369a1", "#f0f9ff"),
    "violet": ("#6d28d9", "#f5f3ff"),
    "slate": ("#334155", "#f8fafc"),
    "green": ("#15803d", "#f0fdf4"),
}


class Svg:
    def __init__(self, w: int, h: int, title: str) -> None:
        self.w, self.h = w, h
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" font-family="{FONT}" role="img" aria-label="{escape(title)}">',
            f"<title>{escape(title)}</title>",
            "<defs>"
            '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
            f'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{MUTED}"/>'
            "</marker>"
            '<filter id="shadow" x="-5%" y="-5%" width="110%" height="120%">'
            '<feDropShadow dx="0" dy="1.5" stdDeviation="2" flood-color="#0f172a" flood-opacity="0.10"/>'
            "</filter></defs>",
            f'<rect width="{w}" height="{h}" rx="18" fill="{BG}" stroke="#e2e8f0"/>',
        ]

    def text(
        self,
        x: float,
        y: float,
        s: str,
        size: int = 13,
        color: str = INK,
        weight: str = "normal",
        anchor: str = "start",
        mono: bool = False,
    ) -> None:
        fam = f' font-family="{MONO}"' if mono else ""
        self.parts.append(
            f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}" '
            f'text-anchor="{anchor}"{fam}>{escape(s)}</text>'
        )

    def header(self, title: str, subtitle: str) -> None:
        self.text(32, 44, title, 22, INK, "700")
        self.text(32, 68, subtitle, 13, MUTED)

    def card(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        title: str,
        lines: list[str],
        color: str = "indigo",
        badge: str | None = None,
        mono_lines: bool = False,
    ) -> None:
        stroke, fill = C[color]
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" '
            f'stroke="{stroke}" stroke-opacity="0.45" filter="url(#shadow)"/>'
        )
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="5" height="{h}" rx="2.5" fill="{stroke}"/>'
        )
        tx = x + 18
        if badge:
            self.parts.append(f'<circle cx="{x + 28}" cy="{y + 24}" r="12" fill="{stroke}"/>')
            self.text(x + 28, y + 28.5, badge, 12, "#ffffff", "700", "middle")
            tx = x + 48
        self.text(tx, y + 29, title, 14.5, stroke, "700")
        for i, line in enumerate(lines):
            self.text(
                x + 18,
                y + 54 + i * 19,
                line,
                12.5 if not mono_lines else 11.5,
                INK if not mono_lines else MUTED,
                mono=mono_lines,
            )

    def arrow(
        self, x1: float, y1: float, x2: float, y2: float, label: str = "", dashed: bool = False
    ) -> None:
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        self.parts.append(
            f'<path d="M{x1},{y1} L{x2},{y2}" stroke="{MUTED}" stroke-width="1.6" fill="none" '
            f'marker-end="url(#arrow)"{dash}/>'
        )
        if label and abs(x1 - x2) < 1:
            self.text(x1 + 8, (y1 + y2) / 2 + 4, label, 11, MUTED)
        elif label:
            self.text((x1 + x2) / 2, min(y1, y2) - 6, label, 11, MUTED, anchor="middle")

    def path(self, d: str, dashed: bool = False) -> None:
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        self.parts.append(
            f'<path d="{d}" stroke="{MUTED}" stroke-width="1.6" fill="none" '
            f'marker-end="url(#arrow)"{dash}/>'
        )

    def band(self, x: float, y: float, w: float, h: float, label: str) -> None:
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="#f8fafc" '
            f'stroke="{LINE}" stroke-dasharray="4 4"/>'
        )
        self.text(x + 14, y + 20, label.upper(), 11, MUTED, "700")

    def pill(
        self, x: float, y: float, s: str, color: str = "slate", w: float | None = None
    ) -> float:
        stroke, fill = C[color]
        w = w or 14 + 7.2 * len(s)
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="22" rx="11" fill="{fill}" '
            f'stroke="{stroke}" stroke-opacity="0.6"/>'
        )
        self.text(x + w / 2, y + 15, s, 11.5, stroke, "600", "middle")
        return w

    def save(self, name: str) -> None:
        (OUT / name).write_text("\n".join([*self.parts, "</svg>"]) + "\n")
        print("wrote", OUT / name)


def architecture() -> None:
    s = Svg(1200, 760, "srev architecture")
    s.header(
        "srev architecture",
        "From an undocumented database to scored, verified constraints and a normalised model",
    )
    # inputs
    s.band(24, 92, 230, 640, "Inputs")
    s.card(
        40,
        124,
        198,
        120,
        "Database",
        ["built-in generator", "or PostgreSQL / SQLite", "with declared keys"],
        "slate",
    )
    s.card(
        40, 262, 198, 104, "Query workload", [".sql · pg_stat_statements", "CSV · JSONL"], "slate"
    )
    s.card(40, 384, 198, 104, "Curated gold", ["semantic FDs and FKs", "(optional JSON)"], "slate")
    s.card(40, 506, 198, 104, "LLM endpoint", ["vLLM or any", "OpenAI-compatible API"], "slate")
    s.card(40, 628, 198, 88, "Run config", ["configs/*.yaml"], "slate")
    # preparation + evidence
    s.band(276, 92, 250, 640, "Prepare & observe")
    s.card(
        292,
        124,
        218,
        104,
        "Ground truth",
        ["PK/UNIQUE → UCC, FK → FK", "curated labels kept apart"],
        "green",
        "1",
    )
    s.card(
        292,
        246,
        218,
        104,
        "Noise injector",
        ["renames · NULLs · typos", "orphans · L0–L3, seeded"],
        "amber",
        "2",
    )
    s.card(
        292,
        368,
        218,
        104,
        "Working copy",
        ["materialised with", "no constraints at all"],
        "slate",
        "3",
    )
    s.card(
        292,
        490,
        218,
        104,
        "Extractor",
        ["types · statistics · top-k", "seeded row samples"],
        "sky",
        "4",
    )
    s.card(
        292,
        612,
        218,
        104,
        "Query-log miner",
        ["equi-join pairs", "+ parse-failure counts"],
        "sky",
        "5",
    )
    # methods
    s.band(548, 92, 300, 640, "Propose")
    s.card(
        564,
        124,
        268,
        104,
        "Profiling baseline",
        ["all minimal UCCs, FDs, INDs", "IND into UCC → FK candidate"],
        "violet",
        "6",
    )
    s.card(
        564,
        246,
        268,
        104,
        "Metanome (optional)",
        ["HyFD · HyUCC · BINDER", "via metanome-cli"],
        "violet",
    )
    s.card(
        564,
        368,
        268,
        126,
        "LLM proposer",
        [
            "prompt = chosen evidence",
            "channels within a token budget",
            "JSON-schema output, cached",
        ],
        "indigo",
        "7",
    )
    s.card(564, 512, 268, 92, "Heuristic proposer", ["names + statistics rules (no LLM)"], "indigo")
    s.card(
        564,
        622,
        268,
        94,
        "Hybrid ranker",
        ["LLM / heuristic scores the", "baseline's candidates (RQ5)"],
        "indigo",
    )
    # verify / evaluate / outputs
    s.band(870, 92, 306, 640, "Check & report")
    s.card(
        886,
        124,
        274,
        126,
        "SQL verifier",
        ["FK row coverage ≥ 0.95", "UCC uniqueness ≥ 0.99", "FD g3 error ≤ 0.05"],
        "rose",
        "8",
    )
    s.card(
        886,
        268,
        274,
        126,
        "Evaluator",
        ["P / R / F1 per FK, UCC, FD", "bootstrap CIs · top-k · overlap", "hallucination taxonomy"],
        "teal",
        "9",
    )
    s.card(
        886,
        412,
        274,
        104,
        "Synthesiser",
        ["BCNF → PostgreSQL DDL", "migration · Mermaid ER"],
        "green",
        "10",
    )
    s.card(
        886,
        534,
        274,
        182,
        "Run directory",
        [
            "report.md · metrics.csv",
            "manifest.json (git, seeds,",
            "  checksums, model, hardware)",
            "prompts/ · llm_cache/",
            "synth/ · noise_curve_*.svg",
        ],
        "slate",
    )
    # arrows: inputs
    s.arrow(238, 184, 290, 176)
    s.path("M238,436 L262,436 L262,200 L290,200", dashed=True)
    s.path("M238,314 L256,314 L256,690 L290,690", dashed=True)
    s.path("M238,560 L268,560 L268,603 L540,603 L540,470 L562,470", dashed=True)
    # preparation chain
    s.arrow(401, 228, 401, 244)
    s.arrow(401, 350, 401, 366)
    s.arrow(401, 472, 401, 488)
    # working copy -> data-driven methods; evidence -> semantic methods
    s.arrow(510, 410, 562, 176)
    s.arrow(510, 420, 562, 298)
    s.arrow(510, 530, 562, 420)
    s.arrow(510, 550, 562, 556)
    s.arrow(510, 650, 562, 440)
    # baseline candidates feed the hybrid ranker
    s.parts.append(
        f'<path d="M832,214 L842,214 L842,668 L834,668" stroke="{C["violet"][0]}" '
        'stroke-width="1.6" fill="none" stroke-dasharray="5 4" marker-end="url(#arrow)"/>'
    )
    s.parts.append(
        f'<text x="852" y="560" font-size="10" fill="{C["violet"][0]}" '
        'font-weight="600" text-anchor="middle" transform="rotate(-90 852 560)">'
        "candidates</text>"
    )
    # proposers -> verifier (bus), baselines -> evaluator, gold -> evaluator
    for y in (430, 556, 650):
        s.parts.append(f'<path d="M832,{y} L858,{y}" stroke="{MUTED}" stroke-width="1.6"/>')
    s.path("M858,650 L858,188 L884,188")
    s.path("M832,150 L872,150 L872,330 L884,330")
    s.path("M510,140 L534,140 L534,84 L878,84 L878,300 L884,300", dashed=True)
    s.text(700, 80, "gold labels (never shown to methods)", 10.5, C["green"][0], "600", "middle")
    # verifier -> evaluator -> synthesiser -> run dir
    s.arrow(1023, 250, 1023, 266)
    s.arrow(1023, 394, 1023, 410)
    s.arrow(1023, 516, 1023, 532)
    s.save("architecture.svg")


def pipeline() -> None:
    s = Svg(1200, 330, "srev run pipeline")
    s.header(
        "What `srev run` does for every database × noise level",
        "Each step writes an artefact into runs/<timestamp>_<name>/ so every number is traceable",
    )
    steps = [
        ("Load data", ["generate or read,", "checksum"], "slate", "manifest.json"),
        ("Gold labels", ["declared +", "curated"], "green", "gold/*.json"),
        ("Add noise", ["seeded, logged", "renames mapped"], "amber", "noise/*.json"),
        ("Extract", ["stats, samples,", "join pairs"], "sky", "evidence/*.json"),
        ("Propose", ["each method ×", "channels × seeds"], "indigo", "prompts/, llm_cache/"),
        ("Verify", ["SQL statistic", "per proposal"], "rose", "proposals.jsonl"),
        ("Evaluate", ["raw and +verify", "variants"], "teal", "metrics.csv"),
        ("Report", ["tables, charts,", "DDL + ER"], "violet", "report.md, synth/"),
    ]
    w, gap, x0, y = 128, 18, 32, 104
    for i, (title, lines, color, artefact) in enumerate(steps):
        x = x0 + i * (w + gap)
        s.card(x, y, w, 118, title, lines, color, str(i + 1))
        s.text(x + w / 2, y + 150, artefact, 11, MUTED, anchor="middle", mono=True)
        if i < len(steps) - 1:
            s.arrow(x + w + 2, y + 59, x + w + gap - 2, y + 59)
    s.text(
        32,
        300,
        "Variants: every method is scored twice when verify: true — as proposed "
        "(LLM-only) and after the SQL verifier (hybrid mode).",
        12.5,
        MUTED,
    )
    s.save("pipeline.svg")


def evaluation_modes() -> None:
    s = Svg(1200, 420, "Methods and evaluation modes")
    s.header(
        "Methods compared and how they are scored",
        "All methods emit the same proposal format {type, lhs, rhs, confidence, rationale}",
    )
    rows = [
        ("profiling", "exact UCC / FD / IND discovery", "violet", "complete, but noisy"),
        (
            "llm[channels]",
            "vLLM prompt per evidence combination",
            "indigo",
            "semantic, may hallucinate",
        ),
        (
            "heuristic[channels]",
            "name + statistics rules, no model",
            "indigo",
            "transparent reference point",
        ),
        ("hybrid-*", "candidates from profiling, ranked", "indigo", "RQ5: best of both?"),
    ]
    for i, (name, desc, color, note) in enumerate(rows):
        y = 100 + i * 70
        s.card(32, y, 380, 56, name, [], color)
        s.text(50, y + 46, desc, 12, MUTED)
        s.text(440, y + 34, note, 12.5, INK)
        s.parts.append(
            f'<path d="M412,{y + 28} L432,{y + 28} M610,{y + 28} L650,{y + 28}" '
            f'stroke="{MUTED}" stroke-width="1.6"/>'
        )
    s.card(700, 100, 220, 118, "as proposed", ["LLM-only / raw", "scored against gold"], "slate")
    s.card(700, 238, 220, 118, "+verify", ["only proposals the", "SQL verifier accepts"], "rose")
    s.card(
        950,
        100,
        218,
        256,
        "scored on",
        [
            "exact match",
            "column-set overlap",
            "top-k = ceil(k·|gold|)",
            "bootstrap 95% CI",
            "micro / macro across DBs",
            "paired permutation test",
            "hallucination rate",
            "parse-failure rate",
        ],
        "teal",
    )
    s.parts.append(f'<path d="M650,128 L650,338" stroke="{MUTED}" stroke-width="1.6"/>')
    s.arrow(650, 160, 698, 160)
    s.arrow(650, 296, 698, 296)
    s.arrow(920, 160, 948, 200)
    s.arrow(920, 296, 948, 260)
    s.text(
        32,
        396,
        "FK direction matters (A→B ≠ B→A); FDs are split into single right-hand sides; "
        "UCC column order is ignored.",
        12.5,
        MUTED,
    )
    s.save("evaluation-modes.svg")


def noise_levels() -> None:
    s = Svg(1200, 420, "Noise presets")
    s.header(
        "Noise presets L0–L3 (RQ3 robustness curves)",
        "Seeded perturbations applied to a copy of the data; gold labels and the workload are renamed consistently",
    )
    keys = [
        ("rename_columns", "column renames", "indigo"),
        ("rename_tables", "table renames", "violet"),
        ("nulls", "NULL cells", "sky"),
        ("typos", "typo cells (text)", "amber"),
        ("orphans", "dangling FK values", "rose"),
    ]
    levels = [("L0", "none"), ("L1", "low"), ("L2", "medium"), ("L3", "high")]
    x0, y0, colw, barmax = 230, 110, 230, 190
    for j, (lv, name) in enumerate(levels):
        s.text(x0 + j * colw + barmax / 2, y0, f"{lv} · {name}", 14, INK, "700", "middle")
    for i, (key, label, color) in enumerate(keys):
        y = y0 + 28 + i * 46
        s.text(32, y + 16, label, 13, INK, "600")
        for j, (_, name) in enumerate(levels):
            value = getattr(PRESETS[name], key)
            scale = 1.0 if key.startswith("rename") else 0.10
            x = x0 + j * colw
            s.parts.append(
                f'<rect x="{x}" y="{y}" width="{barmax}" height="22" rx="6" fill="#f1f5f9"/>'
            )
            if value:
                bw = max(4, barmax * min(1.0, value / scale))
                s.parts.append(
                    f'<rect x="{x}" y="{y}" width="{bw:.1f}" height="22" rx="6" fill="{C[color][0]}" fill-opacity="0.85"/>'
                )
            s.text(x + barmax + 8, y + 16, f"{value:.0%}" if value else "—", 12, INK, "600")
    s.text(
        32,
        388,
        "Bars: renames as share of columns/tables (full bar = 100%); cell perturbations "
        "as share of cells (full bar = 10%). PK/UNIQUE columns are protected; comments are "
        "dropped from L1.",
        12,
        MUTED,
    )
    s.save("noise-levels.svg")


def hallucination_taxonomy() -> None:
    s = Svg(1200, 360, "Hallucination taxonomy")
    s.header(
        "How every false positive is classified",
        "Checked in priority order — the first matching category wins, so each FP has exactly one",
    )
    tests = [
        (["references a table or", "column that does not exist?"], CATEGORIES[0], "rose"),
        (["an FK whose reverse", "is a gold FK?"], CATEGORIES[1], "amber"),
        (["overlaps a gold label but", "has a different arity?"], CATEGORIES[2], "amber"),
        (["rejected by the", "SQL verifier?"], CATEGORIES[3], "rose"),
    ]
    x, w, gap = 32, 210, 26
    for i, (q, cat, color) in enumerate(tests):
        xi = x + i * (w + gap)
        s.card(xi, 100, w, 90, f"Q{i + 1}", q, "slate")
        s.arrow(xi + w / 2, 192, xi + w / 2, 226, "yes")
        s.card(xi, 228, w, 48, cat, [], color)
        s.arrow(xi + w + 2, 145, xi + w + gap - 2, 145, "no")
    xe = x + 4 * (w + gap)
    s.card(
        xe,
        100,
        196,
        176,
        "plausible_not_in_gold",
        [
            "holds in the data but",
            "is not in the gold set",
            "→ manual audit",
            "(gold may be incomplete)",
        ],
        "green",
    )
    cfg = VerifyConfig()
    s.text(
        32,
        318,
        f"Hallucination rate = (missing-column + verifier-rejected proposals) ÷ all "
        f"proposals. Verifier thresholds: FK coverage ≥ {cfg.fk_min_coverage}, UCC uniqueness "
        f"≥ {cfg.ucc_min_uniqueness}, FD g3 ≤ {cfg.fd_max_g3}.",
        12.5,
        MUTED,
    )
    s.save("hallucination-taxonomy.svg")


def evidence_channels() -> None:
    s = Svg(1200, 330, "Evidence channels")
    s.header(
        "Evidence channels for the RQ2 ablation",
        "Each run config lists channel combinations; names are always included",
    )
    chans = [
        ("names", "table and column names, SQL types", "indigo", "customer.c_custkey INTEGER"),
        ("stats", "distinct, NULLs, min/max, top-k values", "sky", "[distinct=150; nulls=0; …]"),
        ("samples", "seeded sample rows per table", "amber", "(5090, Gus Hanason, N, …)"),
        ("logs", "equi-join pairs mined from queries", "teal", "o_custkey = c_custkey: 4"),
    ]
    for i, (name, desc, color, example) in enumerate(chans):
        x = 32 + i * 290
        s.card(x, 96, 270, 132, name, [desc], color)
        s.text(x + 18, 196, example, 11, MUTED, mono=True)
    combos = [
        "names",
        "names + samples",
        "names + stats + samples",
        "names + stats + samples + logs",
    ]
    x = 32
    s.text(32, 268, "Ablation in configs/experiment.yaml:", 12.5, INK, "600")
    x = 280
    for combo in combos:
        x += s.pill(x, 254, combo, "indigo") + 10
    s.text(
        32,
        306,
        "Prompts are trimmed to the token budget (samples first, then top-k values, join "
        "pairs, statistics) and stored under their SHA-256.",
        12.5,
        MUTED,
    )
    s.save("evidence-channels.svg")


if __name__ == "__main__":
    architecture()
    pipeline()
    evaluation_modes()
    noise_levels()
    hallucination_taxonomy()
    evidence_channels()
