"""A small, seeded generator with the TPC-H schema.

This is *not* the official ``dbgen`` and the data distribution is simplified;
it reproduces the TPC-H table/column layout and its declared keys so the
pipeline can be piloted without downloading anything. For the real benchmark,
load ``dbgen`` output into PostgreSQL and use ``source: postgres`` in the run
config.
"""

from __future__ import annotations

import datetime as dt
import random
from decimal import Decimal

from srev.datasets.base import ColumnDef, Dataset, ForeignKeyDef, TableDef

_REGIONS = ["AFRICA", "AMERICA", "ASIA", "EUROPE", "MIDDLE EAST"]
_NATIONS = [
    ("ALGERIA", 0),
    ("ARGENTINA", 1),
    ("BRAZIL", 1),
    ("CANADA", 1),
    ("EGYPT", 4),
    ("ETHIOPIA", 0),
    ("FRANCE", 3),
    ("GERMANY", 3),
    ("INDIA", 2),
    ("INDONESIA", 2),
    ("IRAN", 4),
    ("IRAQ", 4),
    ("JAPAN", 2),
    ("JORDAN", 4),
    ("KENYA", 0),
    ("MOROCCO", 0),
    ("MOZAMBIQUE", 0),
    ("PERU", 1),
    ("CHINA", 2),
    ("ROMANIA", 3),
    ("SAUDI ARABIA", 4),
    ("VIETNAM", 2),
    ("RUSSIA", 3),
    ("UNITED KINGDOM", 3),
    ("UNITED STATES", 1),
]
_SEGMENTS = ["AUTOMOBILE", "BUILDING", "FURNITURE", "HOUSEHOLD", "MACHINERY"]
_PRIORITIES = ["1-URGENT", "2-HIGH", "3-MEDIUM", "4-NOT SPECIFIED", "5-LOW"]
_SHIPMODES = ["AIR", "FOB", "MAIL", "RAIL", "REG AIR", "SHIP", "TRUCK"]
_INSTRUCT = ["COLLECT COD", "DELIVER IN PERSON", "NONE", "TAKE BACK RETURN"]
_CONTAINERS = ["SM CASE", "SM BOX", "MED BAG", "MED BOX", "LG CASE", "LG PACK", "JUMBO JAR"]
_TYPES = ["STANDARD", "SMALL", "MEDIUM", "LARGE", "ECONOMY", "PROMO"]
_MATERIALS = ["TIN", "NICKEL", "BRASS", "STEEL", "COPPER"]
_COLOURS = ["almond", "azure", "blush", "coral", "forest", "ivory", "khaki", "linen", "navy"]
_WORDS = [
    "carefully",
    "final",
    "deposits",
    "sleep",
    "quickly",
    "regular",
    "packages",
    "haggle",
    "furiously",
    "ironic",
    "accounts",
    "boost",
    "slyly",
    "express",
    "requests",
    "wake",
    "blithely",
    "pending",
    "theodolites",
    "nag",
]

