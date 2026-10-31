"""Screenshots for the data-engineering workflow (discover, graph, check, dbt, HTML report).

Usage (from the repository root):

    python docs/media/tools/capture_de.py --build /tmp/srev-de \\
        --mermaid path/to/mermaid.min.js [--dbt path/to/dbt --dsn postgresql://...]
    node docs/media/tools/render.mjs /tmp/srev-de docs/media

Generates a small HR dataset as CSV files (a schema that is not among the
training generators), runs the real commands and turns their output into
pages. The dbt screenshot is produced only when a dbt binary and a
PostgreSQL DSN are given.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import random
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

_spec = importlib.util.spec_from_file_location("capture", Path(__file__).with_name("capture.py"))
assert _spec and _spec.loader
capture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(capture)


def make_hr(d: Path) -> None:
    rng = random.Random(5)
    d.mkdir(parents=True)

    def write(name: str, header: list[str], rows: list[tuple[object, ...]]) -> None:
        with (d / f"{name}.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)

    deps = [("Sales", "Berlin"), ("IT", "Potsdam"), ("HR", "Berlin"), ("Ops", "Hamburg"),
            ("R&D", "Potsdam")]  # fmt: skip
    write("departments", ["DepartmentID", "Name", "City"],
          [(i, n, c) for i, (n, c) in enumerate(deps, 1)])  # fmt: skip
    emps = [
        (1000 + i, f"Emp {i}", rng.randint(1, 5), "" if i <= 5 else 1000 + rng.randint(1, 40),
         f"e{i}@corp.example", rng.randint(40, 120) * 1000)
        for i in range(1, 121)
    ]  # fmt: skip
    write("employees", ["EmployeeID", "FullName", "DepartmentID", "ManagerID", "Email", "Salary"],
          emps)  # fmt: skip
    write("projects", ["ProjectID", "Title", "OwnerDept"],
          [(p, f"Project {p}", rng.randint(1, 5)) for p in range(1, 31)])  # fmt: skip
    pairs = sorted({(rng.choice(emps)[0], rng.randint(1, 30)) for _ in range(230)})
    write("assignments", ["EmployeeID", "ProjectID", "Role", "Hours"],
          [(e, p, rng.choice(["dev", "lead", "qa"]), rng.randint(5, 40)) for e, p in pairs])  # fmt: skip


def run(cmd: str, cwd: Path, ok_codes: tuple[int, ...] = (0,)) -> str:
    proc = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, check=False)
    if proc.returncode not in ok_codes:
        raise RuntimeError(f"{cmd} failed ({proc.returncode}): {proc.stderr[-500:]}")
    return (proc.stdout + proc.stderr).rstrip("\n")


ANSI = re.compile(r"\x1b\[[0-9;]*m")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", required=True, type=Path)
    ap.add_argument("--mermaid", type=Path, required=True)
    ap.add_argument("--dbt", help="dbt executable (with dbt-postgres)")
    ap.add_argument(
        "--dsn", help="PostgreSQL DSN for the dbt run, e.g. postgresql://u@host:5432/db"
    )
    args = ap.parse_args()
    build: Path = args.build
    shutil.rmtree(build, ignore_errors=True)
    ws = build / "ws"
    make_hr(ws / "hr")
    pages: list[dict[str, object]] = []

    def page(name: str, html: str, **opts: object) -> None:
        (build / f"{name}.html").write_text(html)
        pages.append({"html": f"{name}.html", "out": name, **opts})

    discover_cmd = "srev discover --csv hr --name hr --html --dbt -o out"
    discover_out = run(discover_cmd, ws)
    graph_cmd = "srev graph --constraints out/constraints.json --evidence out/evidence.json"
    graph_out = run(graph_cmd, ws)
    join_cmd = graph_cmd + " --from assignments --to departments"
    join_out = run(join_cmd, ws)
    page("de-discover", capture.terminal_page(
        "srev discover — keys, foreign keys and join paths of an unseen CSV export",
        [capture.terminal_block(discover_cmd, discover_out),
         capture.terminal_block("srev graph --constraints … --evidence …", graph_out),
         capture.terminal_block("srev graph … --from assignments --to departments", join_out)],
    ))  # fmt: skip

    # drift: a day later someone dropped a column and loaded duplicates and an orphan
    shutil.copy(ws / "out" / "data.sqlite", ws / "today.sqlite")
    con = sqlite3.connect(ws / "today.sqlite")
    con.execute("INSERT INTO assignments SELECT * FROM assignments LIMIT 3")
    con.execute("INSERT INTO assignments VALUES (9999, 1, 'dev', 8)")
    con.execute("ALTER TABLE employees DROP COLUMN email")
    con.execute("ALTER TABLE projects ADD COLUMN budget INTEGER")
    con.commit()
    con.close()
    check_cmd = ("srev check --constraints out/constraints.json --sqlite today.sqlite "
                 "--evidence out/evidence.json --name hr")  # fmt: skip
    check_out = run(check_cmd + "; echo exit code: $?", ws)
    page("de-check", capture.terminal_page(
        "srev check — constraints as a data contract (after a bad load)",
        [capture.terminal_block(check_cmd + "; echo exit code: $?", check_out)],
    ))  # fmt: skip

    if args.dbt and args.dsn:
        run(f"srev ingest csv hr --dsn {args.dsn} --schema hr", ws)
        run(f"srev discover --dsn {args.dsn} --schema hr --name hr --dbt -o pg", ws)
        m = re.match(r"postgresql://(?P<user>[^@:]+)(:(?P<pw>[^@]*))?@(?P<host>[^:/]+)"
                     r"(:(?P<port>\d+))?/(?P<db>\w+)", args.dsn)  # fmt: skip
        assert m, "unsupported DSN format"
        profiles = ws / "profiles"
        profiles.mkdir()
        (profiles / "profiles.yml").write_text(json.dumps({"srev_hr": {"target": "dev", "outputs": {
            "dev": {"type": "postgres", "host": m["host"], "port": int(m["port"] or 5432),
                    "user": m["user"], "password": m["pw"] or "", "dbname": m["db"],
                    "schema": "hr_dbt", "threads": 2}}}}))  # fmt: skip
        dbt_out = ANSI.sub(
            "", run(f"{args.dbt} test --profiles-dir ../../profiles", ws / "pg" / "dbt")
        )
        lines = [ln.split(" ", 1)[1] if re.match(r"^\d\d:\d\d:\d\d ", ln) else ln
                 for ln in dbt_out.splitlines()]  # fmt: skip
        keep = [ln for ln in lines if " PASS " in ln or "Done." in ln or "Finished" in ln]
        page("de-dbt", capture.terminal_page(
            "dbt test on the project generated by srev discover --dbt (PostgreSQL)",
            [capture.terminal_block("cd pg/dbt && dbt test", "\n".join(keep))],
        ))  # fmt: skip

    report = (ws / "out" / "report.html").read_text()
    shutil.copy(args.mermaid, build / "mermaid.min.js")
    report = re.sub(r'src="https://cdn\.jsdelivr\.net/npm/mermaid@[^"]+"', 'src="mermaid.min.js"',
                    report)  # fmt: skip
    report = report.replace("<main>", "<main id='shot'>", 1)
    (build / "html-report.html").write_text(report)
    pages.append({"html": "html-report.html", "out": "html-report-overview",
                  "viewport": [1280, 1180], "clip": True, "wait": False})  # fmt: skip
    pages.append({"html": "html-report.html", "out": "html-report-columns",
                  "selector": "#t-employees", "colorScheme": "dark"})  # fmt: skip
    (build / "pages.json").write_text(json.dumps(pages, indent=1))
    print(f"built {len(pages)} pages in {build}")


if __name__ == "__main__":
    main()
