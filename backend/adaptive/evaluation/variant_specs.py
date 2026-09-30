"""Synthetic schema-variant benchmark: seed, variants and gold (Phase 8-08).

The same course offerings are stored twelve ways, grouped by what a mapping
must understand: structure (tables and optional relations), format (values
a closed-list transform reads), code values (a reviewed value dictionary),
and meaning (misleading or abbreviated names). The gold mapping of every
variant is derived from column annotations, so the spec and its answer
cannot drift apart.

Everything here is synthetic and deterministic; nothing connects anywhere.
"""

from __future__ import annotations

from collections.abc import Callable
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from ..catalog.contracts import ColumnCatalog, DatabaseCatalog, ForeignKeyCatalog, TableCatalog
from ..catalog.postgres import PostgresAdapter, postgres_affinity
from ..mapping.transforms import FieldTransform

SEED_VERSION = "variant-benchmark-seed-v1"
BENCHMARK_VERSION = "variant-benchmark-v1"

CANONICAL_FIELDS = (
    "Course.code",
    "Course.name",
    "Course.credits",
    "Course.category",
    "Department.code",
    "Department.name",
    "Instructor.name",
    "CourseOffering.year",
    "CourseOffering.semester",
    "CourseOffering.section",
    "CourseOffering.capacity",
    "CourseOffering.enrolled",
    "Schedule.day",
    "Schedule.start_time",
    "Schedule.end_time",
)

# --- seed ----------------------------------------------------------------------

DEPARTMENTS = {"BIZ": "경영학부", "CSE": "컴퓨터공학과", "LIB": "교양교육원", "MATH": "수학과"}
INSTRUCTORS = ("교원A", "교원B", "교원C", "교원D", "교원E", "교원F")
# code -> (name, credits, category, department, target grade)
COURSES: dict[str, tuple[str, int, str, str, int]] = {
    "BIZ101": ("경영학원론", 3, "일반선택", "BIZ", 1),
    "CSE101": ("프로그래밍기초", 3, "전공", "CSE", 1),
    "CSE201": ("자료구조", 3, "전공", "CSE", 2),
    "CSE301": ("데이터베이스", 3, "전공", "CSE", 3),
    "CSE390": ("캡스톤디자인", 2, "전공", "CSE", 4),
    "HUM101": ("사고와표현", 2, "교양", "LIB", 1),
    "HUM102": ("글쓰기", 1, "교양", "LIB", 1),
    "MTH101": ("대학수학", 3, "교양", "MATH", 1),
    "MTH201": ("선형대수", 3, "전공", "MATH", 2),
}
# (id, course, section, year, semester, instructor, capacity, enrolled, day, start, end)
OFFERINGS: tuple[tuple[int, str, str, int, str, str, int, int, str, str, str], ...] = (
    (1, "CSE101", "01", 2026, "1", "교원A", 40, 32, "월", "09:00", "10:30"),
    (2, "CSE101", "02", 2026, "1", "교원B", 40, 40, "화", "13:00", "14:30"),
    (3, "CSE201", "01", 2026, "1", "교원B", 35, 35, "화", "10:30", "12:00"),
    (4, "CSE201", "01", 2026, "2", "교원C", 35, 29, "수", "13:30", "15:00"),
    (5, "CSE301", "01", 2026, "1", "교원A", 30, 24, "목", "15:00", "16:30"),
    (6, "CSE390", "01", 2026, "2", "교원A", 20, 12, "금", "18:00", "21:00"),
    (7, "MTH101", "01", 2026, "1", "교원D", 50, 41, "금", "09:00", "10:30"),
    (8, "MTH201", "01", 2025, "2", "교원D", 45, 20, "월", "13:00", "14:30"),
    (9, "HUM101", "01", 2026, "1", "교원E", 45, 38, "수", "10:30", "12:00"),
    (10, "HUM102", "01", 2025, "1", "교원E", 30, 30, "토", "09:00", "11:00"),
    (11, "BIZ101", "01", 2026, "1", "교원F", 120, 101, "목", "18:00", "19:30"),
    (12, "BIZ101", "01", 2025, "2", "교원F", 120, 88, "금", "15:00", "16:30"),
)
# Offerings that the optional-schedule variants store without a timetable.
UNSCHEDULED = frozenset({6, 12})

CATEGORY_CODES = {"전공": "M", "교양": "G", "일반선택": "E"}
WEEKDAY_CODES = {"월": "MON", "화": "TUE", "수": "WED", "목": "THU", "금": "FRI", "토": "SAT"}
SEMESTER_CODES = {"1": "10", "2": "20"}
# Held-out spellings (Phase 8-10), different from every development variant.
WEEKDAY_FULL = {"월": "월요일", "화": "화요일", "수": "수요일", "목": "목요일", "금": "금요일", "토": "토요일"}
WEEKDAY_LOWER = {"월": "mon", "화": "tue", "수": "wed", "목": "thu", "금": "fri", "토": "sat"}
CATEGORY_NUMBERS = {"전공": "1", "교양": "2", "일반선택": "3"}
SEMESTER_TEXT = {"1": "1학기", "2": "2학기"}

