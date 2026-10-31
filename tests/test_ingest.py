import datetime as dt

import pytest

from srev.cli import main
from srev.db import SQLiteConnector, materialize
from srev.ingest import dataset_from_csv, dataset_from_frames, infer_type, read_csv, sanitize


def test_infer_type_and_sanitize():
    assert infer_type(["1", "2", None]) == "INTEGER"
    assert infer_type(["01067", "10115"]) == "TEXT"  # leading zeros stay text
    assert infer_type(["1.5", "2", "-3e2"]) == "DOUBLE PRECISION"
    assert infer_type(["2024-01-31", "2023-02-28"]) == "DATE"
    assert infer_type(["2024-02-30"]) == "TEXT"  # not a real date
    assert infer_type(["true", "No"]) == "BOOLEAN"
    assert infer_type(["9999999999"]) == "BIGINT"
    assert infer_type([None, None]) == "TEXT"
    taken: set[str] = set()
    assert sanitize("Customer ID", taken) == "customer_id"
    assert sanitize("customer-id", taken) == "customer_id_2"
    assert sanitize("2nd value") == "c_2nd_value"


def _write(tmp_path):
    d = tmp_path / "export"
    d.mkdir()
    (d / "Customers.csv").write_text(
        "Customer ID;Name;Zip;Joined\n1;Ada;01067;2020-01-05\n"
        "2;Alan;10115;\n3;Grace;NULL;2021-07-01\n"
    )
    (d / "orders.csv").write_text("order_id,customer_id,amount\n10,1,9.5\n11,1,3\n12,3,\n")
    return d


def test_read_csv_and_folder(tmp_path):
    d = _write(tmp_path)
    t = read_csv(d / "Customers.csv")
    assert t.name == "customers"
    assert [(c.name, c.type) for c in t.columns] == [
        ("customer_id", "INTEGER"),
        ("name", "TEXT"),
        ("zip", "TEXT"),
        ("joined", "DATE"),
    ]
    assert t.rows[0] == (1, "Ada", "01067", dt.date(2020, 1, 5))
    assert t.rows[1][3] is None and t.rows[2][2] is None
    ds = dataset_from_csv([d])
    assert [t.name for t in ds.tables] == ["customers", "orders"]
    assert ds.table("orders").rows[2] == (12, 3, None)
    with SQLiteConnector(":memory:") as conn:
        materialize(ds, conn)
        assert conn.query("SELECT SUM(amount) FROM orders")[0][0] == 12.5
    with pytest.raises(FileNotFoundError):
        dataset_from_csv([tmp_path / "nothing"])
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError):
        dataset_from_csv([tmp_path / "empty"])


def test_dataframes():
    pd = pytest.importorskip("pandas")
    frames = {
        "Orders": pd.DataFrame(
            {
                "id": [1, 2, 3],
                "cust": [10.0, None, 11.0],
                "when": pd.to_datetime(["2024-01-01", "2024-01-02", None]),
            }
        ),
    }
    ds = dataset_from_frames(frames)
    t = ds.table("orders")
    assert [c.type for c in t.columns] == ["INTEGER", "INTEGER", "DATE"]
    assert t.rows[1] == (2, None, dt.date(2024, 1, 2))


def test_cli_ingest(tmp_path, capsys):
    d = _write(tmp_path)
    db = tmp_path / "x.sqlite"
    assert main(["ingest", "csv", str(d), "--sqlite", str(db)]) == 0
    out = capsys.readouterr().out
    assert "customers (3 rows)" in out and "zip TEXT" in out
