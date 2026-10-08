"""A rehearsal school with storage shapes no earlier phase handled (Phase 9-04).

The teammates' evaluation (ADR-086) is rehearsed on a school the developer
made, so it is development data, never adaptation evidence. Its courses are
the ``other_school`` rows (same seed), stored the way another Korean
catalogue export might:

- credits as ``학점-이론-실습`` text (``3-3-0``);
- year and semester in one column (``2026-1``);
- start and end time in one column (``09:00-10:15``);
- the section glued to the course code (``SW1001-01``);
- category as English codes (``MR`` major required ...);
- Korean one-letter weekdays and a denormalised professor name.

Everything is invented and deterministic. CLI (DSN only from
``SCHEMA_ADAPTIVE_SYNTHETIC_POSTGRES_DSN``, never printed)::

    python -m backend.adaptive.fixtures.rehearsal_school create|drop|check
"""

from __future__ import annotations

import os
import sys
from typing import Any

from . import other_school

SCHEMA = "rehearsal_school"
DSN_ENV = other_school.DSN_ENV
TYPE_CODES = {"11": "MR", "12": "ME", "21": "GR", "22": "GE", "30": "FE"}
TYPE_NAMES = {"MR": "전공필수", "ME": "전공선택", "GR": "교양필수", "GE": "교양선택", "FE": "일반선택"}
WEEKDAYS = dict(zip(other_school.WEEKDAYS, "월화수목금"))
SEMESTERS = {"10": "1", "20": "2"}

TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "dept": ("dept_cd", "dept_name", "college"),
    "lecture": (
        "lecture_id", "lec_no", "title", "dept_cd", "type_cd", "credit_txt", "term",
        "prof_name", "cap", "enrolled", "grade_year",
    ),
    "lecture_time": ("lt_id", "lecture_id", "dow", "time_range", "room"),
}

_DDL = (
    "CREATE TABLE {dept} (dept_cd varchar(4) PRIMARY KEY, dept_name varchar(40) NOT NULL UNIQUE,"
    " college varchar(40) NOT NULL)",
    "CREATE TABLE {lecture} (lecture_id integer PRIMARY KEY, lec_no varchar(12) NOT NULL,"
    " title varchar(60) NOT NULL, dept_cd varchar(4) NOT NULL REFERENCES {dept} (dept_cd),"
    " type_cd char(2) NOT NULL, credit_txt varchar(10) NOT NULL, term varchar(7) NOT NULL,"
    " prof_name varchar(20) NOT NULL, cap integer NOT NULL, enrolled integer NOT NULL,"
    " grade_year integer NOT NULL)",
    "CREATE TABLE {lecture_time} (lt_id integer PRIMARY KEY,"
    " lecture_id integer NOT NULL REFERENCES {lecture} (lecture_id),"
    " dow varchar(1) NOT NULL, time_range varchar(11) NOT NULL, room varchar(20) NOT NULL)",
)


def _clock(hhmm: str) -> str:
    return f"{hhmm[:2]}:{hhmm[2:]}"


def school_rows(seed: int = other_school.SEED) -> dict[str, list[tuple[Any, ...]]]:
    """All rows, table by table, in insertion order (``other_school`` content, new shapes)."""
    source = other_school.school_rows(seed)
    professor = {number: name for number, name, _dept in source["gyosu"]}
    course = {row[0]: row for row in source["gyogwamok"]}
    rows: dict[str, list[tuple[Any, ...]]] = {
        "dept": [tuple(row) for row in source["hakgwa"]],
        "lecture": [],
        "lecture_time": [],
    }
    for ks_id, gm_cd, gs_no, year, semester, section, capacity, enrolled in source["gangjwa"]:
        _gm, name, dept, category, credits, theory, practice, grade = course[gm_cd]
        rows["lecture"].append((
            ks_id, f"{gm_cd}-{section[-2:]}", name, dept, TYPE_CODES[category],
            f"{credits}-{theory}-{practice}", f"{year}-{SEMESTERS[semester]}",
            professor[gs_no], capacity, enrolled, grade,
        ))
    for sp_id, ks_id, day, start, end, room in source["siganpyo"]:
        rows["lecture_time"].append((sp_id, ks_id, WEEKDAYS[day], f"{_clock(start)}-{_clock(end)}", room))
    return rows


