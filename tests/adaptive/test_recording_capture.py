from __future__ import annotations

import unittest

from backend.adaptive.harness import (
    FieldMapping,
    HarnessRequest,
    MappingProfile,
    MappingStatus,
    Ontology,
    QueryIR,
    RecordedFailure,
    RecordedFailureCode,
    RecordedModel,
    RecordedSuccess,
    RecordingCaptureError,
    RecordingCaptureErrorCode,
    RecordingCaptureSession,
    mapping_fingerprint,
    question_fingerprint,
)


class _PrivateObject:
    def __deepcopy__(self, memo):
        del memo
        raise RuntimeError("PRIVATE_RESPONSE_LITERAL")


class RecordingCaptureSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mapping = MappingProfile(
            database_id="fixture-flat-v1",
            version=1,
            status=MappingStatus.PUBLISHED,
            fields={"Course.name": FieldMapping(table="courses", column="name")},
        )

    @staticmethod
    def _request(index: int, question: str | None = None) -> HarnessRequest:
        return HarnessRequest(
            request_id=f"capture-run:P2-{index:03d}:flat",
            database_id="fixture-flat-v1",
            question=question or f"합성 질문 {index}",
        )

    def test_success_values_preserve_json_types_order_and_identity(self) -> None:
        values = [
            {"nested": [1, "값", None]},
            "invalid QueryIR string",
            17,
            1.25,
            True,
            None,
            [False, 2, "목록"],
        ]
        session = RecordingCaptureSession("capture-run-v1")
        requests = []
        for index, value in enumerate(values, start=1):
            request = self._request(index)
            requests.append(request)
            session.record_success(request, self.mapping, value)

        recording = session.finalize()
        self.assertEqual(recording.model_run_id, "capture-run-v1")
        self.assertEqual(
            [record.request_id for record in recording.records],
            [request.request_id for request in requests],
        )
        for record, request, expected in zip(
            recording.records, requests, values, strict=True
        ):
            with self.subTest(request_id=request.request_id):
                self.assertIsInstance(record, RecordedSuccess)
                self.assertEqual(record.response, expected)
                self.assertIs(type(record.response), type(expected))
                self.assertEqual(
                    record.question_sha256, question_fingerprint(request.question)
                )
                self.assertEqual(
                    record.mapping_sha256, mapping_fingerprint(self.mapping)
                )
                self.assertEqual(record.database_id, request.database_id)

    def test_response_is_defensively_copied_and_inputs_are_unchanged(self) -> None:
        request = self._request(1)
        request_before = request.model_dump(mode="json")
        mapping_before = self.mapping.model_dump(mode="json")
        response = {"nested": {"count": 1}}
        session = RecordingCaptureSession("capture-run-v1")
        session.record_success(request, self.mapping, response)
        response["nested"]["count"] = 99

        first = session.finalize()
        self.assertEqual(first.records[0].response["nested"]["count"], 1)
        first.records.clear()
        second = session.finalize()
        self.assertEqual(len(second.records), 1)
        self.assertEqual(second.records[0].response["nested"]["count"], 1)
        self.assertEqual(request.model_dump(mode="json"), request_before)
        self.assertEqual(self.mapping.model_dump(mode="json"), mapping_before)

    def test_each_explicit_failure_code_is_recorded(self) -> None:
        session = RecordingCaptureSession("capture-run-v1")
        for index, failure_code in enumerate(RecordedFailureCode, start=1):
            session.record_failure(self._request(index), self.mapping, failure_code)
        recording = session.finalize()
        self.assertEqual(
            [record.failure_code for record in recording.records],
            list(RecordedFailureCode),
        )
        self.assertTrue(
            all(isinstance(record, RecordedFailure) for record in recording.records)
        )

    def test_duplicate_ids_never_overwrite_existing_success_or_failure(self) -> None:
        scenarios = ("success-first", "failure-first")
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                request = self._request(1)
                session = RecordingCaptureSession("capture-run-v1")
                if scenario == "success-first":
                    session.record_success(request, self.mapping, {"kept": True})
                    expected_type = RecordedSuccess
                else:
                    session.record_failure(
                        request, self.mapping, RecordedFailureCode.TIMEOUT
                    )
                    expected_type = RecordedFailure
                with self.assertRaises(RecordingCaptureError) as caught:
                    if scenario == "success-first":
                        session.record_failure(
                            request, self.mapping, RecordedFailureCode.TIMEOUT
                        )
                    else:
                        session.record_success(
                            request, self.mapping, {"replacement": True}
                        )
                self.assertIs(
                    caught.exception.code,
                    RecordingCaptureErrorCode.DUPLICATE_REQUEST_ID,
                )
                recording = session.finalize()
                self.assertEqual(len(recording.records), 1)
                self.assertIsInstance(recording.records[0], expected_type)

    def test_invalid_values_are_rejected_without_consuming_request_id(self) -> None:
        invalid_values = (
            float("nan"),
            float("inf"),
            float("-inf"),
            {"nested": float("nan")},
            b"bytes",
            object(),
            _PrivateObject(),
        )
        for value in invalid_values:
            with self.subTest(value_type=type(value).__name__):
                request = self._request(1, "PRIVATE_QUESTION_LITERAL")
                session = RecordingCaptureSession("capture-run-v1")
                with self.assertRaises(RecordingCaptureError) as caught:
                    session.record_success(request, self.mapping, value)
                self.assertIs(
                    caught.exception.code, RecordingCaptureErrorCode.INVALID_RESPONSE
                )
                self.assertEqual(str(caught.exception), "INVALID_RESPONSE")
                self.assertNotIn("PRIVATE", str(caught.exception))
                session.record_success(request, self.mapping, "safe-json-string")
                self.assertEqual(len(session.finalize().records), 1)

    def test_invalid_failure_code_and_model_run_id_are_safe(self) -> None:
        private = "PRIVATE_FAILURE_OR_RUN_ID"
        with self.assertRaises(RecordingCaptureError) as caught:
            RecordingCaptureSession(private + "!")
        self.assertIs(
            caught.exception.code, RecordingCaptureErrorCode.INVALID_MODEL_RUN_ID
        )
        self.assertNotIn(private, str(caught.exception))

        session = RecordingCaptureSession("capture-run-v1")
        with self.assertRaises(RecordingCaptureError) as caught:
            session.record_failure(
                self._request(1, private),
                self.mapping,
                "TIMEOUT",  # type: ignore[arg-type]
            )
        self.assertIs(
            caught.exception.code, RecordingCaptureErrorCode.INVALID_FAILURE_CODE
        )
        self.assertNotIn(private, str(caught.exception))

    def test_empty_finalize_and_capture_after_finalize_are_blocked(self) -> None:
        empty = RecordingCaptureSession("capture-run-v1")
        with self.assertRaises(RecordingCaptureError) as caught:
            empty.finalize()
        self.assertIs(caught.exception.code, RecordingCaptureErrorCode.EMPTY_SESSION)

        session = RecordingCaptureSession("capture-run-v1")
        session.record_success(self._request(1), self.mapping, {})
        session.finalize()
        with self.assertRaises(RecordingCaptureError) as caught:
            session.record_success(self._request(2), self.mapping, {})
        self.assertIs(
            caught.exception.code, RecordingCaptureErrorCode.SESSION_FINALIZED
        )

    def test_same_inputs_are_deterministic_and_replayable(self) -> None:
        request = self._request(1)
        response = {
            "entity": "Course",
            "select": ["Course.name"],
            "filters": [],
            "limit": 10,
        }
        recordings = []
        for _ in range(2):
            session = RecordingCaptureSession("capture-run-v1")
            session.record_success(request, self.mapping, response)
            recordings.append(session.finalize())
        self.assertEqual(
            recordings[0].model_dump_json(), recordings[1].model_dump_json()
        )
        replayed = RecordedModel(recordings[0]).generate_ir(
            request, Ontology(entities=set(), fields={}), self.mapping
        )
        self.assertEqual(
            QueryIR.model_validate(replayed), QueryIR.model_validate(response)
        )


if __name__ == "__main__":
    unittest.main()
