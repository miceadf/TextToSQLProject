"""Synthetic PostgreSQL fixture (Phase 7-03, fake-verified scope).

No live PostgreSQL server, CNU data, or Ollama call is used here.
All identifiers are composed with ``psycopg2.sql.Identifier`` and all
values use ``%s`` binding. Schema names are never interpolated.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable
from typing import Any

from .seed import DEFAULT_SEED
from .contracts import FixtureSeed

SYNTHETIC_DSN_ENV = "SCHEMA_ADAPTIVE_SYNTHETIC_POSTGRES_DSN"
SYNTHETIC_DATABASE = "schema_adaptive_synthetic"
SCHEMA_PREFIX = "schema_adaptive_smoke_"
SCHEMA_PATTERN = re.compile(r"^schema_adaptive_smoke_[0-9a-f]{16}$")

EXPECTED_TABLES = (
    "course_offerings",
    "courses",
    "departments",
    "instructors",
    "schedules",
)

EXPECTED_PRIMARY_KEYS = {
    "departments": ["department_code"],
    "instructors": ["instructor_id"],
    "courses": ["course_code"],
    "course_offerings": ["offering_id"],
    "schedules": ["schedule_id"],
}
EXPECTED_FOREIGN_KEYS = {
    "departments": set(),
    "instructors": set(),
    "courses": {("department_code", "departments", "department_code")},
    "course_offerings": {
        ("course_code", "courses", "course_code"),
        ("instructor_id", "instructors", "instructor_id"),
    },
    "schedules": {("offering_id", "course_offerings", "offering_id")},
}

_FIXED_FIXTURE_MESSAGE = "synthetic PostgreSQL fixture failed"


class SyntheticFixtureError(Exception):
    """Fixed-message fixture failure (no DSN/SQL/schema details)."""

    def __init__(self) -> None:
        super().__init__(_FIXED_FIXTURE_MESSAGE)


def validate_schema_name(name: object) -> bool:
    return isinstance(name, str) and SCHEMA_PATTERN.fullmatch(name) is not None


def generate_schema_name(
    token_hex: Callable[[int], str] | None = None,
) -> str:
    generator = token_hex or secrets.token_hex
    try:
        suffix = generator(8)
    except Exception:
        raise SyntheticFixtureError from None
    name = f"{SCHEMA_PREFIX}{suffix}"
    if not validate_schema_name(name):
        raise SyntheticFixtureError
    return name


def _require_sql(sql_module: Any | None) -> Any:
    if sql_module is not None:
        return sql_module
    try:
        from psycopg2 import sql as psycopg_sql  # type: ignore[import-not-found]
    except Exception:
        raise SyntheticFixtureError from None
    return psycopg_sql


def synthetic_table_rows(seed: FixtureSeed = DEFAULT_SEED) -> dict[str, list[tuple[Any, ...]]]:
    departments = sorted(
        {
            (offering.department_code, offering.department_name)
            for offering in seed.offerings
        }
    )
    courses = sorted(
        {
            (
                offering.course_code,
                offering.course_name,
                offering.credits,
                offering.category,
                offering.department_code,
            )
            for offering in seed.offerings
        }
    )
    instructor_names = sorted({offering.instructor_name for offering in seed.offerings})
    instructor_ids = {
        name: instructor_id for instructor_id, name in enumerate(instructor_names, start=1)
    }
    offerings = sorted(seed.offerings, key=lambda item: item.offering_key)
    offering_rows = [
        (
            offering_id,
            offering.course_code,
            instructor_ids[offering.instructor_name],
            offering.year,
            offering.semester,
            offering.section,
            offering.capacity,
            offering.enrolled,
        )
        for offering_id, offering in enumerate(offerings, start=1)
    ]
    schedule_rows = [
        (offering_id, offering_id, offering.day, offering.start_time, offering.end_time)
        for offering_id, offering in enumerate(offerings, start=1)
    ]
    instructor_rows = [(instructor_ids[name], name) for name in instructor_names]
    return {
        "departments": departments,
        "instructors": instructor_rows,
        "courses": courses,
        "course_offerings": offering_rows,
        "schedules": schedule_rows,
    }


def _qualified(sql: Any, schema: str, table: str) -> Any:
    return sql.SQL(".").join([sql.Identifier(schema), sql.Identifier(table)])


def ddl_operations(schema: str, sql: Any) -> list[Any]:
    qualified = {table: _qualified(sql, schema, table) for table in EXPECTED_TABLES}
    return [
        sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)),
        sql.SQL(
            "CREATE TABLE {} ("
            "department_code TEXT PRIMARY KEY, "
            "department_name TEXT NOT NULL UNIQUE)"
        ).format(qualified["departments"]),
        sql.SQL(
            "CREATE TABLE {} ("
            "instructor_id INTEGER PRIMARY KEY, "
            "instructor_name TEXT NOT NULL UNIQUE)"
        ).format(qualified["instructors"]),
        sql.SQL(
            "CREATE TABLE {} ("
            "course_code TEXT PRIMARY KEY, "
            "course_name TEXT NOT NULL, "
            "credits NUMERIC NOT NULL CHECK (credits > 0), "
            "category TEXT NOT NULL, "
            "department_code TEXT NOT NULL REFERENCES {} (department_code))"
        ).format(qualified["courses"], qualified["departments"]),
        sql.SQL(
            "CREATE TABLE {} ("
            "offering_id INTEGER PRIMARY KEY, "
            "course_code TEXT NOT NULL REFERENCES {} (course_code), "
            "instructor_id INTEGER NOT NULL REFERENCES {} (instructor_id), "
            "academic_year INTEGER NOT NULL, "
            "semester TEXT NOT NULL, "
            "section TEXT NOT NULL, "
            "capacity INTEGER NOT NULL CHECK (capacity > 0), "
            "enrolled INTEGER NOT NULL CHECK (enrolled >= 0 AND enrolled <= capacity), "
            "UNIQUE (academic_year, semester, course_code, section))"
        ).format(
            qualified["course_offerings"],
            qualified["courses"],
            qualified["instructors"],
        ),
        sql.SQL(
            "CREATE TABLE {} ("
            "schedule_id INTEGER PRIMARY KEY, "
            "offering_id INTEGER NOT NULL UNIQUE "
            "REFERENCES {} (offering_id), "
            "day_of_week TEXT NOT NULL, "
            "start_time TEXT NOT NULL, "
            "end_time TEXT NOT NULL, "
            "CHECK (start_time < end_time))"
        ).format(qualified["schedules"], qualified["course_offerings"]),
    ]


def _insert_statement(sql: Any, schema: str, table: str, columns: list[str]) -> Any:
    qualified = _qualified(sql, schema, table)
    identifiers = sql.SQL(", ").join(sql.Identifier(column) for column in columns)
    placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in columns)
    return sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        qualified, identifiers, placeholders
    )


def insert_operations(
    schema: str, sql: Any, seed: FixtureSeed = DEFAULT_SEED
) -> list[tuple[Any, tuple[Any, ...]]]:
    rows = synthetic_table_rows(seed)
    specs: list[tuple[str, list[str], list[tuple[Any, ...]]]] = [
        ("departments", ["department_code", "department_name"], rows["departments"]),
        ("instructors", ["instructor_id", "instructor_name"], rows["instructors"]),
        (
            "courses",
            ["course_code", "course_name", "credits", "category", "department_code"],
            rows["courses"],
        ),
        (
            "course_offerings",
            [
                "offering_id",
                "course_code",
                "instructor_id",
                "academic_year",
                "semester",
                "section",
                "capacity",
                "enrolled",
            ],
            rows["course_offerings"],
        ),
        (
            "schedules",
            ["schedule_id", "offering_id", "day_of_week", "start_time", "end_time"],
            rows["schedules"],
        ),
    ]
    operations: list[tuple[Any, tuple[Any, ...]]] = []
    for table, columns, table_rows in specs:
        statement = _insert_statement(sql, schema, table, columns)
        for row in table_rows:
            operations.append((statement, tuple(row)))
    return operations


def setup_synthetic_schema(
    connection: Any,
    schema: str,
    *,
    seed: FixtureSeed = DEFAULT_SEED,
    sql_module: Any | None = None,
) -> None:
    if not validate_schema_name(schema):
        raise SyntheticFixtureError
    sql = _require_sql(sql_module)
    cursor: Any | None = None
    try:
        try:
            cursor = connection.cursor()
        except Exception:
            raise SyntheticFixtureError from None
        try:
            for statement in ddl_operations(schema, sql):
                cursor.execute(statement, ())
            for statement, params in insert_operations(schema, sql, seed):
                cursor.execute(statement, params)
            connection.commit()
        except SyntheticFixtureError:
            try:
                connection.rollback()
            except Exception:
                pass
            raise
        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass
            raise SyntheticFixtureError from None
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass


def cleanup_synthetic_schema(
    connection: Any,
    schema: str,
    *,
    sql_module: Any | None = None,
) -> None:
    if not validate_schema_name(schema):
        raise SyntheticFixtureError
    sql = _require_sql(sql_module)
    cursor: Any | None = None
    try:
        try:
            cursor = connection.cursor()
        except Exception:
            raise SyntheticFixtureError from None
        try:
            cursor.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema)
                ),
                (),
            )
            connection.commit()
        except SyntheticFixtureError:
            raise
        except Exception:
            raise SyntheticFixtureError from None
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
