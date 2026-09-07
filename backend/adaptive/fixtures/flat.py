from __future__ import annotations

import sqlite3
from pathlib import Path

from .common import build_atomic_database
from .contracts import FixtureSeed
from .seed import DEFAULT_SEED


def build_flat_database(
    path: str | Path,
    seed: FixtureSeed = DEFAULT_SEED,
) -> Path:
    """Build one denormalized course-offering table from logical seed data."""

    def write(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE course_offerings (
                course_code TEXT NOT NULL,
                course_name TEXT NOT NULL,
                credits REAL NOT NULL CHECK (credits > 0),
                category TEXT NOT NULL,
                department_code TEXT NOT NULL,
                department_name TEXT NOT NULL,
                instructor_name TEXT NOT NULL,
                year INTEGER NOT NULL CHECK (year BETWEEN 2000 AND 2100),
                semester TEXT NOT NULL,
                section TEXT NOT NULL,
                capacity INTEGER NOT NULL CHECK (capacity > 0),
                enrolled INTEGER NOT NULL CHECK (
                    enrolled >= 0 AND enrolled <= capacity
                ),
                day TEXT NOT NULL,
                start_time TEXT NOT NULL CHECK (start_time < end_time),
                end_time TEXT NOT NULL,
                PRIMARY KEY (year, semester, course_code, section)
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO course_offerings (
                course_code,
                course_name,
                credits,
                category,
                department_code,
                department_name,
                instructor_name,
                year,
                semester,
                section,
                capacity,
                enrolled,
                day,
                start_time,
                end_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    offering.course_code,
                    offering.course_name,
                    offering.credits,
                    offering.category,
                    offering.department_code,
                    offering.department_name,
                    offering.instructor_name,
                    offering.year,
                    offering.semester,
                    offering.section,
                    offering.capacity,
                    offering.enrolled,
                    offering.day,
                    offering.start_time,
                    offering.end_time,
                )
                for offering in seed.offerings
            ],
        )

    return build_atomic_database(path, write)
