"""Run a real srev session and build the HTML pages that become README screenshots and the demo video.

Usage (from the repository root, with srev installed):

    python docs/media/tools/capture.py --build /tmp/srev-media [--mermaid path/to/mermaid.min.js]
    node docs/media/tools/render.mjs /tmp/srev-media docs/media

Every terminal screenshot shows the actual output of the commands below; where output
is shortened, the page says so. Nothing is typed in by hand.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
from pathlib import Path

import markdown

REPO = Path(__file__).resolve().parents[3]

SESSION = [
    "srev datasets list",
    "srev datasets load university --sqlite demo/university.sqlite --noise medium "
    "--gold demo/gold.json --workload demo/workload.sql",
    "srev extract --sqlite demo/university.sqlite --name university --rows 20 -o demo/evidence.json",
    "srev qlog demo/workload.sql --evidence demo/evidence.json -o demo/joins.json",
    "srev propose --evidence demo/evidence.json --joins demo/joins.json --heuristic "
    "-o demo/proposals.json",
    "srev profile --sqlite demo/university.sqlite -o demo/baseline.json",
    "srev verify --proposals demo/proposals.json --sqlite demo/university.sqlite "
    "-o demo/verified.json",
    "srev evaluate --proposals demo/verified.json --accepted-only --gold demo/gold.json "
    "--evidence demo/evidence.json -o demo/eval.json",
    "srev evaluate --proposals demo/baseline.json --gold demo/gold.json -o demo/eval_baseline.json",
    "srev synth --evidence demo/evidence.json --proposals demo/verified.json -o demo/synth",
]
RUN = "srev run --config configs/offline.yaml --run-dir runs/offline-demo"
TAIL = [
    "ls runs/offline-demo",
    "head -n 18 runs/offline-demo/synth/university_L0.sql",
]

TERMINAL_CSS = """
body { margin: 0; background: transparent; font-family: 'DejaVu Sans Mono', Menlo, monospace; }
.win { width: 1180px; margin: 10px; border-radius: 12px; overflow: hidden; background: #0b1020;
       box-shadow: 0 10px 30px rgba(15,23,42,.35); }
