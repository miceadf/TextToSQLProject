"""A second, invented school stored the way its own systems would (Phase 8-17).

Every browser check before this phase used the legacy database, which the
chat model was fine-tuned on. This module builds a deliberately different
course database in the approved local ``schema_adaptive_synthetic`` database
so the screen flow can be checked on something the model has never seen:
romanized abbreviations outside the ontology hints, code values with a code
table, split credits, ``MON`` weekdays, ``char(4)`` ``0900`` times and more
than one timetable row per offering.

Everything is invented and deterministic. The twelve questions and their
answers are fixed here, computed from the Python rows rather than the
database, before any chat run.

CLI (DSN only from ``SCHEMA_ADAPTIVE_SYNTHETIC_POSTGRES_DSN``, never printed)::

    python -m backend.adaptive.fixtures.other_school create|drop|check
"""

from __future__ import annotations

import os
import random
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

SCHEMA = "other_school"
SEED = 8017
YEAR = 2026
DSN_ENV = "SCHEMA_ADAPTIVE_SYNTHETIC_POSTGRES_DSN"

# (code, name, college, courses). Names are generic subjects, not any school's catalogue.
DEPARTMENTS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("SW", "소프트웨어학부", "IT대학", (
        "프로그래밍기초", "자료구조", "알고리즘", "운영체제", "데이터베이스", "소프트웨어공학",
        "컴퓨터네트워크", "웹프로그래밍", "모바일프로그래밍", "캡스톤디자인")),
    ("AI", "인공지능학과", "IT대학", (
        "인공지능개론", "기계학습", "딥러닝", "데이터베이스시스템", "확률과통계", "선형대수",
        "자연어처리", "컴퓨터비전", "강화학습")),
    ("EE", "전자전기공학부", "IT대학", (
        "회로이론", "전자기학", "신호및시스템", "디지털논리회로", "전자회로", "제어공학",
        "전력공학", "반도체공학", "마이크로프로세서")),
    ("ME", "기계공학과", "공과대학", (
        "정역학", "동역학", "열역학", "유체역학", "재료역학", "기계설계", "기계공작법", "자동제어")),
    ("IE", "산업공학과", "공과대학", ("경영과학", "생산관리", "품질공학", "인간공학", "시뮬레이션", "데이터마이닝")),
    ("CE", "화학공학과", "공과대학", ("일반화학", "유기화학", "물리화학", "화공열역학", "반응공학", "공정설계")),
    ("BA", "경영학부", "경영대학", (
        "경영학원론", "회계원리", "마케팅원론", "재무관리", "인사관리", "경영정보시스템", "데이터베이스경영")),
    ("EC", "경제학과", "경영대학", ("경제학원론", "미시경제학", "거시경제학", "계량경제학", "국제경제학", "화폐금융론")),
    ("KL", "국어국문학과", "인문대학", ("국어학개론", "현대문학사", "고전문학강독", "국어문법론", "현대시론")),
    ("EL", "영어영문학과", "인문대학", ("영어학개론", "영미문학개론", "영어음성학", "영작문", "셰익스피어")),
    ("LA", "교양교육원", "교양교육원", (
        "대학글쓰기", "영어회화", "철학의이해", "역사와문화", "컴퓨팅사고", "미적분학", "일반물리학",
        "창의적사고", "진로설계")),
)

ISU_CODES = (("11", "전공필수"), ("12", "전공선택"), ("21", "교양필수"), ("22", "교양선택"), ("30", "일반선택"))
WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI")
_SURNAMES = "김이박최정강조윤장임한오서신권황안송류홍"
_GIVEN = ("민준", "서연", "도윤", "하은", "지호", "수빈", "예준", "지우", "현우", "채원", "준서", "유진",
          "태윤", "소율", "건우", "다인", "시우", "나연", "우진", "혜린")

TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "isu_gubun": ("isu_cd", "isu_nm"),
    "hakgwa": ("hg_cd", "hg_nm", "dh_nm"),
    "gyosu": ("gs_no", "gs_nm", "hg_cd"),
    "gyogwamok": ("gm_cd", "gm_nm", "hg_cd", "isu_cd", "hakjeom", "iron_si", "silsup_si", "suggang_hangnyeon"),
    "gangjwa": ("ks_id", "gm_cd", "gs_no", "nyeondo", "hakgi_cd", "ban_no", "jeongwon", "sugang_inwon"),
    "siganpyo": ("sp_id", "ks_id", "yoil", "si_st", "si_ed", "gangui_sil"),
}

_DDL = (
    "CREATE TABLE {isu_gubun} (isu_cd char(2) PRIMARY KEY, isu_nm varchar(20) NOT NULL)",
    "CREATE TABLE {hakgwa} (hg_cd varchar(4) PRIMARY KEY, hg_nm varchar(40) NOT NULL UNIQUE,"
    " dh_nm varchar(40) NOT NULL)",
    "CREATE TABLE {gyosu} (gs_no integer PRIMARY KEY, gs_nm varchar(20) NOT NULL,"
    " hg_cd varchar(4) NOT NULL REFERENCES {hakgwa} (hg_cd))",
    "CREATE TABLE {gyogwamok} (gm_cd varchar(8) PRIMARY KEY, gm_nm varchar(60) NOT NULL,"
    " hg_cd varchar(4) NOT NULL REFERENCES {hakgwa} (hg_cd),"
    " isu_cd char(2) NOT NULL REFERENCES {isu_gubun} (isu_cd),"
    " hakjeom numeric(3,1) NOT NULL, iron_si integer NOT NULL, silsup_si integer NOT NULL,"
    " suggang_hangnyeon integer NOT NULL)",
    "CREATE TABLE {gangjwa} (ks_id integer PRIMARY KEY,"
    " gm_cd varchar(8) NOT NULL REFERENCES {gyogwamok} (gm_cd),"
    " gs_no integer NOT NULL REFERENCES {gyosu} (gs_no), nyeondo integer NOT NULL,"
    " hakgi_cd char(2) NOT NULL, ban_no varchar(3) NOT NULL, jeongwon integer NOT NULL,"
    " sugang_inwon integer NOT NULL, UNIQUE (nyeondo, hakgi_cd, gm_cd, ban_no))",
    "CREATE TABLE {siganpyo} (sp_id integer PRIMARY KEY,"
    " ks_id integer NOT NULL REFERENCES {gangjwa} (ks_id), yoil char(3) NOT NULL,"
    " si_st char(4) NOT NULL, si_ed char(4) NOT NULL, gangui_sil varchar(20) NOT NULL)",
)


# Phase 8-19: classroom buildings by department, for the extension-field check.
# Rooms come from the offering number, not the random stream, so every other
# value stays exactly as in Phase 8-17.
BUILDINGS = {
    "SW": "IT관", "AI": "IT관", "EE": "IT관", "ME": "공학관", "IE": "공학관", "CE": "공학관",
    "BA": "경영관", "EC": "경영관", "KL": "인문관", "EL": "인문관", "LA": "교양관",
}


def _room(code: str, offering_id: int) -> str:
    floor = 1 + offering_id % 4
    return f"{BUILDINGS[code]} {floor * 100 + (offering_id * 7) % 20 + 1}호"


def _add_minutes(hhmm: str, minutes: int) -> str:
    total = int(hhmm[:2]) * 60 + int(hhmm[2:]) + minutes
    return f"{total // 60:02d}{total % 60:02d}"


