"""Explicit reviewed mappings for the synthetic fixture only."""

from __future__ import annotations

from datetime import datetime, timezone

from ..catalog.contracts import DatabaseCatalog
from ..fixtures.contracts import FixtureVariant
from ..mapping.contracts import PublishedFieldMapping, PublishedMappingProfile
from .reference import FIELD_TO_ATTRIBUTE


_TABLES = {
    FixtureVariant.FLAT: {
        "Course": "course_offerings",
        "Department": "course_offerings",
        "Instructor": "course_offerings",
        "CourseOffering": "course_offerings",
        "Schedule": "course_offerings",
    },
    FixtureVariant.NORMALIZED: {
        "Course": "courses",
        "Department": "departments",
        "Instructor": "instructors",
        "CourseOffering": "course_offerings",
        "Schedule": "schedules",
    },
    FixtureVariant.LEGACY: {
        "Course": "lecture_info",
        "Department": "lecture_info",
        "Instructor": "lecture_info",
        "CourseOffering": "lecture_info",
        "Schedule": "lecture_info",
    },
    FixtureVariant.CODED: {
        "Course": "tb_sbjt",
        "Department": "tb_dept",
        "Instructor": "tb_prof",
        "CourseOffering": "tb_lect",
        "Schedule": "tb_lect_tm",
    },
    FixtureVariant.HYBRID: {
        "Course": "subjects",
        "Department": "class_sections",
        "Instructor": "class_sections",
        "CourseOffering": "class_sections",
        "Schedule": "class_sections",
    },
}

_COLUMNS = {
    FixtureVariant.FLAT: {
        **FIELD_TO_ATTRIBUTE,
    },
    FixtureVariant.NORMALIZED: {
        **FIELD_TO_ATTRIBUTE,
        "CourseOffering.year": "academic_year",
        "Schedule.day": "day_of_week",
    },
    FixtureVariant.LEGACY: {
        "Course.code": "sbjt_cd",
        "Course.name": "sbjt_nm",
        "Course.credits": "pnt",
        "Course.category": "isu_gb",
        "Department.code": "dept_cd",
        "Department.name": "dept_nm",
        "Instructor.name": "prof_nm",
        "CourseOffering.year": "yyyy",
        "CourseOffering.semester": "hakgi",
        "CourseOffering.section": "bunban",
        "CourseOffering.capacity": "limit_count",
        "CourseOffering.enrolled": "applicant_count",
        "Schedule.day": "lecture_day",
        "Schedule.start_time": "begin_time",
        "Schedule.end_time": "finish_time",
    },
    FixtureVariant.CODED: {
        "Course.code": "sbjt_cd",
        "Course.name": "sbjt_nm",
        "Course.credits": "pnt",
        "Course.category": "isu_gb",
        "Department.code": "dept_cd",
        "Department.name": "dept_nm",
        "Instructor.name": "prof_nm",
        "CourseOffering.year": "yyyy",
        "CourseOffering.semester": "hakgi",
        "CourseOffering.section": "bunban",
        "CourseOffering.capacity": "limit_cnt",
        "CourseOffering.enrolled": "aply_cnt",
        "Schedule.day": "yoil",
        "Schedule.start_time": "st_tm",
        "Schedule.end_time": "ed_tm",
    },
    FixtureVariant.HYBRID: {
        "Course.code": "subject_code",
        "Course.name": "subject_title",
        "Course.credits": "credit_hours",
        "Course.category": "course_type",
        "Department.code": "dept_code",
        "Department.name": "dept_name",
        "Instructor.name": "professor",
        "CourseOffering.year": "term_year",
        "CourseOffering.semester": "term",
        "CourseOffering.section": "section_no",
        "CourseOffering.capacity": "seat_limit",
        "CourseOffering.enrolled": "seats_taken",
        "Schedule.day": "weekday",
        "Schedule.start_time": "time_from",
        "Schedule.end_time": "time_to",
    },
}


def fixture_published_mapping(
    variant: FixtureVariant,
    catalog: DatabaseCatalog,
    *,
    ontology_version: int = 1,
) -> PublishedMappingProfile:
    """Build an explicit synthetic PUBLISHED profile after catalog checks.

    This mapping is fixture-specific test metadata, not an automatically
    published production Mapping Proposal.
    """

    if catalog.database_id != f"fixture-{variant.value}-v1":
        raise ValueError("catalog does not belong to the expected fixture variant")
    by_table = {table.name: {column.name for column in table.columns} for table in catalog.tables}
    fields: dict[str, PublishedFieldMapping] = {}
    for semantic_field in sorted(FIELD_TO_ATTRIBUTE):
        entity = semantic_field.split(".", 1)[0]
        table = _TABLES[variant][entity]
        column = _COLUMNS[variant][semantic_field]
        if column not in by_table.get(table, set()):
            raise ValueError("fixture published mapping disagrees with catalog")
        fields[semantic_field] = PublishedFieldMapping(
            table=table,
            column=column,
            reviewer="fixture-v1-contract",
            reason="synthetic fixture column checked against catalog",
        )
    return PublishedMappingProfile(
        database_id=catalog.database_id,
        version=1,
        schema_fingerprint=catalog.schema_fingerprint,
        ontology_version=ontology_version,
        published_by="fixture-v1-contract",
        published_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
        fields=fields,
    )
