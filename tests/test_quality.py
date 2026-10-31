import json

from srev.cli import main
from srev.db import SQLiteConnector
from srev.extract import extract
from srev.model import Proposal, Verdict, VerifiedProposal
from srev.quality import QualityReport, run_checks, to_markdown


def _vp(p, value, statistic):
    return VerifiedProposal(
        proposal=p, verdict=Verdict(accepted=True, status="ok", statistic=statistic, value=value)
    )


def _db(path):
    conn = SQLiteConnector(path)
    conn.execute("CREATE TABLE parent (id INTEGER, name TEXT)")
    conn.insert_rows("parent", ["id", "name"], [(i, f"p{i}") for i in range(10)])
    conn.execute("CREATE TABLE child (pid INTEGER, v INTEGER)")
    conn.insert_rows("child", ["pid", "v"], [(i % 10, i) for i in range(100)])
    return conn


CONSTRAINTS = [
    _vp(Proposal(type="UCC", lhs=["parent.id"]), 1.0, "uniqueness"),
    _vp(Proposal(type="FK", lhs=["child.pid"], rhs=["parent.id"]), 1.0, "coverage"),
    _vp(Proposal(type="UCC", lhs=["parent.name"]), 1.0, "uniqueness"),
    _vp(Proposal(type="FD", lhs=["child.v"], rhs=["child.pid"]), 0.0, "g3"),
]


def test_checks_detect_failures_drift_and_schema_changes(tmp_path):
    conn = _db(tmp_path / "a.sqlite")
    baseline = extract(conn, "db", rows_per_table=0)
    ok = run_checks(conn, CONSTRAINTS, baseline_evidence=baseline)
    assert ok.summary()["pass"] == 4 and ok.exit_code() == 0 and not ok.schema_changes

    conn.insert_rows("child", ["pid", "v"], [(99, 1000 + i) for i in range(4)])  # orphans
    conn.insert_rows("parent", ["id", "name"], [(3, "dup")])  # duplicate key
    conn.execute("ALTER TABLE parent DROP COLUMN name")
    conn.execute("CREATE TABLE extra (x INTEGER)")
    report = run_checks(conn, CONSTRAINTS, baseline_evidence=baseline, tolerance=0.01)
    status = {r.constraint.describe(): r for r in report.results}
    assert status["UCC (parent.id)"].status == "fail"
    assert status["UCC (parent.id)"].delta < 0
    # the duplicate parent key makes the referenced side non-unique, so the FK fails too
    assert status["FK (child.pid) -> (parent.id)"].status == "fail"
    assert status["UCC (parent.name)"].status == "missing"
    assert status["FD (child.v) -> child.pid"].status == "pass"
    kinds = {(c.kind, c.target) for c in report.schema_changes}
    assert ("column_removed", "parent.name") in kinds and ("table_added", "extra") in kinds
    assert report.exit_code() == 1 and report.exit_code("warn") == 1
    md = to_markdown(report)
    assert "Schema drift" in md and "❌ fail" in md
    conn.close()


def test_warn_only_respects_fail_on(tmp_path):
    report = QualityReport(database="x")
    report.results = []
    assert report.exit_code() == 0
    conn = _db(tmp_path / "b.sqlite")
    conn.insert_rows("child", ["pid", "v"], [(99, 2000 + i) for i in range(4)])
    r = run_checks(conn, CONSTRAINTS[1:2])
    assert r.summary()["warn"] == 1
    assert r.exit_code("fail") == 0 and r.exit_code("warn") == 1
    conn.close()


def test_cli_check(tmp_path, capsys):
    conn = _db(tmp_path / "c.sqlite")
    conn.close()
    cfile = tmp_path / "constraints.json"
    cfile.write_text(json.dumps([c.model_dump() for c in CONSTRAINTS]))
    code = main(
        [
            "check",
            "--constraints",
            str(cfile),
            "--sqlite",
            str(tmp_path / "c.sqlite"),
            "-o",
            str(tmp_path / "r.json"),
        ]
    )
    assert code == 0
    assert "✅ pass 4" in capsys.readouterr().out
    assert json.loads((tmp_path / "r.json").read_text())["results"]
