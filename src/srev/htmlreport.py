"""Self-contained HTML data-profiling report (F-23).

One file with inline CSS (light and dark theme): summary cards, table roles,
an ER diagram, per-column profiles (NULL and uniqueness bars, ranges, top
values, key badges), the verified constraints and the review list. The ER
diagram is rendered by Mermaid loaded from a pinned CDN URL; without network
access the diagram source is shown instead.
"""

from __future__ import annotations

import datetime as dt
from html import escape
from pathlib import Path
from typing import Any

from srev import __version__
from srev.discover import Discovery
from srev.extract import Evidence, TableEvidence
from srev.graph import SchemaGraph
from srev.model import split_ref
from srev.synth import synthesize

MERMAID_URL = "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"

CSS = """
:root { --bg:#f8fafc; --card:#ffffff; --ink:#0f172a; --muted:#64748b; --line:#e2e8f0;
  --accent:#4f46e5; --good:#15803d; --warn:#b45309; --bad:#be123c; --bar:#e2e8f0;
  --chip:#eef2ff; --chip-ink:#3730a3; }
@media (prefers-color-scheme: dark) { :root { --bg:#0b1020; --card:#111827; --ink:#e5e7eb;
  --muted:#94a3b8; --line:#1f2937; --accent:#818cf8; --good:#4ade80; --warn:#fbbf24;
  --bad:#fb7185; --bar:#1f2937; --chip:#1e1b4b; --chip-ink:#c7d2fe; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font:14px/1.5 Inter, 'Segoe UI', system-ui, sans-serif; }
main { max-width:1180px; margin:0 auto; padding:24px 16px 64px; }
h1 { font-size:26px; margin:0 0 4px; } h2 { font-size:19px; margin:32px 0 12px; }
h3 { font-size:16px; margin:0; } .muted { color:var(--muted); }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px;
  margin-top:18px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }
.card b { display:block; font-size:24px; } .card span { color:var(--muted); font-size:12px;
  text-transform:uppercase; letter-spacing:.04em; }
table { width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line);
  border-radius:12px; overflow:hidden; }
th, td { text-align:left; padding:7px 10px; border-bottom:1px solid var(--line); vertical-align:top; }
th { font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }
tr:last-child td { border-bottom:none; }
.scroll { overflow-x:auto; }
.bar { width:110px; height:8px; background:var(--bar); border-radius:4px; display:inline-block;
  vertical-align:middle; margin-right:6px; overflow:hidden; }
.bar i { display:block; height:100%; background:var(--accent); }
.bar.nulls i { background:var(--warn); }
.chip { display:inline-block; background:var(--chip); color:var(--chip-ink); border-radius:10px;
  padding:0 7px; margin:1px 2px; font-size:12px; white-space:nowrap; }
.badge { display:inline-block; border-radius:6px; padding:0 6px; font-size:11px; font-weight:700;
  margin-left:4px; border:1px solid currentColor; }
.pk { color:var(--good); } .uk { color:var(--accent); } .fk { color:var(--warn); }
.role { font-weight:600; } .role.fact { color:var(--bad); } .role.bridge { color:var(--warn); }
.role.dimension { color:var(--accent); } .role.lookup { color:var(--good); }
section.tbl { background:var(--card); border:1px solid var(--line); border-radius:12px;
  padding:16px; margin-bottom:16px; }
section.tbl table { border:none; } .mono { font-family:'JetBrains Mono', Menlo, monospace; font-size:12.5px; }
.er { background:#fff; border-radius:12px; padding:12px; border:1px solid var(--line); overflow:auto; }
footer { margin-top:40px; color:var(--muted); font-size:12px; }
"""


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def _bar(value: float, cls: str = "") -> str:
    v = max(0.0, min(1.0, value))
    return f'<span class="bar {cls}"><i style="width:{100 * v:.1f}%"></i></span>{_pct(v)}'


def _val(v: Any, width: int = 28) -> str:
    s = "∅" if v is None else str(v)
    return escape(s if len(s) <= width else s[: width - 1] + "…")


def _badges(table: str, discovery: Discovery) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    pk_done = False
    for p in sorted(discovery.accepted(), key=lambda p: (p.type != "UCC", -p.confidence)):
        if p.type == "UCC" and p.table == table.lower():
            kind = "uk" if pk_done else "pk"
            pk_done = True
            for ref in p.lhs:
                out.setdefault(split_ref(ref)[1], []).append(
                    f'<span class="badge {kind}">{kind.upper()}</span>'
                )
        elif p.type == "FK" and p.table == table.lower():
            for ref, tgt in zip(p.lhs, p.rhs, strict=True):
                out.setdefault(split_ref(ref)[1], []).append(
                    f'<span class="badge fk" title="references {escape(tgt)}">FK → '
                    f"{escape(tgt)}</span>"
                )
    return out


