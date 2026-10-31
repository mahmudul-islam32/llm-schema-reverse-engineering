import json
import sqlite3

import pytest

from srev.cli import main
from srev.config import DatasetSpec, ExperimentConfig
from srev.datasets.spider import list_databases, load_spider
from srev.runner import run_experiment
from srev.truth import gold_from_dataset


@pytest.fixture
def spider_root(tmp_path):
    """A two-database benchmark in Spider layout (no FKs declared in the files)."""
    root = tmp_path / "spider"
    entries = []
    for db_id, n in (("shop", 30), ("club", 20)):
        (root / "database" / db_id).mkdir(parents=True)
        con = sqlite3.connect(root / "database" / db_id / f"{db_id}.sqlite")
        con.execute('CREATE TABLE "Customer" (Customer_ID INTEGER, Name TEXT)')
        con.execute("CREATE TABLE orders (order_id INTEGER, customer_id INTEGER, amount REAL)")
        con.executemany('INSERT INTO "Customer" VALUES (?, ?)', [(i, f"c{i}") for i in range(n)])
        con.executemany(
            "INSERT INTO orders VALUES (?, ?, ?)", [(i, i % n, i * 1.5) for i in range(3 * n)]
        )
        con.commit()
        con.close()
        entries.append(
            {
                "db_id": db_id,
                "table_names_original": ["customer", "orders"],  # case differs on purpose
                "column_names_original": [
                    [-1, "*"],
                    [0, "customer_id"],
                    [0, "name"],
                    [1, "order_id"],
                    [1, "customer_id"],
                    [1, "amount"],
                ],
                "column_types": ["text", "number", "text", "number", "number", "number"],
                "primary_keys": [1, [3]],
                "foreign_keys": [[4, 1]],
            }
        )
    (root / "tables.json").write_text(json.dumps(entries))
    (root / "dev.json").write_text(
        json.dumps(
            [
                {
                    "db_id": "shop",
                    "query": "SELECT * FROM orders o JOIN customer c "
                    "ON o.customer_id = c.customer_id",
                },
                {"db_id": "club", "query": "SELECT count(*) FROM orders"},
            ]
        )
    )
    return root


def test_load_spider_uses_annotations(spider_root):
    ds = load_spider(spider_root, "shop", queries_json="dev.json")
    gold = gold_from_dataset(ds)
    keys = {g.key() for g in gold.labels}
    assert ("UCC", ("customer.customer_id",), ()) in keys
    assert ("UCC", ("orders.order_id",), ()) in keys
    assert ("FK", ("orders.customer_id",), ("customer.customer_id",)) in keys
    assert ds.queries and "JOIN" in ds.queries[0]
    assert [d["db_id"] for d in list_databases(spider_root)] == ["club", "shop"]
    with pytest.raises(FileNotFoundError):
        load_spider(spider_root, "shop", db_dir="missing")


def test_spider_spec_expands(spider_root):
    spec = DatasetSpec(name="spider", source="spider", path=str(spider_root), limit=1)
    assert [s.name for s in spec.expand()] == ["spider_club"]
    spec = DatasetSpec(name="sp", source="spider", path=str(spider_root), db_ids=["shop"])
    assert [s.db_id for s in spec.expand()] == ["shop"]


def test_spider_run(spider_root, tmp_path, capsys):
    cfg = ExperimentConfig.model_validate(
        {
            "name": "spider",
            "output_dir": str(tmp_path),
            "datasets": [
                {
                    "name": "spider",
                    "source": "spider",
                    "path": str(spider_root),
                    "queries_json": "dev.json",
                }
            ],
            "methods": [
                {
                    "name": "approx",
                    "kind": "baseline",
                    "profiling": {"ind_min_coverage": 0.9, "ind_coverage": "rows"},
                },
                {"name": "lr", "kind": "learned"},
            ],
            "eval": {"bootstrap": 0},
        }
    )
    run_dir = run_experiment(cfg, tmp_path / "run")
    metrics = json.loads((run_dir / "metrics.json").read_text())
    fk = next(
        m
        for m in metrics
        if m["db_id"] == "spider_shop_none" and m["type"] == "FK" and m["variant"] == "approx"
    )
    assert fk["recall"] == 1.0
    assert {m["dataset"] for m in metrics} == {"spider_club", "spider_shop"}
    assert any(m["variant"] == "lr" for m in metrics)  # two databases enable LODO
    assert main(["datasets", "spider", str(spider_root)]) == 0
    assert "shop" in capsys.readouterr().out
