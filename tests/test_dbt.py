import yaml

from srev.dbt import build_project
from srev.extract import extract
from srev.model import Proposal, Verdict, VerifiedProposal


def _vp(p: Proposal, value: float, statistic: str) -> VerifiedProposal:
    return VerifiedProposal(
        proposal=p, verdict=Verdict(accepted=True, status="ok", statistic=statistic, value=value)
    )


def test_dbt_project(university_db, tmp_path):
    ev = extract(university_db, "university", rows_per_table=2)
    constraints = [
        _vp(Proposal(type="UCC", lhs=["student.student_id"]), 1.0, "uniqueness"),
        _vp(Proposal(type="UCC", lhs=["takes.student_id", "takes.section_id"]), 1.0, "uniqueness"),
        _vp(
            Proposal(type="FK", lhs=["takes.student_id"], rhs=["student.student_id"]),
            1.0,
            "coverage",
        ),
        _vp(
            Proposal(type="FK", lhs=["advisor.instructor_id"], rhs=["instructor.instructor_id"]),
            0.97,
            "coverage",
        ),
        _vp(Proposal(type="FD", lhs=["course.dept_id"], rhs=["course.dept_name"]), 0.0, "g3"),
        _vp(
            Proposal(
                type="FK",
                lhs=["section.course_id", "section.sec_no"],
                rhs=["course.course_id", "course.credits"],
            ),
            1.0,
            "coverage",
        ),
    ]
    counts = build_project(ev, constraints, tmp_path / "dbt", schema="uni")
    assert counts == {"column_tests": 4, "singular_tests": 3, "warn": 1}
    project = yaml.safe_load((tmp_path / "dbt" / "dbt_project.yml").read_text())
    assert project["name"] == "srev_university" and project["config-version"] == 2
    sources = yaml.safe_load((tmp_path / "dbt" / "models" / "sources.yml").read_text())
    src = sources["sources"][0]
    assert src["schema"] == "uni"
    tables = {t["name"]: {c["name"]: c["data_tests"] for c in t["columns"]} for t in src["tables"]}
    assert tables["student"]["student_id"] == ["unique", "not_null"]
    rel = tables["takes"]["student_id"][0]["relationships"]
    assert rel["arguments"] == {"to": "source('raw', 'student')", "field": "student_id"}
    approx = tables["advisor"]["instructor_id"][0]["relationships"]
    assert approx["config"] == {"severity": "warn"}
    tests = sorted(p.name for p in (tmp_path / "dbt" / "tests").glob("*.sql"))
    assert [t.split("_")[0] for t in tests] == ["fd", "fk", "unique"]
    fd_sql = next((tmp_path / "dbt" / "tests").glob("fd_*.sql")).read_text()
    assert "count(distinct dept_name) > 1" in fd_sql and "{{ source('raw', 'course') }}" in fd_sql
    assert (tmp_path / "dbt" / "models" / "staging" / "stg_student.sql").exists()

    build_project(ev, constraints[2:3], tmp_path / "old", tests_key="tests", legacy_test_args=True)
    old = yaml.safe_load((tmp_path / "old" / "models" / "sources.yml").read_text())
    takes = next(t for t in old["sources"][0]["tables"] if t["name"] == "takes")
    assert takes["columns"][0]["tests"][0]["relationships"]["field"] == "student_id"
