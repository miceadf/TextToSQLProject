from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.adaptive.catalog import DatabaseCatalog
from backend.adaptive.compiler.contracts import JoinCompilationRequest
from backend.adaptive.compiler.sqlite import JoinCapableSQLiteCompiler
from backend.adaptive.evaluation.comparison import semantic_row_multiset
from backend.adaptive.evaluation.contracts import ReviewStatus
from backend.adaptive.evaluation.fixture_mapping import fixture_published_mapping
from backend.adaptive.evaluation.isolation import check_set_isolation
from backend.adaptive.evaluation.loader import load_evaluation_set
from backend.adaptive.fixtures import (
    DEFAULT_SEED,
    EXTENDED_VARIANTS,
    FixtureVariant,
    build_fixture_bundle,
)
from backend.adaptive.harness.executor import SQLiteReadOnlyExecutor
from backend.adaptive.ontology import load_default_ontology
from backend.adaptive.policy import AstPolicyContext, AstReadOnlyPolicy
from backend.adaptive.relations.contracts import JoinPlanStatus
from backend.adaptive.relations.physical import build_physical_relation_graph
from backend.adaptive.relations.planner import plan_join_path


ROOT = Path(__file__).resolve().parents[2]
DEV_PATH = ROOT / "backend/adaptive/evaluation/data/fixture_v1_cases.yaml"
DRAFT_PATH = ROOT / "backend/adaptive/evaluation/data/fixture_v1_independent_cases.yaml"


class IndependentSetDraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ontology = load_default_ontology()
        cls.dev = load_evaluation_set(DEV_PATH, seed=DEFAULT_SEED, ontology=cls.ontology)
        # Full 30-to-50 case contract applies; no require_full bypass.
        cls.draft = load_evaluation_set(DRAFT_PATH, seed=DEFAULT_SEED, ontology=cls.ontology)
        cls.temp = tempfile.TemporaryDirectory()
        cls.bundle = build_fixture_bundle(
            Path(cls.temp.name) / "fixture", variants=EXTENDED_VARIANTS
        )
        cls.executor = SQLiteReadOnlyExecutor({
            artifact.database_id: artifact.database_path
            for artifact in cls.bundle.artifacts.values()
        })
        cls.compiler = JoinCapableSQLiteCompiler()
        cls.policy = AstReadOnlyPolicy()
        cls.contexts = {}
        for variant, artifact in cls.bundle.artifacts.items():
            catalog = DatabaseCatalog.model_validate_json(
                artifact.catalog_path.read_text(encoding="utf-8")
            )
            graph = build_physical_relation_graph(catalog)
            mapping = fixture_published_mapping(variant, catalog)
            cls.contexts[variant] = (artifact.database_id, graph, mapping)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_draft_has_thirty_unapproved_cases_with_fresh_ids(self) -> None:
        self.assertEqual(len(self.draft.cases), 30)
        self.assertEqual(
            [case.case_id for case in self.draft.cases],
            [f"P2-{number:03d}" for number in range(41, 71)],
        )
        self.assertNotEqual(self.draft.benchmark_id, self.dev.benchmark_id)
        for case in self.draft.cases:
            with self.subTest(case=case.case_id):
                self.assertIs(case.review_status, ReviewStatus.NEEDS_HUMAN_REVIEW)
                self.assertIsNone(case.reviewer_id)

    def test_draft_covers_zero_single_and_multiple_results(self) -> None:
        buckets = {case.expected.cardinality for case in self.draft.cases}
        self.assertIn(0, buckets)
        self.assertIn(1, buckets)
        self.assertTrue(any(count > 1 for count in buckets))
        filter_counts = {len(case.gold_query_ir.filters) for case in self.draft.cases}
        self.assertIn(1, filter_counts)
        self.assertTrue(any(count > 1 for count in filter_counts))

    def test_draft_is_isolated_from_development_set(self) -> None:
        check_set_isolation(self.draft, self.dev)

    def test_every_draft_ir_matches_explicit_semantic_rows_in_all_three_schemas(self) -> None:
        for case in self.draft.cases:
            expected = semantic_row_multiset(
                case.expected.rows, case.gold_query_ir.select, self.ontology
            )
            for variant in FixtureVariant:
                with self.subTest(case=case.case_id, variant=variant.value):
                    database_id, graph, mapping = self.contexts[variant]
                    semantic_fields = [
                        *case.gold_query_ir.select,
                        *(item.field for item in case.gold_query_ir.filters),
                    ]
                    join_result = plan_join_path(graph, mapping, semantic_fields)
                    self.assertIs(join_result.status, JoinPlanStatus.RESOLVED)
                    self.assertIsNotNone(join_result.plan)
                    compiled = self.compiler.compile(JoinCompilationRequest(
                        query_ir=case.gold_query_ir,
                        mapping=mapping,
                        join_result=join_result,
                    ))
                    self.policy.validate(AstPolicyContext(
                        query=compiled,
                        query_ir=case.gold_query_ir,
                        mapping=mapping,
                        join_result=join_result,
                    ))
                    columns, actual_rows = self.executor.execute(database_id, compiled)
                    self.assertEqual(columns, case.gold_query_ir.select)
                    self.assertEqual(
                        semantic_row_multiset(actual_rows, columns, self.ontology), expected
                    )
                    self.assertEqual(len(actual_rows), case.expected.cardinality)


class SetIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        ontology = load_default_ontology()
        cls.dev = load_evaluation_set(DEV_PATH, seed=DEFAULT_SEED, ontology=ontology)
        cls.draft = load_evaluation_set(DRAFT_PATH, seed=DEFAULT_SEED, ontology=ontology)

    def test_same_benchmark_is_rejected(self) -> None:
        same = self.draft.model_copy(update={"benchmark_id": self.dev.benchmark_id})
        with self.assertRaisesRegex(ValueError, "ISOLATION_SAME_BENCHMARK"):
            check_set_isolation(same, self.dev)

    def test_case_id_overlap_is_rejected(self) -> None:
        cases = list(self.draft.cases)
        cases[0] = cases[0].model_copy(update={"case_id": "P2-001"})
        altered = self.draft.model_copy(update={"cases": cases})
        with self.assertRaisesRegex(ValueError, "ISOLATION_CASE_ID_OVERLAP"):
            check_set_isolation(altered, self.dev)

    def test_normalized_question_overlap_is_rejected(self) -> None:
        cases = list(self.draft.cases)
        noisy = "  " + "  ".join(self.dev.cases[0].question_ko.split()) + " "
        cases[0] = cases[0].model_copy(update={"question_ko": noisy})
        altered = self.draft.model_copy(update={"cases": cases})
        with self.assertRaisesRegex(ValueError, "ISOLATION_QUESTION_OVERLAP"):
            check_set_isolation(altered, self.dev)

    def test_paraphrase_family_reuse_is_rejected(self) -> None:
        dev_cases = list(self.dev.cases)
        dev_cases[0] = dev_cases[0].model_copy(
            update={"paraphrase_family": "shared-meaning"}
        )
        dev = self.dev.model_copy(update={"cases": dev_cases})
        draft_cases = list(self.draft.cases)
        draft_cases[0] = draft_cases[0].model_copy(
            update={"paraphrase_family": "shared-meaning"}
        )
        draft = self.draft.model_copy(update={"cases": draft_cases})
        with self.assertRaisesRegex(ValueError, "ISOLATION_PARAPHRASE_FAMILY_REUSE"):
            check_set_isolation(draft, dev)

    def test_intent_overlap_is_rejected(self) -> None:
        cases = list(self.draft.cases)
        cases[0] = cases[0].model_copy(
            update={"gold_query_ir": self.dev.cases[0].gold_query_ir}
        )
        altered = self.draft.model_copy(update={"cases": cases})
        with self.assertRaisesRegex(ValueError, "ISOLATION_INTENT_OVERLAP"):
            check_set_isolation(altered, self.dev)

    def test_error_codes_carry_no_original_text(self) -> None:
        cases = list(self.draft.cases)
        cases[0] = cases[0].model_copy(update={"case_id": "P2-001"})
        altered = self.draft.model_copy(update={"cases": cases})
        try:
            check_set_isolation(altered, self.dev)
        except ValueError as error:
            message = str(error)
        else:
            self.fail("expected isolation failure")
        self.assertEqual(message, "ISOLATION_CASE_ID_OVERLAP")
        self.assertNotIn("P2-001", message)


if __name__ == "__main__":
    unittest.main()