QUERIES = [
    # pricing summary with order and customer context (Q3 style)
    """SELECT l.l_orderkey, SUM(l.l_extendedprice * (1 - l.l_discount)) AS revenue,
              o.o_orderdate, o.o_shippriority
       FROM customer c, orders o, lineitem l
       WHERE c.c_mktsegment = 'BUILDING' AND c.c_custkey = o.o_custkey
         AND l.l_orderkey = o.o_orderkey AND o.o_orderdate < DATE '1995-03-15'
       GROUP BY l.l_orderkey, o.o_orderdate, o.o_shippriority""",
    # local supplier volume (Q5 style)
    """SELECT n.n_name, SUM(l.l_extendedprice * (1 - l.l_discount)) AS revenue
       FROM customer c
       JOIN orders o ON c.c_custkey = o.o_custkey
       JOIN lineitem l ON l.l_orderkey = o.o_orderkey
       JOIN supplier s ON l.l_suppkey = s.s_suppkey AND c.c_nationkey = s.s_nationkey
       JOIN nation n ON s.s_nationkey = n.n_nationkey
       JOIN region r ON n.n_regionkey = r.r_regionkey
       WHERE r.r_name = 'ASIA'
       GROUP BY n.n_name""",
    # returned items per customer (Q10 style)
    """SELECT c.c_custkey, c.c_name, n.n_name
       FROM customer c JOIN orders o ON c.c_custkey = o.o_custkey
       JOIN lineitem l ON l.l_orderkey = o.o_orderkey
       JOIN nation n ON c.c_nationkey = n.n_nationkey
       WHERE l.l_returnflag = 'R'""",
    # important stock (Q11 style)
    """SELECT ps.ps_partkey, SUM(ps.ps_supplycost * ps.ps_availqty)
       FROM partsupp ps, supplier s, nation n
       WHERE ps.ps_suppkey = s.s_suppkey AND s.s_nationkey = n.n_nationkey
         AND n.n_name = 'GERMANY'
       GROUP BY ps.ps_partkey""",
    # profit by part supplier pair (Q9 style)
    """SELECT p.p_name, l.l_quantity, ps.ps_supplycost
       FROM part p, supplier s, lineitem l, partsupp ps
       WHERE s.s_suppkey = l.l_suppkey AND ps.ps_suppkey = l.l_suppkey
         AND ps.ps_partkey = l.l_partkey AND p.p_partkey = l.l_partkey
         AND p.p_name LIKE '%green%'""",
    # minimum cost supplier (Q2 style)
    """SELECT s.s_acctbal, s.s_name, n.n_name, p.p_partkey
       FROM part p, supplier s, partsupp ps, nation n, region r
       WHERE p.p_partkey = ps.ps_partkey AND s.s_suppkey = ps.ps_suppkey
         AND s.s_nationkey = n.n_nationkey AND n.n_regionkey = r.r_regionkey
         AND r.r_name = 'EUROPE'""",
    # customer distribution (Q13 style)
    """SELECT c.c_custkey, COUNT(o.o_orderkey)
       FROM customer c LEFT OUTER JOIN orders o ON c.c_custkey = o.o_custkey
       GROUP BY c.c_custkey""",
    # shipping modes (Q12 style)
    """SELECT l.l_shipmode, COUNT(*) FROM orders o JOIN lineitem l
       ON o.o_orderkey = l.l_orderkey WHERE l.l_shipmode IN ('MAIL', 'SHIP')
       GROUP BY l.l_shipmode""",
    # a statement the miner cannot parse, kept on purpose to exercise failure counting
    "SELEC broken FROM",
]


def _text(rng: random.Random, n: int, width: int = 40) -> str:
    return " ".join(rng.choice(_WORDS) for _ in range(n))[:width].rstrip()


def _money(rng: random.Random, lo: float, hi: float) -> Decimal:
    return Decimal(f"{rng.uniform(lo, hi):.2f}")


