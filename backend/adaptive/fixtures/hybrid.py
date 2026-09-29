from __future__ import annotations

import sqlite3
from pathlib import Path

from .common import build_atomic_database
from .contracts import FixtureSeed
from .seed import DEFAULT_SEED


def build_hybrid_database(
    path: str | Path,
    seed: FixtureSeed = DEFAULT_SEED,
) -> Path:
    """Build a partially denormalized two-table schema.

    Subjects keep only course attributes; department, instructor and schedule
    columns live on each section row, so the department moves off the course.
    """

    def write_database(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE subjects (
                subject_code TEXT PRIMARY KEY,
                subject_title TEXT NOT NULL,
                credit_hours REAL NOT NULL CHECK (credit_hours > 0),
                course_type TEXT NOT NULL
            );

            CREATE TABLE class_sections (
                section_id INTEGER PRIMARY KEY,
                subject_code TEXT NOT NULL,
                dept_code TEXT NOT NULL,
                dept_name TEXT NOT NULL,
                professor TEXT NOT NULL,
                term_year INTEGER NOT NULL CHECK (term_year BETWEEN 2000 AND 2100),
                term TEXT NOT NULL,
                section_no TEXT NOT NULL,
                seat_limit INTEGER NOT NULL CHECK (seat_limit > 0),
                seats_taken INTEGER NOT NULL
                    CHECK (seats_taken >= 0 AND seats_taken <= seat_limit),
                weekday TEXT NOT NULL,
                time_from TEXT NOT NULL,
                time_to TEXT NOT NULL,
                CHECK (time_from < time_to),
                UNIQUE (term_year, term, subject_code, section_no),
                FOREIGN KEY (subject_code) REFERENCES subjects (subject_code)
            );
            """
        )

        subjects = sorted(
            {
                (
                    offering.course_code,
                    offering.course_name,
                    offering.credits,
                    offering.category,
                )
                for offering in seed.offerings
            }
        )
        offerings = sorted(seed.offerings, key=lambda item: item.offering_key)

        connection.executemany(
            "INSERT INTO subjects VALUES (?, ?, ?, ?)",
            subjects,
        )
        connection.executemany(
            """
            INSERT INTO class_sections (
                section_id, subject_code, dept_code, dept_name, professor,
                term_year, term, section_no, seat_limit, seats_taken,
                weekday, time_from, time_to
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    section_id,
                    offering.course_code,
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
                for section_id, offering in enumerate(offerings, start=1)
            ],
        )

    return build_atomic_database(path, write_database)
