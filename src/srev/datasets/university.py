"""University schema with deliberate denormalisation.

Declared PK/FK/UNIQUE constraints give FK and UCC gold labels; the
denormalised columns (``course.dept_name``, ``course.building``,
``student.city``) give curated *semantic* FD gold labels. ``course_prereq``
contains a self-referencing relationship (two FKs into ``course``).
"""

from __future__ import annotations

import random

from srev.datasets.base import ColumnDef, CuratedFD, Dataset, ForeignKeyDef, TableDef

_DEPTS = [
    ("Computer Science", "Taylor"),
    ("Biology", "Watson"),
    ("Physics", "Newton"),
    ("History", "Painter"),
    ("Finance", "Painter"),
    ("Music", "Packard"),
    ("Mathematics", "Gauss"),
    ("Chemistry", "Curie"),
]
_FIRST = [
    "Ada",
    "Alan",
    "Grace",
    "Linus",
    "Barbara",
    "Edsger",
    "Donald",
    "Frances",
    "Ken",
    "Margaret",
    "Tim",
    "Radia",
]
_LAST = [
    "Lovelace",
    "Turing",
    "Hopper",
    "Torvalds",
    "Liskov",
    "Dijkstra",
    "Knuth",
    "Allen",
    "Thompson",
    "Hamilton",
]
_CITIES = [
    ("10115", "Berlin"),
    ("20095", "Hamburg"),
    ("80331", "Munich"),
    ("50667", "Cologne"),
    ("60311", "Frankfurt"),
    ("70173", "Stuttgart"),
    ("40213", "Dusseldorf"),
    ("04109", "Leipzig"),
    ("01067", "Dresden"),
    ("30159", "Hanover"),
    ("90402", "Nuremberg"),
    ("28195", "Bremen"),
]
_TOPICS = [
    "Databases",
    "Algorithms",
    "Genetics",
    "Optics",
    "Antiquity",
    "Markets",
    "Harmony",
    "Topology",
    "Polymers",
]
_LEVELS = ["Intro to", "Advanced", "Seminar in", "Topics in"]
_SEMESTERS = ["Spring", "Fall"]
_GRADES = ["A", "A-", "B+", "B", "B-", "C+", "C", "F"]

QUERIES = [
    """SELECT s.name, c.title FROM student s
       JOIN takes t ON t.student_id = s.student_id
       JOIN section sec ON sec.section_id = t.section_id
       JOIN course c ON c.course_id = sec.course_id""",
    """SELECT i.name, d.dept_name FROM instructor i JOIN department d ON i.dept_id = d.dept_id""",
    """SELECT s.name, i.name FROM advisor a, student s, instructor i
       WHERE a.student_id = s.student_id AND a.instructor_id = i.instructor_id""",
    """SELECT c.title, p.title FROM course_prereq cp
       JOIN course c ON cp.course_id = c.course_id
       JOIN course p ON cp.prereq_id = p.course_id""",
    """SELECT d.dept_name, COUNT(*) FROM student s JOIN department d ON s.dept_id = d.dept_id
       GROUP BY d.dept_name""",
    """SELECT sec.year, i.name FROM section sec JOIN instructor i
       ON sec.instructor_id = i.instructor_id WHERE sec.semester = 'Fall'""",
    """SELECT c.title FROM course c JOIN department d ON c.dept_id = d.dept_id
       WHERE d.budget > 50000""",
]


