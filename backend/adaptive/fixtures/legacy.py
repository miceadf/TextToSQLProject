from __future__ import annotations

import sqlite3
from pathlib import Path

from .common import build_atomic_database
from .contracts import FixtureSeed
from .seed import DEFAULT_SEED


def build_legacy_database(
    path: str | Path,
    seed: FixtureSeed = DEFAULT_SEED,
) -> Path:
    """Build a denormalized legacy-style course-offering database."""

    def write(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE lecture_info (
                sbjt_cd TEXT NOT NULL,
                sbjt_nm TEXT NOT NULL,
                pnt REAL NOT NULL CHECK (pnt > 0),
                isu_gb TEXT NOT NULL,
                dept_cd TEXT NOT NULL,
                dept_nm TEXT NOT NULL,
                prof_nm TEXT NOT NULL,
                yyyy INTEGER NOT NULL CHECK (yyyy BETWEEN 2000 AND 2100),
                hakgi TEXT NOT NULL,
                bunban TEXT NOT NULL,
                limit_count INTEGER NOT NULL CHECK (limit_count > 0),
                applicant_count INTEGER NOT NULL CHECK (
                    applicant_count >= 0 AND applicant_count <= limit_count
                ),
                lecture_day TEXT NOT NULL,
                begin_time TEXT NOT NULL CHECK (begin_time < finish_time),
                finish_time TEXT NOT NULL,
                PRIMARY KEY (yyyy, hakgi, sbjt_cd, bunban)
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO lecture_info (
                sbjt_cd,
                sbjt_nm,
                pnt,
                isu_gb,
                dept_cd,
                dept_nm,
                prof_nm,
                yyyy,
                hakgi,
                bunban,
                limit_count,
                applicant_count,
                lecture_day,
                begin_time,
                finish_time
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
