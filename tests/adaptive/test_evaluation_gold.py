from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from backend.adaptive.evaluation.contracts import EvaluationSet
from backend.adaptive.evaluation.loader import evaluation_set_bytes, load_evaluation_set
from backend.adaptive.evaluation.reference import (
    offering_key,
    ontology_fingerprint,
    query_ir_fingerprint,
    seed_fingerprint,
    semantic_row,
)
from backend.adaptive.evaluation.validator import validate_evaluation_set
from backend.adaptive.fixtures.seed import DEFAULT_SEED
from backend.adaptive.ontology.loader import load_default_ontology


ONTOLOGY = load_default_ontology()


def _gold_document() -> dict:
    offerings = sorted(
        (item for item in DEFAULT_SEED.offerings if item.course_code == "CSE201"),
        key=lambda item: item.offering_key,
    )
    fields = ["Course.name", "CourseOffering.section"]
    return {
        "schema_version": "fixture-evaluation-set-v1",
        "benchmark_id": "fixture-v1",
        "version": 1,
        "seed_version": DEFAULT_SEED.version,
        "seed_fingerprint": seed_fingerprint(DEFAULT_SEED),
        "ontology_version": ONTOLOGY.version,
        "ontology_fingerprint": ontology_fingerprint(ONTOLOGY),
        "query_ir_fingerprint": query_ir_fingerprint(),
        "cases": [
            {
                "case_id": "P2-001",
                "question_ko": "자료구조 강좌의 이름과 분반을 보여줘",
                "gold_query_ir": {
                    "entity": "CourseOffering",
                    "select": fields,
                    "filters": [
                        {"field": "Course.code", "operator": "equals", "value": "CSE201"}
                    ],
                    "limit": 50,
                },
                "expected": {
                    "offering_keys": [offering_key(item).model_dump(mode="json") for item in offerings],
                    "rows": [semantic_row(item, fields) for item in offerings],
                    "cardinality": 2,
                    "answerability": "MATCHES",
                },
                "intent_family": "section-by-course",
                "review_status": "NEEDS_HUMAN_REVIEW",
                "review_rationale": "Synthetic seed rows inspected manually.",
                "author_id": "repo-author",
            }
        ],
    }


def _validated(raw: dict) -> EvaluationSet:
    item = EvaluationSet.model_validate(raw)
    validate_evaluation_set(item, DEFAULT_SEED, ONTOLOGY, require_full=False)
    return item


