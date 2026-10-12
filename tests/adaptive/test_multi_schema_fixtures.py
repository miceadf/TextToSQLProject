from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.adaptive.catalog import DatabaseCatalog
from backend.adaptive.fixtures import CORE_VARIANTS, DEFAULT_SEED, FixtureVariant, build_fixture_bundle
from backend.adaptive.mapping import MappingProposal
from backend.adaptive.evaluation.ontology_version import recorded_ontology


FLAT_QUERY = """
SELECT
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
FROM course_offerings
ORDER BY year, semester, course_code, section
"""

NORMALIZED_QUERY = """
SELECT
    c.course_code,
    c.course_name,
    c.credits,
    c.category,
    d.department_code,
    d.department_name,
    i.instructor_name,
    o.academic_year AS year,
    o.semester,
    o.section,
    o.capacity,
    o.enrolled,
    s.day_of_week AS day,
    s.start_time,
    s.end_time
FROM course_offerings AS o
JOIN courses AS c ON c.course_code = o.course_code
JOIN departments AS d ON d.department_code = c.department_code
JOIN instructors AS i ON i.instructor_id = o.instructor_id
JOIN schedules AS s ON s.offering_id = o.offering_id
ORDER BY year, semester, c.course_code, o.section
"""

LEGACY_QUERY = """
SELECT
    sbjt_cd AS course_code,
    sbjt_nm AS course_name,
    pnt AS credits,
    isu_gb AS category,
    dept_cd AS department_code,
    dept_nm AS department_name,
    prof_nm AS instructor_name,
    yyyy AS year,
    hakgi AS semester,
    bunban AS section,
    limit_count AS capacity,
    applicant_count AS enrolled,
    lecture_day AS day,
    begin_time AS start_time,
    finish_time AS end_time
FROM lecture_info
ORDER BY year, semester, course_code, section
"""


EXPECTED_TABLES = {
    FixtureVariant.FLAT: ["course_offerings"],
    FixtureVariant.NORMALIZED: [
        "course_offerings",
        "courses",
        "departments",
        "instructors",
        "schedules",
    ],
    FixtureVariant.LEGACY: ["lecture_info"],
}

