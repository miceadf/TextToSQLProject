from __future__ import annotations

import sqlite3
from pathlib import Path

from .common import build_atomic_database
from .contracts import FixtureSeed
from .seed import DEFAULT_SEED


def build_coded_database(
    path: str | Path,
    seed: FixtureSeed = DEFAULT_SEED,
) -> Path:
    """Build the normalized structure with legacy-style abbreviated names."""

    def write_database(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE tb_dept (
                dept_cd TEXT PRIMARY KEY,
                dept_nm TEXT NOT NULL UNIQUE
            );

            CREATE TABLE tb_prof (
                prof_no INTEGER PRIMARY KEY,
                prof_nm TEXT NOT NULL UNIQUE
            );

            CREATE TABLE tb_sbjt (
                sbjt_cd TEXT PRIMARY KEY,
                sbjt_nm TEXT NOT NULL,
                pnt REAL NOT NULL CHECK (pnt > 0),
                isu_gb TEXT NOT NULL,
                dept_cd TEXT NOT NULL,
                FOREIGN KEY (dept_cd) REFERENCES tb_dept (dept_cd)
            );

            CREATE TABLE tb_lect (
                lect_no INTEGER PRIMARY KEY,
                sbjt_cd TEXT NOT NULL,
                prof_no INTEGER NOT NULL,
                yyyy INTEGER NOT NULL CHECK (yyyy BETWEEN 2000 AND 2100),
                hakgi TEXT NOT NULL,
                bunban TEXT NOT NULL,
                limit_cnt INTEGER NOT NULL CHECK (limit_cnt > 0),
                aply_cnt INTEGER NOT NULL
                    CHECK (aply_cnt >= 0 AND aply_cnt <= limit_cnt),
                UNIQUE (yyyy, hakgi, sbjt_cd, bunban),
                FOREIGN KEY (sbjt_cd) REFERENCES tb_sbjt (sbjt_cd),
                FOREIGN KEY (prof_no) REFERENCES tb_prof (prof_no)
            );

            CREATE TABLE tb_lect_tm (
                tm_no INTEGER PRIMARY KEY,
                lect_no INTEGER NOT NULL UNIQUE,
                yoil TEXT NOT NULL,
                st_tm TEXT NOT NULL,
                ed_tm TEXT NOT NULL,
                CHECK (st_tm < ed_tm),
                FOREIGN KEY (lect_no) REFERENCES tb_lect (lect_no)
            );
            """
        )

        departments = sorted(
            {
                (offering.department_code, offering.department_name)
                for offering in seed.offerings
            }
        )
        subjects = sorted(
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
        professor_names = sorted(
            {offering.instructor_name for offering in seed.offerings}
        )
        professor_numbers = {
            name: number
            for number, name in enumerate(professor_names, start=1)
        }
        offerings = sorted(seed.offerings, key=lambda item: item.offering_key)

        connection.executemany("INSERT INTO tb_dept VALUES (?, ?)", departments)
        connection.executemany(
            "INSERT INTO tb_prof VALUES (?, ?)",
            [(professor_numbers[name], name) for name in professor_names],
        )
        connection.executemany(
            "INSERT INTO tb_sbjt VALUES (?, ?, ?, ?, ?)",
            subjects,
        )
        connection.executemany(
            """
            INSERT INTO tb_lect (
                lect_no, sbjt_cd, prof_no, yyyy, hakgi, bunban, limit_cnt, aply_cnt
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    number,
                    offering.course_code,
                    professor_numbers[offering.instructor_name],
                    offering.year,
                    offering.semester,
                    offering.section,
                    offering.capacity,
                    offering.enrolled,
                )
                for number, offering in enumerate(offerings, start=1)
            ],
        )
        connection.executemany(
            """
            INSERT INTO tb_lect_tm (tm_no, lect_no, yoil, st_tm, ed_tm)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    number,
                    number,
                    offering.day,
                    offering.start_time,
                    offering.end_time,
                )
                for number, offering in enumerate(offerings, start=1)
            ],
        )

    return build_atomic_database(path, write_database)
