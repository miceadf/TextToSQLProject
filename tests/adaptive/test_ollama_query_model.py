from __future__ import annotations

import contextlib
import io
import json
import socket
import unittest
import asyncio
from concurrent.futures import CancelledError
from urllib.error import HTTPError, URLError

from backend.adaptive.harness import (
    CaptureTarget,
    DataType,
    ExecutionHarness,
    FieldMapping,
    HarnessErrorCode,
    HarnessRequest,
    MappingProfile,
    MappingStatus,
    OllamaQueryModel,
    Ontology,
    OntologyField,
    Operator,
    RecordedFailure,
    RecordedFailureCode,
    RecordedSuccess,
    RecordingProviderError,
    capture_recording,
)
from backend.adaptive.harness.ollama import PROMPT_VERSION


class SequenceTransport:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, dict[str, object], float]] = []

    def __call__(self, url: str, payload: dict[str, object], timeout: float) -> object:
        self.calls.append((url, payload, timeout))
        outcome = self.outcomes[len(self.calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class OllamaQueryModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = HarnessRequest(
            request_id="synthetic-1", database_id="synthetic-db", question="합성 질문"
        )
        self.ontology = Ontology(
            entities={"Course", "Schedule"},
            fields={
                "Course.name": OntologyField(
                    name="Course.name",
                    data_type=DataType.STRING,
                    allowed_operators={Operator.CONTAINS, Operator.EQUALS},
                ),
                "Course.credits": OntologyField(
                    name="Course.credits",
                    data_type=DataType.INTEGER,
                    allowed_operators={Operator.GREATER_THAN},
                ),
            },
        )
        self.mapping = MappingProfile(
            database_id="synthetic-db",
            version=1,
            status=MappingStatus.PUBLISHED,
            fields={"Course.name": FieldMapping(table="PRIVATE_TABLE", column="PRIVATE_COLUMN")},
        )

    def model(self, transport: SequenceTransport) -> OllamaQueryModel:
        return OllamaQueryModel(
            "http://localhost:11434/", "exact-model:tag", 7.5, transport=transport
        )

    def generate(self, model: OllamaQueryModel) -> object:
        return model.generate_ir(self.request, self.ontology, self.mapping)

    def test_exact_call_and_deterministic_semantic_prompt(self) -> None:
        transport = SequenceTransport([{"response": "null"}, {"response": "null"}])
        model = self.model(transport)
        self.assertIsNone(self.generate(model))
        self.assertIsNone(self.generate(model))
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(transport.calls[0], transport.calls[1])
        url, payload, timeout = transport.calls[0]
        self.assertEqual(url, "http://localhost:11434/api/generate")
        self.assertEqual(timeout, 7.5)
        self.assertEqual(set(payload), {"model", "prompt", "stream", "options"})
        self.assertEqual(payload["model"], "exact-model:tag")
        self.assertIs(payload["stream"], False)
        self.assertEqual(payload["options"], {"temperature": 0})
        prompt = payload["prompt"]
        self.assertIn(PROMPT_VERSION, prompt)
        self.assertIn("합성 질문", prompt)
        context = json.loads(prompt.split("Context: ", 1)[1].split("\nQuestion:", 1)[0])
        self.assertEqual(context["entities"], ["Course", "Schedule"])
        self.assertEqual(context["available_fields"], ["Course.name"])
        self.assertEqual(context["fields"][0], {
            "name": "Course.credits", "type": "integer", "operators": ["greater_than"]
        })
        self.assertNotIn("PRIVATE_TABLE", prompt)
        self.assertNotIn("PRIVATE_COLUMN", prompt)
        self.assertNotIn("synthetic-db", prompt)

    def test_explicit_v2_uses_exact_available_fields_and_distinct_template(self) -> None:
        v1_transport = SequenceTransport([{"response": "null"}])
        v2_transport = SequenceTransport([{"response": "null"}])
        v1 = self.model(v1_transport)
        v2 = OllamaQueryModel(
            "http://localhost:11434/", "exact-model:tag", 7.5,
            transport=v2_transport, prompt_version="query-ir-v2",
        )
        self.generate(v1)
        self.generate(v2)
        first = v1.declared_provenance("0.34.2")
        second = v2.declared_provenance("0.34.2")
        self.assertNotEqual(first.prompt_template_sha256, second.prompt_template_sha256)
        self.assertEqual(first.generation_config_sha256, second.generation_config_sha256)
        self.assertEqual(second, v2.declared_provenance("0.34.2"))
        prompt = v2_transport.calls[0][1]["prompt"]
        self.assertIn("query-ir-v2", prompt)
        self.assertIn("copy a complete string exactly", prompt)
        self.assertEqual(json.loads(prompt.split("Context: ", 1)[1].split("\nQuestion:", 1)[0])["available_fields"], ["Course.name"])
        for secret in ("PRIVATE_TABLE", "PRIVATE_COLUMN", "gold_query_ir", "EXPECTED_RESULT"):
            self.assertNotIn(secret, prompt)
        self.assertEqual(v1_transport.calls[0][1]["options"], v2_transport.calls[0][1]["options"])
        self.assertEqual(len(v2_transport.calls), 1)

    def test_unknown_prompt_version_rejected_before_transport(self) -> None:
        with self.assertRaisesRegex(ValueError, "^INVALID_PROMPT_VERSION$"):
            OllamaQueryModel("http://localhost:11434", "exact", 7, prompt_version="private")

    def test_placeholder_text_in_context_or_question_is_not_expanded(self) -> None:
        special_field = "Course.{question}"
        ontology = self.ontology.model_copy(deep=True)
        ontology.fields[special_field] = OntologyField(
            name=special_field,
            data_type=DataType.STRING,
            allowed_operators={Operator.EQUALS},
        )
        mapping = self.mapping.model_copy(deep=True)
        mapping.fields[special_field] = FieldMapping(table="synthetic", column="synthetic")
        question = self.request.model_copy(update={"question": "literal {context}"})
        transport = SequenceTransport([{"response": "null"}])
        self.model(transport).generate_ir(question, ontology, mapping)
        prompt = transport.calls[0][1]["prompt"]
        self.assertIn(special_field, prompt)
        self.assertTrue(prompt.endswith("Question: literal {context}"))

    def test_all_json_types_and_non_json_text_preserved(self) -> None:
        values = [
            ('{"entity":"Course","select":["Course.name"]}', dict),
            ('[1,"a",null]', list),
            ('"hello"', str),
            ('12', int),
            ('1.25', float),
            ('true', bool),
            ('null', type(None)),
            ('SELECT * FROM PRIVATE_TABLE;', str),
            ('```sql\nSELECT 1;\n```', str),
            ('Here is the query: SELECT 1', str),
        ]
        transport = SequenceTransport([{"response": raw} for raw, _ in values])
        model = self.model(transport)
        for raw, expected_type in values:
            with self.subTest(raw=raw):
                result = self.generate(model)
                self.assertIs(type(result), expected_type)
                self.assertEqual(result, json.loads(raw) if raw[0] in '{["' or raw in {'12', '1.25', 'true', 'null'} else raw)
        self.assertEqual(len(transport.calls), len(values))

    def test_empty_vs_json_null(self) -> None:
        transport = SequenceTransport([{"response": ""}, {"response": " \n "}, {"response": "null"}])
        model = self.model(transport)
        for _ in range(2):
            with self.assertRaises(RecordingProviderError) as caught:
                self.generate(model)
            self.assertIs(caught.exception.failure_code, RecordedFailureCode.EMPTY_RESPONSE)
        self.assertIsNone(self.generate(model))

    def test_ambiguous_json_is_rejected_without_coercion(self) -> None:
        raw_values = ['{"a":1,"a":2}', '{"nested":{"a":1,"a":2}}', 'NaN', 'Infinity', '1e999']
        transport = SequenceTransport([{"response": raw} for raw in raw_values])
        model = self.model(transport)
        for _ in raw_values:
            with self.assertRaises(RecordingProviderError) as caught:
                self.generate(model)
            self.assertIs(caught.exception.failure_code, RecordedFailureCode.PROVIDER_ERROR)

    def test_typed_transport_errors_without_payload_leak(self) -> None:
        secret = "PRIVATE_QUESTION_RESPONSE_HTTP_BODY_PROMPT_C_PATH"
        failures = [
            (TimeoutError(secret), RecordedFailureCode.TIMEOUT),
            (socket.timeout(secret), RecordedFailureCode.TIMEOUT),
            (URLError(TimeoutError(secret)), RecordedFailureCode.TIMEOUT),
            (URLError(secret), RecordedFailureCode.PROVIDER_ERROR),
            (HTTPError("http://localhost/" + secret, 500, secret, {}, io.BytesIO(secret.encode())), RecordedFailureCode.PROVIDER_ERROR),
            (CancelledError(secret), RecordedFailureCode.CANCELLED),
            (asyncio.CancelledError(secret), RecordedFailureCode.CANCELLED),
            (RuntimeError(secret), RecordedFailureCode.PROVIDER_ERROR),
            ({"error": secret, "response": "null"}, RecordedFailureCode.PROVIDER_ERROR),
            ({"response": 5}, RecordedFailureCode.PROVIDER_ERROR),
            ([], RecordedFailureCode.PROVIDER_ERROR),
        ]
        transport = SequenceTransport([outcome for outcome, _ in failures])
        model = self.model(transport)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            for _, code in failures:
                with self.assertRaises(RecordingProviderError) as caught:
                    self.generate(model)
                self.assertIs(caught.exception.failure_code, code)
                self.assertEqual(str(caught.exception), code.value)
                self.assertIsNone(caught.exception.__context__)
                self.assertNotIn(secret, str(caught.exception))
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(len(transport.calls), len(failures))
        self.assertTrue(all(call[1]["model"] == "exact-model:tag" for call in transport.calls))

    def test_capture_order_continues_after_typed_failure(self) -> None:
        raw = '{"entity":"Course","select":["Course.name"],"filters":[],"limit":10}'
        transport = SequenceTransport([{"response": raw}, TimeoutError("PRIVATE"), {"response": "SELECT 1"}])
        model = self.model(transport)
        targets = [
            CaptureTarget(
                request=self.request.model_copy(update={"request_id": f"synthetic-{index}"}),
                ontology=self.ontology,
                mapping=self.mapping,
            )
            for index in range(3)
        ]
        recording = capture_recording("ollama-synthetic-v1", model, targets)
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual([record.request_id for record in recording.records], [target.request.request_id for target in targets])
        self.assertIsInstance(recording.records[0], RecordedSuccess)
        self.assertEqual(recording.records[0].response, json.loads(raw))
        self.assertIsInstance(recording.records[1], RecordedFailure)
        self.assertIs(recording.records[1].failure_code, RecordedFailureCode.TIMEOUT)
        self.assertIsInstance(recording.records[2], RecordedSuccess)
        self.assertEqual(recording.records[2].response, "SELECT 1")

    def test_harness_rejects_sql_and_invalid_semantic_ir(self) -> None:
        responses = [
            ("SELECT * FROM courses", HarnessErrorCode.MODEL_INVALID_OUTPUT),
            ("```json\n{}\n```", HarnessErrorCode.MODEL_INVALID_OUTPUT),
            ('{"entity":"Course","select":["Course.missing"]}', HarnessErrorCode.UNKNOWN_FIELD),
            ('{"entity":"Course","select":["Course.name"],"filters":[{"field":"Course.name","operator":"greater_than","value":"x"}]}', HarnessErrorCode.INVALID_OPERATOR),
        ]
        transport = SequenceTransport([{"response": raw} for raw, _ in responses])
        harness = ExecutionHarness(self.model(transport), executor=None)
        for _, expected in responses:
            result = harness.run(self.request, self.ontology, self.mapping)
            self.assertEqual(result.error.code, expected)
        self.assertEqual(len(transport.calls), len(responses))

    def test_invalid_configuration_has_fixed_errors(self) -> None:
        for kwargs, code in [
            ({"base_url": "http://user:secret@localhost"}, "INVALID_OLLAMA_BASE_URL"),
            ({"base_url": "http://localhost/db?dsn=secret"}, "INVALID_OLLAMA_BASE_URL"),
            ({"model_tag": ""}, "INVALID_OLLAMA_MODEL_TAG"),
            ({"timeout": 0}, "INVALID_OLLAMA_TIMEOUT"),
        ]:
            config = {"base_url": "http://localhost:11434", "model_tag": "exact", "timeout": 2}
            config.update(kwargs)
            with self.assertRaises(ValueError) as caught:
                OllamaQueryModel(**config)
            self.assertEqual(str(caught.exception), code)

    def test_unpublished_or_mismatched_mapping_never_calls_transport(self) -> None:
        transport = SequenceTransport([])
        model = self.model(transport)
        for changes in [
            {"status": MappingStatus.DRAFT},
            {"database_id": "other-db"},
        ]:
            with self.assertRaises(RecordingProviderError) as caught:
                model.generate_ir(
                    self.request,
                    self.ontology,
                    self.mapping.model_copy(update=changes),
                )
            self.assertIs(caught.exception.failure_code, RecordedFailureCode.PROVIDER_ERROR)
        self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
