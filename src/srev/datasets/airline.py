"""Airline operations schema with self-references and parallel foreign keys.

Covers relationship shapes the other generators lack: a self-referencing FK
(``crew.supervisor_id -> crew.crew_id``), two FKs from one table into the
same parent (``route.origin_id`` and ``route.dest_id`` into ``airport``), a
composite key on a junction table, and a curated semantic FD
(``airport.city -> country``).
"""

from __future__ import annotations

import datetime as dt
import random

from srev.datasets.base import ColumnDef, CuratedFD, Dataset, ForeignKeyDef, TableDef

_AIRPORTS = [
    ("BER", "Berlin", "Germany"), ("MUC", "Munich", "Germany"), ("HAM", "Hamburg", "Germany"),
    ("FRA", "Frankfurt", "Germany"), ("CDG", "Paris", "France"), ("ORY", "Paris", "France"),
    ("LHR", "London", "United Kingdom"), ("LGW", "London", "United Kingdom"),
    ("AMS", "Amsterdam", "Netherlands"), ("MAD", "Madrid", "Spain"), ("BCN", "Barcelona", "Spain"),
    ("FCO", "Rome", "Italy"), ("VIE", "Vienna", "Austria"), ("ZRH", "Zurich", "Switzerland"),
    ("CPH", "Copenhagen", "Denmark"), ("WAW", "Warsaw", "Poland"),
]  # fmt: skip
_MODELS = [("A320", 180), ("A321", 220), ("B737", 189), ("E190", 100), ("A350", 300)]
_RANKS = ["Captain", "First Officer", "Purser", "Flight Attendant"]
_FIRST = ["Lena", "Jonas", "Mia", "Paul", "Emma", "Felix", "Lea", "Noah", "Hanna", "Ben"]
_LAST = ["Becker", "Wagner", "Schulz", "Hoffmann", "Koch", "Richter", "Klein", "Wolf"]

QUERIES = [
    """SELECT a.city, b.city, r.distance_km FROM route r
       JOIN airport a ON r.origin_id = a.airport_id
       JOIN airport b ON r.dest_id = b.airport_id""",
    """SELECT f.flight_date, ac.model FROM flight f JOIN aircraft ac
       ON f.aircraft_id = ac.aircraft_id""",
    """SELECT f.flight_id, r.distance_km FROM flight f, route r WHERE f.route_id = r.route_id""",
    """SELECT c.name, s.name AS supervisor FROM crew c LEFT JOIN crew s
       ON c.supervisor_id = s.crew_id""",
    """SELECT fc.role, c.name FROM flight_crew fc JOIN crew c ON fc.crew_id = c.crew_id
       JOIN flight f ON fc.flight_id = f.flight_id""",
]


