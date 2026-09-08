from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from backend.adaptive.catalog import DatabaseCatalog
from backend.adaptive.compiler import SchemaAdaptiveSQLiteCompiler
from backend.adaptive.fixtures import FixtureVariant, build_fixture_bundle
from backend.adaptive.harness import (
    DataType,
    ExecutionHarness,
    FakeModel,
    HarnessErrorCode,
    HarnessStatus,
    Ontology,
    OntologyField,
    Operator,
    SQLiteReadOnlyExecutor,
)
from backend.adaptive.mapping import (
    PublishedFieldMapping,
    PublishedMappingProfile,
    to_harness_mapping,
)
from backend.adaptive.policy import SchemaAdaptiveAstPolicy
from backend.adaptive.relations import build_physical_relation_graph


class SchemaAdaptiveHarnessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        bundle = build_fixture_bundle(Path(self.temp_dir.name) / "fixtures")
        self.artifact = bundle.artifacts[FixtureVariant.NORMALIZED]
        catalog = DatabaseCatalog.model_validate_json(
            self.artifact.catalog_path.read_text(encoding="utf-8")
        )
        self.graph = build_physical_relation_graph(catalog)
        self.published = PublishedMappingProfile(
            database_id=catalog.database_id,
            version=1,
            schema_fingerprint=catalog.schema_fingerprint,
            ontology_version=1,
            published_by="test-admin",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            fields={
                "Course.name": PublishedFieldMapping(
                    table="courses",
                    column="course_name",
                    reviewer="test-admin",
                ),
                "CourseOffering.year": PublishedFieldMapping(
                    table="course_offerings",
                    column="academic_year",
                    reviewer="test-admin",
                ),
                "Instructor.name": PublishedFieldMapping(
                    table="instructors",
                    column="instructor_name",
                    reviewer="test-admin",
                ),
                "Schedule.day": PublishedFieldMapping(
                    table="schedules",
                    column="day_of_week",
                    reviewer="test-admin",
                ),
            },
        )
        self.runtime_mapping = to_harness_mapping(self.published)
        self.ontology = Ontology(
            entities={"Course"},
            fields={
                "Course.name": OntologyField(
                    name="Course.name",
                    data_type=DataType.STRING,
                    allowed_operators={Operator.EQUALS},
                ),
                "CourseOffering.year": OntologyField(
                    name="CourseOffering.year",
                    data_type=DataType.INTEGER,
                    allowed_operators={Operator.EQUALS},
                ),
                "Instructor.name": OntologyField(
                    name="Instructor.name",
                    data_type=DataType.STRING,
                    allowed_operators={Operator.EQUALS},
                ),
                "Schedule.day": OntologyField(
                    name="Schedule.day",
                    data_type=DataType.STRING,
                    allowed_operators={Operator.EQUALS},
                ),
            },
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_existing_harness_executes_resolved_multi_table_query(self) -> None:
        response = {
            "entity": "Course",
            "select": ["Course.name", "Instructor.name", "Schedule.day"],
            "filters": [
                {"field": "Course.name", "operator": "equals", "value": "데이터베이스"},
                {"field": "CourseOffering.year", "operator": "equals", "value": 2026},
            ],
            "limit": 10,
        }
        harness = ExecutionHarness(
            model=FakeModel({"질문": response}),
            compiler=SchemaAdaptiveSQLiteCompiler(self.graph, self.published),
            sql_policy=SchemaAdaptiveAstPolicy(self.graph, self.published),
            executor=SQLiteReadOnlyExecutor(
                {self.artifact.database_id: self.artifact.database_path}
            ),
        )

        result = harness.run(
            {"database_id": self.artifact.database_id, "question": "질문"},
            self.ontology,
            self.runtime_mapping,
        )

        self.assertEqual(result.status, HarnessStatus.SUCCEEDED)
        self.assertEqual((result.sql or "").count(" JOIN "), 3)
        self.assertEqual(
            result.rows,
            [
                {
                    "Course.name": "데이터베이스",
                    "Instructor.name": "교원A",
                    "Schedule.day": "THU",
                }
            ],
        )

    def test_runtime_mapping_drift_is_rejected_before_execution(self) -> None:
        fields = dict(self.runtime_mapping.fields)
        fields["Course.name"] = fields["Course.name"].model_copy(
            update={"column": "unexpected_column"}
        )
        drifted_mapping = self.runtime_mapping.model_copy(update={"fields": fields})
        harness = ExecutionHarness(
            model=FakeModel(
                {
                    "질문": {
                        "entity": "Course",
                        "select": ["Course.name"],
                        "filters": [],
                    }
                }
            ),
            compiler=SchemaAdaptiveSQLiteCompiler(self.graph, self.published),
            executor=SQLiteReadOnlyExecutor(
                {self.artifact.database_id: self.artifact.database_path}
            ),
        )

        result = harness.run(
            {"database_id": self.artifact.database_id, "question": "질문"},
            self.ontology,
            drifted_mapping,
        )

        self.assertEqual(result.status.value, "FAILED")
        self.assertEqual(result.error.code, HarnessErrorCode.INVALID_JOIN_PLAN)
        self.assertIsNone(result.sql)


if __name__ == "__main__":
    unittest.main()