Level = Literal["department", "instructor", "course", "offering", "schedule", "flat"]


def _instructor_id(name: str) -> int:
    return INSTRUCTORS.index(name) + 1


def level_records(level: Level, unscheduled: frozenset[int]) -> list[dict[str, Any]]:
    """Logical rows of one storage level, in a fixed order."""
    if level == "department":
        return [{"code": code, "name": name} for code, name in sorted(DEPARTMENTS.items())]
    if level == "instructor":
        return [{"id": _instructor_id(name), "name": name} for name in INSTRUCTORS]
    if level == "course":
        return [
            {"code": code, "name": name, "credits": credits, "category": category,
             "department": department, "grade": grade}
            for code, (name, credits, category, department, grade) in sorted(COURSES.items())
        ]
    records = []
    for (oid, course, section, year, semester, instructor, capacity, enrolled,
         day, start, end) in OFFERINGS:
        scheduled = oid not in unscheduled
        if level == "schedule":
            if scheduled:
                records.append({"id": oid, "offering": oid, "day": day, "start": start, "end": end})
            continue
        name, credits, category, department, grade = COURSES[course]
        records.append({
            "id": oid, "course": course, "section": section, "year": year,
            "semester": semester, "instructor_id": _instructor_id(instructor),
            "instructor": instructor, "capacity": capacity, "enrolled": enrolled,
            "course_name": name, "credits": credits, "category": category,
            "department": department, "department_name": DEPARTMENTS[department],
            "grade": grade,
            "day": day if scheduled else None,
            "start": start if scheduled else None,
            "end": end if scheduled else None,
        })
    return records


def canonical_rows(unscheduled: frozenset[int] = frozenset()) -> list[dict[str, Any]]:
    """One row per offering in canonical semantic values (reference input)."""
    return [
        {
            "Course.code": row["course"],
            "Course.name": row["course_name"],
            "Course.credits": row["credits"],
            "Course.category": row["category"],
            "Department.code": row["department"],
            "Department.name": row["department_name"],
            "Instructor.name": row["instructor"],
            "CourseOffering.year": row["year"],
            "CourseOffering.semester": row["semester"],
            "CourseOffering.section": row["section"],
            "CourseOffering.capacity": row["capacity"],
            "CourseOffering.enrolled": row["enrolled"],
            "Schedule.day": row["day"],
            "Schedule.start_time": row["start"],
            "Schedule.end_time": row["end"],
        }
        for row in level_records("offering", unscheduled)
    ]


# --- variant specification -------------------------------------------------------


@dataclass(frozen=True)
class Col:
    name: str
    pg_type: str  # exactly as ``format_type`` reports it
    key: str  # attribute of the level record
    semantic: str | None = None
    fmt: Callable[[Any], Any] | None = None
    transform: FieldTransform | None = None
    # further transforms that are equally correct for this column
    also_transforms: frozenset[FieldTransform] = frozenset()
    dictionary: dict[str, str] | None = None  # stored -> canonical value
    nullable: bool = False

    def value(self, record: dict[str, Any]) -> Any:
        raw = record[self.key]
        return raw if raw is None or self.fmt is None else self.fmt(raw)


