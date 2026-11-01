"""Museum collection with an ownership-provenance chain (cultural heritage).

Shaped like a typical collection-management export: artists, objects,
owners (people and institutions), a chain of provenance events per object in
which every event points to the previous one (a self-referencing FK), and
exhibitions with a many-to-many loan table. Denormalised columns provide
curated semantic FDs (``object.artist_id -> artist_name``,
``owner.city -> country``). All names and records are synthetic.
"""

from __future__ import annotations

import datetime as dt
import random

from srev.datasets.base import ColumnDef, CuratedFD, Dataset, ForeignKeyDef, TableDef

_FIRST = ["Anna", "Karl", "Marie", "Otto", "Clara", "Paul", "Ida", "Hugo", "Lotte", "Max"]
_LAST = ["Albers", "Brandt", "Corinth", "Dix", "Ernst", "Feininger", "Grosz", "Hoch"]
_PLACES = [
    ("Berlin", "Germany"), ("Munich", "Germany"), ("Dresden", "Germany"),
    ("Vienna", "Austria"), ("Paris", "France"), ("Lyon", "France"),
    ("Amsterdam", "Netherlands"), ("Zurich", "Switzerland"),
    ("New York", "United States"), ("London", "United Kingdom"),
]  # fmt: skip
_MEDIA = ["oil on canvas", "watercolour", "etching", "bronze", "charcoal on paper", "tempera"]
_EVENTS = ["purchase", "gift", "bequest", "auction", "confiscation", "restitution", "loan"]

QUERIES = [
    """SELECT o.title, a.name FROM object o JOIN artist a ON o.artist_id = a.artist_id""",
    """SELECT o.title, p.event_type, w.name FROM provenance_event p
       JOIN object o ON p.object_id = o.object_id
       JOIN owner w ON p.owner_id = w.owner_id ORDER BY p.object_id, p.start_year""",
    """SELECT p.event_id, prev.owner_id FROM provenance_event p
       LEFT JOIN provenance_event prev ON p.previous_event_id = prev.event_id""",
    """SELECT e.title, o.title FROM loan l JOIN exhibition e ON l.exhibition_id = e.exhibition_id
       JOIN object o ON l.object_id = o.object_id""",
    """SELECT w.city, COUNT(*) FROM provenance_event p JOIN owner w ON p.owner_id = w.owner_id
       WHERE p.event_type = 'confiscation' GROUP BY w.city""",
]