def generate(seed: int = 42, scale: float = 1.0) -> Dataset:
    rng = random.Random(seed)
    n_supp = max(5, int(20 * scale))
    n_cust = max(10, int(150 * scale))
    n_part = max(10, int(200 * scale))
    n_orders = max(20, int(600 * scale))

    region = TableDef(
        "region",
        [
            ColumnDef("r_regionkey", "INTEGER", False),
            ColumnDef("r_name", "VARCHAR(25)", False),
            ColumnDef("r_comment", "VARCHAR(152)"),
        ],
        rows=[(i, name, _text(rng, 6)) for i, name in enumerate(_REGIONS)],
        primary_key=["r_regionkey"],
    )
    nation = TableDef(
        "nation",
        [
            ColumnDef("n_nationkey", "INTEGER", False),
            ColumnDef("n_name", "VARCHAR(25)", False),
            ColumnDef("n_regionkey", "INTEGER", False),
            ColumnDef("n_comment", "VARCHAR(152)"),
        ],
        rows=[(i, name, r, _text(rng, 6)) for i, (name, r) in enumerate(_NATIONS)],
        primary_key=["n_nationkey"],
        foreign_keys=[ForeignKeyDef(["n_regionkey"], "region", ["r_regionkey"])],
    )
    supplier = TableDef(
        "supplier",
        [
            ColumnDef("s_suppkey", "INTEGER", False),
            ColumnDef("s_name", "VARCHAR(25)", False),
            ColumnDef("s_address", "VARCHAR(40)", False),
            ColumnDef("s_nationkey", "INTEGER", False),
            ColumnDef("s_phone", "VARCHAR(15)", False),
            ColumnDef("s_acctbal", "NUMERIC(12,2)", False),
            ColumnDef("s_comment", "VARCHAR(101)"),
        ],
        rows=[
            (
                i,
                f"Supplier#{i:09d}",
                _text(rng, 3),
                (nk := rng.randrange(25)),
                f"{nk + 10}-{rng.randrange(100, 999)}-{rng.randrange(1000, 9999)}",
                _money(rng, -999, 9999),
                _text(rng, 8, 101),
            )
            for i in range(1, n_supp + 1)
        ],
        primary_key=["s_suppkey"],
        foreign_keys=[ForeignKeyDef(["s_nationkey"], "nation", ["n_nationkey"])],
    )
    customer = TableDef(
        "customer",
        [
            ColumnDef("c_custkey", "INTEGER", False),
            ColumnDef("c_name", "VARCHAR(25)", False),
            ColumnDef("c_address", "VARCHAR(40)", False),
            ColumnDef("c_nationkey", "INTEGER", False),
            ColumnDef("c_phone", "VARCHAR(15)", False),
            ColumnDef("c_acctbal", "NUMERIC(12,2)", False),
            ColumnDef("c_mktsegment", "VARCHAR(10)", False),
            ColumnDef("c_comment", "VARCHAR(117)"),
        ],
        rows=[
            (
                i,
                f"Customer#{i:09d}",
                _text(rng, 3),
                (nk := rng.randrange(25)),
                f"{nk + 10}-{rng.randrange(100, 999)}-{rng.randrange(1000, 9999)}",
                _money(rng, -999, 9999),
                rng.choice(_SEGMENTS),
                _text(rng, 8, 117),
            )
            for i in range(1, n_cust + 1)
        ],
        primary_key=["c_custkey"],
        foreign_keys=[ForeignKeyDef(["c_nationkey"], "nation", ["n_nationkey"])],
    )
    part_rows = []
    for i in range(1, n_part + 1):
        name = " ".join(rng.sample(_COLOURS, 3))
        mfgr = rng.randrange(1, 6)
        part_rows.append(
            (
                i,
                name,
                f"Manufacturer#{mfgr}",
                f"Brand#{mfgr}{rng.randrange(1, 6)}",
                f"{rng.choice(_TYPES)} {rng.choice(_MATERIALS)}",
                rng.randrange(1, 51),
                rng.choice(_CONTAINERS),
                Decimal(f"{900 + (i % 1000) + i / 100:.2f}"),
                _text(rng, 3, 23),
            )
        )
    part = TableDef(
        "part",
        [
            ColumnDef("p_partkey", "INTEGER", False),
            ColumnDef("p_name", "VARCHAR(55)", False),
            ColumnDef("p_mfgr", "VARCHAR(25)", False),
            ColumnDef("p_brand", "VARCHAR(10)", False),
            ColumnDef("p_type", "VARCHAR(25)", False),
            ColumnDef("p_size", "INTEGER", False),
            ColumnDef("p_container", "VARCHAR(10)", False),
            ColumnDef("p_retailprice", "NUMERIC(12,2)", False),
            ColumnDef("p_comment", "VARCHAR(23)"),
        ],
        rows=part_rows,
        primary_key=["p_partkey"],
    )
    ps_rows = []
    supps_of_part: dict[int, list[int]] = {}
    for p in range(1, n_part + 1):
        supps = rng.sample(range(1, n_supp + 1), min(4, n_supp))
        supps_of_part[p] = supps
        for s in supps:
            ps_rows.append((p, s, rng.randrange(1, 10000), _money(rng, 1, 1000), _text(rng, 10)))
    partsupp = TableDef(
        "partsupp",
        [
            ColumnDef("ps_partkey", "INTEGER", False),
            ColumnDef("ps_suppkey", "INTEGER", False),
            ColumnDef("ps_availqty", "INTEGER", False),
            ColumnDef("ps_supplycost", "NUMERIC(12,2)", False),
            ColumnDef("ps_comment", "VARCHAR(199)"),
        ],
        rows=ps_rows,
        primary_key=["ps_partkey", "ps_suppkey"],
        foreign_keys=[
            ForeignKeyDef(["ps_partkey"], "part", ["p_partkey"]),
            ForeignKeyDef(["ps_suppkey"], "supplier", ["s_suppkey"]),
        ],
    )

    start = dt.date(1992, 1, 1)
    o_rows, l_rows = [], []
    for k in range(1, n_orders + 1):
        okey = k * 4 - rng.randrange(0, 3)  # sparse keys, like dbgen
        odate = start + dt.timedelta(days=rng.randrange(0, 2400))
        total = Decimal("0")
        for ln in range(1, rng.randrange(1, 8) + 1):
            p = rng.randrange(1, n_part + 1)
            s = rng.choice(supps_of_part[p])
            qty = rng.randrange(1, 51)
            price = Decimal(qty) * part_rows[p - 1][7]
            disc = Decimal(rng.randrange(0, 11)) / 100
            tax = Decimal(rng.randrange(0, 9)) / 100
            ship = odate + dt.timedelta(days=rng.randrange(1, 122))
            l_rows.append(
                (
                    okey,
                    p,
                    s,
                    ln,
                    qty,
                    price,
                    disc,
                    tax,
                    rng.choice("ARN"),
                    "F" if ship < dt.date(1995, 6, 17) else "O",
                    ship,
                    odate + dt.timedelta(days=rng.randrange(30, 91)),
                    ship + dt.timedelta(days=rng.randrange(1, 31)),
                    rng.choice(_INSTRUCT),
                    rng.choice(_SHIPMODES),
                    _text(rng, 4, 44),
                )
            )
            total += price * (1 + tax) * (1 - disc)
        o_rows.append(
            (
                okey,
                rng.randrange(1, n_cust + 1),
                rng.choice("FOP"),
                total.quantize(Decimal("0.01")),
                odate,
                rng.choice(_PRIORITIES),
                f"Clerk#{rng.randrange(1, 1001):09d}",
                0,
                _text(rng, 6, 79),
            )
        )
    orders = TableDef(
        "orders",
        [
            ColumnDef("o_orderkey", "INTEGER", False),
            ColumnDef("o_custkey", "INTEGER", False),
            ColumnDef("o_orderstatus", "CHAR(1)", False),
            ColumnDef("o_totalprice", "NUMERIC(12,2)", False),
            ColumnDef("o_orderdate", "DATE", False),
            ColumnDef("o_orderpriority", "VARCHAR(15)", False),
            ColumnDef("o_clerk", "VARCHAR(15)", False),
            ColumnDef("o_shippriority", "INTEGER", False),
            ColumnDef("o_comment", "VARCHAR(79)"),
        ],
        rows=o_rows,
        primary_key=["o_orderkey"],
        foreign_keys=[ForeignKeyDef(["o_custkey"], "customer", ["c_custkey"])],
    )
    lineitem = TableDef(
        "lineitem",
        [
            ColumnDef("l_orderkey", "INTEGER", False),
            ColumnDef("l_partkey", "INTEGER", False),
            ColumnDef("l_suppkey", "INTEGER", False),
            ColumnDef("l_linenumber", "INTEGER", False),
            ColumnDef("l_quantity", "INTEGER", False),
            ColumnDef("l_extendedprice", "NUMERIC(12,2)", False),
            ColumnDef("l_discount", "NUMERIC(12,2)", False),
            ColumnDef("l_tax", "NUMERIC(12,2)", False),
            ColumnDef("l_returnflag", "CHAR(1)", False),
            ColumnDef("l_linestatus", "CHAR(1)", False),
            ColumnDef("l_shipdate", "DATE", False),
            ColumnDef("l_commitdate", "DATE", False),
            ColumnDef("l_receiptdate", "DATE", False),
            ColumnDef("l_shipinstruct", "VARCHAR(25)", False),
            ColumnDef("l_shipmode", "VARCHAR(10)", False),
            ColumnDef("l_comment", "VARCHAR(44)"),
        ],
        rows=l_rows,
        primary_key=["l_orderkey", "l_linenumber"],
        foreign_keys=[
            ForeignKeyDef(["l_orderkey"], "orders", ["o_orderkey"]),
            ForeignKeyDef(["l_partkey"], "part", ["p_partkey"]),
            ForeignKeyDef(["l_suppkey"], "supplier", ["s_suppkey"]),
            ForeignKeyDef(["l_partkey", "l_suppkey"], "partsupp", ["ps_partkey", "ps_suppkey"]),
        ],
    )
    return Dataset(
        name="tpch_mini",
        tables=[region, nation, supplier, customer, part, partsupp, orders, lineitem],
        description="Seeded mini generator with the TPC-H schema and declared keys.",
        licence="Generated data; schema layout follows the TPC-H specification.",
        queries=QUERIES,
        params={"seed": seed, "scale": scale},
    )
