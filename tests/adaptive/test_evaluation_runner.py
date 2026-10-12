"""End-to-end synthetic runner checks; oracle smoke is not model accuracy."""

from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from backend.adaptive.catalog.contracts import DatabaseCatalog
from backend.adaptive.evaluation.contracts import ReviewStatus
from backend.adaptive.evaluation.__main__ import main as evaluation_cli_main
from backend.adaptive.evaluation.fixture_mapping import fixture_published_mapping
from backend.adaptive.evaluation.loader import load_evaluation_set
from backend.adaptive.evaluation.run_contracts import ComparisonStatus, EvaluationRunConfig
from backend.adaptive.evaluation.run_report import deterministic_request_id
from backend.adaptive.evaluation.runner import run_evaluation
from backend.adaptive.fixtures import FixtureVariant, build_fixture_bundle
from backend.adaptive.harness.contracts import HarnessStage
from backend.adaptive.harness.contracts import HarnessErrorCode
from backend.adaptive.harness.errors import HarnessFailure
from backend.adaptive.harness.model import FakeModel
from backend.adaptive.ontology import load_default_ontology


ROOT = Path(__file__).resolve().parents[2]
GOLD_PATH = ROOT / "backend/adaptive/evaluation/data/fixture_v1_cases.yaml"


class _CapturingModel:
    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def generate_ir(self, request, ontology, mapping):
        self.requests.append((request, ontology, mapping))
        return self.responses[request.question]


class _VariantModel:
    def __init__(self, responses, target_question, divergent_ir):
        self.responses = responses
        self.target_question = target_question
        self.divergent_ir = divergent_ir

    def generate_ir(self, request, ontology, mapping):
        del ontology
        if request.question == self.target_question and mapping.database_id == "fixture-normalized-v1":
            return self.divergent_ir
        return self.responses[request.question]


class _ExceptionModel:
    def __init__(self, responses, target_question):
        self.responses = responses
        self.target_question = target_question

    def generate_ir(self, request, ontology, mapping):
        del ontology, mapping
        if request.question == self.target_question:
            raise RuntimeError("SENSITIVE_PROVIDER_RESPONSE_AND_LOCAL_PATH")
        return self.responses[request.question]


class EvaluationRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.bundle = build_fixture_bundle(Path(cls.temp.name) / "fixtures")
        cls.gold = load_evaluation_set(GOLD_PATH)
        cls.responses = {
            case.question_ko: case.gold_query_ir.model_dump(mode="json")
            for case in cls.gold.cases
        }

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    @staticmethod
    def _config(*, oracle=False, variants=None):
        return EvaluationRunConfig(
            model_run_id="phase5-test-v1",
            model_label="oracle-smoke" if oracle else "synthetic-faults",
            variants=variants or ["flat", "normalized", "legacy"],
            oracle_smoke=oracle,
        )

    def _run(self, model=None, *, oracle=False, variants=None):
        return run_evaluation(
            self.gold,
            model or FakeModel(self.responses),
            self._config(oracle=oracle, variants=variants),
            self.bundle,
            ontology=load_default_ontology().at_version(2),
        )

    def test_offline_runner_uses_gold_version_without_changing_historical_gold(self):
        report = run_evaluation(
            self.gold, FakeModel(self.responses), self._config(variants=["flat"]), self.bundle,
        )
        failed = [attempt for attempt in report.attempts if not attempt.execution_success]
        self.assertEqual(failed, [])
        self.assertEqual(report.totals.execution_success.count, 40)
        self.assertEqual({item.mapping_version for item in report.fixture_metadata}, {1})
        self.assertTrue(all(case.review_status is ReviewStatus.NEEDS_HUMAN_REVIEW for case in self.gold.cases))

    @staticmethod
    def _attempts(report, case_id):
        return [attempt for attempt in report.attempts if attempt.case_id == case_id]

    def test_oracle_smoke_executes_every_case_and_variant(self):
        report = self._run(oracle=True)
        self.assertEqual(report.schema_version, "fixture-evaluation-run-v1")
        self.assertTrue(report.config.oracle_smoke)
        self.assertTrue(report.gold_human_review_pending)
        self.assertTrue(all(case.review_status is ReviewStatus.NEEDS_HUMAN_REVIEW for case in self.gold.cases))
        self.assertEqual(len(report.attempts), 120)
        self.assertEqual(report.totals.scheduled_attempts, 120)
        self.assertEqual(report.totals.completed_attempts, 120)
        self.assertEqual(report.totals.execution_success.count, 120)
        self.assertEqual(report.totals.semantic_correct.count, 120)
        self.assertEqual(report.totals.structural_ir_exact.count, 120)
        self.assertEqual(report.cross_schema_consistent.count, 40)
        self.assertEqual(report.cross_schema_consistent.denominator, 40)
        self.assertEqual(
            [(item.case_id, item.variant) for item in report.attempts],
            [
                (case.case_id, variant)
                for case in self.gold.cases
                for variant in ("flat", "normalized", "legacy")
            ],
        )
        self.assertTrue(all(item.execution_success and item.semantic_correct for item in report.attempts))
        self.assertTrue(all(item.comparison_status is ComparisonStatus.COMPARABLE for item in report.attempts))
        self.assertTrue(any("oracle" in warning.lower() for warning in report.warnings))
        self.assertTrue(any("human" in warning.lower() for warning in report.warnings))

    def test_model_receives_question_and_runtime_context_only(self):
        model = _CapturingModel(self.responses)
        report = self._run(model, variants=["flat"])
        self.assertEqual(len(model.requests), 40)
        first_request, runtime_ontology, runtime_mapping = model.requests[0]
        self.assertEqual(first_request.question, self.gold.cases[0].question_ko)
        self.assertEqual(first_request.database_id, "fixture-flat-v1")
        self.assertTrue(runtime_ontology.fields)
        self.assertTrue(runtime_mapping.fields)
        self.assertFalse(hasattr(first_request, "gold_query_ir"))
        self.assertFalse(hasattr(first_request, "expected"))
        self.assertEqual(
            report.attempts[0].request_id,
            deterministic_request_id(
                self._config(variants=["flat"]), self.gold, "P2-001", "flat"
            ),
        )

    def test_structural_ir_is_separate_from_semantic_answer(self):
        case = self.gold.cases[0]
        changed = copy.deepcopy(self.responses)
        changed[case.question_ko]["select"].reverse()
        report = self._run(FakeModel(changed), variants=["flat"])
        attempt = self._attempts(report, case.case_id)[0]
        self.assertFalse(attempt.structural_ir_exact)
        self.assertTrue(attempt.execution_success)
        self.assertIs(attempt.comparison_status, ComparisonStatus.COMPARABLE)
        self.assertTrue(attempt.semantic_correct)

        changed = copy.deepcopy(self.responses)
        changed[case.question_ko]["limit"] = 1
        report = self._run(FakeModel(changed), variants=["flat"])
        attempt = self._attempts(report, case.case_id)[0]
        self.assertFalse(attempt.structural_ir_exact)
        self.assertTrue(attempt.execution_success)
        self.assertFalse(attempt.semantic_correct)
        self.assertEqual(attempt.failure_code, "SEMANTIC_MISMATCH")

    def test_projection_mismatch_is_not_semantic_failure_or_correct(self):
        case = self.gold.cases[0]
        changed = copy.deepcopy(self.responses)
        changed[case.question_ko]["select"] = ["Course.code"]
        report = self._run(FakeModel(changed), variants=["flat"])
        attempt = self._attempts(report, case.case_id)[0]
        self.assertFalse(attempt.structural_ir_exact)
        self.assertTrue(attempt.execution_success)
        self.assertIs(attempt.comparison_status, ComparisonStatus.NOT_COMPARABLE_PROJECTION)
        self.assertIsNone(attempt.semantic_correct)
        self.assertEqual(attempt.failure_code, "NOT_COMPARABLE_PROJECTION")
        self.assertEqual(report.totals.not_comparable.count, 1)
        self.assertEqual(report.totals.semantic_correct.denominator, 40)
        self.assertEqual(report.totals.semantic_correct_among_comparable.denominator, 39)

    def test_wrong_filter_value_and_typed_value_are_not_structural_matches(self):
        case = self.gold.cases[2]
        changed = copy.deepcopy(self.responses)
        changed[case.question_ko]["filters"][0]["value"] = "자료구조"
        report = self._run(FakeModel(changed), variants=["flat"])
        attempt = self._attempts(report, case.case_id)[0]
        self.assertFalse(attempt.structural_ir_exact)
        self.assertTrue(attempt.execution_success)
        self.assertFalse(attempt.semantic_correct)
        self.assertEqual(attempt.failure_code, "SEMANTIC_MISMATCH")

        numeric_case = next(
            item for item in self.gold.cases
            if any(isinstance(f.value, int) and not isinstance(f.value, bool) for f in item.gold_query_ir.filters)
        )
        changed = copy.deepcopy(self.responses)
        changed[numeric_case.question_ko]["filters"][0]["value"] = "2026"
        report = self._run(FakeModel(changed), variants=["flat"])
        attempt = self._attempts(report, numeric_case.case_id)[0]
        self.assertFalse(attempt.structural_ir_exact)
        self.assertFalse(attempt.execution_success)
        self.assertEqual(attempt.failure_code, "INVALID_VALUE_TYPE")

    def test_bad_model_output_and_exception_do_not_remove_attempts(self):
        target = self.gold.cases[0]
        broken = dict(self.responses)
        broken[target.question_ko] = {"not_query_ir": "SENSITIVE_PROVIDER_RESPONSE"}
        report = self._run(FakeModel(broken), variants=["flat"])
        first = report.attempts[0]
        self.assertFalse(first.execution_success)
        self.assertIs(first.comparison_status, ComparisonStatus.NOT_EXECUTED)
        self.assertIsNone(first.semantic_correct)
        self.assertEqual(first.failure_code, "MODEL_INVALID_OUTPUT")
        self.assertEqual(first.failure_stage, HarnessStage.IR_GENERATED)
        self.assertTrue(report.attempts[1].execution_success)
        self.assertEqual(report.totals.scheduled_attempts, 40)
        self.assertEqual(report.totals.completed_attempts, 40)
        self.assertEqual(report.totals.semantic_correct.denominator, 40)
        self.assertNotIn("SENSITIVE_PROVIDER_RESPONSE", report.model_dump_json())

        report = self._run(_ExceptionModel(self.responses, target.question_ko), variants=["flat"])
        self.assertEqual(report.attempts[0].failure_code, "MODEL_EXCEPTION")
        self.assertEqual(report.attempts[0].failure_stage, HarnessStage.IR_GENERATED)
        self.assertTrue(report.attempts[1].execution_success)
        self.assertNotIn("SENSITIVE_PROVIDER_RESPONSE_AND_LOCAL_PATH", report.model_dump_json())

    def test_nonfinite_model_numeric_output_is_rejected_without_aborting(self):
        numeric_case = next(
            item for item in self.gold.cases
            if any(isinstance(f.value, int) for f in item.gold_query_ir.filters)
        )
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=str(value)):
                changed = copy.deepcopy(self.responses)
                changed[numeric_case.question_ko]["filters"][0]["value"] = value
                report = self._run(FakeModel(changed), variants=["flat"])
                attempt = self._attempts(report, numeric_case.case_id)[0]
                self.assertFalse(attempt.execution_success)
                self.assertEqual(attempt.failure_code, "MODEL_NONFINITE_OUTPUT")
                self.assertEqual(attempt.failure_stage, HarnessStage.IR_GENERATED)
                self.assertEqual(report.totals.completed_attempts, 40)

    def test_zero_gold_does_not_treat_execution_failure_as_empty_correct(self):
        zero = next(case for case in self.gold.cases if case.expected.cardinality == 0)
        report = self._run(variants=["flat"])
        self.assertTrue(self._attempts(report, zero.case_id)[0].semantic_correct)
        broken = dict(self.responses)
        broken[zero.question_ko] = {}
        report = self._run(FakeModel(broken), variants=["flat"])
        attempt = self._attempts(report, zero.case_id)[0]
        self.assertFalse(attempt.execution_success)
        self.assertIsNone(attempt.semantic_correct)

    def test_variant_dependent_answer_is_reported_inconsistent(self):
        case = self.gold.cases[0]
        divergent_ir = copy.deepcopy(self.responses[case.question_ko])
        divergent_ir["limit"] = 1
        model = _VariantModel(self.responses, case.question_ko, divergent_ir)
        report = self._run(model)
        attempts = self._attempts(report, case.case_id)
        self.assertEqual([item.semantic_correct for item in attempts], [True, False, True])
        consistency = next(item for item in report.case_consistency if item.case_id == case.case_id)
        self.assertFalse(consistency.ir_consistent)
        self.assertFalse(consistency.result_consistent)
        self.assertFalse(consistency.cross_schema_consistent)
        self.assertEqual(consistency.reason_code, "CROSS_SCHEMA_INCONSISTENCY")
        self.assertEqual(report.cross_schema_consistent.count, 39)

    def test_two_variants_do_not_claim_three_schema_consistency(self):
        report = self._run(variants=["flat", "normalized"])
        self.assertEqual(report.cross_schema_consistent.denominator, 0)
        self.assertTrue(all(item.cross_schema_consistent is None for item in report.case_consistency))

    def test_report_core_is_stable_and_private_payload_free(self):
        first = self._run(oracle=True, variants=["flat"])
        second = self._run(oracle=True, variants=["flat"])
        self.assertEqual(first.model_dump_json(), second.model_dump_json())
        encoded = json.dumps(first.model_dump(mode="json"), ensure_ascii=False)
        self.assertNotIn(self.temp.name, encoded)
        self.assertNotIn("SELECT ", encoded)
        self.assertNotIn("elapsed_ms", encoded)
        self.assertNotIn("rows", encoded)
        self.assertNotIn("question_ko", encoded)
        self.assertNotIn("model_accuracy", encoded)
        self.assertEqual(len(first.fixture_metadata), 1)

    def test_scheduled_denominators_include_failures_and_tags(self):
        broken = dict(self.responses)
        broken[self.gold.cases[0].question_ko] = {}
        report = self._run(FakeModel(broken))
        self.assertEqual(report.totals.scheduled_attempts, 120)
        self.assertEqual(report.totals.completed_attempts, 120)
        self.assertEqual(report.totals.execution_success.count, 117)
        self.assertEqual(report.totals.execution_success.denominator, 120)
        self.assertEqual(report.totals.semantic_correct.count, 117)
        self.assertEqual(report.totals.semantic_correct.denominator, 120)
        self.assertEqual(sum(item.scheduled_attempts for item in report.by_variant.values()), 120)
        self.assertTrue(all(item.scheduled_attempts == 40 for item in report.by_variant.values()))
        self.assertEqual(report.totals.failure_by_stage_and_code["IR_GENERATED:MODEL_INVALID_OUTPUT"], 3)
        self.assertTrue(report.by_coverage_tag)

    def test_gold_and_fixture_drift_fail_before_any_model_call(self):
        model = _CapturingModel(self.responses)
        stale_gold = self.gold.model_copy(update={"seed_fingerprint": "0" * 64})
        with self.assertRaisesRegex(ValueError, "drift"):
            run_evaluation(stale_gold, model, self._config(), self.bundle)
        self.assertEqual(model.requests, [])
        stale_gold = self.gold.model_copy(update={"ontology_fingerprint": "0" * 64})
        with self.assertRaisesRegex(ValueError, "drift"):
            run_evaluation(stale_gold, model, self._config(), self.bundle)
        stale_gold = self.gold.model_copy(update={"query_ir_fingerprint": "0" * 64})
        with self.assertRaisesRegex(ValueError, "drift"):
            run_evaluation(stale_gold, model, self._config(), self.bundle)
        stale_bundle = self.bundle.model_copy(update={"seed_version": 99})
        with self.assertRaisesRegex(ValueError, "drift"):
            run_evaluation(self.gold, model, self._config(), stale_bundle)
        self.assertEqual(model.requests, [])

        artifact = self.bundle.artifacts[FixtureVariant.FLAT]
        catalog = DatabaseCatalog.model_validate_json(artifact.catalog_path.read_text(encoding="utf-8"))
        altered = fixture_published_mapping(FixtureVariant.FLAT, catalog).model_copy(update={"version": 2})
        with self.assertRaisesRegex(ValueError, "drift"):
            run_evaluation(
                self.gold, model, self._config(), self.bundle,
                published_mappings={FixtureVariant.FLAT: altered},
            )
        self.assertEqual(model.requests, [])

    def test_join_ast_and_executor_failures_keep_stage_codes(self):
        scenarios = (
            (
                "SchemaAdaptiveSQLiteCompiler", "compile",
                HarnessErrorCode.AMBIGUOUS_JOIN_PATH, HarnessStage.SQL_COMPILED,
            ),
            (
                "SchemaAdaptiveAstPolicy", "validate",
                HarnessErrorCode.UNSAFE_SQL, HarnessStage.SQL_VALIDATED,
            ),
            (
                "SQLiteReadOnlyExecutor", "execute",
                HarnessErrorCode.EXECUTION_ERROR, HarnessStage.EXECUTED,
            ),
        )
        for class_name, method_name, code, stage in scenarios:
            with self.subTest(stage=stage):
                with patch(f"backend.adaptive.evaluation.runner.{class_name}") as mocked_class:
                    getattr(mocked_class.return_value, method_name).side_effect = HarnessFailure(
                        code, "SENSITIVE_FAILURE_DETAILS", stage=stage
                    )
                    report = self._run(variants=["flat"])
                self.assertEqual(len(report.attempts), 40)
                self.assertEqual(report.totals.completed_attempts, 40)
                self.assertEqual(report.totals.execution_success.count, 0)
                self.assertTrue(all(attempt.failure_code == code.value for attempt in report.attempts))
                self.assertTrue(all(attempt.failure_stage is stage for attempt in report.attempts))
                self.assertTrue(all(attempt.semantic_correct is None for attempt in report.attempts))
                self.assertNotIn("SENSITIVE_FAILURE_DETAILS", report.model_dump_json())

    def test_catalog_fingerprint_drift_is_fatal_before_model_invocation(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle = build_fixture_bundle(Path(folder) / "fixtures")
            catalog_path = bundle.artifacts[FixtureVariant.FLAT].catalog_path
            stored_catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            stored_catalog["schema_fingerprint"] = "0" * 64
            catalog_path.write_text(
                json.dumps(stored_catalog, ensure_ascii=False), encoding="utf-8"
            )
            model = _CapturingModel(self.responses)
            with self.assertRaisesRegex(ValueError, "drift"):
                run_evaluation(self.gold, model, self._config(), bundle)
            self.assertEqual(model.requests, [])

    def test_catalog_metadata_tamper_with_unchanged_hash_is_fatal(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle = build_fixture_bundle(Path(folder) / "fixtures")
            catalog_path = bundle.artifacts[FixtureVariant.FLAT].catalog_path
            stored_catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            column = stored_catalog["tables"][0]["columns"][0]
            column["nullable"] = not column["nullable"]
            catalog_path.write_text(
                json.dumps(stored_catalog, ensure_ascii=False), encoding="utf-8"
            )
            model = _CapturingModel(self.responses)
            with self.assertRaisesRegex(ValueError, "drift"):
                run_evaluation(self.gold, model, self._config(), bundle)
            self.assertEqual(model.requests, [])

    def test_live_fixture_row_drift_is_fatal_before_model_invocation(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as folder:
            bundle = build_fixture_bundle(Path(folder) / "fixtures")
            database_path = bundle.artifacts[FixtureVariant.FLAT].database_path
            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    "UPDATE course_offerings SET capacity = capacity + 1 WHERE course_code = ?",
                    ("CSE101",),
                )
                connection.commit()
            finally:
                connection.close()
            model = _CapturingModel(self.responses)
            with self.assertRaisesRegex(ValueError, "seed content drift"):
                run_evaluation(self.gold, model, self._config(), bundle)
            self.assertEqual(model.requests, [])

    def test_unmapped_model_field_is_a_validation_failure(self):
        case = self.gold.cases[0]
        changed = copy.deepcopy(self.responses)
        changed[case.question_ko]["select"] = ["Course.unknown"]
        report = self._run(FakeModel(changed), variants=["flat"])
        first = report.attempts[0]
        self.assertFalse(first.execution_success)
        self.assertIs(first.comparison_status, ComparisonStatus.NOT_EXECUTED)
        self.assertEqual(first.failure_stage, HarnessStage.IR_VALIDATED)
        self.assertEqual(first.failure_code, "UNKNOWN_FIELD")
        self.assertTrue(report.attempts[1].execution_success)

    def test_cli_run_smoke_write_policy_and_inspection_regression(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "smoke.json"
            args = [
                "run-smoke", "--gold", str(GOLD_PATH), "--variants", "flat", "--output", str(output),
            ]
            summary = io.StringIO()
            with redirect_stdout(summary):
                self.assertEqual(evaluation_cli_main(args), 0)
            self.assertTrue(output.exists())
            original = output.read_bytes()
            payload = json.loads(original)
            self.assertTrue(payload["config"]["oracle_smoke"])
            self.assertEqual(payload["totals"]["completed_attempts"], 40)
            self.assertIn("oracle", summary.getvalue().lower())
            with self.assertRaises(FileExistsError):
                evaluation_cli_main(args)
            self.assertEqual(output.read_bytes(), original)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(evaluation_cli_main([*args, "--force"]), 0)
            self.assertEqual(output.read_bytes(), original)
            with self.assertRaisesRegex(ValueError, "different files"):
                evaluation_cli_main([
                    "run-smoke", "--gold", str(GOLD_PATH), "--output", str(GOLD_PATH),
                ])
            inspection = io.StringIO()
            with redirect_stdout(inspection):
                self.assertEqual(evaluation_cli_main(["--format", "json"]), 0)
            self.assertEqual(json.loads(inspection.getvalue())["case_count"], 40)
            self.assertNotIn("run-smoke", inspection.getvalue())


if __name__ == "__main__":
    unittest.main()
