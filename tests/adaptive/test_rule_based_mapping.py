from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.adaptive.catalog import SQLiteAdapter
from backend.adaptive.mapping import (
    MappingReviewStatus,
    MappingSignal,
    RuleBasedSchemaAligner,
)
from backend.adaptive.ontology import load_default_ontology


class RuleBasedSchemaAlignerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.flat_path = Path(self.temp_dir.name) / "flat.sqlite3"
        self.abbreviated_path = Path(self.temp_dir.name) / "abbreviated.sqlite3"
        self._create_database(
            self.flat_path,
            """
            CREATE TABLE courses (
                subject_name TEXT NOT NULL,
                credit_hours REAL NOT NULL,
                category TEXT,
                dept_name TEXT
            );
            """,
        )
        self._create_database(
            self.abbreviated_path,
            """
            CREATE TABLE dept_mst (
                dept_cd TEXT PRIMARY KEY,
                dept_nm TEXT NOT NULL
            );
            CREATE TABLE sbjt_mst (
                sbjt_cd TEXT PRIMARY KEY,
                sbjt_nm TEXT NOT NULL,
                pnt INTEGER NOT NULL,
                isu_gb TEXT,
                dept_cd TEXT REFERENCES dept_mst(dept_cd)
            );
            """,
        )
        self.adapter = SQLiteAdapter(
            {"flat": self.flat_path, "abbreviated": self.abbreviated_path}
        )
        self.ontology = load_default_ontology()
        self.aligner = RuleBasedSchemaAligner()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _create_database(path: Path, ddl: str) -> None:
        connection = sqlite3.connect(path)
        try:
            connection.executescript(ddl)
            connection.commit()
        finally:
            connection.close()

    def test_loads_versioned_course_domain_ontology(self) -> None:
        self.assertEqual(self.ontology.version, 3)
        self.assertEqual(self.ontology.domain, "university-course")
        self.assertIn("Course.name", self.ontology.fields)
        self.assertIn("Department.name", self.ontology.fields)
        self.assertIn("Schedule.start_time", self.ontology.fields)

    def test_maps_flat_schema_with_confidence_and_evidence(self) -> None:
        proposal = self.aligner.align(self.adapter.inspect("flat"), self.ontology)

        course_name = proposal.candidates["Course.name"][0]
        self.assertEqual((course_name.table, course_name.column), ("courses", "subject_name"))
        self.assertEqual(course_name.confidence, 1.0)
        self.assertEqual(
            course_name.review_status,
            MappingReviewStatus.AUTO_APPROVE_CANDIDATE,
        )
        self.assertEqual(
            {evidence.signal for evidence in course_name.evidence},
            {
                MappingSignal.COLUMN_NAME,
                MappingSignal.DATA_TYPE,
                MappingSignal.TABLE_NAME,
            },
        )

        department_name = proposal.candidates["Department.name"][0]
        self.assertEqual(
            (department_name.table, department_name.column),
            ("courses", "dept_name"),
        )
        self.assertEqual(department_name.confidence, 0.9)

    def test_maps_abbreviated_multi_table_schema(self) -> None:
        proposal = self.aligner.align(
            self.adapter.inspect("abbreviated"),
            self.ontology,
        )

        expected = {
            "Course.code": ("sbjt_mst", "sbjt_cd"),
            "Course.name": ("sbjt_mst", "sbjt_nm"),
            "Course.credits": ("sbjt_mst", "pnt"),
            "Course.category": ("sbjt_mst", "isu_gb"),
            "Department.code": ("dept_mst", "dept_cd"),
            "Department.name": ("dept_mst", "dept_nm"),
        }
        for semantic_field, physical_field in expected.items():
            candidate = proposal.candidates[semantic_field][0]
            self.assertEqual((candidate.table, candidate.column), physical_field)
            self.assertEqual(
                candidate.review_status,
                MappingReviewStatus.AUTO_APPROVE_CANDIDATE,
            )

        self.assertIn("Instructor.name", proposal.unmapped_fields)
        self.assertEqual(proposal.schema_fingerprint, self.adapter.inspect("abbreviated").schema_fingerprint)

    def test_invalid_threshold_order_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RuleBasedSchemaAligner(
                auto_approve_threshold=0.8,
                review_threshold=0.9,
            )


if __name__ == "__main__":
    unittest.main()