def generate(seed: int = 42, scale: float = 1.0) -> Dataset:
    rng = random.Random(seed)
    n_instr = max(8, int(40 * scale))
    n_students = max(20, int(300 * scale))
    n_courses = max(10, int(60 * scale))

    def person() -> str:
        return f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"

    dept_rows = [
        (i + 1, name, bld, rng.randrange(40, 120) * 1000) for i, (name, bld) in enumerate(_DEPTS)
    ]
    department = TableDef(
        "department",
        [
            ColumnDef("dept_id", "INTEGER", False),
            ColumnDef("dept_name", "VARCHAR(40)", False),
            ColumnDef("building", "VARCHAR(20)"),
            ColumnDef("budget", "INTEGER"),
        ],
        rows=dept_rows,
        primary_key=["dept_id"],
        uniques=[["dept_name"]],
    )

    instructor = TableDef(
        "instructor",
        [
            ColumnDef("instructor_id", "INTEGER", False),
            ColumnDef("name", "VARCHAR(40)", False),
            ColumnDef("dept_id", "INTEGER"),
            ColumnDef("salary", "INTEGER"),
            ColumnDef("email", "VARCHAR(60)"),
        ],
        rows=[
            (
                100 + i,
                (nm := person()),
                rng.randrange(1, len(_DEPTS) + 1),
                rng.randrange(45, 130) * 1000,
                f"{nm.split()[1].lower()}.{100 + i}@uni.example",
            )
            for i in range(n_instr)
        ],
        primary_key=["instructor_id"],
        uniques=[["email"]],
        foreign_keys=[ForeignKeyDef(["dept_id"], "department", ["dept_id"])],
    )

    student_rows = []
    for i in range(n_students):
        zip_code, city = rng.choice(_CITIES)
        student_rows.append(
            (
                10000 + i,
                person(),
                rng.randrange(1, len(_DEPTS) + 1),
                zip_code,
                city,
                rng.randrange(0, 130),
            )
        )
    student = TableDef(
        "student",
        [
            ColumnDef("student_id", "INTEGER", False),
            ColumnDef("name", "VARCHAR(40)", False),
            ColumnDef("dept_id", "INTEGER"),
            ColumnDef("zip_code", "VARCHAR(5)"),
            ColumnDef("city", "VARCHAR(30)"),
            ColumnDef("tot_cred", "INTEGER"),
        ],
        rows=student_rows,
        primary_key=["student_id"],
        foreign_keys=[ForeignKeyDef(["dept_id"], "department", ["dept_id"])],
    )

    course_rows = []
    for i in range(n_courses):
        d = dept_rows[rng.randrange(len(dept_rows))]
        course_rows.append(
            (
                f"C{i + 101:04d}",
                f"{rng.choice(_LEVELS)} {rng.choice(_TOPICS)} {i}",
                d[0],
                d[1],
                d[2],
                rng.choice([3, 4, 5, 6]),
            )
        )
    course = TableDef(
        "course",
        [
            ColumnDef("course_id", "VARCHAR(8)", False),
            ColumnDef("title", "VARCHAR(60)"),
            ColumnDef("dept_id", "INTEGER"),
            ColumnDef("dept_name", "VARCHAR(40)"),
            ColumnDef("building", "VARCHAR(20)"),
            ColumnDef("credits", "INTEGER"),
        ],
        rows=course_rows,
        primary_key=["course_id"],
        foreign_keys=[ForeignKeyDef(["dept_id"], "department", ["dept_id"])],
    )

    prereq_rows = set()
    for _ in range(n_courses):
        a, b = rng.sample(range(n_courses), 2)
        prereq_rows.add((course_rows[a][0], course_rows[b][0]))
    course_prereq = TableDef(
        "course_prereq",
        [ColumnDef("course_id", "VARCHAR(8)", False), ColumnDef("prereq_id", "VARCHAR(8)", False)],
        rows=sorted(prereq_rows),
        primary_key=["course_id", "prereq_id"],
        foreign_keys=[
            ForeignKeyDef(["course_id"], "course", ["course_id"]),
            ForeignKeyDef(["prereq_id"], "course", ["course_id"]),
        ],
    )

    section_rows = []
    seen = set()
    sid = 1
    for c in course_rows:
        for year in (2023, 2024, 2025):
            for sem in _SEMESTERS:
                if rng.random() < 0.45:
                    continue
                for sec_no in range(1, rng.randrange(1, 3) + 1):
                    key = (c[0], sem, year, sec_no)
                    if key in seen:
                        continue
                    seen.add(key)
                    section_rows.append(
                        (
                            sid,
                            c[0],
                            sec_no,
                            sem,
                            year,
                            rng.randrange(100, 100 + n_instr),
                            f"R{rng.randrange(100, 400)}",
                        )
                    )
                    sid += 1
    section = TableDef(
        "section",
        [
            ColumnDef("section_id", "INTEGER", False),
            ColumnDef("course_id", "VARCHAR(8)", False),
            ColumnDef("sec_no", "INTEGER", False),
            ColumnDef("semester", "VARCHAR(6)", False),
            ColumnDef("year", "INTEGER", False),
            ColumnDef("instructor_id", "INTEGER"),
            ColumnDef("room", "VARCHAR(8)"),
        ],
        rows=section_rows,
        primary_key=["section_id"],
        uniques=[["course_id", "sec_no", "semester", "year"]],
        foreign_keys=[
            ForeignKeyDef(["course_id"], "course", ["course_id"]),
            ForeignKeyDef(["instructor_id"], "instructor", ["instructor_id"]),
        ],
    )

    takes_rows = set()
    for s in student_rows:
        for sec in rng.sample(section_rows, min(len(section_rows), rng.randrange(1, 6))):
            takes_rows.add((s[0], sec[0], rng.choice(_GRADES)))
    takes = TableDef(
        "takes",
        [
            ColumnDef("student_id", "INTEGER", False),
            ColumnDef("section_id", "INTEGER", False),
            ColumnDef("grade", "VARCHAR(2)"),
        ],
        rows=sorted(takes_rows),
        primary_key=["student_id", "section_id"],
        foreign_keys=[
            ForeignKeyDef(["student_id"], "student", ["student_id"]),
            ForeignKeyDef(["section_id"], "section", ["section_id"]),
        ],
    )

    advisor = TableDef(
        "advisor",
        [ColumnDef("student_id", "INTEGER", False), ColumnDef("instructor_id", "INTEGER")],
        rows=[
            (s[0], rng.randrange(100, 100 + n_instr)) for s in student_rows if rng.random() < 0.7
        ],
        primary_key=["student_id"],
        foreign_keys=[
            ForeignKeyDef(["student_id"], "student", ["student_id"]),
            ForeignKeyDef(["instructor_id"], "instructor", ["instructor_id"]),
        ],
    )

    return Dataset(
        name="university",
        tables=[department, instructor, student, course, course_prereq, section, takes, advisor],
        description="University schema with declared keys and denormalised department data.",
        licence="Generated data (MIT).",
        curated_fds=[
            CuratedFD("course", ["dept_id"], "dept_name", "denormalised department name"),
            CuratedFD("course", ["dept_id"], "building", "denormalised department building"),
            CuratedFD("course", ["dept_name"], "building", "department name is unique"),
            CuratedFD("student", ["zip_code"], "city", "postal code determines city"),
        ],
        queries=QUERIES,
        params={"seed": seed, "scale": scale},
    )
