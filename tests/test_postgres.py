"""PostgreSQL integration tests; run with SREV_TEST_DSN=postgresql://..."""

import pytest

from srev.db import materialize
from srev.extract import extract
from srev.model import Proposal
from srev.synth import synthesize
from srev.truth import gold_from_connector, gold_from_dataset, strip_constraints
from srev.verify import Verifier

pytestmark = pytest.mark.postgres


def test_declared_constraints_roundtrip(pg_conn, tpch):
    materialize(tpch, pg_conn, with_constraints=True)
    exported = gold_from_connector(pg_conn, "tpch")
    expected = {g.key() for g in gold_from_dataset(tpch).labels if g.source == "declared"}
    assert {g.key() for g in exported.labels} == expected
    tables = strip_constraints(pg_conn, f"{pg_conn.schema}_stripped")
    assert len(tables) == 8
    from srev.db import PostgresConnector

    with PostgresConnector(pg_conn.dsn, schema=f"{pg_conn.schema}_stripped") as stripped:
        assert gold_from_connector(stripped, "s").labels == []
        assert stripped.query(f"SELECT COUNT(*) FROM {stripped.qtable('lineitem')}")[0][0] == len(
            tpch.table("lineitem").rows
        )


def test_extract_verify_and_synth(pg_conn, university, university_gold):
    materialize(university, pg_conn)
    ev = extract(pg_conn, "university", rows_per_table=5)
    assert ev.dialect == "postgres" and ev.table("student").row_count > 0
    v = Verifier(pg_conn)
    assert all(v.verify(g).accepted for g in university_gold.labels)
    # integer = varchar comparison falls back to a text cast instead of erroring
    mixed = v.verify(Proposal(type="FK", lhs=["student.student_id"], rhs=["course.course_id"]))
    assert mixed.status == "rejected" and mixed.value == 0.0
    res = synthesize(
        ev,
        university_gold.labels,
        source_schema=pg_conn.schema,
        target_schema=f"{pg_conn.schema}_stripped",
    )
    pg_conn.execute(res.ddl)
    pg_conn.execute(res.migration)
    n = pg_conn.query(f"SELECT COUNT(*) FROM {pg_conn.schema}_stripped.course_dept_id")[0][0]
    assert n > 0
