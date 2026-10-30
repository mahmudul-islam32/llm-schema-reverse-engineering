import json

from srev.extract import extract, sample_rows
from srev.qlog import load_workload, mine_joins, split_sql


def test_extract_statistics(sqlite_conn):
    sqlite_conn.execute("CREATE TABLE t (id INTEGER, grp TEXT, v INTEGER)")
    sqlite_conn.insert_rows(
        "t", ["id", "grp", "v"], [(1, "a", 10), (2, "a", None), (3, "b", 30), (4, "a", 40)]
    )
    ev = extract(sqlite_conn, "db", rows_per_table=2, top_k=1, seed=1)
    t = ev.table("t")
    assert t.row_count == 4
    grp = t.column("grp")
    assert (grp.distinct_count, grp.null_count, grp.top_k) == (2, 0, [("a", 3)])
    v = t.column("v")
    assert v.null_count == 1 and v.null_ratio == 0.25 and (v.min, v.max) == (10, 40)
    assert v.family == "integer"
    assert len(t.sample_rows) == 2
    assert ev.has_ref("t.grp") and not ev.has_ref("t.nope") and not ev.has_ref("x.id")
    json.loads(ev.model_dump_json())  # serialisable


def test_sampling_is_deterministic_and_order_independent(sqlite_conn):
    rows = [(i, f"v{i}") for i in range(100)]
    sqlite_conn.execute("CREATE TABLE a (k INTEGER, v TEXT)")
    sqlite_conn.insert_rows("a", ["k", "v"], rows)
    sqlite_conn.execute("CREATE TABLE b (k INTEGER, v TEXT)")
    sqlite_conn.insert_rows("b", ["k", "v"], list(reversed(rows)))
    s1 = sample_rows(sqlite_conn, "a", ["k", "v"], 10, seed=3)
    s2 = sample_rows(sqlite_conn, "a", ["k", "v"], 10, seed=3)
    s3 = sample_rows(sqlite_conn, "a", ["k", "v"], 10, seed=4)
    assert s1 == s2 != s3
    # same seed, same table name semantics: physical order does not matter
    assert sorted(map(tuple, sample_rows(sqlite_conn, "b", ["k", "v"], 100, 1))) == rows


def test_evidence_has_no_declared_constraints(university, university_db):
    ev = extract(university_db, "u")
    dumped = ev.model_dump_json().lower()
    assert "primary" not in dumped and "foreign" not in dumped
    assert {t.name for t in ev.tables} == {t.name for t in university.tables}


def test_mine_joins_resolves_aliases_and_unqualified_columns():
    schema = {"orders": ["o_id", "o_cust"], "customer": ["c_id", "c_name"]}
    queries = [
        "SELECT * FROM orders o JOIN customer c ON o.o_cust = c.c_id",
        "SELECT * FROM orders, customer WHERE o_cust = c_id AND c_name = 'x'",
        "SELECT * FROM orders o WHERE o.o_id = $1",
        "SELEC nonsense",
    ]
    stats = mine_joins(queries, schema)
    assert stats.n_queries == 4 and stats.n_parsed == 3 and stats.n_failed == 1
    assert stats.failure_rate == 0.25
    assert [(p.left, p.right, p.count) for p in stats.pairs] == [
        ("customer.c_id", "orders.o_cust", 2)
    ]


def test_unresolved_columns_are_counted():
    stats = mine_joins(["SELECT * FROM a, b WHERE x = y"], {"a": ["x", "y"], "b": ["x", "y"]})
    assert stats.pairs == [] and stats.n_unresolved_columns == 1


def test_workload_formats(tmp_path):
    sql = tmp_path / "w.sql"
    sql.write_text("SELECT 1; -- a comment; with semicolon\nSELECT ';' ;\n\nSELECT 3")
    assert split_sql(sql.read_text()) == ["SELECT 1", "SELECT ';'", "SELECT 3"]
    assert len(load_workload(sql)) == 3
    csv_file = tmp_path / "pgss.csv"
    csv_file.write_text('query,calls\n"SELECT * FROM a JOIN b ON a.x = b.y",7\n"SELECT 1",1\n')
    entries = load_workload(csv_file)
    assert entries[0].calls == 7
    stats = mine_joins(entries, {"a": ["x"], "b": ["y"]})
    assert stats.pairs[0].count == 7
    jl = tmp_path / "w.jsonl"
    jl.write_text('{"query": "SELECT 1", "calls": 2}\n\n')
    assert load_workload(jl)[0].calls == 2
