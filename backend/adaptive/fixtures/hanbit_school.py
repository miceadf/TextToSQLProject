"""Phase 9-29: Hanbit University, an invented school to rehearse the full admin + chat flow before teammates' tests.

It is development data, never adaptation evidence (ADR-086). Everything is
invented and deterministic. New shapes: team teaching, ISO integer weekdays,
one-character regular and seasonal term codes, and online classes with no
timetable rows.

CLI (DSN only from ``SCHEMA_ADAPTIVE_SYNTHETIC_POSTGRES_DSN``, never printed)::

    python -m backend.adaptive.fixtures.hanbit_school create|drop|check
"""

from __future__ import annotations

import os
import random
import sys
from datetime import time
from typing import Any

from . import other_school

SCHEMA = "hanbit_univ"
SEED = 9300
YEAR = 2026
DSN_ENV = other_school.DSN_ENV

DEPARTMENTS = (
    ("CSE", "컴퓨터공학과", "공과대학", ("프로그래밍입문", "자료구조", "알고리즘", "운영체제", "데이터베이스", "컴퓨터구조", "네트워크프로그래밍", "소프트웨어공학")),
    ("DSC", "데이터사이언스학과", "공과대학", ("데이터사이언스개론", "통계학", "데이터베이스응용", "데이터시각화", "빅데이터분석", "기계학습", "데이터마이닝")),
    ("ECE", "전자공학과", "공과대학", ("회로이론", "전자기학", "디지털시스템", "신호처리", "반도체개론", "제어공학", "임베디드시스템")),
    ("MEC", "기계공학과", "공과대학", ("정역학", "동역학", "열역학", "유체역학", "재료역학", "기계설계", "로봇공학")),
    ("BIZ", "경영학과", "경영대학", ("경영학원론", "회계원리", "마케팅", "재무관리", "조직행동", "경영정보시스템", "운영관리")),
    ("PSY", "심리학과", "인문대학", ("심리학개론", "발달심리", "인지심리", "사회심리", "심리통계", "상담심리")),
    ("KOR", "국어국문학과", "인문대학", ("국어학개론", "현대문학", "고전문학", "한국어문법", "글쓰기와표현", "한국문화읽기")),
    ("GEN", "교양학부", "교양대학", ("대학글쓰기", "영어회화", "철학의이해", "역사와문화", "컴퓨팅사고", "시민과사회", "체육", "진로설계")),
)
POSITIONS = ("교수", "부교수", "조교수", "강사")
COMPLETIONS = ("전공필수", "전공선택", "교양필수", "교양선택", "일반선택")
TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "college": ("college_id", "college_name"),
    "department": ("dept_id", "dept_code", "dept_name", "college_id"),
    "professor": ("prof_id", "prof_name", "dept_id", "position"),
    "subject": ("subject_code", "subject_name", "dept_id", "credit", "completion_type", "target_grade"),
    "open_class": ("class_id", "subject_code", "class_no", "open_year", "term_code", "capacity", "enrolled", "delivery_mode", "english_yn"),
    "class_professor": ("class_id", "prof_id", "role"),
    "class_time": ("time_id", "class_id", "weekday", "start_time", "end_time", "room"),
}
_DDL = (
    "CREATE TABLE {college} (college_id smallint PRIMARY KEY, college_name varchar(30) NOT NULL UNIQUE)",
    "CREATE TABLE {department} (dept_id integer PRIMARY KEY, dept_code varchar(4) NOT NULL UNIQUE, dept_name varchar(40) NOT NULL UNIQUE, college_id smallint NOT NULL REFERENCES {college} (college_id))",
    "CREATE TABLE {professor} (prof_id integer PRIMARY KEY, prof_name varchar(20) NOT NULL, dept_id integer NOT NULL REFERENCES {department} (dept_id), position varchar(10) NOT NULL)",
    "CREATE TABLE {subject} (subject_code varchar(8) PRIMARY KEY, subject_name varchar(60) NOT NULL, dept_id integer NOT NULL REFERENCES {department} (dept_id), credit smallint NOT NULL, completion_type varchar(10) NOT NULL, target_grade smallint NOT NULL)",
    "CREATE TABLE {open_class} (class_id integer PRIMARY KEY, subject_code varchar(8) NOT NULL REFERENCES {subject} (subject_code), class_no char(2) NOT NULL, open_year smallint NOT NULL, term_code char(1) NOT NULL, capacity smallint NOT NULL, enrolled smallint NOT NULL, delivery_mode varchar(6) NOT NULL, english_yn char(1) NOT NULL, UNIQUE (open_year, term_code, subject_code, class_no), CHECK (term_code IN ('1','2','3','4')), CHECK (english_yn IN ('Y','N')))",
    "CREATE TABLE {class_professor} (class_id integer NOT NULL REFERENCES {open_class} (class_id), prof_id integer NOT NULL REFERENCES {professor} (prof_id), role varchar(4) NOT NULL, PRIMARY KEY (class_id, prof_id))",
    "CREATE TABLE {class_time} (time_id integer PRIMARY KEY, class_id integer NOT NULL REFERENCES {open_class} (class_id), weekday smallint NOT NULL CHECK (weekday BETWEEN 1 AND 7), start_time time NOT NULL, end_time time NOT NULL, room varchar(20) NOT NULL)",
)