class GoldValidationTests(unittest.TestCase):
    def test_valid_two_sections_and_duplicate_projection_values(self) -> None:
        gold = _validated(_gold_document())
        self.assertEqual(gold.cases[0].expected.cardinality, 2)
        self.assertEqual(
            [row["Course.name"] for row in gold.cases[0].expected.rows],
            ["자료구조", "자료구조"],
        )

    def test_seed_and_ontology_drift(self) -> None:
        for property_name, value in (("seed_version", 2), ("seed_fingerprint", "0" * 64), ("ontology_version", ONTOLOGY.version + 1), ("ontology_version", 1), ("ontology_fingerprint", "0" * 64), ("query_ir_fingerprint", "0" * 64)):
            with self.subTest(property_name=property_name):
                raw = _gold_document()
                raw[property_name] = value
                with self.assertRaisesRegex(ValueError, "drift"):
                    _validated(raw)

    def test_wrong_offering_key_or_wrong_row_is_rejected(self) -> None:
        raw = _gold_document()
        raw["cases"][0]["expected"]["offering_keys"][1]["section"] = "99"
        with self.assertRaises(ValueError):
            _validated(raw)

    def test_empty_result_and_answerability(self) -> None:
        raw = _gold_document()
        raw["cases"][0]["gold_query_ir"]["filters"][0]["value"] = "NO-SUCH-COURSE"
        raw["cases"][0]["expected"] = {
            "offering_keys": [], "rows": [], "cardinality": 0, "answerability": "NO_MATCH"
        }
        _validated(raw)
        raw["cases"][0]["expected"]["answerability"] = "MATCHES"
        with self.assertRaises(ValueError):
            _validated(raw)

    def test_cardinality_and_duplicate_offering_key_rejected(self) -> None:
        raw = _gold_document()
        raw["cases"][0]["expected"]["cardinality"] = 1
        with self.assertRaises(ValueError):
            _validated(raw)
        raw = _gold_document()
        keys = raw["cases"][0]["expected"]["offering_keys"]
        keys[1] = copy.deepcopy(keys[0])
        with self.assertRaises(ValueError):
            _validated(raw)
        raw = _gold_document()
        raw["cases"][0]["expected"]["rows"][0]["Course.name"] = "오답"
        with self.assertRaises(ValueError):
            _validated(raw)

    def test_bad_shape_and_type_rejected(self) -> None:
        raw = _gold_document()
        raw["cases"][0]["expected"]["rows"][0]["CourseOffering.capacity"] = 35
        with self.assertRaisesRegex(ValueError, "projection shape"):
            _validated(raw)
        raw = _gold_document()
        raw["cases"][0]["expected"]["rows"][0]["CourseOffering.section"] = True
        with self.assertRaises(ValueError):
            _validated(raw)

    def test_unknown_field_operator_and_filter_value_type(self) -> None:
        for field, operator, value in (
            ("Course.unknown", "equals", "x"),
            ("Course.code", "greater_than", "CSE201"),
            ("Course.code", "equals", True),
        ):
            with self.subTest(field=field, operator=operator):
                raw = _gold_document()
                raw["cases"][0]["gold_query_ir"]["filters"][0] = {
                    "field": field, "operator": operator, "value": value
                }
                with self.assertRaises(ValueError):
                    _validated(raw)

    def test_bool_is_not_numeric_filter(self) -> None:
        raw = _gold_document()
        raw["cases"][0]["gold_query_ir"]["filters"][0] = {
            "field": "CourseOffering.capacity", "operator": "equals", "value": True
        }
        with self.assertRaisesRegex(ValueError, "type mismatch"):
            _validated(raw)

    def test_duplicate_question_and_intent_policy(self) -> None:
        raw = _gold_document()
        second = copy.deepcopy(raw["cases"][0])
        second["case_id"] = "P2-002"
        raw["cases"].append(second)
        with self.assertRaisesRegex(ValueError, "duplicate question"):
            _validated(raw)
        second["question_ko"] = "자료구조의 분반별 강좌 이름 알려줘"
        with self.assertRaisesRegex(ValueError, "duplicate intent"):
            _validated(raw)
        for item in raw["cases"]:
            item["paraphrase_family"] = "same-meaning"
        _validated(raw)

    def test_reordered_and_filters_do_not_create_new_intent(self) -> None:
        raw = _gold_document()
        case = raw["cases"][0]
        case["gold_query_ir"]["filters"].append({
            "field": "CourseOffering.section", "operator": "equals", "value": "01"
        })
        offering = next(item for item in DEFAULT_SEED.offerings if item.course_code == "CSE201" and item.section == "01")
        fields = case["gold_query_ir"]["select"]
        case["expected"] = {
            "offering_keys": [offering_key(offering).model_dump(mode="json")],
            "rows": [semantic_row(offering, fields)],
            "cardinality": 1,
            "answerability": "MATCHES",
        }
        second = copy.deepcopy(case)
        second["case_id"] = "P2-002"
        second["question_ko"] = "자료구조 01분반 강의의 과목명과 분반을 다시 보여줘."
        second["intent_family"] = "different-family"
        second["gold_query_ir"]["filters"].reverse()
        raw["cases"].append(second)
        with self.assertRaisesRegex(ValueError, "duplicate intent"):
            _validated(raw)

    def test_physical_name_and_self_review_rejected(self) -> None:
        raw = _gold_document()
        raw["cases"][0]["question_ko"] = "course_offerings 에서 자료구조를 보여줘"
        with self.assertRaisesRegex(ValueError, "physical schema"):
            _validated(raw)
        raw = _gold_document()
        raw["cases"][0]["question_ko"] = "course_name 컬럼의 과목명과 분반을 알려줘."
        with self.assertRaisesRegex(ValueError, "physical schema"):
            _validated(raw)
        raw = _gold_document()
        raw["cases"][0]["review_status"] = "HUMAN_APPROVED"
        raw["cases"][0]["reviewer_id"] = "repo-author"
        with self.assertRaisesRegex(ValueError, "self-approve"):
            _validated(raw)

    def test_whitespace_question_and_physical_literal_rejected(self) -> None:
        raw = _gold_document()
        raw["cases"][0]["question_ko"] = "  자료구조 강좌 보여줘 "
        with self.assertRaisesRegex(ValueError, "trimmed Korean"):
            _validated(raw)
        raw = _gold_document()
        raw["cases"][0]["gold_query_ir"]["filters"][0]["value"] = "sbjt_nm"
        with self.assertRaisesRegex(ValueError, "physical schema"):
            _validated(raw)

    def test_full_set_size_and_deterministic_core(self) -> None:
        raw = _gold_document()
        gold = EvaluationSet.model_validate(raw)
        with self.assertRaisesRegex(ValueError, "30 to 50"):
            validate_evaluation_set(gold, DEFAULT_SEED, ONTOLOGY)
        self.assertEqual(evaluation_set_bytes(gold), evaluation_set_bytes(_validated(copy.deepcopy(raw))))

    def test_loader_rejects_implicit_limit_and_extra_ir_key(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "gold.yaml"
            raw = _gold_document()
            del raw["cases"][0]["gold_query_ir"]["limit"]
            target.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "limit must be explicit"):
                load_evaluation_set(target, require_full=False)
            raw = _gold_document()
            raw["cases"][0]["gold_query_ir"]["sql"] = "SELECT *"
            target.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsupported gold_query_ir"):
                load_evaluation_set(target, require_full=False)

    def test_loader_yaml_json_and_duplicate_keys(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            raw = _gold_document()
            yaml_file = Path(folder) / "gold.yaml"
            json_file = Path(folder) / "gold.json"
            yaml_file.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
            json_file.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(
                evaluation_set_bytes(load_evaluation_set(yaml_file, require_full=False)),
                evaluation_set_bytes(load_evaluation_set(json_file, require_full=False)),
            )
            yaml_file.write_text("benchmark_id: one\nbenchmark_id: two\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate evaluation object key"):
                load_evaluation_set(yaml_file, require_full=False)


if __name__ == "__main__":
    unittest.main()
