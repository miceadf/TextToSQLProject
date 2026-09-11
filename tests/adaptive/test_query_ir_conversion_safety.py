from __future__ import annotations

import unittest
from pathlib import Path

from backend.adaptive.dataset.contracts import DatasetRecord
from backend.adaptive.dataset.conversion_contracts import (
    ConversionReasonCode,
    ConversionStatus,
)
from backend.adaptive.dataset.converter import convert_record
from backend.adaptive.dataset.source_profile import load_dataset_schema_profile
from backend.adaptive.ontology.loader import load_default_ontology


PROFILE_PATH = (
    Path(__file__).parents[2]
    / "backend"
    / "adaptive"
    / "dataset"
    / "profiles"
    / "cnu-courses-v1.yaml"
)


def _record(sql: str, *, line: int = 1) -> DatasetRecord:
    return DatasetRecord(
        source_line=line,
        instruction="synthetic instruction",
        question="synthetic question",
        sql=sql,
    )


class QueryIrConversionSafetyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ontology = load_default_ontology()
        cls.profile = load_dataset_schema_profile(PROFILE_PATH, cls.ontology)

    def convert(self, sql: str, *, line: int = 1):
        return convert_record(
            _record(sql, line=line),
            self.profile,
            self.ontology,
        )

    def assert_not_converted(
        self,
        sql: str,
        expected_reason: ConversionReasonCode,
    ) -> None:
        result = self.convert(sql)
        self.assertIn(
            result.status,
            (ConversionStatus.REVIEW_REQUIRED, ConversionStatus.UNSUPPORTED),
        )
        self.assertIn(expected_reason, result.reason_codes)
        self.assertIsNone(result.query_ir)
        self.assertIsNone(result.intent_signature)

    def test_unmapped_and_quoted_identifier_values_are_never_guessed(self) -> None:
        self.assert_not_converted(
            "SELECT lecture_time FROM courses LIMIT 10",
            ConversionReasonCode.UNMAPPED_SOURCE_COLUMN,
        )
        self.assert_not_converted(
            "SELECT class_mode FROM courses LIMIT 10",
            ConversionReasonCode.UNMAPPED_SOURCE_COLUMN,
        )
        result = self.convert(
            'SELECT subject_name FROM courses WHERE dept_name = "컴퓨터공학과" LIMIT 10'
        )
        self.assertEqual(result.status, ConversionStatus.REVIEW_REQUIRED)
        self.assertIn(
            ConversionReasonCode.AMBIGUOUS_QUOTED_IDENTIFIER,
            result.reason_codes,
        )
        self.assertIsNone(result.query_ir)
        self.assert_not_converted(
            'SELECT "컴퓨터공학과" FROM courses LIMIT 10',
            ConversionReasonCode.AMBIGUOUS_QUOTED_IDENTIFIER,
        )

    def test_like_acceptance_boundary_is_conservative(self) -> None:
        cases = (
            ("'database%'", ConversionReasonCode.UNSUPPORTED_LIKE_PATTERN),
            ("'%database'", ConversionReasonCode.UNSUPPORTED_LIKE_PATTERN),
            ("'database'", ConversionReasonCode.UNSUPPORTED_LIKE_PATTERN),
            ("'%data_base%'", ConversionReasonCode.UNSUPPORTED_LIKE_PATTERN),
            ("'%data%%base%'", ConversionReasonCode.UNSUPPORTED_LIKE_PATTERN),
            ("'%%'", ConversionReasonCode.EMPTY_CONTAINS_VALUE),
        )

        for pattern, reason in cases:
            with self.subTest(pattern=pattern):
                self.assert_not_converted(
                    "SELECT subject_name FROM courses "
                    f"WHERE subject_name LIKE {pattern} LIMIT 10",
                    reason,
                )

    def test_unsupported_projection_forms_do_not_silently_lose_intent(self) -> None:
        cases = (
            "SELECT * FROM courses LIMIT 10",
            "SELECT subject_name AS name FROM courses LIMIT 10",
            "SELECT LOWER(subject_name) FROM courses LIMIT 10",
            "SELECT COUNT(subject_name) FROM courses LIMIT 10",
            "SELECT capacity - enrolled FROM courses LIMIT 10",
        )

        for sql in cases:
            with self.subTest(sql=sql):
                result = self.convert(sql)
                self.assertIn(
                    result.status,
                    (ConversionStatus.REVIEW_REQUIRED, ConversionStatus.UNSUPPORTED),
                )
                self.assertIn(
                    ConversionReasonCode.ANALYZER_NOT_CURRENT_IR_CANDIDATE,
                    result.reason_codes,
                )
                self.assertIsNone(result.query_ir)

    def test_unsupported_boolean_and_predicate_forms_do_not_convert(self) -> None:
        cases = (
            "course_year = 2025 OR capacity > 10",
            "NOT course_year = 2025",
            "course_year IN (2024, 2025)",
            "course_year BETWEEN 2024 AND 2025",
            "professor IS NULL",
        )

        for predicate in cases:
            with self.subTest(predicate=predicate):
                result = self.convert(
                    "SELECT subject_name FROM courses WHERE "
                    f"{predicate} LIMIT 10"
                )
                self.assertIn(
                    ConversionReasonCode.ANALYZER_NOT_CURRENT_IR_CANDIDATE,
                    result.reason_codes,
                )
                self.assertIsNone(result.query_ir)

    def test_column_comparison_placeholder_cast_and_null_do_not_convert(self) -> None:
        cases = (
            "course_year = enrolled",
            "course_year = ?",
            "course_year = CAST('2025' AS INTEGER)",
            "course_year = NULL",
        )

        for predicate in cases:
            with self.subTest(predicate=predicate):
                result = self.convert(
                    "SELECT subject_name FROM courses WHERE "
                    f"{predicate} LIMIT 10"
                )
                self.assertNotIn(
                    result.status,
                    (
                        ConversionStatus.CONVERTED_EXACT,
                        ConversionStatus.CONVERTED_WITH_EXECUTION_POLICY,
                    ),
                )
                self.assertIsNone(result.query_ir)

    def test_invalid_limit_and_offset_do_not_convert(self) -> None:
        cases = (
            "SELECT subject_name FROM courses LIMIT 0",
            "SELECT subject_name FROM courses LIMIT 201",
            "SELECT subject_name FROM courses LIMIT 1.5",
            "SELECT subject_name FROM courses LIMIT ?",
            "SELECT subject_name FROM courses LIMIT 10 OFFSET 1",
        )

        for sql in cases:
            with self.subTest(sql=sql):
                result = self.convert(sql)
                self.assertIn(
                    ConversionReasonCode.ANALYZER_NOT_CURRENT_IR_CANDIDATE,
                    result.reason_codes,
                )
                self.assertIsNone(result.query_ir)

    def test_duplicate_semantic_projection_is_not_deduplicated(self) -> None:
        self.assert_not_converted(
            "SELECT subject_name, subject_name FROM courses LIMIT 10",
            ConversionReasonCode.DUPLICATE_SEMANTIC_PROJECTION,
        )

    def test_unknown_table_is_not_inferred_from_column_names(self) -> None:
        result = self.convert("SELECT subject_name FROM archived_courses LIMIT 10")

        self.assertIn(
            ConversionReasonCode.UNKNOWN_SOURCE_TABLE,
            result.reason_codes,
        )
        self.assertIsNone(result.query_ir)

    def test_lossy_decimal_is_not_coerced_to_float(self) -> None:
        result = self.convert(
            "SELECT subject_name FROM courses "
            "WHERE credit_hours = 0.12345678901234567890123456789 LIMIT 10"
        )

        self.assertIn(
            ConversionReasonCode.UNSAFE_LITERAL_COERCION,
            result.reason_codes,
        )
        self.assertIsNone(result.query_ir)

    def test_reason_codes_follow_enum_order_deterministically(self) -> None:
        sql = (
            "SELECT lecture_time, lecture_time FROM courses "
            'WHERE dept_name = "private-value" LIMIT 10'
        )

        results = [self.convert(sql) for _ in range(3)]
        for result in results:
            self.assertEqual(
                result.reason_codes,
                sorted(result.reason_codes, key=list(ConversionReasonCode).index),
            )
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])

    def test_parse_failure_is_structured_and_never_echoes_source(self) -> None:
        secret = "private-literal-92831"
        result = self.convert(f"SELECT '{secret}' FROM", line=29)

        self.assertEqual(result.source_line, 29)
        self.assertEqual(result.status, ConversionStatus.UNSUPPORTED)
        self.assertIn(ConversionReasonCode.CONVERSION_ERROR, result.reason_codes)
        self.assertIsNone(result.canonical_hash)
        self.assertIsNone(result.shape_hash)
        self.assertIsNone(result.query_ir)
        serialized = result.model_dump_json()
        self.assertNotIn(secret, serialized)
        self.assertNotIn("SELECT", serialized)

    def test_failure_does_not_prevent_following_record_conversion(self) -> None:
        first = self.convert("SELECT 'secret' FROM", line=7)
        second = self.convert(
            "SELECT subject_name FROM courses LIMIT 10",
            line=8,
        )

        self.assertEqual(first.status, ConversionStatus.UNSUPPORTED)
        self.assertEqual(second.source_line, 8)
        self.assertEqual(second.status, ConversionStatus.CONVERTED_EXACT)
        self.assertIsNotNone(second.query_ir)


if __name__ == "__main__":
    unittest.main()