def _table_section(t: TableEvidence, discovery: Discovery, role: str) -> str:
    badges = _badges(t.name, discovery)
    rows = []
    for c in t.columns:
        non_null = t.row_count - c.null_count
        uniq = c.distinct_count / non_null if non_null else 0.0
        nulls = c.null_count / t.row_count if t.row_count else 0.0
        tops = "".join(f'<span class="chip">{_val(v, 18)} ×{n}</span>' for v, n in c.top_k[:4])
        rows.append(
            f"<tr><td class='mono'>{escape(c.name)}{''.join(badges.get(c.name.lower(), []))}</td>"
            f"<td class='mono muted'>{escape(c.type)}</td><td>{_bar(nulls, 'nulls')}</td>"
            f"<td>{c.distinct_count}</td><td>{_bar(uniq)}</td>"
            f"<td class='mono'>{_val(c.min)} … {_val(c.max)}</td><td>{tops}</td></tr>"
        )
    return (
        f"<section class='tbl' id='t-{escape(t.name)}'><h3>{escape(t.name)} "
        f"<span class='role {role}'>· {role}</span> "
        f"<span class='muted'>· {t.row_count:,} rows · {len(t.columns)} columns</span></h3>"
        "<div class='scroll'><table><tr><th>column</th><th>type</th><th>nulls</th>"
        "<th>distinct</th><th>uniqueness</th><th>range</th><th>top values</th></tr>"
        + "".join(rows)
        + "</table></div></section>"
    )


def render(evidence: Evidence, discovery: Discovery) -> str:
    accepted = discovery.accepted()
    graph = SchemaGraph.from_constraints(evidence, accepted)
    roles = {r.name: r for r in graph.roles()}
    counts = discovery.counts()
    n_rows = sum(t.row_count for t in evidence.tables)
    n_cols = sum(len(t.columns) for t in evidence.tables)
    mermaid = synthesize(evidence, accepted, normal_form="none").mermaid
    cards = "".join(
        f"<div class='card'><b>{v}</b><span>{k}</span></div>"
        for k, v in [
            ("tables", len(evidence.tables)), ("columns", n_cols), ("rows", f"{n_rows:,}"),
            ("foreign keys", counts["FK"]), ("keys", counts["UCC"]),
            ("dependencies", counts["FD"]), ("to review", len(discovery.review)),
        ]
    )  # fmt: skip
    role_rows = "".join(
        f"<tr><td><a href='#t-{escape(r.name)}'>{escape(r.name)}</a></td>"
        f"<td class='role {r.role}'>{r.role}</td><td>{r.rows:,}</td>"
        f"<td>{escape(', '.join(r.references) or '–')}{' (+self)' if r.self_reference else ''}</td>"
        f"<td>{escape(', '.join(r.referenced_by) or '–')}</td>"
        f"<td class='muted'>{escape(r.reason)}</td></tr>"
        for r in roles.values()
    )
    cons_rows = "".join(
        f"<tr><td>{v.proposal.type}</td><td class='mono'>{escape(v.proposal.describe()[len(v.proposal.type) + 1 :])}</td>"
        f"<td>{v.proposal.confidence:.2f}</td><td>{escape(v.verdict.statistic)}="
        f"{v.verdict.value}</td><td class='muted'>{escape(v.proposal.rationale[:90])}</td></tr>"
        for v in sorted(
            discovery.constraints, key=lambda v: (v.proposal.type, v.proposal.describe())
        )
    )
    review_rows = "".join(
        f"<tr><td class='mono'>{escape(v.proposal.describe())}</td>"
        f"<td>{v.proposal.confidence:.2f}</td><td>{escape(v.verdict.statistic)}={v.verdict.value}"
        f"</td><td class='muted'>{escape(v.proposal.rationale[:90])}</td></tr>"
        for v in discovery.review
    )
    tables = "".join(
        _table_section(t, discovery, roles[t.name.lower()].role if t.name.lower() in roles else "")
        for t in evidence.tables
    )
    generated = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
    review_html = (
        "<h2>Needs review</h2><p class='muted'>Foreign-key candidates that hold in the data but "
        "scored below the acceptance threshold or failed a plausibility guard.</p>"
        "<div class='scroll'><table><tr><th>candidate</th><th>score</th><th>statistic</th>"
        f"<th>why</th></tr>{review_rows}</table></div>"
        if review_rows
        else ""
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(evidence.database)} · srev profile</title>
<style>{CSS}</style></head>
<body><main>
<h1>{escape(evidence.database)}</h1>
<div class="muted">Data profile and discovered constraints · generated {generated} by srev {__version__}
· discovery took {discovery.seconds}s</div>
<div class="cards">{cards}</div>
<h2>Table roles</h2>
<div class="scroll"><table><tr><th>table</th><th>role</th><th>rows</th><th>references</th>
<th>referenced by</th><th>why</th></tr>{role_rows}</table></div>
<h2>Entity-relationship diagram</h2>
<div class="er"><pre class="mermaid">{escape(mermaid)}</pre></div>
<h2>Verified constraints</h2>
<div class="scroll"><table><tr><th>type</th><th>constraint</th><th>score</th><th>measured</th>
<th>evidence</th></tr>{cons_rows}</table></div>
{review_html}
<h2>Column profiles</h2>
{tables}
<footer>Constraints were discovered automatically and verified on the data; review them before
enforcing. Statistics: uniqueness = distinct ÷ non-NULL rows.</footer>
</main>
<script src="{MERMAID_URL}"></script>
<script>if (window.mermaid) {{ mermaid.initialize({{ startOnLoad: true, theme: 'neutral',
er: {{ useMaxWidth: false }} }}); }}</script>
</body></html>
"""


def write(path: str | Path, evidence: Evidence, discovery: Discovery) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(evidence, discovery))
    return path
