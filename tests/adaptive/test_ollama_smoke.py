from __future__ import annotations

import contextlib
import io
import json
import unittest
from unittest.mock import patch

from backend.adaptive.harness.ollama import OllamaQueryModel
from backend.adaptive.harness.ollama_smoke import main, run_smoke


class SequenceTransport:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, dict[str, object], float]] = []

    def __call__(self, url: str, payload: dict[str, object], timeout: float) -> object:
        self.calls.append((url, payload, timeout))
        outcome = self.outcomes[len(self.calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class OllamaSmokeTests(unittest.TestCase):
    def model(self, transport: SequenceTransport) -> OllamaQueryModel:
        return OllamaQueryModel(
            "http://127.0.0.1:11434", "synthetic-model:exact", 5,
            transport=transport,
        )

    def test_no_live_flag_never_constructs_model(self) -> None:
        output = io.StringIO()
        with patch("backend.adaptive.harness.ollama_smoke.OllamaQueryModel") as model:
            with contextlib.redirect_stdout(output):
                code = main([
                    "--base-url", "http://127.0.0.1:11434",
                    "--model-tag", "synthetic-model:exact", "--timeout", "5",
                ])
            model.assert_not_called()
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue()), {"status": "NOT_RUN"})

    def test_synthetic_cases_classify_without_leaking_output(self) -> None:
        private = "PRIVATE_MODEL_OUTPUT_C_PATH_HTTP_BODY"
        responses = [
            {"response": f"SELECT '{private}'"},
            {"response": '{"entity":"Course","select":["Course.unknown"]}'},
        ]
        transport = SequenceTransport(responses)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cases = run_smoke(self.model(transport))
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual([case["status"] for case in cases], ["NON_JSON_TEXT", "INVALID_SEMANTICS"])
        self.assertEqual(cases[1]["semantic_validation_code"], "UNKNOWN_FIELD")
        self.assertNotIn(private, json.dumps(cases))
        self.assertEqual([case["case_id"] for case in cases], ["synthetic-01", "synthetic-02"])
        for _, payload, timeout in transport.calls:
            self.assertEqual(timeout, 5)
            self.assertFalse(payload["stream"])
            self.assertNotIn("synthetic_courses", payload["prompt"])

    def test_json_string_and_null_are_not_mislabeled_non_json(self) -> None:
        transport = SequenceTransport([{"response": '"safe"'}, {"response": "null"}])
        cases = run_smoke(self.model(transport))
        self.assertEqual([case["json_parsed"] for case in cases], [True, True])
        self.assertEqual([case["json_type"] for case in cases], ["string", "null"])
        self.assertEqual([case["status"] for case in cases], ["INVALID_QUERY_IR"] * 2)
        self.assertEqual(len(transport.calls), 2)

    def test_valid_and_bad_operator_are_distinguished(self) -> None:
        good = '{"entity":"Course","select":["Course.name"],"filters":[]}'
        bad = '{"entity":"Course","select":["Course.name"],"filters":[{"field":"Course.name","operator":"greater_than","value":"x"}]}'
        transport = SequenceTransport([{"response": good}, {"response": bad}])
        cases = run_smoke(self.model(transport))
        self.assertEqual([case["status"] for case in cases], ["VALID_QUERY_IR", "INVALID_SEMANTICS"])
        self.assertEqual(cases[1]["semantic_validation_code"], "INVALID_OPERATOR")

    def test_provider_failure_is_fixed_code_and_stops_remaining_cases(self) -> None:
        secret = "PRIVATE_TIMEOUT_BODY_PATH"
        transport = SequenceTransport([TimeoutError(secret), {"response": "null"}])
        cases = run_smoke(self.model(transport))
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(cases[0]["status"], "PROVIDER_FAILURE")
        self.assertEqual(cases[0]["failure_code"], "TIMEOUT")
        self.assertNotIn(secret, json.dumps(cases))

    def test_live_main_prints_only_safe_summary(self) -> None:
        secret = "PRIVATE_SQL_RESPONSE"
        transport = SequenceTransport([
            {"response": f"SELECT '{secret}'"},
            {"response": '{"entity":"Course","select":["Course.name"]}'},
        ])
        output = io.StringIO()
        with patch("backend.adaptive.harness.ollama_smoke.OllamaQueryModel", return_value=self.model(transport)):
            with contextlib.redirect_stdout(output):
                code = main([
                    "--live", "--base-url", "http://127.0.0.1:11434",
                    "--model-tag", "synthetic-model:exact", "--timeout", "5",
                ])
        self.assertEqual(code, 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["status"], "COMPLETED")
        self.assertEqual(len(transport.calls), 2)
        self.assertNotIn(secret, output.getvalue())
        self.assertNotIn("가상 과목", output.getvalue())

    def test_provider_failure_cannot_report_completed(self) -> None:
        transport = SequenceTransport([TimeoutError("PRIVATE_TIMEOUT")])
        output = io.StringIO()
        with patch("backend.adaptive.harness.ollama_smoke.OllamaQueryModel", return_value=self.model(transport)):
            with contextlib.redirect_stdout(output):
                code = main([
                    "--live", "--base-url", "http://127.0.0.1:11434",
                    "--model-tag", "synthetic-model:exact", "--timeout", "5",
                ])
        summary = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(summary["status"], "INCOMPLETE")
        self.assertEqual(summary["cases"][0]["failure_code"], "TIMEOUT")
        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn("PRIVATE_TIMEOUT", output.getvalue())

    def test_remote_base_url_is_rejected_before_model_construction(self) -> None:
        output = io.StringIO()
        with patch("backend.adaptive.harness.ollama_smoke.OllamaQueryModel") as model:
            with contextlib.redirect_stdout(output):
                code = main([
                    "--live", "--base-url", "https://remote.example",
                    "--model-tag", "synthetic-model:exact", "--timeout", "5",
                ])
            model.assert_not_called()
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["code"], "LOCAL_OLLAMA_REQUIRED")


if __name__ == "__main__":
    unittest.main()