@dataclass(frozen=True)
class Table:
    name: str
    level: Level
    columns: tuple[Col, ...]
    pk: tuple[str, ...]
    # (constraint name, local columns, referenced table, referenced columns)
    fks: tuple[tuple[str, tuple[str, ...], str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class Variant:
    name: str
    axis: Literal["structure", "format", "code", "meaning", "combined"]
    description: str
    tables: tuple[Table, ...]
    anchor: str
    unscheduled: frozenset[int] = field(default_factory=frozenset)
    notes: tuple["Note", ...] = ()

    def table(self, name: str) -> Table:
        return next(table for table in self.tables if table.name == name)


# admin-notes-v1 reasons: abbreviation, code column, packed values, name that
# does not match the meaning.
NOTE_REASONS = frozenset({"ABBREVIATION", "CODE", "PACKED", "MISLEADING"})


@dataclass(frozen=True)
class Note:
    """An administrator's description of one column (``admin-notes-v1``).

    Written only for abbreviations, code columns, packed formats and columns
    whose name does not match their meaning; never names a canonical field
    or a transform. Code meanings use the administrator's own words.
    """

    table: str
    column: str
    reason: str  # one of NOTE_REASONS: why the rule allows this note
    text: str
    codes: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        texts = [self.text, *(part for pair in self.codes for part in pair)]
        if self.reason not in NOTE_REASONS:
            raise ValueError("note needs one of the admin-notes-v1 reasons")
        if (self.reason == "CODE") != bool(self.codes):
            raise ValueError("exactly code notes carry a code table")
        if not self.text.strip() or len(self.text) > 200 or len(self.codes) > 32:
            raise ValueError("note must be one bounded sentence")
        if any(not part.strip() or len(part) > 50 for pair in self.codes for part in pair):
            raise ValueError("codes and meanings must be short")
        codes = [code for code, _meaning in self.codes]
        if len(codes) != len(set(codes)):
            raise ValueError("a code appears once")
        if any(unicodedata.category(char) in ("Cc", "Cf") for text in texts for char in text):
            raise ValueError("note must not contain control or format characters")


@dataclass(frozen=True)
class GoldField:
    locations: frozenset[tuple[str, str]]  # empty: unsupported
    primary: tuple[str, str] | None
    transforms: frozenset[FieldTransform | None]
    dictionary: dict[str, str] | None


def _day(**kwargs: Any) -> Col:
    base = {"name": "day_of_week", "pg_type": "text", "key": "day", "semantic": "Schedule.day",
            "also_transforms": frozenset({FieldTransform.KOREAN_WEEKDAY})}
    base.update(kwargs)
    return Col(**base)


def _base_tables(
    *,
    course_cols: dict[str, Col] | None = None,
    offering_cols: dict[str, Col] | None = None,
    schedule_cols: dict[str, Col] | None = None,
) -> tuple[Table, ...]:
    """Normalized five-table layout; ``*_cols`` replace or add columns by name."""
    course = {
        "course_code": Col("course_code", "text", "code", "Course.code"),
        "course_name": Col("course_name", "text", "name", "Course.name"),
        "credits": Col("credits", "numeric", "credits", "Course.credits"),
        "category": Col("category", "text", "category", "Course.category"),
        "department_code": Col("department_code", "text", "department"),
    }
    offering = {
        "offering_id": Col("offering_id", "integer", "id"),
        "course_code": Col("course_code", "text", "course"),
        "instructor_id": Col("instructor_id", "integer", "instructor_id"),
        "academic_year": Col("academic_year", "integer", "year", "CourseOffering.year"),
        "semester": Col("semester", "text", "semester", "CourseOffering.semester"),
        "section": Col("section", "text", "section", "CourseOffering.section"),
        "capacity": Col("capacity", "integer", "capacity", "CourseOffering.capacity"),
        "enrolled": Col("enrolled", "integer", "enrolled", "CourseOffering.enrolled"),
    }
    schedule = {
        "schedule_id": Col("schedule_id", "integer", "id"),
        "offering_id": Col("offering_id", "integer", "offering"),
        "day_of_week": _day(),
        "start_time": Col("start_time", "text", "start", "Schedule.start_time"),
        "end_time": Col("end_time", "text", "end", "Schedule.end_time"),
    }
    for target, changes in ((course, course_cols), (offering, offering_cols), (schedule, schedule_cols)):
        for name, col in (changes or {}).items():
            if col is None:
                target.pop(name, None)
            else:
                target[name] = col
    return (
        Table("departments", "department", (
            Col("department_code", "text", "code", "Department.code"),
            Col("department_name", "text", "name", "Department.name"),
        ), ("department_code",)),
        Table("instructors", "instructor", (
            Col("instructor_id", "integer", "id"),
            Col("instructor_name", "text", "name", "Instructor.name"),
        ), ("instructor_id",)),
        Table("courses", "course", tuple(course.values()), ("course_code",), (
            ("fk_01", ("department_code",), "departments", ("department_code",)),
        )),
        Table("course_offerings", "offering", tuple(offering.values()), ("offering_id",), (
            ("fk_01", ("course_code",), "courses", ("course_code",)),
            ("fk_02", ("instructor_id",), "instructors", ("instructor_id",)),
        )),
        Table("schedules", "schedule", tuple(schedule.values()), ("schedule_id",), (
            ("fk_01", ("offering_id",), "course_offerings", ("offering_id",)),
        )),
    )


def _slash(credits: Any) -> str:
    return f"{int(credits)}/{int(credits)}/0"


def _dict_col(col: Col, codes: dict[str, str]) -> Col:
    """The same column storing codes, with its gold dictionary."""
    return Col(
        col.name, col.pg_type, col.key, col.semantic, fmt=codes.__getitem__,
        dictionary={stored: canonical for canonical, stored in codes.items()},
        nullable=col.nullable,
    )


_BASE_DAY = _day()
_TIME = "time without time zone"


def _variants() -> tuple[Variant, ...]:
    flat_columns = (
        Col("offering_id", "integer", "id"),
        Col("course_code", "text", "course", "Course.code"),
        Col("course_name", "text", "course_name", "Course.name"),
        Col("credits", "numeric", "credits", "Course.credits"),
        Col("category", "text", "category", "Course.category"),
        Col("department_code", "text", "department", "Department.code"),
        Col("department_name", "text", "department_name", "Department.name"),
        Col("instructor_name", "text", "instructor", "Instructor.name"),
        Col("academic_year", "integer", "year", "CourseOffering.year"),
        Col("semester", "text", "semester", "CourseOffering.semester"),
        Col("section", "text", "section", "CourseOffering.section"),
        Col("capacity", "integer", "capacity", "CourseOffering.capacity"),
        Col("enrolled", "integer", "enrolled", "CourseOffering.enrolled"),
        _day(),
        Col("start_time", "text", "start", "Schedule.start_time"),
        Col("end_time", "text", "end", "Schedule.end_time"),
    )
    abbreviated = (
        Table("tb_dept", "department", (
            Col("dept_cd", "text", "code", "Department.code"),
            Col("dept_nm", "text", "name", "Department.name"),
        ), ("dept_cd",)),
        Table("tb_prof", "instructor", (
            Col("prof_no", "integer", "id"),
            Col("prof_nm", "text", "name", "Instructor.name"),
        ), ("prof_no",)),
        Table("tb_sbjt", "course", (
            Col("sbjt_cd", "text", "code", "Course.code"),
            Col("sbjt_nm", "text", "name", "Course.name"),
            Col("pnt", "numeric", "credits", "Course.credits"),
            Col("isu_gb", "text", "category", "Course.category"),
            Col("dept_cd", "text", "department"),
        ), ("sbjt_cd",), (("fk_01", ("dept_cd",), "tb_dept", ("dept_cd",)),)),
        Table("tb_lect", "offering", (
            Col("lect_no", "integer", "id"),
            Col("sbjt_cd", "text", "course"),
            Col("prof_no", "integer", "instructor_id"),
            Col("yyyy", "integer", "year", "CourseOffering.year"),
            Col("hakgi", "text", "semester", "CourseOffering.semester"),
            Col("bunban", "text", "section", "CourseOffering.section"),
            Col("limit_cnt", "integer", "capacity", "CourseOffering.capacity"),
            Col("aply_cnt", "integer", "enrolled", "CourseOffering.enrolled"),
        ), ("lect_no",), (
            ("fk_01", ("sbjt_cd",), "tb_sbjt", ("sbjt_cd",)),
            ("fk_02", ("prof_no",), "tb_prof", ("prof_no",)),
        )),
        Table("tb_lect_tm", "schedule", (
            Col("tm_no", "integer", "id"),
            Col("lect_no", "integer", "offering"),
            _day(name="yoil"),
            Col("st_tm", "text", "start", "Schedule.start_time"),
            Col("ed_tm", "text", "end", "Schedule.end_time"),
        ), ("tm_no",), (("fk_01", ("lect_no",), "tb_lect", ("lect_no",)),)),
    )
    legacy_like = (
        Table("department", "department", (
            Col("dept_code", "text", "code", "Department.code"),
            Col("dept_name", "text", "name", "Department.name"),
        ), ("dept_code",)),
        Table("subject", "course", (
            Col("subject_code", "text", "code", "Course.code"),
            Col("subject_name", "text", "name", "Course.name"),
            Col("credit_hours", "text", "credits", "Course.credits", fmt=_slash,
                transform=FieldTransform.SLASH_FIRST_NUMERIC),
            Col("category", "text", "category", "Course.category"),
            Col("target_year", "integer", "grade"),
        ), ("subject_code",)),
        Table("cnu_courses", "offering", (
            Col("offer_id", "integer", "id"),
            Col("subject_code", "text", "course"),
            Col("section", "text", "section", "CourseOffering.section"),
            Col("course_year", "integer", "grade"),
            Col("professor", "text", "instructor", "Instructor.name"),
            Col("capacity", "integer", "capacity", "CourseOffering.capacity"),
            Col("enrolled", "integer", "enrolled", "CourseOffering.enrolled"),
            Col("dept_code", "text", "department"),
        ), ("offer_id",), (
            ("fk_01", ("dept_code",), "department", ("dept_code",)),
            ("fk_02", ("subject_code",), "subject", ("subject_code",)),
        )),
        Table("course_schedule", "schedule", (
            Col("schedule_id", "integer", "id"),
            Col("offer_id", "integer", "offering"),
            _dict_col(_BASE_DAY, WEEKDAY_CODES),
            Col("start_time", _TIME, "start", "Schedule.start_time",
                transform=FieldTransform.TIME_TO_HHMM),
            Col("end_time", _TIME, "end", "Schedule.end_time",
                transform=FieldTransform.TIME_TO_HHMM),
        ), ("schedule_id",), (("fk_01", ("offer_id",), "cnu_courses", ("offer_id",)),)),
    )
    return (
        Variant("base", "structure", "normalized five tables, plain names and values",
                _base_tables(), "course_offerings"),
        Variant("flat", "structure", "one table per offering",
                (Table("course_offerings", "flat", flat_columns, ("offering_id",)),),
                "course_offerings"),
        Variant("optional-schedule", "structure", "two offerings have no timetable row",
                _base_tables(), "course_offerings", UNSCHEDULED),
        Variant("credits-slash", "format", "credits stored as 'credits/theory/practice' text",
                _base_tables(course_cols={
                    "credits": None,
                    "credit_hours": Col("credit_hours", "text", "credits", "Course.credits",
                                        fmt=_slash, transform=FieldTransform.SLASH_FIRST_NUMERIC),
                }), "course_offerings"),
        Variant("time-type", "format", "start and end stored as PostgreSQL time",
                _base_tables(schedule_cols={
                    "start_time": Col("start_time", _TIME, "start", "Schedule.start_time",
                                      transform=FieldTransform.TIME_TO_HHMM),
                    "end_time": Col("end_time", _TIME, "end", "Schedule.end_time",
                                    transform=FieldTransform.TIME_TO_HHMM),
                }), "course_offerings"),
        Variant("category-code", "code", "category stored as one-letter codes",
                _base_tables(course_cols={
                    "category": _dict_col(Col("category", "text", "category", "Course.category"),
                                          CATEGORY_CODES),
                }), "course_offerings"),
        Variant("weekday-english", "code", "weekday stored as English abbreviations",
                _base_tables(schedule_cols={"day_of_week": _dict_col(_BASE_DAY, WEEKDAY_CODES)}),
                "course_offerings"),
        Variant("semester-code", "code", "semester stored as '10' and '20'",
                _base_tables(offering_cols={
                    "semester": _dict_col(
                        Col("semester", "text", "semester", "CourseOffering.semester"),
                        SEMESTER_CODES,
                    ),
                }), "course_offerings"),
        Variant("abbreviated", "meaning", "romanized abbreviations for every name",
                abbreviated, "tb_lect"),
        Variant("grade-trap", "meaning", "course_year holds the target grade; no offering year",
                _base_tables(offering_cols={
                    "academic_year": None,
                    "course_year": Col("course_year", "integer", "grade"),
                }), "course_offerings"),
        Variant("capacity-trap", "meaning", "capacity is the room size; enroll_limit is the cap",
                _base_tables(offering_cols={
                    "capacity": Col("capacity", "integer", "capacity", fmt=lambda value: value + 10),
                    "enroll_limit": Col("enroll_limit", "integer", "capacity",
                                        "CourseOffering.capacity"),
                }), "course_offerings"),
        Variant("legacy-like", "combined",
                "legacy-style names, slash credits, time type, English weekdays, "
                "grade column, no year or semester, two offerings without timetable",
                legacy_like, "cnu_courses", UNSCHEDULED),
    )


def _codes(mapping: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(mapping.items())


_SLASH_NOTE = "학점/주당 이론시간/주당 실습시간을 '/'로 이은 문자열"

# admin-notes-v1 for the development variants (Phase 8-10). The rule, not
# the 8-09 results, decides which columns get a note.
DEV_NOTES: dict[str, tuple[Note, ...]] = {
    "credits-slash": (Note("courses", "credit_hours", "PACKED", _SLASH_NOTE),),
    "category-code": (
        Note("courses", "category", "CODE", "이수구분 코드",
             _codes({"M": "전공", "G": "교양", "E": "일반선택"})),
    ),
    "weekday-english": (
        Note("schedules", "day_of_week", "CODE", "수업 요일의 영문 약자",
             _codes({"MON": "월요일", "TUE": "화요일", "WED": "수요일", "THU": "목요일",
                     "FRI": "금요일", "SAT": "토요일"})),
    ),
    "semester-code": (
        Note("course_offerings", "semester", "CODE", "개설 학기 코드", _codes({"10": "1학기", "20": "2학기"})),
    ),
    "abbreviated": (
        Note("tb_dept", "dept_cd", "ABBREVIATION", "학과 코드"),
        Note("tb_dept", "dept_nm", "ABBREVIATION", "학과 이름"),
        Note("tb_prof", "prof_no", "ABBREVIATION", "교수 번호"),
        Note("tb_prof", "prof_nm", "ABBREVIATION", "교수 이름"),
        Note("tb_sbjt", "sbjt_cd", "ABBREVIATION", "과목 코드"),
        Note("tb_sbjt", "sbjt_nm", "ABBREVIATION", "과목 이름"),
        Note("tb_sbjt", "pnt", "ABBREVIATION", "학점"),
        Note("tb_sbjt", "isu_gb", "ABBREVIATION", "이수구분"),
        Note("tb_lect", "lect_no", "ABBREVIATION", "개설 강좌 번호"),
        Note("tb_lect", "yyyy", "ABBREVIATION", "개설 연도"),
        Note("tb_lect", "hakgi", "ABBREVIATION", "개설 학기"),
        Note("tb_lect", "bunban", "ABBREVIATION", "분반"),
        Note("tb_lect", "limit_cnt", "ABBREVIATION", "수강 제한 인원"),
        Note("tb_lect", "aply_cnt", "ABBREVIATION", "수강 신청 인원"),
        Note("tb_lect_tm", "tm_no", "ABBREVIATION", "시간표 번호"),
        Note("tb_lect_tm", "yoil", "ABBREVIATION", "수업 요일"),
        Note("tb_lect_tm", "st_tm", "ABBREVIATION", "수업 시작 시각"),
        Note("tb_lect_tm", "ed_tm", "ABBREVIATION", "수업 종료 시각"),
    ),
    "grade-trap": (Note("course_offerings", "course_year", "MISLEADING", "수강 대상 학년(1~4)"),),
    "capacity-trap": (
        Note("course_offerings", "capacity", "MISLEADING", "강의실 좌석 수"),
    ),
    "legacy-like": (
        Note("subject", "credit_hours", "PACKED", _SLASH_NOTE),
        Note("subject", "target_year", "MISLEADING", "수강 대상 학년(1~4)"),
        Note("cnu_courses", "course_year", "MISLEADING", "수강 대상 학년(1~4)"),
        Note("course_schedule", "day_of_week", "CODE", "수업 요일의 영문 약자",
             _codes({"MON": "월요일", "TUE": "화요일", "WED": "수요일", "THU": "목요일",
                     "FRI": "금요일", "SAT": "토요일"})),
    ),
}

VARIANTS: tuple[Variant, ...] = tuple(
    replace(spec, notes=DEV_NOTES.get(spec.name, ())) for spec in _variants()
)
VARIANT_NAMES = tuple(variant.name for variant in VARIANTS)


def _ho_tables(
    *,
    subject_cols: dict[str, Col | None] | None = None,
    lecture_cols: dict[str, Col | None] | None = None,
    time_cols: dict[str, Col | None] | None = None,
    dept_cols: dict[str, Col | None] | None = None,
    teacher_cols: dict[str, Col | None] | None = None,
) -> tuple[Table, ...]:
    """Held-out naming scheme; ``*_cols`` replace, add or (None) drop columns."""
    dept = {
        "dept_id": Col("dept_id", "text", "code", "Department.code"),
        "dept_title": Col("dept_title", "text", "name", "Department.name"),
    }
    teacher = {
        "teacher_id": Col("teacher_id", "integer", "id"),
        "teacher_name": Col("teacher_name", "text", "name", "Instructor.name"),
    }
    subject = {
        "subject_id": Col("subject_id", "text", "code", "Course.code"),
        "subject_title": Col("subject_title", "text", "name", "Course.name"),
        "credit": Col("credit", "numeric", "credits", "Course.credits"),
        "course_div": Col("course_div", "text", "category", "Course.category"),
        "dept_id": Col("dept_id", "text", "department"),
    }
    lecture = {
        "lecture_id": Col("lecture_id", "integer", "id"),
        "subject_id": Col("subject_id", "text", "course"),
        "teacher_id": Col("teacher_id", "integer", "instructor_id"),
        "open_year": Col("open_year", "integer", "year", "CourseOffering.year"),
        "term": Col("term", "text", "semester", "CourseOffering.semester"),
        "class_no": Col("class_no", "text", "section", "CourseOffering.section"),
        "max_enroll": Col("max_enroll", "integer", "capacity", "CourseOffering.capacity"),
        "enroll_count": Col("enroll_count", "integer", "enrolled", "CourseOffering.enrolled"),
    }
    lecture_time = {
        "time_id": Col("time_id", "integer", "id"),
        "lecture_id": Col("lecture_id", "integer", "offering"),
        "weekday": Col("weekday", "text", "day", "Schedule.day",
                       also_transforms=frozenset({FieldTransform.KOREAN_WEEKDAY})),
        "begin_time": Col("begin_time", "text", "start", "Schedule.start_time"),
        "end_time": Col("end_time", "text", "end", "Schedule.end_time"),
    }
    for target, changes in ((dept, dept_cols), (teacher, teacher_cols), (subject, subject_cols),
                            (lecture, lecture_cols), (lecture_time, time_cols)):
        for name, col in (changes or {}).items():
            if col is None:
                target.pop(name, None)
            else:
                target[name] = col
    return (
        Table("dept_master", "department", tuple(dept.values()), ("dept_id",)),
        Table("teacher", "instructor", tuple(teacher.values()), ("teacher_id",)),
        Table("subject_master", "course", tuple(subject.values()), ("subject_id",), (
            ("fk_01", ("dept_id",), "dept_master", ("dept_id",)),
        )),
        Table("lecture", "offering", tuple(lecture.values()), ("lecture_id",), (
            ("fk_01", ("subject_id",), "subject_master", ("subject_id",)),
            ("fk_02", ("teacher_id",), "teacher", ("teacher_id",)),
        )),
        Table("lecture_time", "schedule", tuple(lecture_time.values()), ("time_id",), (
            ("fk_01", ("lecture_id",), "lecture", ("lecture_id",)),
        )),
    )


def _units(credits: Any) -> str:
    """Held-out packed format: credits/theory/practice with real practice hours."""
    value = int(credits)
    return f"{value}/{max(value - 1, 0)}/2"


_HO_TIME = {
    "begin_time": Col("begin_time", _TIME, "start", "Schedule.start_time",
                      transform=FieldTransform.TIME_TO_HHMM),
    "end_time": Col("end_time", _TIME, "end", "Schedule.end_time",
                    transform=FieldTransform.TIME_TO_HHMM),
}


def _holdout_variants() -> tuple[Variant, ...]:
    weekday = Col("weekday", "text", "day", "Schedule.day")
    return (
        Variant("ho-weekday-full", "code", "weekday spelled out ('월요일')",
                _ho_tables(time_cols={"weekday": _dict_col(weekday, WEEKDAY_FULL)}), "lecture"),
        Variant("ho-category-numeric", "code", "category stored as '1', '2', '3'",
                _ho_tables(subject_cols={"course_div": _dict_col(
                    Col("course_div", "text", "category", "Course.category"), CATEGORY_NUMBERS)}),
                "lecture",
                notes=(Note("subject_master", "course_div", "CODE", "이수구분 코드",
                            _codes({"1": "전공", "2": "교양", "3": "일반선택"})),)),
        Variant("ho-semester-text", "code", "semester stored as '1학기', '2학기'",
                _ho_tables(lecture_cols={"term": _dict_col(
                    Col("term", "text", "semester", "CourseOffering.semester"), SEMESTER_TEXT)}),
                "lecture"),
        Variant("ho-units", "format",
                "credits packed in unit_info; credit_hours is the weekly hour total",
                _ho_tables(subject_cols={
                    "credit": None,
                    "credit_hours": Col("credit_hours", "integer", "credits", fmt=lambda c: int(c) + 1),
                    "unit_info": Col("unit_info", "text", "credits", "Course.credits", fmt=_units,
                                     transform=FieldTransform.SLASH_FIRST_NUMERIC),
                }), "lecture",
                notes=(
                    Note("subject_master", "credit_hours", "MISLEADING", "주당 수업 시간 합계(이론+실습)"),
                    Note("subject_master", "unit_info", "PACKED", _SLASH_NOTE),
                )),
        Variant("ho-trap", "meaning",
                "academic_year is the target grade and capacity the room size",
                _ho_tables(lecture_cols={
                    "open_year": None,
                    "academic_year": Col("academic_year", "integer", "grade"),
                    "open_yr": Col("open_yr", "integer", "year", "CourseOffering.year"),
                    "capacity": Col("capacity", "integer", "capacity", fmt=lambda v: v + 15),
                }), "lecture",
                notes=(
                    Note("lecture", "academic_year", "MISLEADING", "수강 대상 학년(1~4)"),
                    Note("lecture", "open_yr", "ABBREVIATION", "개설 연도"),
                    Note("lecture", "capacity", "MISLEADING", "강의실 좌석 수"),
                )),
        Variant("ho-mixed", "combined",
                "time type, lowercase English weekdays, new abbreviations, packed credits, "
                "two offerings without timetable",
                _ho_tables(
                    dept_cols={"dept_title": None, "dpt_ttl": Col("dpt_ttl", "text", "name", "Department.name")},
                    teacher_cols={"teacher_name": None, "instr": Col("instr", "text", "name", "Instructor.name")},
                    subject_cols={
                        "subject_title": None,
                        "crs_ttl": Col("crs_ttl", "text", "name", "Course.name"),
                        "credit": None,
                        "unit_info": Col("unit_info", "text", "credits", "Course.credits", fmt=_slash,
                                         transform=FieldTransform.SLASH_FIRST_NUMERIC),
                    },
                    time_cols={**_HO_TIME, "weekday": _dict_col(weekday, WEEKDAY_LOWER)},
                ), "lecture", UNSCHEDULED,
                notes=(
                    Note("dept_master", "dpt_ttl", "ABBREVIATION", "학과 이름"),
                    Note("teacher", "instr", "ABBREVIATION", "담당 교수 이름"),
                    Note("subject_master", "crs_ttl", "ABBREVIATION", "과목 이름"),
                    Note("subject_master", "unit_info", "PACKED", _SLASH_NOTE),
                    Note("lecture_time", "weekday", "CODE", "수업 요일의 영문 소문자 약자",
                         _codes({"mon": "월요일", "tue": "화요일", "wed": "수요일", "thu": "목요일",
                                 "fri": "금요일", "sat": "토요일"})),
                )),
    )


HOLDOUT_VARIANTS: tuple[Variant, ...] = _holdout_variants()
SUITES: dict[str, tuple[Variant, ...]] = {"dev": VARIANTS, "holdout": HOLDOUT_VARIANTS}


def variant(name: str) -> Variant:
    return next(item for suite in SUITES.values() for item in suite if item.name == name)


# --- derived views -------------------------------------------------------------


def table_rows(spec: Variant, table: Table) -> list[tuple[Any, ...]]:
    level: Level = "offering" if table.level == "flat" else table.level
    return [
        tuple(col.value(record) for col in table.columns)
        for record in level_records(level, spec.unscheduled)
    ]


def spec_catalog(spec: Variant, database_id: str) -> DatabaseCatalog:
    """The catalog the PostgreSQL inspector reports for this variant."""
    tables = []
    for table in sorted(spec.tables, key=lambda item: item.name):
        foreign_keys = [
            ForeignKeyCatalog(
                id=fk_id, sequence=sequence, column=column, referenced_table=target,
                referenced_column=target_columns[sequence], on_update="NO ACTION",
                on_delete="NO ACTION", match="SIMPLE",
            )
            for fk_id, (_name, columns, target, target_columns) in enumerate(
                sorted(table.fks, key=lambda item: item[0])
            )
            for sequence, column in enumerate(columns)
        ]
        tables.append(TableCatalog(
            name=table.name,
            columns=[
                ColumnCatalog(
                    name=col.name,
                    declared_type=col.pg_type,
                    affinity=postgres_affinity(col.pg_type),
                    nullable=col.name not in table.pk and _nullable(spec, table, col),
                    default_sql=None,
                    primary_key_position=(table.pk.index(col.name) + 1) if col.name in table.pk else 0,
                    hidden=False,
                    generated=False,
                    sample_values=[],
                )
                for col in table.columns
            ],
            primary_key=list(table.pk),
            foreign_keys=sorted(foreign_keys, key=lambda item: (item.id, item.sequence, item.column)),
        ))
    return DatabaseCatalog(
        database_id=database_id,
        dialect="postgres",
        tables=tables,
        schema_fingerprint=PostgresAdapter._fingerprint(tables),  # noqa: SLF001 - same rule
    )


def _nullable(spec: Variant, table: Table, col: Col) -> bool:
    """Only a flat table's timetable columns can be empty (unscheduled offerings)."""
    return table.level == "flat" and bool(spec.unscheduled) and col.key in {"day", "start", "end"}


def gold_fields(spec: Variant) -> dict[str, GoldField]:
    located: dict[str, tuple[str, str, Col]] = {}
    for table in spec.tables:
        for col in table.columns:
            if col.semantic is None:
                continue
            if col.semantic in located:
                raise ValueError(f"{spec.name}: '{col.semantic}' is annotated twice")
            located[col.semantic] = (table.name, col.name, col)
    gold: dict[str, GoldField] = {}
    for semantic in CANONICAL_FIELDS:
        if semantic not in located:
            gold[semantic] = GoldField(frozenset(), None, frozenset({None}), None)
            continue
        table_name, column_name, col = located[semantic]
        locations = {(table_name, column_name)}
        # A foreign-key copy of the same value is an equally correct location.
        for table in spec.tables:
            for _name, columns, target, target_columns in table.fks:
                if len(columns) == 1 and (target, target_columns[0]) == (table_name, column_name):
                    locations.add((table.name, columns[0]))
        gold[semantic] = GoldField(
            frozenset(locations),
            (table_name, column_name),
            frozenset({col.transform, *col.also_transforms}),
            dict(col.dictionary) if col.dictionary is not None else None,
        )
    return gold


def sample_values(spec: Variant, limit: int = 10) -> dict[tuple[str, str], list[Any]]:
    """Bounded distinct stored values per column (optional proposer input)."""
    samples: dict[tuple[str, str], list[Any]] = {}
    for table in spec.tables:
        rows = table_rows(spec, table)
        for index, col in enumerate(table.columns):
            values = sorted({row[index] for row in rows if row[index] is not None}, key=str)
            samples[(table.name, col.name)] = values[:limit]
    return samples
