"""A "legacy" retail database: abbreviated names and no declared foreign keys.

Only primary keys and one UNIQUE constraint are declared, as in many systems
where FKs were dropped for load performance. The relationships are therefore
given as *curated* gold labels, which is how real legacy databases have to be
labelled (cf. the IMDB/JOB setting in docs/RESEARCH.md).
"""

from __future__ import annotations

import datetime as dt
import random
from decimal import Decimal

from srev.datasets.base import ColumnDef, CuratedFD, CuratedFK, Dataset, TableDef

_REGIONS = [("N", "North"), ("S", "South"), ("E", "East"), ("W", "West"), ("C", "Central")]
_CATS = [
    ("EL", "Electronics"),
    ("HG", "Home and Garden"),
    ("TY", "Toys"),
    ("BK", "Books"),
    ("SP", "Sports"),
    ("FD", "Food"),
]
_NOUNS = [
    "lamp",
    "kettle",
    "novel",
    "drone",
    "ball",
    "blender",
    "puzzle",
    "racket",
    "tent",
    "cable",
    "chair",
    "pan",
]
_ADJ = ["smart", "classic", "mini", "pro", "eco", "deluxe", "compact", "rugged"]
_NAMES = [
    "Ali",
    "Bea",
    "Cem",
    "Dana",
    "Emil",
    "Fay",
    "Gus",
    "Hana",
    "Ivo",
    "Jun",
    "Kai",
    "Lea",
    "Mo",
    "Nia",
    "Oda",
    "Pia",
]
_CARRIERS = ["DHL", "UPS", "DPD", "Hermes", "GLS"]
_STATUS = ["NEW", "PAID", "SHIPPED", "DONE", "CANCELLED"]

QUERIES = [
    """SELECT c.cust_nm, o.ord_no FROM tbl_ord o, tbl_cust c WHERE o.cust_no = c.cust_no""",
    """SELECT r.region_desc, COUNT(*) FROM tbl_cust c JOIN lkp_region r
       ON c.region_cd = r.region_cd GROUP BY r.region_desc""",
    """SELECT p.prod_nm, SUM(l.qty) FROM tbl_ord_line l JOIN tbl_prod p ON l.prod_no = p.prod_no
       GROUP BY p.prod_nm""",
    """SELECT o.ord_dt, l.line_no, l.qty FROM tbl_ord o JOIN tbl_ord_line l
       ON l.ord_no = o.ord_no WHERE o.status_cd = 'PAID'""",
    """SELECT s.tracking_no FROM tbl_ship s JOIN tbl_ord o ON s.ord_no = o.ord_no
       WHERE o.status_cd = 'SHIPPED'""",
    """SELECT r.region_desc, COUNT(*) FROM tbl_ord o JOIN lkp_region r
       ON r.region_cd = o.ship_region_cd GROUP BY r.region_desc""",
    """SELECT * FROM tbl_ord WHERE cust_no = $1""",
]


