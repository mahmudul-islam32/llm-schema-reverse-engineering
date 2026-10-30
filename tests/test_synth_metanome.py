import json

import pytest
import sqlglot

from srev.baselines import metanome
from srev.extract import extract
from srev.model import Proposal, Verdict
from srev.synth import synthesize


def test_bcnf_decomposition_and_ddl(university_db, university_gold):
    ev = extract(university_db, "university", rows_per_table=3)
    verdicts = {
        university_gold.labels[0].key(): Verdict(
            accepted=True, status="ok", statistic="uniqueness", value=1.0
        )
    }
    res = synthesize(ev, university_gold.labels, verdicts, target_schema="target")
    assert "CREATE TABLE target.course_dept_id (" in res.ddl
    assert "split off by BCNF" in res.ddl
    course = res.ddl.split("CREATE TABLE target.course (")[1].split(");")[0]
    assert "dept_name" not in course and "building" not in course and "dept_id" in course
    assert "-- evidence:" in res.ddl and "uniqueness=1.0" in res.ddl
    statements = [s for s in sqlglot.parse(res.ddl, read="postgres") if s is not None]
    assert len(statements) > 10
    assert res.mermaid.startswith("erDiagram") and "||--o{" in res.mermaid
    assert res.migration.index("course_dept_id") < res.migration.index("INSERT INTO target.course ")
    assert res.trace and all(t.evidence_id for t in res.trace)
    flat = synthesize(ev, university_gold.labels, normal_form="none")
    assert "course_dept_id" not in flat.ddl


def test_synth_ignores_unknown_columns(university_db):
    ev = extract(university_db, "u", rows_per_table=0)
    res = synthesize(ev, [Proposal(type="UCC", lhs=["student.ghost"])])
    assert "ghost" not in res.ddl


def test_metanome_result_parsing():
    def col(t, c):
        return {"tableIdentifier": f"{t}.csv", "columnIdentifier": c}

    lines = [
        json.dumps(
            {
                "type": "UniqueColumnCombination",
                "columnCombination": {"columnIdentifiers": [col("customer", "c_id")]},
            }
        ),
        json.dumps(
            {
                "type": "FunctionalDependency",
                "determinant": {"columnIdentifiers": [col("customer", "zip")]},
                "dependant": col("customer", "city"),
            }
        ),
        json.dumps(
            {
                "type": "FunctionalDependency",
                "determinant": {"columnIdentifiers": []},
                "dependant": col("customer", "country"),
            }
        ),
        json.dumps(
            {
                "type": "InclusionDependency",
                "dependant": {"columnIdentifiers": [col("orders", "o_cust")]},
                "referenced": {"columnIdentifiers": [col("customer", "c_id")]},
            }
        ),
        json.dumps(
            {
                "type": "InclusionDependency",
                "dependant": {"columnIdentifiers": [col("orders", "o_x")]},
                "referenced": {"columnIdentifiers": [col("customer", "zip")]},
            }
        ),
        "garbage",
        "",
    ]
    uccs, fds, inds, bad = metanome.parse_results(lines)
    assert [u.lhs for u in uccs] == [["customer.c_id"]]
    assert [(f.lhs, f.rhs) for f in fds] == [(["customer.zip"], ["customer.city"])]
    assert len(inds) == 2 and bad == 1
    fks = metanome.fks_from_inds(inds, uccs)
    assert [(f.lhs, f.rhs) for f in fks] == [(["orders.o_cust"], ["customer.c_id"])]


def test_metanome_command_and_availability(tmp_path, university_db):
    cfg = metanome.MetanomeConfig(cli_jar=str(tmp_path / "cli.jar"))
    cmd = metanome.build_command(cfg, "hyfd", [tmp_path / "a.csv"], "tag")
    assert "de.metanome.algorithms.hyfd.HyFD" in cmd and "--header" in cmd
    assert cmd[cmd.index("--file-key") + 1] == "INPUT_GENERATOR"
    with pytest.raises(metanome.MetanomeUnavailable):
        metanome.run(university_db, tmp_path, cfg)
    files = metanome.export_csv(university_db, tmp_path / "csv")
    assert len(files) == 8 and files[0].read_text().splitlines()[0]