def school_rows(seed: int = SEED) -> dict[str, list[tuple[Any, ...]]]:
    """All rows, table by table, in insertion order."""
    rng = random.Random(seed)
    rows: dict[str, list[tuple[Any, ...]]] = {table: [] for table in TABLE_COLUMNS}
    rows["isu_gubun"] = list(ISU_CODES)
    names = [surname + given for surname in _SURNAMES for given in _GIVEN]
    rng.shuffle(names)
    professors: dict[str, list[int]] = {}
    for code, name, college, _courses in DEPARTMENTS:
        rows["hakgwa"].append((code, name, college))
        professors[code] = []
        for _ in range(3):
            number = 1001 + len(rows["gyosu"])
            rows["gyosu"].append((number, names.pop(), code))
            professors[code].append(number)
    offering_id = 0
    schedule_id = 0
    for code, _name, _college, courses in DEPARTMENTS:
        for index, course in enumerate(courses):
            course_code = f"{code}{1001 + index * 10}"
            credits = rng.choice((3, 3, 3, 2, 2, 1))
            theory, practice = {3: rng.choice(((3, 0), (2, 2))), 2: rng.choice(((2, 0), (1, 2))), 1: (0, 2)}[credits]
            if code == "LA":
                category = rng.choice(("21", "22", "22"))
            else:
                category = rng.choice(("11", "12", "12", "30")) if index else "11"
            rows["gyogwamok"].append(
                (course_code, course, code, category, credits, theory, practice, rng.randint(1, 4))
            )
            semester = rng.choice(("10", "20"))
            sections = ("001", "002") if rng.random() < 0.3 else ("001",)
            for section in sections:
                offering_id += 1
                capacity = rng.choice((30, 40, 40, 50, 60, 80))
                rows["gangjwa"].append((
                    offering_id, course_code, rng.choice(professors[code]), YEAR, semester, section,
                    capacity, rng.randint(capacity // 2, capacity),
                ))
                if credits == 3 and rng.random() < 0.7:
                    days = rng.choice((("MON", "WED"), ("TUE", "THU")))
                    start = rng.choice(("0900", "1030", "1200", "1330", "1500", "1630"))
                    sessions = [(day, start, _add_minutes(start, 75)) for day in days]
                else:
                    start = rng.choice(("0900", "1000", "1300", "1400", "1500"))
                    length = {3: 150, 2: 100, 1: 50}[credits]
                    sessions = [(rng.choice(WEEKDAYS), start, _add_minutes(start, length))]
                for day, begin, end in sessions:
                    schedule_id += 1
                    rows["siganpyo"].append((schedule_id, offering_id, day, begin, end, _room(code, offering_id)))
    return rows


def joined_rows(seed: int = SEED) -> list[dict[str, Any]]:
    """One dict per timetable row, the way a course list is read."""
    rows = school_rows(seed)
    department = {code: name for code, name, _college in rows["hakgwa"]}
    professor = {number: name for number, name, _dept in rows["gyosu"]}
    course = {row[0]: row for row in rows["gyogwamok"]}
    offering = {row[0]: row for row in rows["gangjwa"]}
    joined = []
    for _sid, ks_id, day, start, end, room in rows["siganpyo"]:
        _ks, gm_cd, gs_no, year, semester, section, capacity, enrolled = offering[ks_id]
        _gm, name, dept, category, credits, _theory, _practice, grade = course[gm_cd]
        joined.append({
            "course_code": gm_cd, "course_name": name, "department": department[dept], "category": category,
            "credits": credits, "grade": grade, "instructor": professor[gs_no], "year": year,
            "semester": semester, "section": section, "capacity": capacity, "enrolled": enrolled,
            "day": day, "start": start, "end": end, "room": room,
        })
    return joined


def _instructor_with_two_offerings(seed: int = SEED) -> str:
    counts: dict[str, set[int]] = {}
    rows = school_rows(seed)
    professor = {number: name for number, name, _dept in rows["gyosu"]}
    for ks_id, _gm, gs_no, *_rest in rows["gangjwa"]:
        counts.setdefault(professor[gs_no], set()).add(ks_id)
    return sorted(name for name, ids in counts.items() if len(ids) >= 2)[0]


@dataclass(frozen=True)
class Question:
    qid: str
    text: str
    # None: the question names something the ontology does not have; a
    # correct answer declines instead of returning rows.
    predicate: Callable[[dict[str, Any]], bool] | None
    meaning: str


_PROFESSOR = _instructor_with_two_offerings()

QUESTIONS: tuple[Question, ...] = (
    Question("q01", "소웨 전공필수 과목 보여줘",
             lambda r: r["department"] == "소프트웨어학부" and r["category"] == "11", "학과 약어 + 이수 구분 코드"),
    Question("q02", "전전 과목 보여줘", lambda r: r["department"] == "전자전기공학부", "학과 약어"),
    Question("q03", "기공 전공선택 과목 알려줘",
             lambda r: r["department"] == "기계공학과" and r["category"] == "12", "학과 약어 + 이수 구분 코드"),
    Question("q04", "교양필수 과목 보여줘", lambda r: r["category"] == "21", "이수 구분 코드"),
    Question("q05", "월요일 수업 보여줘", lambda r: r["day"] == "MON", "영문 요일"),
    Question("q06", "2학기 개설 과목 보여줘", lambda r: r["semester"] == "20", "학기 코드"),
    Question("q07", "2학점 과목 보여줘", lambda r: r["credits"] == 2, "분리된 학점 column"),
    Question("q08", "데이터베이스 과목 보여줘", lambda r: "데이터베이스" in r["course_name"], "과목명 부분 일치"),
    Question("q09", f"{_PROFESSOR} 교수님 강의 보여줘", lambda r: r["instructor"] == _PROFESSOR, "교수명"),
    Question("q10", "정원 60명 이상인 강의 보여줘", lambda r: r["capacity"] >= 60, "정원 비교"),
    Question("q11", "금요일 오전 수업 보여줘",
             lambda r: r["day"] == "FRI" and r["start"] < "1200", "영문 요일 + char(4) 시간"),
    # Phase 8-18: first fixed as a decline, because the ontology had no target
    # grade; that premise was wrong (the legacy chatbot answered it), so the
    # field was added and the gold is the first-year courses.
    Question("q12", "1학년 대상 과목 보여줘", lambda r: r["grade"] == 1, "대상 학년"),
    # Phase 8-19: the classroom is an extension field of this database only.
    Question("q13", "IT관 303호 수업 보여줘", lambda r: r["room"] == "IT관 303호", "확장 항목(강의실)"),
    Question("q14", "교양관에서 하는 수업 보여줘", lambda r: r["room"].startswith("교양관 "), "확장 항목(강의실 건물)"),
)


def gold(question: Question, seed: int = SEED) -> frozenset[str] | None:
    """The course codes a correct answer lists, or ``None`` for a decline."""
    if question.predicate is None:
        return None
    return frozenset(row["course_code"] for row in joined_rows(seed) if question.predicate(row))


def grade(question: Question, answer: dict[str, Any], seed: int = SEED) -> tuple[bool, str]:
    """(passed, short reason) for one chat answer."""
    expected = gold(question, seed)
    status = answer.get("status")
    if expected is None:
        return status in ("UNSUPPORTED", "NOT_UNDERSTOOD"), f"status={status}"
    if status != "SUCCEEDED":
        return False, f"status={status}"
    got = {row.get("course_code") for row in answer.get("rows", []) if isinstance(row, dict)}
    if got == expected:
        return True, f"{len(got)} courses"
    return False, f"missing={len(expected - got)} extra={len(got - expected)}"


# --- PostgreSQL (only the CLI connects) -----------------------------------------------


def _drop_statements(sql: Any) -> list[tuple[Any, tuple[Any, ...]]]:
    """Own tables, children first, then the schema; never CASCADE.

    Anything outside these six tables that depends on them (a view in
    another schema, say) makes PostgreSQL refuse instead of dropping it.
    """
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


def _connect(environ: dict[str, str]) -> Any:
    from ..harness.postgres_live_smoke import validate_dsn

    dsn = environ.get(DSN_ENV, "")
    validate_dsn(dsn)  # loopback host and the schema_adaptive_synthetic database only
    import psycopg2

    return psycopg2.connect(dsn)


def main(argv: list[str] | None = None, environ: dict[str, str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in ("create", "drop", "check"):
        print("usage: python -m backend.adaptive.fixtures.other_school create|drop|check")
        return 2
    try:
        connection = _connect(dict(os.environ if environ is None else environ))
    except Exception:
        print("other_school: connection refused (set a loopback schema_adaptive_synthetic DSN)")
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
        print(f"other_school: {args[0]} failed")
        return 1
    finally:
        connection.close()
    if args[0] != "check":
        print(f"other_school: {args[0]} done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