def joined_rows(seed: int = other_school.SEED) -> list[dict[str, Any]]:
    """One dict per timetable row in this school's own values (for fixing answers)."""
    rows = school_rows(seed)
    department = {code: name for code, name, _college in rows["dept"]}
    lecture = {row[0]: row for row in rows["lecture"]}
    joined = []
    for _lt, lecture_id, dow, time_range, room in rows["lecture_time"]:
        (_id, lec_no, title, dept, type_cd, credit_txt, term, prof, cap, enrolled, grade) = lecture[lecture_id]
        start, end = time_range.split("-")
        joined.append({
            "lec_no": lec_no, "title": title, "department": department[dept], "type_cd": type_cd,
            "credits": int(credit_txt.split("-")[0]), "term": term, "professor": prof, "capacity": cap,
            "enrolled": enrolled, "grade": grade, "dow": dow, "start": start, "end": end, "room": room,
        })
    return joined


# --- PostgreSQL (only the CLI connects) -----------------------------------------------


def _drop_statements(sql: Any) -> list[tuple[Any, tuple[Any, ...]]]:
    """Own tables, children first, then the schema; never CASCADE."""
    tables = [
        (sql.SQL("DROP TABLE IF EXISTS {}").format(sql.SQL(".").join([sql.Identifier(SCHEMA), sql.Identifier(table)])), ())
        for table in reversed(TABLE_COLUMNS)
    ]
    return [*tables, (sql.SQL("DROP SCHEMA IF EXISTS {}").format(sql.Identifier(SCHEMA)), ())]


def _statements(sql: Any) -> list[tuple[Any, tuple[Any, ...]]]:
    names = {table: sql.SQL(".").join([sql.Identifier(SCHEMA), sql.Identifier(table)]) for table in TABLE_COLUMNS}
    operations: list[tuple[Any, tuple[Any, ...]]] = [
        *_drop_statements(sql),
        (sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(SCHEMA)), ()),
    ]
    operations += [(sql.SQL(text).format(**names), ()) for text in _DDL]
    for table, table_rows in school_rows().items():
        statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            names[table],
            sql.SQL(", ").join(sql.Identifier(column) for column in TABLE_COLUMNS[table]),
            sql.SQL(", ").join(sql.Placeholder() for _ in TABLE_COLUMNS[table]),
        )
        operations += [(statement, tuple(row)) for row in table_rows]
    return operations


def main(argv: list[str] | None = None, environ: dict[str, str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in ("create", "drop", "check"):
        print("usage: python -m backend.adaptive.fixtures.rehearsal_school create|drop|check")
        return 2
    try:
        connection = other_school._connect(dict(os.environ if environ is None else environ))
    except Exception:
        print("rehearsal_school: connection refused (set a loopback schema_adaptive_synthetic DSN)")
        return 1
    try:
        from psycopg2 import sql

        with connection.cursor() as cursor:
            if args[0] == "create":
                for statement, params in _statements(sql):
                    cursor.execute(statement, params)
            elif args[0] == "drop":
                for statement, params in _drop_statements(sql):
                    cursor.execute(statement, params)
            else:
                expected = {table: len(rows) for table, rows in school_rows().items()}
                for table, count in expected.items():
                    cursor.execute(sql.SQL("SELECT count(*) FROM {}").format(
                        sql.SQL(".").join([sql.Identifier(SCHEMA), sql.Identifier(table)])))
                    found = cursor.fetchone()[0]
                    print(f"{table}: {found} rows" + ("" if found == count else f" (expected {count})"))
                    if found != count:
                        return 1
        connection.commit()
    except Exception:
        connection.rollback()
        print(f"rehearsal_school: {args[0]} failed")
        return 1
    finally:
        connection.close()
    print(f"rehearsal_school: {args[0]} done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