.bar { height: 36px; background: #1e2433; display: flex; align-items: center; padding: 0 14px;
       color: #94a3b8; font: 13px Inter, 'DejaVu Sans', sans-serif; }
.dot { width: 12px; height: 12px; border-radius: 6px; margin-right: 8px; }
.title { flex: 1; text-align: center; margin-right: 60px; }
pre { margin: 0; padding: 16px 20px 20px; color: #cbd5e1; font-size: 14px; line-height: 1.55;
      white-space: pre-wrap; word-break: break-all; }
.p { color: #4ade80; } .c { color: #f8fafc; font-weight: bold; } .i { color: #64748b; }
.m { color: #67e8f9; } .w { color: #fbbf24; } .note { color: #a78bfa; font-style: italic; }
"""

PLAYER_JS = """
const steps = JSON.parse(document.getElementById('data').textContent);
const pre = document.getElementById('out');
const sleep = ms => new Promise(r => setTimeout(r, ms));
function add(html) { pre.insertAdjacentHTML('beforeend', html); trim(); }
function trim() {
  while (pre.scrollHeight > pre.clientHeight + 1 && pre.childNodes.length > 1)
    pre.removeChild(pre.firstChild);
}
(async () => {
  await sleep(600);
  for (const s of steps) {
    const line = document.createElement('span');
    line.innerHTML = '<span class="p">$ </span><span class="c"></span>';
    pre.appendChild(line);
    const c = line.querySelector('.c');
    for (const ch of s.cmd) { c.textContent += ch; trim(); await sleep(s.cmd.length > 90 ? 9 : 16); }
    add('\\n'); await sleep(350);
    for (const l of s.lines) { add(l + '\\n'); await sleep(s.lines.length > 20 ? 28 : 70); }
    await sleep(s.pause || 900);
  }
  window.__done = true;
})();
"""


def run(cmd: str, cwd: Path) -> str:
    proc = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, check=True)
    return (proc.stdout + proc.stderr).rstrip("\n")


def colour(line: str) -> str:
    esc = html.escape(line)
    if re.match(r"^\d\d:\d\d:\d\d INFO", line):
        return f'<span class="i">{esc}</span>'
    if re.match(r"^(FK|UCC|FD)\s+P=", line) or line.startswith("accepted"):
        return f'<span class="m">{esc}</span>'
    if "WARNING" in line:
        return f'<span class="w">{esc}</span>'
    return esc


def terminal_block(cmd: str, output: str, max_lines: int | None = None) -> str:
    lines = output.splitlines()
    if max_lines and len(lines) > max_lines:
        head, tail = lines[: max_lines - 4], lines[-3:]
        skipped = len(lines) - len(head) - len(tail)
        body = [colour(x) for x in head]
        body.append(f'<span class="note">… {skipped} more log lines …</span>')
        body += [colour(x) for x in tail]
    else:
        body = [colour(x) for x in lines]
    return f'<span class="p">$ </span><span class="c">{html.escape(cmd)}</span>\n' + "\n".join(body)


def terminal_page(title: str, blocks: list[str]) -> str:
    dots = "".join(
        f'<span class="dot" style="background:{c}"></span>'
        for c in ("#ef4444", "#f59e0b", "#22c55e")
    )
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><style>{TERMINAL_CSS}</style></head>"
        f"<body><div class='win' id='shot'><div class='bar'>{dots}<span class='title'>"
        f"{html.escape(title)}</span></div><pre>{chr(10).join(blocks)}</pre></div></body></html>"
    )


DOC_CSS = """
body { margin: 0; background: transparent; font-family: Inter, 'Segoe UI', 'DejaVu Sans', sans-serif; }
.doc { width: max-content; min-width: 1120px; margin: 10px; padding: 28px 36px; background: #fff; border: 1px solid #d0d7de;
       border-radius: 12px; color: #1f2328; box-shadow: 0 8px 24px rgba(15,23,42,.12); }
h1 { font-size: 26px; border-bottom: 1px solid #d8dee4; padding-bottom: 8px; margin-top: 0; }
h2 { font-size: 20px; border-bottom: 1px solid #d8dee4; padding-bottom: 6px; }
h3 { font-size: 16px; }
table { border-collapse: collapse; font-size: 13px; margin: 8px 0 16px; }
th, td { border: 1px solid #d0d7de; padding: 5px 10px; }
th { background: #f6f8fa; } tr:nth-child(even) td { background: #f6f8fa; }
code, pre { font-family: 'DejaVu Sans Mono', monospace; font-size: 13px; }
pre { background: #f6f8fa; padding: 14px 16px; border-radius: 8px; line-height: 1.45;
      white-space: pre-wrap; }
.note { color: #6e7781; font-style: italic; font-size: 13px; }
li { font-size: 14px; line-height: 1.6; }
.k { color: #cf222e; } .s { color: #0a3069; } .cm { color: #6e7781; } .t { color: #8250df; }
"""


def doc_page(body: str, base: str = "") -> str:
    base_tag = f"<base href='{base}'>" if base else ""
    return (
        f"<!doctype html><html><head><meta charset='utf-8'>{base_tag}<style>{DOC_CSS}</style>"
        f"</head><body><div class='doc' id='shot'>{body}</div></body></html>"
    )


def section(md: str, heading: str) -> str:
    parts = re.split(r"(?m)^## ", md)
    for p in parts:
        if p.startswith(heading):
            return "## " + p
    raise KeyError(heading)


def keep_table_rows(md: str, keep: callable) -> str:  # type: ignore[valid-type]
    out = []
    for line in md.splitlines():
        if (
            line.startswith("| ")
            and not line.startswith("| database")
            and not line.startswith("| variant")
            and not keep(line)
        ):
            continue
        out.append(line)
    return "\n".join(out)


def highlight_sql(sql: str) -> str:
    out = []
    for line in sql.splitlines():
        esc = html.escape(line)
        if "--" in esc:
            code, _, comment = esc.partition("--")
            esc = code + f'<span class="cm">--{comment}</span>'
        esc = re.sub(
            r"\b(CREATE|TABLE|SCHEMA|IF|NOT|EXISTS|NULL|CONSTRAINT|PRIMARY|KEY|UNIQUE|ALTER|ADD|"
            r"FOREIGN|REFERENCES)\b",
            r'<span class="k">\1</span>',
            esc,
        )
        esc = re.sub(
            r"\b(INTEGER|VARCHAR\(\d+\)|CHAR\(\d+\)|DATE|NUMERIC\([\d,]+\))",
            r'<span class="t">\1</span>',
            esc,
        )
        out.append(esc)
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", required=True, type=Path)
    ap.add_argument("--mermaid", type=Path, help="local mermaid.min.js for the ER diagram")
    args = ap.parse_args()
    build: Path = args.build
    ws = build / "ws"
    shutil.rmtree(build, ignore_errors=True)
    (ws / "demo").mkdir(parents=True)
    shutil.copytree(REPO / "configs", ws / "configs")

    sessions = [{"cmd": c, "out": run(c, ws)} for c in SESSION]
    run_out = run(RUN, ws)
    tails = [{"cmd": c, "out": run(c, ws)} for c in TAIL]
    (build / "sessions.json").write_text(
        json.dumps([*sessions, {"cmd": RUN, "out": run_out}, *tails], indent=1)
    )
    rd = ws / "runs" / "offline-demo"
    pages: list[dict[str, object]] = []

    def page(name: str, content: str, **opts: object) -> None:
        (build / f"{name}.html").write_text(content)
        pages.append({"html": f"{name}.html", "out": name, **opts})

    # 1. step-by-step CLI session
    page(
        "cli-walkthrough",
        terminal_page(
            "srev — step-by-step pipeline on a noisy university database (SQLite)",
            [terminal_block(s["cmd"], s["out"]) for s in sessions[1:]],
        ),
    )
    # 2. the experiment runner
    page(
        "experiment-run",
        terminal_page(
            "srev run — 3 datasets × 4 noise levels × 3 methods",
            [terminal_block(RUN, run_out, max_lines=16), terminal_block(*tails[0].values())],
        ),
    )
    # 3. report: overview + aggregate FK tables + robustness chart
    report = (rd / "report.md").read_text()
    head = report.split("## Databases")[0]
    dbs = section(report, "Databases")
    agg = section(report, "Aggregate results")
    fk_tables = "\n".join(
        "### " + t
        for t in re.split(r"(?m)^### ", agg)[1:]
        if t.startswith("FK · noise L0") or t.startswith("FK · noise L3")
    )
    md = (
        head
        + dbs
        + "## Aggregate results (excerpt: FK at L0 and L3)\n\n"
        + fk_tables
        + "\n## Robustness to noise (excerpt)\n\n![FK](noise_curve_fk.svg)\n"
    )
    page(
        "report-overview",
        doc_page(
            markdown.markdown(md, extensions=["tables"])
            + "<p class='note'>Rendered excerpt of runs/offline-demo/report.md. Methods: profiling "
            "baseline, offline heuristic and heuristic hybrid; no LLM was used in this run.</p>",
            base=rd.as_uri() + "/",
        ),
    )
    # 4. hallucination breakdown for one database across noise levels
    hal = keep_table_rows(section(report, "Hallucinations"), lambda ln: "| university_" in ln)
    page(
        "report-hallucinations",
        doc_page(
            markdown.markdown(hal, extensions=["tables"])
            + "<p class='note'>Excerpt: rows for the university database only.</p>"
        ),
    )
    # 5. synthesised DDL
    ddl = "\n".join((rd / "synth" / "university_L0.sql").read_text().splitlines()[:46])
    page(
        "synth-ddl",
        doc_page(
            "<h3>synth/university_L0.sql (first 46 lines)</h3><pre>" + highlight_sql(ddl) + "</pre>"
        ),
    )
    # 6. the prompt an LLM receives
    from srev.extract import Evidence
    from srev.llm import PromptConfig, build_prompt
    from srev.qlog import JoinStats

    ev = Evidence.load(rd / "evidence" / "retail_legacy_L0.json")
    joins = JoinStats.model_validate_json(
        (rd / "evidence" / "retail_legacy_L0.joins.json").read_text()
    )
    prompt = build_prompt(ev, joins, PromptConfig(max_sample_rows=3))
    user = prompt.user.splitlines()
    shown = "\n".join(user[:22]) + "\n…\n" + "\n".join(user[-12:])
    page(
        "prompt",
        doc_page(
            f"<h3>Prompt sent to the LLM (sha256 {prompt.sha256[:16]}…, ≈{prompt.est_tokens} tokens)"
            f"</h3><pre><b>SYSTEM</b>\n{html.escape(prompt.system)}\n\n<b>USER</b>\n"
            f"{html.escape(shown)}</pre><p class='note'>Built by srev.llm.build_prompt from the "
            "retail_legacy evidence with all four channels; the middle of the user message is "
            "elided here.</p>"
        ),
    )
    # 7. ER diagram
    if args.mermaid:
        shutil.copy(args.mermaid, build / "mermaid.min.js")
        mmd = (rd / "synth" / "university_L0.mmd").read_text()
        page(
            "er-diagram",
            (
                "<!doctype html><html><head><meta charset='utf-8'><script src='mermaid.min.js'></script>"
                f"<style>{DOC_CSS} .doc {{ width: auto; display: inline-block; }}</style></head><body>"
                f"<div class='doc' id='shot'><pre class='mermaid' style='background:#fff'>"
                f"{html.escape(mmd)}</pre></div><script>mermaid.initialize({{startOnLoad: false, "
                "theme: 'neutral', er: {useMaxWidth: false}}); mermaid.run().then(() => "
                "window.__done = true);</script></body></html>"
            ),
            wait=True,
            svg=True,
        )
    # 8. video player
    steps = [
        {"cmd": s["cmd"], "lines": [colour(x) for x in s["out"].splitlines()]} for s in sessions
    ]
    steps.append({"cmd": RUN, "lines": [colour(x) for x in run_out.splitlines()], "pause": 1500})
    steps += [
        {"cmd": t["cmd"], "lines": [colour(x) for x in t["out"].splitlines()], "pause": 2500}
        for t in tails
    ]
    player = (
        terminal_page("srev demo — real output, replayed", [])
        .replace("<pre></pre>", "<pre id='out' style='height:640px;overflow:hidden'></pre>")
        .replace(
            "</body>",
            f"<script id='data' type='application/json'>{json.dumps(steps)}"
            f"</script><script>{PLAYER_JS}</script></body>",
        )
    )
    page("demo", player, video=True)
    # real artefacts copied as-is
    shutil.copy(rd / "noise_curve_fk.svg", build / "sample-noise-curve-fk.svg")
    (build / "pages.json").write_text(json.dumps(pages, indent=1))
    print(f"built {len(pages)} pages in {build}")


if __name__ == "__main__":
    main()
