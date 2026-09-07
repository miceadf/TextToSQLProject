from __future__ import annotations

import sqlite3
from pathlib import Path

from .common import build_atomic_database
from .contracts import FixtureSeed
from .seed import DEFAULT_SEED


def build_normalized_database(
    path: str | Path,
    seed: FixtureSeed = DEFAULT_SEED,
) -> Path:
    """Build normalized course fixture from shared logical seed."""

    def write_database(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE departments (
                department_code TEXT PRIMARY KEY,
                department_name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE instructors (
                instructor_id INTEGER PRIMARY KEY,
                instructor_name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE courses (
                course_code TEXT PRIMARY KEY,
                course_name TEXT NOT NULL,
                credits REAL NOT NULL CHECK (credits > 0),
                category TEXT NOT NULL,
                department_code TEXT NOT NULL,
                FOREIGN KEY (department_code)
                    REFERENCES departments (department_code)
            );

            CREATE TABLE course_offerings (
                offering_id INTEGER PRIMARY KEY,
                course_code TEXT NOT NULL,
                instructor_id INTEGER NOT NULL,
                academic_year INTEGER NOT NULL,
                semester TEXT NOT NULL,
                section TEXT NOT NULL,
                capacity INTEGER NOT NULL CHECK (capacity > 0),
                enrolled INTEGER NOT NULL
                    CHECK (enrolled >= 0 AND enrolled <= capacity),
                UNIQUE (academic_year, semester, course_code, section),
                FOREIGN KEY (course_code) REFERENCES courses (course_code),
                FOREIGN KEY (instructor_id)
                    REFERENCES instructors (instructor_id)
            );

            CREATE TABLE schedules (
                schedule_id INTEGER PRIMARY KEY,
                offering_id INTEGER NOT NULL UNIQUE,
                day_of_week TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                CHECK (start_time < end_time),
                FOREIGN KEY (offering_id)
                    REFERENCES course_offerings (offering_id)
            );
            """
        )

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
        instructor_names = sorted(
            {offering.instructor_name for offering in seed.offerings}
        )
        instructor_ids = {
            name: instructor_id
            for instructor_id, name in enumerate(instructor_names, start=1)
        }

        connection.executemany(
            "INSERT INTO departments VALUES (?, ?)",
            departments,
        )
        connection.executemany(
            "INSERT INTO instructors VALUES (?, ?)",
            [
                (instructor_ids[name], name)
                for name in instructor_names
            ],
        )
        connection.executemany(
            "INSERT INTO courses VALUES (?, ?, ?, ?, ?)",
            courses,
        )

        offerings = sorted(seed.offerings, key=lambda item: item.offering_key)
        connection.executemany(
            """
            INSERT INTO course_offerings (
                offering_id,
                course_code,
                instructor_id,
                academic_year,
                semester,
                section,
                capacity,
                enrolled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
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
            ],
        )
        connection.executemany(
            """
            INSERT INTO schedules (
                schedule_id,
                offering_id,
                day_of_week,
                start_time,
                end_time
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    offering_id,
                    offering_id,
                    offering.day,
                    offering.start_time,
                    offering.end_time,
                )
                for offering_id, offering in enumerate(offerings, start=1)
            ],
        )

    return build_atomic_database(path, write_database)
