import pytest

from srev.cli import main
from srev.db import SQLiteConnector, materialize
from srev.extract import extract
from srev.graph import SchemaGraph
from srev.truth import gold_from_dataset


@pytest.fixture
def tpch_graph(tpch):
    with SQLiteConnector(":memory:") as conn:
        materialize(tpch, conn)
        ev = extract(conn, "tpch", rows_per_table=0)
    return SchemaGraph.from_constraints(ev, gold_from_dataset(tpch).labels), ev


def test_roles_components_hubs(tpch_graph):
    graph, _ = tpch_graph
    roles = {r.name: r for r in graph.roles()}
    assert roles["lineitem"].role == "fact"  # key (l_orderkey, l_linenumber) is not all FKs
    assert roles["region"].role == "lookup"
    assert roles["orders"].role == "dimension"
    assert roles["partsupp"].role == "bridge"  # key made of two FK columns
    assert graph.components() == [sorted(graph.tables)]
    hubs = dict(graph.hubs())
    assert hubs["part"] >= 2 and hubs["supplier"] >= 2
    stars = graph.star_schemas()
    assert {"orders", "customer", "nation", "region"} <= set(stars["lineitem"])


def test_join_path_and_sql(tpch_graph):
    graph, _ = tpch_graph
    path = graph.join_path("lineitem", "region")
    assert [to for _, to, _ in path] == ["supplier", "nation", "region"]  # shortest of two routes
    sql = graph.join_sql("region", "nation")
    assert (
        sql == "SELECT *\nFROM region AS t0\nJOIN nation AS t1 ON t0.r_regionkey = t1.n_regionkey"
    )
    assert graph.join_path("orders", "orders") == []
    with pytest.raises(KeyError):
        graph.join_path("orders", "nowhere")


def test_isolated_and_unreachable(university_db, university_gold):
    ev = extract(university_db, "u", rows_per_table=0)
    fks = [g for g in university_gold.labels if g.type == "FK" and g.table != "advisor"]
    graph = SchemaGraph.from_constraints(ev, fks)
    roles = {r.name: r for r in graph.roles()}
    assert roles["advisor"].role == "isolated"
    assert graph.join_sql("advisor", "student") is None
    assert any(r.role == "fact" or r.role == "bridge" for r in roles.values())


def test_cli_graph(tmp_path, capsys, tpch):
    import json

    with SQLiteConnector(":memory:") as conn:
        materialize(tpch, conn)
        extract(conn, "tpch", rows_per_table=0).save(tmp_path / "ev.json")
    gold = gold_from_dataset(tpch)
    (tmp_path / "c.json").write_text(json.dumps([g.model_dump() for g in gold.labels]))
    args = [
        "graph",
        "--constraints",
        str(tmp_path / "c.json"),
        "--evidence",
        str(tmp_path / "ev.json"),
    ]
    assert main([*args, "-o", str(tmp_path / "g.json")]) == 0
    out = capsys.readouterr().out
    assert "lineitem" in out and "star:" in out
    assert json.loads((tmp_path / "g.json").read_text())["roles"]
    assert main([*args, "--from", "partsupp", "--to", "supplier"]) == 0
    assert "JOIN supplier AS t1" in capsys.readouterr().out
