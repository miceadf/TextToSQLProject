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
from dataclasses import dataclass, field
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

    def table(self, name: str) -> Table:
        return next(table for table in self.tables if table.name == name)


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


VARIANTS: tuple[Variant, ...] = _variants()
VARIANT_NAMES = tuple(variant.name for variant in VARIANTS)


def variant(name: str) -> Variant:
    return next(item for item in VARIANTS if item.name == name)


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