def generate(seed: int = 42, scale: float = 1.0) -> Dataset:
    rng = random.Random(seed)
    n_routes = max(10, int(60 * scale))
    n_flights = max(20, int(400 * scale))
    n_crew = max(12, int(80 * scale))

    airport = TableDef(
        "airport",
        [ColumnDef("airport_id", "INTEGER", False), ColumnDef("iata", "CHAR(3)", False),
         ColumnDef("city", "VARCHAR(30)"), ColumnDef("country", "VARCHAR(30)")],
        rows=[(i + 1, code, city, country) for i, (code, city, country) in enumerate(_AIRPORTS)],
        primary_key=["airport_id"],
        uniques=[["iata"]],
    )  # fmt: skip
    aircraft = TableDef(
        "aircraft",
        [ColumnDef("aircraft_id", "INTEGER", False), ColumnDef("registration", "VARCHAR(8)"),
         ColumnDef("model", "VARCHAR(10)"), ColumnDef("seats", "INTEGER")],
        rows=[(i + 1, f"D-A{chr(65 + i // 26)}{chr(65 + i % 26)}{rng.randrange(10)}",
               *rng.choice(_MODELS)) for i in range(max(6, int(25 * scale)))],
        primary_key=["aircraft_id"],
    )  # fmt: skip
    route_rows: list[tuple[int, int, int, int]] = []
    seen: set[tuple[int, int]] = set()
    while len(route_rows) < n_routes:
        o, d = rng.sample(range(1, len(_AIRPORTS) + 1), 2)
        if (o, d) in seen:
            continue
        seen.add((o, d))
        route_rows.append((500 + len(route_rows), o, d, rng.randrange(250, 2500)))
    route = TableDef(
        "route",
        [ColumnDef("route_id", "INTEGER", False), ColumnDef("origin_id", "INTEGER"),
         ColumnDef("dest_id", "INTEGER"), ColumnDef("distance_km", "INTEGER")],
        rows=route_rows,
        primary_key=["route_id"],
        uniques=[["origin_id", "dest_id"]],
        foreign_keys=[ForeignKeyDef(["origin_id"], "airport", ["airport_id"]),
                      ForeignKeyDef(["dest_id"], "airport", ["airport_id"])],
    )  # fmt: skip
    start = dt.date(2025, 1, 1)
    flight = TableDef(
        "flight",
        [ColumnDef("flight_id", "INTEGER", False), ColumnDef("route_id", "INTEGER"),
         ColumnDef("aircraft_id", "INTEGER"), ColumnDef("flight_date", "DATE"),
         ColumnDef("departure", "VARCHAR(5)")],
        rows=[(70000 + i, rng.choice(route_rows)[0], rng.randrange(1, len(aircraft.rows) + 1),
               start + dt.timedelta(days=rng.randrange(365)),
               f"{rng.randrange(5, 23):02d}:{rng.choice(['00', '15', '30', '45'])}")
              for i in range(n_flights)],
        primary_key=["flight_id"],
        foreign_keys=[ForeignKeyDef(["route_id"], "route", ["route_id"]),
                      ForeignKeyDef(["aircraft_id"], "aircraft", ["aircraft_id"])],
    )  # fmt: skip
    crew_rows: list[tuple[int, str, str, int | None]] = []
    for i in range(n_crew):
        rank = _RANKS[0] if i < max(3, n_crew // 10) else rng.choice(_RANKS[1:])
        supervisor = None if rank == _RANKS[0] else 9000 + rng.randrange(max(3, n_crew // 10))
        crew_rows.append((9000 + i, f"{rng.choice(_FIRST)} {rng.choice(_LAST)}", rank, supervisor))
    crew = TableDef(
        "crew",
        [ColumnDef("crew_id", "INTEGER", False), ColumnDef("name", "VARCHAR(40)"),
         ColumnDef("rank", "VARCHAR(20)"), ColumnDef("supervisor_id", "INTEGER")],
        rows=crew_rows,
        primary_key=["crew_id"],
        foreign_keys=[ForeignKeyDef(["supervisor_id"], "crew", ["crew_id"])],
    )  # fmt: skip
    fc_rows = set()
    for f in flight.rows:
        for c in rng.sample(crew_rows, min(len(crew_rows), rng.randrange(2, 5))):
            fc_rows.add((f[0], c[0], c[2]))
    flight_crew = TableDef(
        "flight_crew",
        [ColumnDef("flight_id", "INTEGER", False), ColumnDef("crew_id", "INTEGER", False),
         ColumnDef("role", "VARCHAR(20)")],
        rows=sorted(fc_rows),
        primary_key=["flight_id", "crew_id"],
        foreign_keys=[ForeignKeyDef(["flight_id"], "flight", ["flight_id"]),
                      ForeignKeyDef(["crew_id"], "crew", ["crew_id"])],
    )  # fmt: skip
    return Dataset(
        name="airline",
        tables=[airport, aircraft, route, flight, crew, flight_crew],
        description="Airline operations with a self-referencing FK and parallel FKs.",
        licence="Generated data (MIT).",
        curated_fds=[CuratedFD("airport", ["city"], "country", "a city lies in one country")],
        queries=QUERIES,
        params={"seed": seed, "scale": scale},
    )
