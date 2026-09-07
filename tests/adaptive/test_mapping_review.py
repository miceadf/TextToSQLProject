from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from backend.adaptive.catalog import SQLiteAdapter
from backend.adaptive.harness import (
    ExecutionHarness,
    FakeModel,
    HarnessStatus,
    Ontology as RuntimeOntology,
    OntologyField as RuntimeOntologyField,
    SQLiteReadOnlyExecutor,
)
from backend.adaptive.mapping import (
    MappingPublishError,
    MappingRepository,
    MappingReviewService,
    MappingWorkflowError,
    ReviewDecision,
    RuleBasedSchemaAligner,
    to_harness_mapping,
)
from backend.adaptive.mapping.cli import main as mapping_cli
from backend.adaptive.ontology import (
    DataType,
    DomainOntology,
    EntityDefinition,
    OntologyField,
    Operator,
)


class MappingReviewWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database_path = self.root / "courses.sqlite3"
        connection = sqlite3.connect(self.database_path)
        try:
            connection.executescript(
                """
                CREATE TABLE courses (
                    course_code TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    category TEXT
                );
                INSERT INTO courses VALUES ('CS101', '데이터베이스', '전공');
                """
            )
            connection.commit()
        finally:
            connection.close()

        self.catalog = SQLiteAdapter({"review-db": self.database_path}).inspect("review-db")
        self.ontology = DomainOntology(
            version=1,
            domain="test-course",
            entities=[
                EntityDefinition(
                    name="Course",
                    label="과목",
                    description="테스트 과목",
                    table_name_hints=["courses"],
                    fields=[
                        OntologyField(
                            name="Course.code",
                            label="과목 코드",
                            description="과목 식별 코드",
                            data_type=DataType.STRING,
                            physical_name_hints=["course_code"],
                            allowed_operators={Operator.EQUALS},
                            required=True,
                        ),
                        OntologyField(
                            name="Course.name",
                            label="과목명",
                            description="과목 이름",
                            data_type=DataType.STRING,
                            physical_name_hints=["title"],
                            allowed_operators={Operator.EQUALS, Operator.CONTAINS},
                            required=True,
                        ),
                        OntologyField(
                            name="Course.category",
                            label="이수 구분",
                            description="과목 영역",
                            data_type=DataType.STRING,
                            physical_name_hints=["category"],
                            allowed_operators={Operator.EQUALS},
                        ),
                    ],
                )
            ],
        )
        self.proposal = RuleBasedSchemaAligner().align(self.catalog, self.ontology)
        self.service = MappingReviewService()
        self.repository = MappingRepository(self.root / "mapping-store")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def reviewed_draft(self):
        draft = self.service.create_draft(self.proposal)
        draft = self.service.approve(
            draft,
            self.proposal,
            "Course.code",
            reviewer="admin",
        )
        draft = self.service.remap(
            draft,
            "Course.name",
            table="courses",
            column="title",
            reviewer="admin",
            reason="관리자 확인",
        )
        return self.service.ignore(
            draft,
            "Course.category",
            reviewer="admin",
            reason="초기 서비스 범위 제외",
        )

    def test_repository_round_trip_and_versioning(self) -> None:
        draft = self.service.create_draft(self.proposal)
        self.repository.save_proposal(self.proposal)
        self.repository.save_draft(draft)

        self.assertEqual(self.repository.load_proposal("review-db"), self.proposal)
        self.assertEqual(self.repository.load_draft("review-db"), draft)
        self.assertEqual(self.repository.next_version("review-db"), 1)

        profile = self.service.publish(
            self.reviewed_draft(),
            self.catalog,
            self.ontology,
            version=1,
            published_by="admin",
        )
        version_path = self.repository.save_published(profile)
        self.assertEqual(version_path.name, "mapping-v0001.json")
        self.assertEqual(self.repository.load_published("review-db"), profile)
        self.assertEqual(self.repository.load_published("review-db", 1), profile)
        self.assertEqual(self.repository.next_version("review-db"), 2)

        with self.assertRaises(MappingWorkflowError):
            self.repository.save_published(profile)
        with self.assertRaises(MappingWorkflowError):
            self.repository.load_draft("../escape")

    def test_publish_creates_harness_compatible_profile(self) -> None:
        profile = self.service.publish(
            self.reviewed_draft(),
            self.catalog,
            self.ontology,
            version=1,
            published_by="admin",
        )
        runtime = to_harness_mapping(profile)

        self.assertEqual(runtime.status.value, "PUBLISHED")
        self.assertEqual(runtime.version, 1)
        self.assertEqual(runtime.fields["Course.code"].column, "course_code")
        self.assertEqual(runtime.fields["Course.name"].column, "title")
        self.assertNotIn("Course.category", runtime.fields)

        harness = ExecutionHarness(
            model=FakeModel(
                {
                    "과목명": {
                        "entity": "Course",
                        "select": ["Course.name"],
                        "filters": [],
                    }
                }
            ),
            executor=SQLiteReadOnlyExecutor({"review-db": self.database_path}),
        )
        runtime_ontology = RuntimeOntology(
            entities={"Course"},
            fields={
                "Course.name": RuntimeOntologyField(
                    name="Course.name",
                    data_type=DataType.STRING,
                    allowed_operators={Operator.EQUALS, Operator.CONTAINS},
                )
            },
        )
        result = harness.run(
            {"database_id": "review-db", "question": "과목명"},
            runtime_ontology,
            runtime,
        )
        self.assertEqual(result.status, HarnessStatus.SUCCEEDED)
        self.assertEqual(result.rows, [{"Course.name": "데이터베이스"}])

    def test_publish_blocks_pending_required_and_stale_mapping(self) -> None:
        pending = self.service.create_draft(self.proposal)
        with self.assertRaises(MappingPublishError) as raised:
            self.service.publish(
                pending,
                self.catalog,
                self.ontology,
                version=1,
                published_by="admin",
            )
        self.assertTrue(any("unreviewed field" in issue for issue in raised.exception.issues))

        rejected = self.service.approve(
            pending,
            self.proposal,
            "Course.name",
            reviewer="admin",
        )
        rejected = self.service.reject(
            rejected,
            "Course.code",
            reviewer="admin",
            reason="코드 의미 불명확",
        )
        rejected = self.service.ignore(
            rejected,
            "Course.category",
            reviewer="admin",
            reason="범위 제외",
        )
        with self.assertRaises(MappingPublishError) as raised:
            self.service.publish(
                rejected,
                self.catalog,
                self.ontology,
                version=1,
                published_by="admin",
            )
        self.assertIn("required field is not mapped: Course.code", raised.exception.issues)

        stale = self.reviewed_draft().model_copy(
            update={"schema_fingerprint": "0" * 64}
        )
        with self.assertRaises(MappingPublishError) as raised:
            self.service.publish(
                stale,
                self.catalog,
                self.ontology,
                version=1,
                published_by="admin",
            )
        self.assertTrue(any("fingerprint" in issue for issue in raised.exception.issues))

        incomplete_fields = dict(self.reviewed_draft().fields)
        incomplete_fields.pop("Course.category")
        incomplete = self.reviewed_draft().model_copy(update={"fields": incomplete_fields})
        with self.assertRaises(MappingPublishError) as raised:
            self.service.publish(
                incomplete,
                self.catalog,
                self.ontology,
                version=1,
                published_by="admin",
            )
        self.assertIn(
            "ontology field missing from draft: Course.category",
            raised.exception.issues,
        )

    def test_publish_blocks_missing_and_duplicate_physical_columns(self) -> None:
        missing = self.reviewed_draft()
        missing = self.service.remap(
            missing,
            "Course.name",
            table="courses",
            column="missing_column",
            reviewer="admin",
            reason="오류 경로",
        )
        with self.assertRaises(MappingPublishError) as raised:
            self.service.publish(
                missing,
                self.catalog,
                self.ontology,
                version=1,
                published_by="admin",
            )
        self.assertTrue(any("does not exist" in issue for issue in raised.exception.issues))

        duplicate = self.reviewed_draft()
        duplicate = self.service.remap(
            duplicate,
            "Course.name",
            table="courses",
            column="course_code",
            reviewer="admin",
            reason="중복 경로",
        )
        with self.assertRaises(MappingPublishError) as raised:
            self.service.publish(
                duplicate,
                self.catalog,
                self.ontology,
                version=1,
                published_by="admin",
            )
        self.assertTrue(any("mapped twice" in issue for issue in raised.exception.issues))

    def test_cli_init_review_and_publish(self) -> None:
        input_dir = self.root / "input"
        input_dir.mkdir()
        proposal_path = input_dir / "proposal.json"
        catalog_path = input_dir / "catalog.json"
        ontology_path = input_dir / "ontology.yaml"
        proposal_path.write_text(self.proposal.model_dump_json(indent=2), encoding="utf-8")
        catalog_path.write_text(self.catalog.model_dump_json(indent=2), encoding="utf-8")
        ontology_path.write_text(self.ontology.model_dump_json(indent=2), encoding="utf-8")
        store = self.root / "cli-store"

        commands = [
            ["--store", str(store), "init", "--proposal", str(proposal_path)],
            ["--store", str(store), "list", "--database-id", "review-db"],
            [
                "--store", str(store), "show", "--database-id", "review-db",
                "--field", "Course.name",
            ],
            [
                "--store", str(store), "approve", "--database-id", "review-db",
                "--field", "Course.code", "--reviewer", "admin",
            ],
            [
                "--store", str(store), "remap", "--database-id", "review-db",
                "--field", "Course.name", "--table", "courses", "--column", "title",
                "--reviewer", "admin", "--reason", "관리자 확인",
            ],
            [
                "--store", str(store), "ignore", "--database-id", "review-db",
                "--field", "Course.category", "--reviewer", "admin",
                "--reason", "범위 제외",
            ],
            [
                "--store", str(store), "publish", "--database-id", "review-db",
                "--catalog", str(catalog_path), "--ontology", str(ontology_path),
                "--reviewer", "admin",
            ],
        ]
        for command in commands:
            with self.subTest(command=command), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(mapping_cli(command), 0)

        published = MappingRepository(store).load_published("review-db")
        self.assertEqual(published.version, 1)
        self.assertEqual(published.fields["Course.name"].column, "title")

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(
                mapping_cli(
                    ["--store", str(store), "init", "--proposal", str(proposal_path)]
                ),
                2,
            )
        self.assertEqual(MappingRepository(store).load_published("review-db"), published)


if __name__ == "__main__":
    unittest.main()