def generate(seed: int = 42, scale: float = 1.0) -> Dataset:
    rng = random.Random(seed)
    n_cust = max(10, int(120 * scale))
    n_prod = max(10, int(80 * scale))
    n_ord = max(20, int(400 * scale))

    region = TableDef(
        "lkp_region",
        [ColumnDef("region_cd", "CHAR(1)", False), ColumnDef("region_desc", "VARCHAR(20)")],
        rows=list(_REGIONS),
        primary_key=["region_cd"],
    )
    cust = TableDef(
        "tbl_cust",
        [
            ColumnDef("cust_no", "INTEGER", False),
            ColumnDef("cust_nm", "VARCHAR(40)"),
            ColumnDef("email", "VARCHAR(60)"),
            ColumnDef("region_cd", "CHAR(1)"),
            ColumnDef("created_dt", "DATE"),
        ],
        rows=[
            (
                5000 + i,
                (nm := f"{rng.choice(_NAMES)} {rng.choice(_NAMES)}son"),
                f"{nm.split()[0].lower()}{i}@mail.example",
                rng.choice(_REGIONS)[0],
                dt.date(2015, 1, 1) + dt.timedelta(days=rng.randrange(3000)),
            )
            for i in range(n_cust)
        ],
        primary_key=["cust_no"],
        uniques=[["email"]],
    )
    prod_rows = []
    for i in range(n_prod):
        cat = rng.choice(_CATS)
        prod_rows.append(
            (
                f"P-{i + 1:05d}",
                f"{rng.choice(_ADJ)} {rng.choice(_NOUNS)}",
                cat[0],
                cat[1],
                Decimal(f"{rng.uniform(2, 500):.2f}"),
            )
        )
    prod = TableDef(
        "tbl_prod",
        [
            ColumnDef("prod_no", "VARCHAR(8)", False),
            ColumnDef("prod_nm", "VARCHAR(40)"),
            ColumnDef("cat_cd", "CHAR(2)"),
            ColumnDef("cat_desc", "VARCHAR(30)"),
            ColumnDef("unit_price", "NUMERIC(10,2)"),
        ],
        rows=prod_rows,
        primary_key=["prod_no"],
    )
    ord_rows: list[tuple[object, ...]] = []
    line_rows: list[tuple[object, ...]] = []
    ship_rows: list[tuple[object, ...]] = []
    for i in range(n_ord):
        ord_no = 900000 + i
        status = rng.choice(_STATUS)
        ord_rows.append(
            (
                ord_no,
                5000 + rng.randrange(n_cust),
                dt.date(2020, 1, 1) + dt.timedelta(days=rng.randrange(1500)),
                rng.choice(_REGIONS)[0],
                status,
            )
        )
        for ln in range(1, rng.randrange(1, 5) + 1):
            p = rng.choice(prod_rows)
            line_rows.append((ord_no, ln, p[0], rng.randrange(1, 6), p[4]))
        if status in ("SHIPPED", "DONE"):
            ship_rows.append(
                (
                    len(ship_rows) + 1,
                    ord_no,
                    rng.choice(_CARRIERS),
                    f"TRK{rng.randrange(10**9, 10**10)}",
                )
            )
    order = TableDef(
        "tbl_ord",
        [
            ColumnDef("ord_no", "INTEGER", False),
            ColumnDef("cust_no", "INTEGER"),
            ColumnDef("ord_dt", "DATE"),
            ColumnDef("ship_region_cd", "CHAR(1)"),
            ColumnDef("status_cd", "VARCHAR(10)"),
        ],
        rows=ord_rows,
        primary_key=["ord_no"],
    )
    line = TableDef(
        "tbl_ord_line",
        [
            ColumnDef("ord_no", "INTEGER", False),
            ColumnDef("line_no", "INTEGER", False),
            ColumnDef("prod_no", "VARCHAR(8)"),
            ColumnDef("qty", "INTEGER"),
            ColumnDef("price", "NUMERIC(10,2)"),
        ],
        rows=line_rows,
        primary_key=["ord_no", "line_no"],
    )
    ship = TableDef(
        "tbl_ship",
        [
            ColumnDef("ship_id", "INTEGER", False),
            ColumnDef("ord_no", "INTEGER"),
            ColumnDef("carrier", "VARCHAR(10)"),
            ColumnDef("tracking_no", "VARCHAR(20)"),
        ],
        rows=ship_rows,
        primary_key=["ship_id"],
    )
    return Dataset(
        name="retail_legacy",
        tables=[region, cust, prod, order, line, ship],
        description="Legacy-style retail schema with abbreviated names and no declared FKs.",
        licence="Generated data (MIT).",
        curated_fks=[
            CuratedFK("tbl_cust", ["region_cd"], "lkp_region", ["region_cd"]),
            CuratedFK("tbl_ord", ["cust_no"], "tbl_cust", ["cust_no"]),
            CuratedFK("tbl_ord", ["ship_region_cd"], "lkp_region", ["region_cd"]),
            CuratedFK("tbl_ord_line", ["ord_no"], "tbl_ord", ["ord_no"]),
            CuratedFK("tbl_ord_line", ["prod_no"], "tbl_prod", ["prod_no"]),
            CuratedFK("tbl_ship", ["ord_no"], "tbl_ord", ["ord_no"]),
        ],
        curated_fds=[
            CuratedFD("tbl_prod", ["cat_cd"], "cat_desc", "denormalised category lookup"),
            CuratedFD("tbl_prod", ["cat_desc"], "cat_cd", "category description is unique"),
        ],
        queries=QUERIES,
        params={"seed": seed, "scale": scale},
    )