_SLOTS = (("09:00", "10:15"), ("10:30", "11:45"), ("12:00", "13:15"), ("13:30", "14:45"), ("15:00", "16:15"), ("16:30", "17:45"), ("13:00", "15:50"), ("18:30", "21:00"))
_BUILDINGS = {"공과대학": "공학관", "경영대학": "경영관", "인문대학": "인문관", "교양대학": "교양관"}


def _clock(value: str) -> time:
    hour, minute = map(int, value.split(":"))
    return time(hour, minute)


def school_rows(seed: int = SEED) -> dict[str, list[tuple[Any, ...]]]:
    """All deterministic rows in insertion order."""
    rng = random.Random(seed)
    colleges = ("공과대학", "경영대학", "인문대학", "교양대학")
    college_ids = {name: index + 1 for index, name in enumerate(colleges)}
    rows: dict[str, list[tuple[Any, ...]]] = {table: [] for table in TABLE_COLUMNS}
    rows["college"] = [(college_ids[name], name) for name in colleges]
    dept_ids = {code: index + 1 for index, (code, *_rest) in enumerate(DEPARTMENTS)}
    for code, name, college, _subjects in DEPARTMENTS:
        rows["department"].append((dept_ids[code], code, name, college_ids[college]))
    surnames = list(other_school._SURNAMES)
    given = list(other_school._GIVEN)
    names = [surname + first for surname in surnames for first in given]
    rng.shuffle(names)
    professor_ids: dict[str, list[int]] = {}
    prof_name: dict[int, str] = {}
    next_prof = 1
    for code, _name, _college, _subjects in DEPARTMENTS:
        professor_ids[code] = []
        for index in range(4):
            pid = next_prof
            next_prof += 1
            position = POSITIONS[(index + (1 if code in ("CSE", "BIZ") else 0)) % len(POSITIONS)]
            name = names.pop()
            rows["professor"].append((pid, name, dept_ids[code], position))
            professor_ids[code].append(pid)
            prof_name[pid] = name
    subjects: list[tuple[str, str, str, int, str, int]] = []
    for code, _dept_name, _college, titles in DEPARTMENTS:
        for index, title in enumerate(titles):
            grade = index % 4 + 1
            completion = (("교양필수", "교양선택") if code == "GEN" else ("전공필수", "전공선택", "일반선택"))[(index + (1 if code == "GEN" else 0)) % (2 if code == "GEN" else 3)]
            credit = 1 if title in ("체육", "진로설계") else (2 if index % 5 == 0 else 3)
            subj_code = f"{code}{grade}{index + 1:03d}"
            row = (subj_code, title, code, credit, completion, grade)
            subjects.append(row)
            rows["subject"].append((subj_code, title, dept_ids[code], credit, completion, grade))
    classes = []
    for subject_index, subject in enumerate(subjects):
        subj_code, _title, code, _credit, _completion, _grade = subject
        terms = ["1" if subject_index % 2 == 0 else "2"]
        if subject_index % 2 == 0:
            terms.append("2" if terms[0] == "1" else "1")
        for term in terms:
            sections = 2 if (subject_index + int(term)) % 7 == 0 else 1
            for section in range(1, sections + 1):
                classes.append((subj_code, code, term, f"{section:02d}"))
    for term, count in (("3", 10), ("4", 6)):
        for index in range(count):
            subj_code, _title, code, *_ = subjects[(index * 3 + (0 if term == "3" else 5)) % len(subjects)]
            classes.append((subj_code, code, term, "01"))
    seen = set()
    class_records = []
    for class_id, (subj_code, code, term, section) in enumerate(classes, 1):
        key = (term, subj_code, section)
        if key in seen:
            section = "02"
            key = (term, subj_code, section)
        seen.add(key)
        capacity = rng.randint(20, 120)
        enrolled = rng.randint(0, capacity)
        mode = "온라인" if term in ("1", "2") and class_id % 12 == 0 else ("혼합" if class_id % 15 == 0 else "대면")
        english = "Y" if class_id % 10 == 0 else "N"
        actual_subj = next(s for s in subjects if s[0] == subj_code)
        rows["open_class"].append((class_id, subj_code, section, YEAR, term, capacity, enrolled, mode, english))
        profs = professor_ids[code]
        lead = profs[(class_id + rng.randrange(len(profs))) % len(profs)]
        assignments = [(class_id, lead, "주담당")]
        if class_id % 8 == 0:
            pool = [pid for pid, _name, dept_id, _pos in rows["professor"] if pid != lead]
            assignments.append((class_id, rng.choice(pool), "공동"))
        rows["class_professor"].extend(assignments)
        class_records.append((class_id, term, mode, actual_subj, code, assignments))
    time_id = 1
    for class_id, term, mode, subject, code, _assignments in class_records:
        if mode == "온라인":
            continue
        _subj_code, _title, _dept_code, *_ = subject
        building = _BUILDINGS[next(college for dcode, _dn, college, _titles in DEPARTMENTS if dcode == code)]
        room = f"{building} {300 + class_id % 500}호"
        if term in ("3", "4"):
            start, end = (("09:00", "11:50") if class_id % 2 else ("13:00", "15:50"))
            for weekday in range(1, 6):
                rows["class_time"].append((time_id, class_id, weekday, _clock(start), _clock(end), room))
                time_id += 1
        else:
            start, end = rng.choice(_SLOTS)
            if start == "13:00":
                weekdays = [rng.randint(1, 5)]
            elif start == "18:30":
                weekdays = [rng.randint(1, 5)]
            elif class_id % 4:
                weekdays = [1, 3] if class_id % 2 else [2, 4]
            else:
                weekdays = [rng.randint(1, 5)]
            if class_id % 17 == 0:
                weekdays = [6]
            for weekday in weekdays:
                rows["class_time"].append((time_id, class_id, weekday, _clock(start), _clock(end), room))
                time_id += 1
    return rows


