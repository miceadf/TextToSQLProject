from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from backend.adaptive.catalog import DatabaseCatalog
from backend.adaptive.compiler.contracts import JoinCompilationRequest
from backend.adaptive.compiler.sqlite import JoinCapableSQLiteCompiler
from backend.adaptive.fixtures import DEFAULT_SEED, FixtureVariant, build_fixture_bundle
from backend.adaptive.harness.contracts import FilterIR, QueryIR
from backend.adaptive.harness.executor import SQLiteReadOnlyExecutor
from backend.adaptive.mapping import PublishedFieldMapping, PublishedMappingProfile
from backend.adaptive.ontology import Operator
from backend.adaptive.policy import AstPolicyContext, AstReadOnlyPolicy
from backend.adaptive.relations.physical import build_physical_relation_graph
from backend.adaptive.relations.planner import plan_join_path


SELECT_FIELDS = [
    "Course.name",
    "Department.name",
    "Instructor.name",
    "Schedule.day",
    "CourseOffering.year",
]

PHYSICAL_FIELDS = {
    FixtureVariant.FLAT: {
        "Course.name": ("course_offerings", "course_name"),
        "Department.name": ("course_offerings", "department_name"),
        "Instructor.name": ("course_offerings", "instructor_name"),
        "Schedule.day": ("course_offerings", "day"),
        "CourseOffering.year": ("course_offerings", "year"),
    },
    FixtureVariant.NORMALIZED: {
        "Course.name": ("courses", "course_name"),
        "Department.name": ("departments", "department_name"),
        "Instructor.name": ("instructors", "instructor_name"),
        "Schedule.day": ("schedules", "day_of_week"),
        "CourseOffering.year": ("course_offerings", "academic_year"),
    },
    FixtureVariant.LEGACY: {
        "Course.name": ("lecture_info", "sbjt_nm"),
        "Department.name": ("lecture_info", "dept_nm"),
        "Instructor.name": ("lecture_info", "prof_nm"),
        "Schedule.day": ("lecture_info", "lecture_day"),
        "CourseOffering.year": ("lecture_info", "yyyy"),
    },
}


class JoinCompilerCrossSchemaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.bundle = build_fixture_bundle(Path(self.temp_dir.name) / "fixtures")
        self.executor = SQLiteReadOnlyExecutor(
            {
                artifact.database_id: artifact.database_path
                for artifact in self.bundle.artifacts.values()
            }
        )
        self.compiler = JoinCapableSQLiteCompiler()
        self.ast_policy = AstReadOnlyPolicy()
        self.query_ir = QueryIR(
            entity="CourseOffering",
            select=SELECT_FIELDS,
            filters=[
                FilterIR(
                    field="Department.name",
                    operator=Operator.EQUALS,
                    value="컴퓨터공학과",
                )
            ],
            limit=50,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_same_query_ir_returns_same_rows_for_all_fixture_schemas(self) -> None:
        rows_by_variant: dict[FixtureVariant, list[tuple[object, ...]]] = {}
        sql_by_variant: dict[FixtureVariant, str] = {}

        for variant, artifact in self.bundle.artifacts.items():
            with self.subTest(variant=variant.value):
                catalog = DatabaseCatalog.model_validate_json(
                    artifact.catalog_path.read_text(encoding="utf-8")
                )
                graph = build_physical_relation_graph(catalog)
                profile = self._published_profile(variant, catalog)
                join_result = plan_join_path(graph, profile, SELECT_FIELDS)
                compiled = self.compiler.compile(
                    JoinCompilationRequest(
                        query_ir=self.query_ir,
                        mapping=profile,
                        join_result=join_result,
                    )
                )
                self.ast_policy.validate(
                    AstPolicyContext(
                        query=compiled,
                        query_ir=self.query_ir,
                        mapping=profile,
                        join_result=join_result,
                    )
                )

                columns, rows = self.executor.execute(artifact.database_id, compiled)
                self.assertEqual(columns, SELECT_FIELDS)
                self.assertEqual(compiled.expected_columns, SELECT_FIELDS)
                self.assertEqual(compiled.parameters, ["컴퓨터공학과", 50])
                self.assertTrue(compiled.sql.endswith("LIMIT ?"))

                rows_by_variant[variant] = sorted(
                    tuple(row[field] for field in SELECT_FIELDS) for row in rows
                )
                sql_by_variant[variant] = compiled.sql

        expected_rows = sorted(
            (
                offering.course_name,
                offering.department_name,
                offering.instructor_name,
                offering.day,
                offering.year,
            )
            for offering in DEFAULT_SEED.offerings
            if offering.department_name == "컴퓨터공학과"
        )
        self.assertEqual(rows_by_variant[FixtureVariant.FLAT], expected_rows)
        self.assertEqual(rows_by_variant[FixtureVariant.NORMALIZED], expected_rows)
        self.assertEqual(rows_by_variant[FixtureVariant.LEGACY], expected_rows)

        self.assertNotIn(" JOIN ", sql_by_variant[FixtureVariant.FLAT].upper())
        self.assertNotIn(" JOIN ", sql_by_variant[FixtureVariant.LEGACY].upper())
        self.assertEqual(
            sql_by_variant[FixtureVariant.NORMALIZED].upper().count(" JOIN "),
            4,
        )

    @staticmethod
    def _published_profile(
        variant: FixtureVariant,
        catalog: DatabaseCatalog,
    ) -> PublishedMappingProfile:
        return PublishedMappingProfile(
            database_id=catalog.database_id,
            version=1,
            schema_fingerprint=catalog.schema_fingerprint,
            ontology_version=1,
            published_by="cross-schema-test",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            fields={
                semantic_field: PublishedFieldMapping(
                    table=table,
                    column=column,
                    reviewer="cross-schema-test",
                )
                for semantic_field, (table, column) in PHYSICAL_FIELDS[variant].items()
            },
        )


if __name__ == "__main__":
    unittest.main()
