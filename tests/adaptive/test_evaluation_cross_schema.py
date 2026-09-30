from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from backend.adaptive.catalog import DatabaseCatalog
from backend.adaptive.compiler.contracts import JoinCompilationRequest
from backend.adaptive.compiler.sqlite import JoinCapableSQLiteCompiler
from backend.adaptive.evaluation.comparison import semantic_row_multiset
from backend.adaptive.evaluation.coverage import (
    build_coverage_report,
    evaluation_set_fingerprint,
    render_review_table,
)
from backend.adaptive.evaluation.fixture_mapping import fixture_published_mapping
from backend.adaptive.evaluation.loader import load_evaluation_set
from backend.adaptive.evaluation.__main__ import main as evaluation_cli_main
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
GOLD_PATH = ROOT / "backend/adaptive/evaluation/data/fixture_v1_cases.yaml"


class EvaluationCrossSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ontology = load_default_ontology()
        cls.gold = load_evaluation_set(GOLD_PATH, seed=DEFAULT_SEED, ontology=cls.ontology)
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

    def test_every_gold_ir_matches_explicit_semantic_rows_in_all_three_schemas(self) -> None:
        for case in self.gold.cases:
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
                    join_count = compiled.sql.upper().count(" JOIN ")
                    self.assertEqual(join_count, len(join_result.plan.steps))
                    if variant in (FixtureVariant.FLAT, FixtureVariant.LEGACY):
                        self.assertEqual(join_count, 0)
                    elif variant is FixtureVariant.NORMALIZED:
                        normalized_joins = join_count
                    elif variant is FixtureVariant.CODED:
                        # Same structure as normalized under abbreviated names.
                        self.assertEqual(join_count, normalized_joins)
                    else:
                        self.assertLessEqual(join_count, 1)

    def test_coverage_report_and_review_table_are_deterministic(self) -> None:
        _, graph, mapping = self.contexts[FixtureVariant.NORMALIZED]
        first = build_coverage_report(self.gold, self.ontology, graph, mapping)
        second = build_coverage_report(self.gold, self.ontology, graph, mapping)
        self.assertEqual(first.model_dump_json(), second.model_dump_json())
        self.assertEqual(first.gold_set_fingerprint, evaluation_set_fingerprint(self.gold))
        self.assertGreaterEqual(first.case_count, 30)
        self.assertLessEqual(first.case_count, 50)
        self.assertEqual(set(first.field_case_counts), set(self.ontology.fields_at(self.gold.ontology_version)))
        self.assertGreater(first.cardinality_bucket_counts.get("zero", 0), 0)
        self.assertGreater(first.cardinality_bucket_counts.get("one", 0), 0)
        self.assertGreater(first.cardinality_bucket_counts.get("multiple", 0), 0)
        self.assertTrue(any(int(step) >= 2 for step in first.normalized_join_step_counts))
        table = render_review_table(self.gold, first)
        self.assertEqual(table, render_review_table(self.gold, second))
        self.assertIn(self.gold.cases[0].case_id, table)
        self.assertIn("NEEDS_HUMAN_REVIEW", table)

    def test_multiset_keeps_duplicates_and_rejects_wrong_type(self) -> None:
        row = {"Course.name": "자료구조"}
        twice = semantic_row_multiset([row, row], ["Course.name"], self.ontology)
        once = semantic_row_multiset([row], ["Course.name"], self.ontology)
        self.assertNotEqual(twice, once)
        self.assertEqual(sum(twice.values()), 2)
        with self.assertRaises(ValueError):
            semantic_row_multiset(
                [{"CourseOffering.capacity": True}],
                ["CourseOffering.capacity"],
                self.ontology,
            )

    def test_inspection_cli_emits_stable_machine_and_review_artifacts(self) -> None:
        first = io.StringIO()
        second = io.StringIO()
        with redirect_stdout(first):
            self.assertEqual(evaluation_cli_main(["--format", "json"]), 0)
        with redirect_stdout(second):
            self.assertEqual(evaluation_cli_main(["--format", "json"]), 0)
        self.assertEqual(first.getvalue(), second.getvalue())
        self.assertEqual(json.loads(first.getvalue())["case_count"], 40)
        review = io.StringIO()
        with redirect_stdout(review):
            self.assertEqual(evaluation_cli_main(["--format", "review-table"]), 0)
        self.assertIn("| P2-001 |", review.getvalue())
        self.assertIn("NEEDS_HUMAN_REVIEW", review.getvalue())


if __name__ == "__main__":
    unittest.main()