def catalog_rows(seed: int = SEED) -> list[dict[str, Any]]:
    """One dictionary per open class, for fixing question answers from Python rows."""
    rows = school_rows(seed)
    departments = {row[0]: (row[1], row[2]) for row in rows["department"]}
    colleges = {row[0]: row[1] for row in rows["college"]}
    professors = {row[0]: row[1] for row in rows["professor"]}
    subjects = {row[0]: row for row in rows["subject"]}
    dept_by_code = {row[1]: row for row in rows["department"]}
    assignments: dict[int, list[tuple[str, str]]] = {}
    for class_id, prof_id, role in rows["class_professor"]:
        assignments.setdefault(class_id, []).append((professors[prof_id], role))
    times: dict[int, list[tuple[int, str, str, str]]] = {}
    for _tid, class_id, weekday, start, end, room in rows["class_time"]:
        times.setdefault(class_id, []).append((weekday, start.strftime("%H:%M"), end.strftime("%H:%M"), room))
    result = []
    for class_id, subj_code, class_no, _year, term, capacity, enrolled, mode, english in rows["open_class"]:
        _code, subject_name, dept_id, credit, completion, grade = subjects[subj_code]
        dept_code, dept_name = departments[dept_id]
        college_id = dept_by_code[dept_code][3]
        profs = sorted(assignments[class_id], key=lambda pair: pair[1] != "주담당")
        result.append({"subject_code": subj_code, "subject_name": subject_name, "dept_code": dept_code,
                       "dept_name": dept_name, "college": colleges[college_id], "credit": credit,
                       "completion_type": completion, "target_grade": grade, "term_code": term,
                       "class_no": class_no, "capacity": capacity, "enrolled": enrolled,
                       "delivery_mode": mode, "english_yn": english, "professors": profs,
                       "times": times.get(class_id, [])})
    return result


def _drop_statements(sql: Any) -> list[tuple[Any, tuple[Any, ...]]]:
    """Own tables, children first, then the schema; never CASCADE."""
    tables = [
        (sql.SQL("DROP TABLE IF EXISTS {}").format(sql.SQL(".").join([sql.Identifier(SCHEMA), sql.Identifier(table)])), ())
        for table in reversed(TABLE_COLUMNS)
    ]
    return [*tables, (sql.SQL("DROP SCHEMA IF EXISTS {}").format(sql.Identifier(SCHEMA)), ())]


def _statements(sql: Any) -> list[tuple[Any, tuple[Any, ...]]]:
    names = {table: sql.SQL(".").join([sql.Identifier(SCHEMA), sql.Identifier(table)]) for table in TABLE_COLUMNS}
    operations = [*_drop_statements(sql), (sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(SCHEMA)), ())]
    operations += [(sql.SQL(text).format(**names), ()) for text in _DDL]
    for table, table_rows in school_rows().items():
        statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            names[table], sql.SQL(", ").join(sql.Identifier(column) for column in TABLE_COLUMNS[table]),
            sql.SQL(", ").join(sql.Placeholder() for _ in TABLE_COLUMNS[table]))
        operations += [(statement, tuple(row)) for row in table_rows]
    return operations


def main(argv: list[str] | None = None, environ: dict[str, str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in ("create", "drop", "check"):
        print("usage: python -m backend.adaptive.fixtures.hanbit_school create|drop|check")
        return 2
    try:
        connection = other_school._connect(dict(os.environ if environ is None else environ))
    except Exception:
        print("hanbit_school: connection refused (set a loopback schema_adaptive_synthetic DSN)")
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
        print(f"hanbit_school: {args[0]} failed")
        return 1
    finally:
        connection.close()
    print(f"hanbit_school: {args[0]} done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
