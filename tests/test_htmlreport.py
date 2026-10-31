from srev.cli import main
from srev.db import SQLiteConnector, materialize
from srev.discover import discover
from srev.extract import extract
from srev.htmlreport import MERMAID_URL, render


def test_html_report(retail, tmp_path):
    with SQLiteConnector(":memory:") as conn:
        materialize(retail, conn)
        result = discover(conn, "retail <legacy>")
        ev = extract(conn, "retail <legacy>", rows_per_table=3)
    html = render(ev, result)
    assert html.startswith("<!doctype html>") and html.rstrip().endswith("</html>")
    assert "retail &lt;legacy&gt;" in html and "<legacy>" not in html  # escaped
    assert "erDiagram" in html and MERMAID_URL in html
    assert "prefers-color-scheme: dark" in html
    for t in retail.tables:
        assert f"id='t-{t.name}'" in html
    assert 'class="badge pk"' in html and "FK →" in html
    assert "Verified constraints" in html


def test_discover_writes_html_and_dbt(tmp_path, tpch):
    db = tmp_path / "t.sqlite"
    with SQLiteConnector(db) as conn:
        materialize(tpch, conn)
    out = tmp_path / "out"
    assert main(["discover", "--sqlite", str(db), "--html", "--dbt", "-o", str(out)]) == 0
    assert (out / "report.html").read_text().count("<section class='tbl'") == len(tpch.tables)
    assert (out / "dbt" / "models" / "sources.yml").exists()