def generate(seed: int = 42, scale: float = 1.0) -> Dataset:
    rng = random.Random(seed)
    n_artists = max(8, int(40 * scale))
    n_objects = max(20, int(300 * scale))
    n_owners = max(10, int(120 * scale))
    n_exhibitions = max(4, int(25 * scale))

    artist_rows = [
        (3000 + i, f"{rng.choice(_FIRST)} {rng.choice(_LAST)}", rng.randrange(1840, 1930),
         rng.choice(_PLACES)[1])
        for i in range(n_artists)
    ]  # fmt: skip
    artist = TableDef(
        "artist",
        [ColumnDef("artist_id", "INTEGER", False), ColumnDef("name", "VARCHAR(60)"),
         ColumnDef("birth_year", "INTEGER"), ColumnDef("nationality", "VARCHAR(30)")],
        rows=artist_rows,
        primary_key=["artist_id"],
    )  # fmt: skip
    obj_rows = []
    for i in range(n_objects):
        a = rng.choice(artist_rows)
        obj_rows.append(
            (f"INV-{i + 1:05d}", f"Untitled {i + 1}" if rng.random() < 0.2 else
             f"{rng.choice(['Portrait', 'Landscape', 'Still life', 'Study'])} {i + 1}",
             a[0], a[1], a[2] + rng.randrange(18, 60), rng.choice(_MEDIA))
        )  # fmt: skip
    obj = TableDef(
        "object",
        [ColumnDef("object_id", "VARCHAR(10)", False), ColumnDef("title", "VARCHAR(80)"),
         ColumnDef("artist_id", "INTEGER"), ColumnDef("artist_name", "VARCHAR(60)"),
         ColumnDef("creation_year", "INTEGER"), ColumnDef("medium", "VARCHAR(30)")],
        rows=obj_rows,
        primary_key=["object_id"],
        foreign_keys=[ForeignKeyDef(["artist_id"], "artist", ["artist_id"])],
    )  # fmt: skip
    owner_rows = []
    for i in range(n_owners):
        city, country = rng.choice(_PLACES)
        kind = "institution" if rng.random() < 0.3 else "person"
        name = (f"{city} Kunstverein {i}" if kind == "institution"
                else f"{rng.choice(_FIRST)} {rng.choice(_LAST)}")  # fmt: skip
        owner_rows.append((700 + i, name, kind, city, country))
    owner = TableDef(
        "owner",
        [ColumnDef("owner_id", "INTEGER", False), ColumnDef("name", "VARCHAR(60)"),
         ColumnDef("owner_type", "VARCHAR(12)"), ColumnDef("city", "VARCHAR(30)"),
         ColumnDef("country", "VARCHAR(30)")],
        rows=owner_rows,
        primary_key=["owner_id"],
    )  # fmt: skip
    event_rows: list[tuple[int, str, int, str, int, int | None]] = []
    eid = 1
    for o in obj_rows:
        year = o[4]
        prev: int | None = None
        for _ in range(rng.randrange(1, 6)):
            year += rng.randrange(1, 25)
            event_rows.append(
                (eid, o[0], rng.choice(owner_rows)[0], rng.choice(_EVENTS), year, prev)
            )
            prev = eid
            eid += 1
    provenance = TableDef(
        "provenance_event",
        [ColumnDef("event_id", "INTEGER", False), ColumnDef("object_id", "VARCHAR(10)"),
         ColumnDef("owner_id", "INTEGER"), ColumnDef("event_type", "VARCHAR(15)"),
         ColumnDef("start_year", "INTEGER"), ColumnDef("previous_event_id", "INTEGER")],
        rows=event_rows,
        primary_key=["event_id"],
        foreign_keys=[ForeignKeyDef(["object_id"], "object", ["object_id"]),
                      ForeignKeyDef(["owner_id"], "owner", ["owner_id"]),
                      ForeignKeyDef(["previous_event_id"], "provenance_event", ["event_id"])],
    )  # fmt: skip
    start = dt.date(1995, 1, 1)
    exhibition = TableDef(
        "exhibition",
        [ColumnDef("exhibition_id", "INTEGER", False), ColumnDef("title", "VARCHAR(80)"),
         ColumnDef("opening", "DATE"), ColumnDef("venue", "VARCHAR(30)")],
        rows=[(90 + i, f"Exhibition {i + 1}", start + dt.timedelta(days=rng.randrange(10000)),
               rng.choice(_PLACES)[0]) for i in range(n_exhibitions)],
        primary_key=["exhibition_id"],
    )  # fmt: skip
    loans = sorted(
        {
            (e[0], rng.choice(obj_rows)[0])
            for e in exhibition.rows
            for _ in range(rng.randrange(3, 15))
        }
    )
    loan = TableDef(
        "loan",
        [ColumnDef("exhibition_id", "INTEGER", False),
         ColumnDef("object_id", "VARCHAR(10)", False)],
        rows=loans,
        primary_key=["exhibition_id", "object_id"],
        foreign_keys=[ForeignKeyDef(["exhibition_id"], "exhibition", ["exhibition_id"]),
                      ForeignKeyDef(["object_id"], "object", ["object_id"])],
    )  # fmt: skip
    return Dataset(
        name="museum",
        tables=[artist, obj, owner, provenance, exhibition, loan],
        description="Museum collection with a self-referencing provenance chain.",
        licence="Generated data (MIT).",
        curated_fds=[
            CuratedFD("object", ["artist_id"], "artist_name", "denormalised artist name"),
            CuratedFD("owner", ["city"], "country", "a city lies in one country"),
        ],
        queries=QUERIES,
        params={"seed": seed, "scale": scale},
    )