EXPECTED_TOP_COLUMNS = {
    FixtureVariant.FLAT: {
        "Course.code": "course_code",
        "Course.name": "course_name",
        "Course.credits": "credits",
        "Course.category": "category",
        "Department.code": "department_code",
        "Department.name": "department_name",
        "Instructor.name": "instructor_name",
        "CourseOffering.year": "year",
        "CourseOffering.semester": "semester",
        "CourseOffering.section": "section",
        "CourseOffering.capacity": "capacity",
        "CourseOffering.enrolled": "enrolled",
        "Schedule.day": "day",
        "Schedule.start_time": "start_time",
        "Schedule.end_time": "end_time",
    },
    FixtureVariant.NORMALIZED: {
        "Course.code": "course_code",
        "Course.name": "course_name",
        "Course.credits": "credits",
        "Course.category": "category",
        "Department.code": "department_code",
        "Department.name": "department_name",
        "Instructor.name": "instructor_name",
        "CourseOffering.year": "academic_year",
        "CourseOffering.semester": "semester",
        "CourseOffering.section": "section",
        "CourseOffering.capacity": "capacity",
        "CourseOffering.enrolled": "enrolled",
        "Schedule.day": "day_of_week",
        "Schedule.start_time": "start_time",
        "Schedule.end_time": "end_time",
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
}

NORMALIZED_TABLE_BY_ENTITY = {
    "Course": "courses",
    "Department": "departments",
    "Instructor": "instructors",
    "CourseOffering": "course_offerings",
    "Schedule": "schedules",
}


class MultiSchemaFixtureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output = Path(self.temp_dir.name) / "fixtures"
        # Phase 9-24: this unversioned historical mapping baseline uses v2.
        self.bundle = build_fixture_bundle(self.output, ontology=recorded_ontology())

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def read_rows(path: Path, query: str) -> list[dict[str, object]]:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in connection.execute(query).fetchall()]
        finally:
            connection.close()

    def test_three_schemas_contain_same_logical_rows(self) -> None:
        expected = DEFAULT_SEED.canonical_rows()
        queries = {
            FixtureVariant.FLAT: FLAT_QUERY,
            FixtureVariant.NORMALIZED: NORMALIZED_QUERY,
            FixtureVariant.LEGACY: LEGACY_QUERY,
        }

        self.assertEqual(set(self.bundle.artifacts), set(CORE_VARIANTS))
        for variant, query in queries.items():
            with self.subTest(variant=variant):
                artifact = self.bundle.artifacts[variant]
                self.assertEqual(self.read_rows(artifact.database_path, query), expected)

    def test_catalogs_capture_expected_tables_and_foreign_keys(self) -> None:
        for variant, artifact in self.bundle.artifacts.items():
            with self.subTest(variant=variant):
                catalog = DatabaseCatalog.model_validate_json(
                    artifact.catalog_path.read_text(encoding="utf-8")
                )
                self.assertEqual(
                    [table.name for table in catalog.tables],
                    EXPECTED_TABLES[variant],
                )
                self.assertEqual(catalog.database_id, artifact.database_id)

        normalized = DatabaseCatalog.model_validate_json(
            self.bundle.artifacts[FixtureVariant.NORMALIZED]
            .catalog_path.read_text(encoding="utf-8")
        )
        foreign_key_count = sum(
            len(table.foreign_keys) for table in normalized.tables
        )
        self.assertEqual(foreign_key_count, 4)
        tables = {table.name: table for table in normalized.tables}
        self.assertEqual(tables["departments"].primary_key, ["department_code"])
        self.assertEqual(tables["instructors"].primary_key, ["instructor_id"])
        self.assertEqual(tables["courses"].primary_key, ["course_code"])
        self.assertEqual(tables["course_offerings"].primary_key, ["offering_id"])
        self.assertEqual(tables["schedules"].primary_key, ["schedule_id"])
        actual_foreign_keys = {
            (
                table.name,
                foreign_key.column,
                foreign_key.referenced_table,
                foreign_key.referenced_column,
            )
            for table in normalized.tables
            for foreign_key in table.foreign_keys
        }
        self.assertEqual(
            actual_foreign_keys,
            {
                ("courses", "department_code", "departments", "department_code"),
                ("course_offerings", "course_code", "courses", "course_code"),
                ("course_offerings", "instructor_id", "instructors", "instructor_id"),
                ("schedules", "offering_id", "course_offerings", "offering_id"),
            },
        )

        connection = sqlite3.connect(
            self.bundle.artifacts[FixtureVariant.NORMALIZED].database_path
        )
        try:
            schedule_count, distinct_offerings = connection.execute(
                "SELECT COUNT(*), COUNT(DISTINCT offering_id) FROM schedules"
            ).fetchone()
            offering_count = connection.execute(
                "SELECT COUNT(*) FROM course_offerings"
            ).fetchone()[0]
            schedule_indexes = connection.execute(
                "PRAGMA index_list(schedules)"
            ).fetchall()
            unique_index_columns = []
            for index in schedule_indexes:
                if not bool(index[2]):
                    continue
                index_name = str(index[1]).replace('"', '""')
                unique_index_columns.append(
                    [
                        column[2]
                        for column in connection.execute(
                            f'PRAGMA index_info("{index_name}")'
                        ).fetchall()
                    ]
                )
        finally:
            connection.close()
        self.assertEqual(schedule_count, offering_count)
        self.assertEqual(distinct_offerings, offering_count)
        self.assertIn(["offering_id"], unique_index_columns)

    def test_aligner_finds_all_expected_semantic_fields(self) -> None:
        for variant, artifact in self.bundle.artifacts.items():
            proposal = MappingProposal.model_validate_json(
                artifact.proposal_path.read_text(encoding="utf-8")
            )
            with self.subTest(variant=variant):
                for semantic_field, expected_column in EXPECTED_TOP_COLUMNS[variant].items():
                    candidate = proposal.candidates[semantic_field][0]
                    self.assertEqual(candidate.column, expected_column)
                    expected_table = (
                        NORMALIZED_TABLE_BY_ENTITY[semantic_field.split(".", 1)[0]]
                        if variant is FixtureVariant.NORMALIZED
                        else (
                            "course_offerings"
                            if variant is FixtureVariant.FLAT
                            else "lecture_info"
                        )
                    )
                    self.assertEqual(candidate.table, expected_table)
                    self.assertGreaterEqual(candidate.confidence, 0.9)
                self.assertEqual(proposal.unmapped_fields, ["Course.target_grade"])  # Phase 8-18: not stored

    def test_rebuild_is_repeatable(self) -> None:
        first_fingerprints = {
            variant: DatabaseCatalog.model_validate_json(
                artifact.catalog_path.read_text(encoding="utf-8")
            ).schema_fingerprint
            for variant, artifact in self.bundle.artifacts.items()
        }
        rebuilt = build_fixture_bundle(self.output, ontology=recorded_ontology())

        self.assertNotEqual(rebuilt.generation_id, self.bundle.generation_id)
        committed = type(rebuilt).model_validate_json(
            rebuilt.manifest_path.read_text(encoding="utf-8")
        )
        self.assertEqual(committed, rebuilt)

        for variant, artifact in rebuilt.artifacts.items():
            with self.subTest(variant=variant):
                catalog = DatabaseCatalog.model_validate_json(
                    artifact.catalog_path.read_text(encoding="utf-8")
                )
                self.assertEqual(
                    catalog.schema_fingerprint,
                    first_fingerprints[variant],
                )
                self.assertEqual(
                    self.read_rows(
                        artifact.database_path,
                        {
                            FixtureVariant.FLAT: FLAT_QUERY,
                            FixtureVariant.NORMALIZED: NORMALIZED_QUERY,
                            FixtureVariant.LEGACY: LEGACY_QUERY,
                        }[variant],
                    ),
                    DEFAULT_SEED.canonical_rows(),
                )


if __name__ == "__main__":
    unittest.main()
