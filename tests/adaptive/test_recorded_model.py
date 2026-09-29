from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.adaptive.catalog.contracts import DatabaseCatalog
from backend.adaptive.evaluation.fixture_mapping import fixture_published_mapping
from backend.adaptive.evaluation.loader import load_evaluation_set
from backend.adaptive.evaluation.run_contracts import EvaluationRunConfig
from backend.adaptive.evaluation.run_report import deterministic_request_id
from backend.adaptive.evaluation.runner import run_evaluation
from backend.adaptive.fixtures import CORE_VARIANTS, FixtureVariant, build_fixture_bundle
from backend.adaptive.harness import (
    DataType,
    ExecutionHarness,
    FieldMapping,
    HarnessErrorCode,
    HarnessRequest,
    MappingProfile,
    MappingStatus,
    Ontology,
    OntologyField,
    Operator,
    QueryIR,
    SQLiteReadOnlyExecutor,
)
from backend.adaptive.harness.recorded import (
    ModelRecording,
    RecordedFailure,
    RecordedFailureCode,
    RecordedModel,
    RecordedModelReplayError,
    RecordedReplayErrorCode,
    RecordedSuccess,
    RecordingLoadError,
    load_model_recording,
    mapping_fingerprint,
    question_fingerprint,
)
from backend.adaptive.mapping import to_harness_mapping


ROOT = Path(__file__).resolve().parents[2]
GOLD_PATH = ROOT / "backend/adaptive/evaluation/data/fixture_v1_cases.yaml"


class RecordedModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ontology = Ontology(
            entities={"Course"},
            fields={
                "Course.name": OntologyField(
                    name="Course.name",
                    data_type=DataType.STRING,
                    allowed_operators={Operator.EQUALS},
                )
            },
        )
        self.mapping = MappingProfile(
            database_id="fixture-flat-v1",
            version=1,
            status=MappingStatus.PUBLISHED,
            fields={"Course.name": FieldMapping(table="courses", column="name")},
        )
        self.request = HarnessRequest(
            request_id="recorded-run:P2-001:flat",
            database_id=self.mapping.database_id,
            question="합성 질문",
        )

    def _success(self, response, **changes) -> RecordedSuccess:
        values = {
            "request_id": self.request.request_id,
            "database_id": self.request.database_id,
            "question_sha256": question_fingerprint(self.request.question),
            "mapping_sha256": mapping_fingerprint(self.mapping),
            "status": "SUCCEEDED",
            "response": response,
        }
        values.update(changes)
        return RecordedSuccess.model_validate(values)

    @staticmethod
    def _write(path: Path, recording: ModelRecording) -> bytes:
        content = recording.model_dump_json(indent=2).encode("utf-8")
        path.write_bytes(content)
        return content

    def test_loader_and_replay_preserve_json_types_and_source(self) -> None:
        response = {
            "entity": "Course",
            "select": ["Course.name"],
            "filters": [{"field": "Course.name", "operator": "equals", "value": "합성값"}],
            "limit": 7,
            "metadata": {"flag": True, "count": 2, "ratio": 1.5, "none": None},
        }
        recording = ModelRecording(model_run_id="recorded-run", records=[self._success(response)])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recording.json"
            original = self._write(path, recording)
            model = RecordedModel.from_path(path)
            first = model.generate_ir(self.request, self.ontology, self.mapping)
            first["metadata"]["count"] = 99
            second = model.generate_ir(self.request, self.ontology, self.mapping)
            self.assertEqual(second, response)
            self.assertIs(type(second["metadata"]["count"]), int)
            self.assertIs(type(second["metadata"]["flag"]), bool)
            self.assertEqual(path.read_bytes(), original)

    def test_context_mismatch_and_missing_record_have_safe_codes(self) -> None:
        model = RecordedModel(
            ModelRecording(model_run_id="recorded-run", records=[self._success({})])
        )
        scenarios = (
            (
                self.request.model_copy(update={"database_id": "other-db"}),
                self.mapping,
                RecordedReplayErrorCode.DATABASE_MISMATCH,
            ),
            (
                self.request.model_copy(update={"question": "다른 비공개 질문"}),
                self.mapping,
                RecordedReplayErrorCode.QUESTION_MISMATCH,
            ),
            (
                self.request,
                self.mapping.model_copy(update={"version": 2}),
                RecordedReplayErrorCode.MAPPING_MISMATCH,
            ),
            (
                self.request.model_copy(update={"request_id": "missing"}),
                self.mapping,
                RecordedReplayErrorCode.RESPONSE_MISSING,
            ),
        )
        for request, mapping, expected in scenarios:
            with self.subTest(code=expected.value):
                with self.assertRaises(RecordedModelReplayError) as caught:
                    model.generate_ir(request, self.ontology, mapping)
                self.assertIs(caught.exception.code, expected)
                self.assertEqual(str(caught.exception), expected.value)
                self.assertNotIn(request.question, str(caught.exception))

    def test_recorded_failures_have_controlled_codes(self) -> None:
        expected = {
            RecordedFailureCode.PROVIDER_ERROR: RecordedReplayErrorCode.PROVIDER_ERROR,
            RecordedFailureCode.TIMEOUT: RecordedReplayErrorCode.TIMEOUT,
            RecordedFailureCode.EMPTY_RESPONSE: RecordedReplayErrorCode.EMPTY_RESPONSE,
            RecordedFailureCode.CANCELLED: RecordedReplayErrorCode.CANCELLED,
        }
        for failure, replay in expected.items():
            with self.subTest(failure=failure.value):
                entry = RecordedFailure(
                    request_id=self.request.request_id,
                    database_id=self.request.database_id,
                    question_sha256=question_fingerprint(self.request.question),
                    mapping_sha256=mapping_fingerprint(self.mapping),
                    status="FAILED",
                    failure_code=failure,
                )
                model = RecordedModel(ModelRecording(model_run_id="recorded-run", records=[entry]))
                with self.assertRaises(RecordedModelReplayError) as caught:
                    model.generate_ir(self.request, self.ontology, self.mapping)
                self.assertIs(caught.exception.code, replay)

    def test_invalid_replayed_value_reaches_existing_query_ir_validation(self) -> None:
        private_value = "PRIVATE_RECORDED_RESPONSE"
        model = RecordedModel(
            ModelRecording(model_run_id="recorded-run", records=[self._success(private_value)])
        )
        result = ExecutionHarness(
            model=model,
            executor=SQLiteReadOnlyExecutor({}),
        ).run(self.request, self.ontology, self.mapping)
        self.assertEqual(result.error.code, HarnessErrorCode.MODEL_INVALID_OUTPUT)
        self.assertNotIn(private_value, result.error.message)

    def test_loader_rejects_malformed_or_ambiguous_json_without_payload_leak(self) -> None:
        private_value = "PRIVATE_QUESTION_OR_RESPONSE"
        valid = ModelRecording(model_run_id="recorded-run", records=[self._success({})])
        duplicate_record = json.loads(valid.model_dump_json())
        duplicate_record["records"].append(duplicate_record["records"][0])
        cases = (
            "{malformed " + private_value,
            valid.model_dump_json().replace("recorded-model-v1", "recorded-model-v99"),
            json.dumps(duplicate_record, ensure_ascii=False),
            valid.model_dump_json().replace('"model_run_id":"recorded-run"', '"model_run_id":7'),
            '{"schema_version":"recorded-model-v1","model_run_id":"x",'
            '"records":[],"model_run_id":"' + private_value + '"}',
            '{"schema_version":"recorded-model-v1","model_run_id":"x",'
            '"records":[],"extra":NaN}',
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recording.json"
            for index, content in enumerate(cases):
                with self.subTest(index=index):
                    path.write_text(content, encoding="utf-8")
                    with self.assertRaises(RecordingLoadError) as caught:
                        load_model_recording(path)
                    self.assertNotIn(private_value, str(caught.exception))
                    self.assertIsNone(caught.exception.__cause__)


class RecordedModelRunnerTests(unittest.TestCase):
    def test_small_recording_replays_across_all_fixture_variants(self) -> None:
        gold = load_evaluation_set(GOLD_PATH)
        case = gold.cases[0]
        with tempfile.TemporaryDirectory() as folder:
            bundle = build_fixture_bundle(Path(folder) / "fixtures")
            entries = []
            contexts = []
            for variant in CORE_VARIANTS:
                artifact = bundle.artifacts[variant]
                catalog = DatabaseCatalog.model_validate_json(
                    artifact.catalog_path.read_text(encoding="utf-8")
                )
                mapping = to_harness_mapping(fixture_published_mapping(variant, catalog))
                request = HarnessRequest(
                    request_id=f"small-replay:{case.case_id}:{variant.value}",
                    database_id=artifact.database_id,
                    question=case.question_ko,
                )
                entries.append(RecordedSuccess(
                    request_id=request.request_id,
                    database_id=request.database_id,
                    question_sha256=question_fingerprint(request.question),
                    mapping_sha256=mapping_fingerprint(mapping),
                    status="SUCCEEDED",
                    response=case.gold_query_ir.model_dump(mode="json"),
                ))
                contexts.append((request, mapping))
            model = RecordedModel(ModelRecording(model_run_id="small-replay", records=entries))
            replay_ontology = Ontology(entities=set(), fields={})
            for request, mapping in contexts:
                with self.subTest(database_id=request.database_id):
                    self.assertEqual(
                        QueryIR.model_validate(model.generate_ir(request, replay_ontology, mapping)),
                        case.gold_query_ir,
                    )

    def test_runner_preserves_recorded_failures_missing_attempts_and_privacy(self) -> None:
        gold = load_evaluation_set(GOLD_PATH)
        config = EvaluationRunConfig(
            model_run_id="synthetic-recorded-v1",
            model_label="synthetic-recorded",
            variants=["flat"],
            oracle_smoke=False,
        )
        private_value = "PRIVATE_RECORDED_RESPONSE"
        with tempfile.TemporaryDirectory() as folder:
            bundle = build_fixture_bundle(Path(folder) / "fixtures")
            artifact = bundle.artifacts[FixtureVariant.FLAT]
            catalog = DatabaseCatalog.model_validate_json(
                artifact.catalog_path.read_text(encoding="utf-8")
            )
            mapping = to_harness_mapping(fixture_published_mapping(FixtureVariant.FLAT, catalog))
            records = []
            for index, case in enumerate(gold.cases):
                request_id = deterministic_request_id(config, gold, case.case_id, "flat")
                identity = {
                    "request_id": request_id,
                    "database_id": artifact.database_id,
                    "question_sha256": question_fingerprint(case.question_ko),
                    "mapping_sha256": mapping_fingerprint(mapping),
                }
                if index == 0:
                    records.append(RecordedFailure(
                        **identity, status="FAILED", failure_code=RecordedFailureCode.TIMEOUT
                    ))
                elif index == 1:
                    continue
                elif index == 2:
                    records.append(RecordedSuccess(
                        **identity, status="SUCCEEDED", response=private_value
                    ))
                else:
                    records.append(RecordedSuccess(
                        **identity,
                        status="SUCCEEDED",
                        response=case.gold_query_ir.model_dump(mode="json"),
                    ))
            path = Path(folder) / "synthetic-recording.json"
            path.write_text(
                ModelRecording(
                    model_run_id=config.model_run_id, records=records
                ).model_dump_json(indent=2),
                encoding="utf-8",
            )
            report = run_evaluation(
                gold, RecordedModel.from_path(path), config, bundle
            )

        self.assertEqual(len(report.attempts), 40)
        self.assertEqual(report.totals.completed_attempts, 40)
        self.assertEqual(report.totals.execution_success.count, 37)
        self.assertEqual(report.attempts[0].failure_code, "RECORDED_TIMEOUT")
        self.assertEqual(report.attempts[1].failure_code, "RECORDED_RESPONSE_MISSING")
        self.assertEqual(report.attempts[2].failure_code, "MODEL_INVALID_OUTPUT")
        self.assertTrue(report.attempts[3].execution_success)
        self.assertNotIn(private_value, report.model_dump_json())
        self.assertFalse(report.config.oracle_smoke)


if __name__ == "__main__":
    unittest.main()
