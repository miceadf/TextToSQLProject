from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from backend.adaptive.catalog.contracts import DatabaseCatalog
from backend.adaptive.evaluation.__main__ import main as evaluation_cli_main
from backend.adaptive.evaluation.fixture_mapping import fixture_published_mapping
from backend.adaptive.evaluation.loader import load_evaluation_set
from backend.adaptive.evaluation.recorded_cli import (
    OFFLINE_REPLAY_WARNING,
    UNVERIFIED_PROVENANCE_WARNING,
)
from backend.adaptive.evaluation.run_contracts import EvaluationRunConfig
from backend.adaptive.evaluation.run_report import deterministic_request_id
from backend.adaptive.fixtures import FixtureVariant, build_fixture_bundle
from backend.adaptive.harness import (
    ModelRecording,
    RecordedFailure,
    RecordedFailureCode,
    RecordedSuccess,
    RecordingLoadError,
    mapping_fingerprint,
    question_fingerprint,
)
from backend.adaptive.mapping import to_harness_mapping


ROOT = Path(__file__).resolve().parents[2]
GOLD_PATH = ROOT / "backend/adaptive/evaluation/data/fixture_v1_cases.yaml"


class RecordedEvaluationCliTests(unittest.TestCase):
    def _write_recording(self, folder: Path) -> Path:
        gold = load_evaluation_set(GOLD_PATH)
        config = EvaluationRunConfig(
            model_run_id="cli-recorded-v1",
            model_label="synthetic-recording",
            variants=["flat"],
            oracle_smoke=False,
        )
        bundle = build_fixture_bundle(folder / "recording-context")
        artifact = bundle.artifacts[FixtureVariant.FLAT]
        catalog = DatabaseCatalog.model_validate_json(
            artifact.catalog_path.read_text(encoding="utf-8")
        )
        mapping = to_harness_mapping(
            fixture_published_mapping(FixtureVariant.FLAT, catalog)
        )
        records = []
        for index, case in enumerate(gold.cases[:3]):
            identity = {
                "request_id": deterministic_request_id(
                    config, gold, case.case_id, "flat"
                ),
                "database_id": artifact.database_id,
                "question_sha256": question_fingerprint(case.question_ko),
                "mapping_sha256": mapping_fingerprint(mapping),
            }
            if index == 0:
                records.append(RecordedSuccess(
                    **identity,
                    status="SUCCEEDED",
                    response=case.gold_query_ir.model_dump(mode="json"),
                ))
            elif index == 1:
                records.append(RecordedFailure(
                    **identity,
                    status="FAILED",
                    failure_code=RecordedFailureCode.TIMEOUT,
                ))
            else:
                records.append(RecordedSuccess(
                    **{
                        **identity,
                        "question_sha256": question_fingerprint("합성 불일치 질문"),
                    },
                    status="SUCCEEDED",
                    response=case.gold_query_ir.model_dump(mode="json"),
                ))
        path = folder / "private-recording.json"
        path.write_text(
            ModelRecording(
                model_run_id=config.model_run_id, records=records
            ).model_dump_json(indent=2),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _args(recording: Path, output: Path) -> list[str]:
        return [
            "run-recorded",
            "--recording", str(recording),
            "--gold", str(GOLD_PATH),
            "--variants", "flat",
            "--model-label", "synthetic-recording",
            "--output", str(output),
        ]

    def test_cli_replays_attempts_and_marks_unverified_offline_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            recording = self._write_recording(folder)
            output = folder / "report.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(evaluation_cli_main(self._args(recording, output)), 0)

            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["config"]["model_run_id"], "cli-recorded-v1")
            self.assertEqual(report["config"]["model_label"], "synthetic-recording")
            self.assertFalse(report["config"]["oracle_smoke"])
            self.assertEqual(report["totals"]["scheduled_attempts"], 40)
            self.assertEqual(report["totals"]["completed_attempts"], 40)
            self.assertEqual(report["attempts"][0]["semantic_correct"], True)
            self.assertEqual(report["attempts"][1]["failure_code"], "RECORDED_TIMEOUT")
            self.assertEqual(
                report["attempts"][2]["failure_code"], "RECORDED_QUESTION_MISMATCH"
            )
            self.assertEqual(
                report["attempts"][3]["failure_code"], "RECORDED_RESPONSE_MISSING"
            )
            self.assertIn(OFFLINE_REPLAY_WARNING, report["warnings"])
            self.assertIn(UNVERIFIED_PROVENANCE_WARNING, report["warnings"])
            rendered = stdout.getvalue()
            self.assertIn("offline replay", rendered.lower())
            self.assertIn("not independently verified", rendered.lower())
            self.assertIn("human review", rendered.lower())
            self.assertNotIn(str(recording.resolve()), rendered)
            self.assertNotIn("합성 불일치 질문", rendered)
            self.assertNotIn("question_ko", output.read_text(encoding="utf-8"))

            original = output.read_bytes()
            with self.assertRaises(FileExistsError):
                evaluation_cli_main(self._args(recording, output))
            self.assertEqual(output.read_bytes(), original)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    evaluation_cli_main([*self._args(recording, output), "--force"]),
                    0,
                )
            self.assertEqual(output.read_bytes(), original)

    def test_invalid_recording_fails_before_report_without_payload_leak(self) -> None:
        private = "PRIVATE_RECORDED_PAYLOAD"
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            recording = folder / "broken.json"
            recording.write_text("{malformed " + private, encoding="utf-8")
            output = folder / "report.json"
            with self.assertRaises(RecordingLoadError) as caught:
                evaluation_cli_main(self._args(recording, output))
            self.assertFalse(output.exists())
            self.assertNotIn(private, str(caught.exception))
            self.assertNotIn(str(recording.resolve()), str(caught.exception))

    def test_cli_rejects_direct_and_hardlink_path_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            recording = self._write_recording(folder)
            with self.assertRaisesRegex(ValueError, "different files"):
                evaluation_cli_main(self._args(recording, recording))
            with self.assertRaisesRegex(ValueError, "different files"):
                evaluation_cli_main(self._args(recording, GOLD_PATH))

            hardlink = folder / "recording-hardlink.json"
            os.link(recording, hardlink)
            with self.assertRaisesRegex(ValueError, "different files"):
                evaluation_cli_main(self._args(recording, hardlink))


if __name__ == "__main__":
    unittest.main()
