from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

import yaml

from backend.adaptive.dataset.contracts import DatasetRecord
from backend.adaptive.dataset.conversion_contracts import (
    ConversionReasonCode,
    ConversionStatus,
    DuplicateProvenance,
    LimitProvenance,
    SourceColumnMapping,
    SourceLiteralType,
)
from backend.adaptive.dataset.converter import convert_record
from backend.adaptive.dataset.source_profile import load_dataset_schema_profile
from backend.adaptive.ontology.contracts import DomainOntology, Operator
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


class QueryIrConversionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ontology = load_default_ontology()
        cls.profile = load_dataset_schema_profile(
            PROFILE_PATH,
            cls.ontology,
            expected_dialect="postgres",
        )

    def convert(
        self,
        sql: str,
        *,
        line: int = 1,
        duplicate_provenance: DuplicateProvenance | None = None,
        default_limit: int = 50,
    ):
        return convert_record(
            _record(sql, line=line),
            self.profile,
            self.ontology,
            duplicate_provenance=duplicate_provenance,
            default_limit=default_limit,
        )

    def test_loads_reviewed_source_profile_with_explicit_mappings(self) -> None:
        profile = self.profile

        self.assertEqual(profile.schema_version, "dataset-schema-profile-v1")
        self.assertEqual(profile.dialect, "postgres")
        self.assertEqual(profile.table_allowlist, ["courses"])
        self.assertEqual(profile.root_entity, "CourseOffering")
        self.assertEqual(profile.ontology_version, 1)  # Phase 8-18: still valid under version 2
        self.assertTrue(self.ontology.accepts_version(profile.ontology_version))
        self.assertEqual(
            profile.mappings_by_physical[("courses", "subject_name")].semantic_field,
            "Course.name",
        )
        self.assertNotIn(("courses", "lecture_time"), profile.mappings_by_physical)
        self.assertNotIn(("courses", "class_mode"), profile.mappings_by_physical)

    def test_profile_loader_rejects_version_dialect_root_and_mapping_errors(self) -> None:
        original = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
        mutations = (
            ("schema version", lambda value: value.__setitem__("schema_version", "v99")),
            ("dialect", lambda value: value.__setitem__("dialect", "sqlite")),
            ("ontology version", lambda value: value.__setitem__("ontology_version", 999)),
            ("root entity", lambda value: value.__setitem__("root_entity", "Unknown")),
            (
                "unknown semantic field",
                lambda value: value["mappings"][0].__setitem__(
                    "semantic_field", "Course.unknown"
                ),
            ),
            (
                "unknown source table",
                lambda value: value["mappings"][0].__setitem__("table", "other"),
            ),
        )

        for label, mutate in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                candidate = copy.deepcopy(original)
                mutate(candidate)
                path = Path(tmp) / "profile.yaml"
                path.write_text(
                    yaml.safe_dump(candidate, allow_unicode=True, sort_keys=False),
                    encoding="utf-8",
                )
                with self.assertRaises(ValueError):
                    load_dataset_schema_profile(
                        path,
                        self.ontology,
                        expected_dialect="postgres",
                    )

    def test_profile_contract_rejects_duplicate_physical_and_semantic_mappings(self) -> None:
        original = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
        variants = []
        duplicate_physical = copy.deepcopy(original)
        duplicate_physical["mappings"].append(copy.deepcopy(duplicate_physical["mappings"][0]))
        variants.append(duplicate_physical)
        duplicate_semantic = copy.deepcopy(original)
        duplicate = copy.deepcopy(duplicate_semantic["mappings"][0])
        duplicate["column"] = "another_column"
        duplicate_semantic["mappings"].append(duplicate)
        duplicate_semantic["mappings"].sort(key=lambda item: (item["table"], item["column"]))
        variants.append(duplicate_semantic)

        for index, candidate in enumerate(variants):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "profile.yaml"
                path.write_text(
                    yaml.safe_dump(candidate, allow_unicode=True, sort_keys=False),
                    encoding="utf-8",
                )
                with self.assertRaises(ValueError):
                    load_dataset_schema_profile(path, self.ontology)

    def test_projection_order_and_profile_root_entity_are_preserved(self) -> None:
        result = self.convert(
            "SELECT subject_name, professor, dept_name FROM courses LIMIT 12"
        )

        self.assertEqual(result.status, ConversionStatus.CONVERTED_EXACT)
        self.assertEqual(result.reason_codes, [])
        self.assertIsNotNone(result.query_ir)
        assert result.query_ir is not None
        self.assertEqual(result.query_ir.entity, "CourseOffering")
        self.assertEqual(
            result.query_ir.select,
            ["Course.name", "Instructor.name", "Department.name"],
        )
        self.assertEqual(result.query_ir.limit, 12)
        self.assertEqual(
            result.intent_signature.limit_provenance,
            LimitProvenance.SOURCE,
        )

    def test_and_predicate_and_supported_operator_order_are_preserved(self) -> None:
        result = self.convert(
            "SELECT subject_name FROM courses WHERE "
            "course_year = 2025 AND enrolled < 29 "
            "AND enrolled <= 28 AND credit_hours > 2 AND credit_hours >= 3 "
            "LIMIT 20"
        )

        self.assertEqual(result.status, ConversionStatus.CONVERTED_EXACT)
        assert result.query_ir is not None
        self.assertEqual(
            [item.operator for item in result.query_ir.filters],
            [
                Operator.EQUALS,
                Operator.LESS_THAN,
                Operator.LESS_THAN_OR_EQUAL,
                Operator.GREATER_THAN,
                Operator.GREATER_THAN_OR_EQUAL,
            ],
        )
        self.assertEqual(
            [item.field for item in result.query_ir.filters],
            [
                "CourseOffering.year",
                "CourseOffering.enrolled",
                "CourseOffering.enrolled",
                "Course.credits",
                "Course.credits",
            ],
        )

    def test_not_equals_is_parsed_but_blocked_by_field_operator_allowlist(self) -> None:
        result = self.convert(
            "SELECT subject_name FROM courses WHERE capacity != 30 LIMIT 20"
        )

        self.assertEqual(result.status, ConversionStatus.REVIEW_REQUIRED)
        self.assertEqual(
            result.reason_codes,
            [ConversionReasonCode.OPERATOR_NOT_ALLOWED_FOR_FIELD],
        )
        self.assertIsNone(result.query_ir)

    def test_string_integer_decimal_and_boolean_literals_remain_typed(self) -> None:
        result = self.convert(
            "SELECT subject_name FROM courses WHERE subject_code = 'CS101' "
            "AND course_year = 2025 AND credit_hours = 3.5 LIMIT 10"
        )

        self.assertEqual(result.status, ConversionStatus.CONVERTED_EXACT)
        assert result.query_ir is not None
        values = [item.value for item in result.query_ir.filters]
        self.assertEqual(values, ["CS101", 2025, 3.5])
        self.assertIs(type(values[0]), str)
        self.assertIs(type(values[1]), int)
        self.assertIs(type(values[2]), float)

        ontology_data = self.ontology.model_dump(mode="json")
        course = next(
            entity for entity in ontology_data["entities"] if entity["name"] == "Course"
        )
        course["fields"].append(
            {
                "name": "Course.active",
                "label": "active",
                "description": "synthetic boolean field",
                "data_type": "boolean",
                "aliases": [],
                "physical_name_hints": ["active"],
                "allowed_operators": ["equals"],
                "required": False,
            }
        )
        boolean_ontology = DomainOntology.model_validate(ontology_data)
        profile_data = self.profile.model_dump(mode="json")
        profile_data["mappings"].append(
            SourceColumnMapping(
                table="courses",
                column="active",
                semantic_field="Course.active",
                allowed_literal_types=[SourceLiteralType.BOOLEAN],
                evidence="synthetic test mapping",
            ).model_dump(mode="json")
        )
        profile_data["mappings"].sort(
            key=lambda item: (item["table"], item["column"])
        )
        boolean_profile = type(self.profile).model_validate(profile_data)
        boolean_result = convert_record(
            _record(
                "SELECT subject_name FROM courses WHERE active = TRUE LIMIT 10"
            ),
            boolean_profile,
            boolean_ontology,
        )
        self.assertEqual(boolean_result.status, ConversionStatus.CONVERTED_EXACT)
        assert boolean_result.query_ir is not None
        self.assertIs(boolean_result.query_ir.filters[0].value, True)

        mismatch = self.convert(
            "SELECT subject_name FROM courses WHERE course_year = TRUE LIMIT 10"
        )
        self.assertIn(
            ConversionReasonCode.LITERAL_TYPE_MISMATCH,
            mismatch.reason_codes,
        )
        self.assertIsNone(mismatch.query_ir)

    def test_negative_and_exact_decimal_literals_preserve_sign_and_precision(self) -> None:
        result = self.convert(
            "SELECT subject_name FROM courses WHERE course_year >= -1 "
            "AND credit_hours = -3.5 LIMIT 10"
        )

        self.assertEqual(result.status, ConversionStatus.CONVERTED_EXACT)
        assert result.query_ir is not None
        self.assertEqual([item.value for item in result.query_ir.filters], [-1, -3.5])

    def test_exact_contains_like_maps_to_contains(self) -> None:
        result = self.convert(
            "SELECT subject_name FROM courses "
            "WHERE subject_name LIKE '%database%' LIMIT 10"
        )

        self.assertEqual(result.status, ConversionStatus.CONVERTED_EXACT)
        assert result.query_ir is not None
        self.assertEqual(len(result.query_ir.filters), 1)
        self.assertEqual(result.query_ir.filters[0].operator, Operator.CONTAINS)
        self.assertEqual(result.query_ir.filters[0].value, "database")

    def test_source_limit_and_execution_default_have_distinct_provenance(self) -> None:
        explicit = self.convert("SELECT subject_name FROM courses LIMIT 17")
        defaulted = self.convert(
            "SELECT subject_name FROM courses",
            default_limit=73,
        )

        self.assertEqual(explicit.status, ConversionStatus.CONVERTED_EXACT)
        self.assertEqual(explicit.query_ir.limit, 17)
        self.assertEqual(
            explicit.intent_signature.limit_provenance,
            LimitProvenance.SOURCE,
        )
        self.assertEqual(
            defaulted.status,
            ConversionStatus.CONVERTED_WITH_EXECUTION_POLICY,
        )
        self.assertEqual(defaulted.query_ir.limit, 73)
        self.assertEqual(
            defaulted.reason_codes,
            [ConversionReasonCode.DEFAULT_LIMIT_APPLIED],
        )
        self.assertEqual(
            defaulted.intent_signature.limit_provenance,
            LimitProvenance.EXECUTION_POLICY_DEFAULT,
        )

    def test_intent_signature_matches_converted_query_ir(self) -> None:
        result = self.convert(
            "SELECT subject_code, subject_name FROM courses "
            "WHERE course_year = 2025 AND category LIKE '%major%' LIMIT 8"
        )

        self.assertEqual(result.status, ConversionStatus.CONVERTED_EXACT)
        assert result.query_ir is not None
        signature = result.intent_signature
        self.assertIsNotNone(signature)
        assert signature is not None
        self.assertEqual(signature.root_entity, result.query_ir.entity)
        self.assertEqual(signature.select, result.query_ir.select)
        self.assertEqual(
            [(item.field, item.operator, item.value) for item in signature.filters],
            [
                (item.field, item.operator, item.value)
                for item in result.query_ir.filters
            ],
        )
        self.assertEqual(signature.limit, result.query_ir.limit)

    def test_duplicate_provenance_is_preserved_and_conflict_blocks_conversion(self) -> None:
        provenance = DuplicateProvenance(
            exact_duplicate_group_id="exact-1",
            question_sql_duplicate_group_id="same-1",
            question_conflict_group_id="conflict-1",
        )
        result = self.convert(
            "SELECT subject_name FROM courses LIMIT 10",
            duplicate_provenance=provenance,
        )

        self.assertEqual(result.duplicate_provenance, provenance)
        self.assertEqual(result.status, ConversionStatus.REVIEW_REQUIRED)
        self.assertEqual(
            result.reason_codes,
            [ConversionReasonCode.QUESTION_SQL_CONFLICT],
        )
        self.assertIsNone(result.query_ir)
        self.assertIsNone(result.intent_signature)
        default_limited_conflict = self.convert(
            "SELECT subject_name FROM courses",
            duplicate_provenance=provenance,
        )
        self.assertEqual(
            default_limited_conflict.reason_codes,
            [ConversionReasonCode.QUESTION_SQL_CONFLICT],
        )

        noncandidate = self.convert(
            "SELECT COUNT(*) FROM courses",
            duplicate_provenance=provenance,
        )
        self.assertEqual(noncandidate.status, ConversionStatus.REVIEW_REQUIRED)
        self.assertEqual(
            noncandidate.reason_codes,
            [
                ConversionReasonCode.ANALYZER_NOT_CURRENT_IR_CANDIDATE,
                ConversionReasonCode.QUESTION_SQL_CONFLICT,
            ],
        )
        unmapped = self.convert(
            "SELECT lecture_time FROM courses LIMIT 10",
            duplicate_provenance=provenance,
        )
        self.assertEqual(unmapped.status, ConversionStatus.REVIEW_REQUIRED)
        self.assertEqual(
            unmapped.reason_codes,
            [
                ConversionReasonCode.QUESTION_SQL_CONFLICT,
                ConversionReasonCode.UNMAPPED_SOURCE_COLUMN,
            ],
        )

    def test_table_alias_hides_the_base_table_qualifier(self) -> None:
        valid = self.convert(
            "SELECT c.subject_name FROM courses AS c LIMIT 10"
        )
        invalid = self.convert(
            "SELECT courses.subject_name FROM courses AS c LIMIT 10"
        )

        self.assertEqual(valid.status, ConversionStatus.CONVERTED_EXACT)
        self.assertEqual(invalid.status, ConversionStatus.UNSUPPORTED)
        self.assertIn(
            ConversionReasonCode.UNKNOWN_SOURCE_TABLE,
            invalid.reason_codes,
        )


if __name__ == "__main__":
    unittest.main()
