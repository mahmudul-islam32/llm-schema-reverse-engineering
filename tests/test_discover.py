import csv
import json
import random

import pytest

from srev import api
from srev.cli import main
from srev.discover import DiscoverConfig
from srev.model import VerifiedProposal


@pytest.fixture(scope="module")
def hr_dir(tmp_path_factory):
    """An unseen schema (not among the training generators) as CSV files."""
    d = tmp_path_factory.mktemp("hr")
    rng = random.Random(5)

    def write(name, header, rows):
        with (d / f"{name}.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)

    write(
        "departments",
        ["DepartmentID", "Name"],
        [(i, n) for i, n in enumerate(["Sales", "IT", "HR", "Ops", "R&D"], 1)],
    )
    emps = [
        (1000 + i, f"Emp {i}", rng.randint(1, 5), "" if i <= 5 else 1000 + rng.randint(1, 20))
        for i in range(1, 121)
    ]
    write("employees", ["EmployeeID", "FullName", "DepartmentID", "ManagerID"], emps)
    write(
        "projects",
        ["ProjectID", "Title", "OwnerDept"],
        [(p, f"Project {p}", rng.randint(1, 5)) for p in range(1, 31)],
    )
    pairs = sorted({(rng.choice(emps)[0], rng.randint(1, 30)) for _ in range(250)})
    write(
        "assignments",
        ["EmployeeID", "ProjectID", "Hours"],
        [(e, p, rng.randint(5, 40)) for e, p in pairs],
    )
    return d


EXPECTED_FKS = {
    ("FK", ("assignments.employeeid",), ("employees.employeeid",)),
    ("FK", ("assignments.projectid",), ("projects.projectid",)),
    ("FK", ("employees.departmentid",), ("departments.departmentid",)),
    ("FK", ("projects.ownerdept",), ("departments.departmentid",)),
}


def test_discover_csv_on_unseen_schema(hr_dir):
    result = api.discover_csv([hr_dir])
    keys = {p.key() for p in result.accepted()}
    assert keys >= EXPECTED_FKS
    assert ("UCC", ("departments.departmentid",), ()) in keys
    assert ("UCC", ("assignments.employeeid", "assignments.projectid"), ()) in keys
    assert all(v.verdict.accepted for v in result.constraints)
    assert result.model["training_positives"] > 0 and not result.model["uses_query_log"]
    fks = [p for p in result.accepted() if p.type == "FK"]
    assert not [p for p in fks if p.key() not in EXPECTED_FKS]  # no false FK
    # self-references are rare in the training data, so the model is unsure about them;
    # with a lower review threshold the candidate surfaces for a human instead of vanishing
    self_ref = ("FK", ("employees.managerid",), ("employees.employeeid",))
    lenient = api.discover_csv([hr_dir], config=DiscoverConfig(review_threshold=0.01))
    review = {v.proposal.key() for v in lenient.review}
    assert self_ref in review | {p.key() for p in lenient.accepted()}
    assert all(v.verdict.accepted for v in lenient.review)


def test_discover_frames_and_to_frame():
    pd = pytest.importorskip("pandas")
    customers = pd.DataFrame({"customer_id": range(1, 41), "name": [f"c{i}" for i in range(40)]})
    orders = pd.DataFrame(
        {"order_id": range(100, 220), "customer_id": [1 + i % 40 for i in range(120)]}
    )
    result = api.discover_frames({"customers": customers, "orders": orders})
    assert ("FK", ("orders.customer_id",), ("customers.customer_id",)) in {
        p.key() for p in result.accepted()
    }
    df = result.to_frame()
    assert {"type", "lhs", "rhs", "confidence", "statistic", "value"} <= set(df.columns)
    strict = api.discover_frames(
        {"customers": customers, "orders": orders}, config=DiscoverConfig(fk_threshold=0.999)
    )
    assert strict.counts()["FK"] <= result.counts()["FK"]


def test_cli_discover(hr_dir, tmp_path, capsys):
    out = tmp_path / "disc"
    assert main(["discover", "--csv", str(hr_dir), "-o", str(out)]) == 0
    for name in (
        "constraints.json",
        "review.json",
        "rejected.json",
        "model.sql",
        "model.mmd",
        "migration.sql",
        "evidence.json",
        "data.sqlite",
    ):
        assert (out / name).exists(), name
    items = [
        VerifiedProposal.model_validate(x)
        for x in json.loads((out / "constraints.json").read_text())
    ]
    assert {v.proposal.key() for v in items} >= EXPECTED_FKS
    assert "FOREIGN KEY" in (out / "model.sql").read_text()
    printed = capsys.readouterr().out
    assert "assignments.projectid" in printed
    # the output feeds the other commands, e.g. synth and verify
    assert (
        main(
            [
                "verify",
                "--proposals",
                str(out / "constraints.json"),
                "--sqlite",
                str(out / "data.sqlite"),
                "-o",
                str(tmp_path / "v.json"),
            ]
        )
        == 0
    )
