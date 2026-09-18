"""In-memory construction of exact, context-bound model recordings."""

from __future__ import annotations

import copy
import json
import re
from enum import Enum
from typing import Any

from pydantic import JsonValue, TypeAdapter

from .contracts import HarnessRequest, MappingProfile
from .recorded import (
    MODEL_RUN_ID_PATTERN,
    ModelRecording,
    RecordedEntry,
    RecordedFailure,
    RecordedFailureCode,
    RecordedSuccess,
    mapping_fingerprint,
    question_fingerprint,
)


class RecordingCaptureErrorCode(str, Enum):
    INVALID_MODEL_RUN_ID = "INVALID_MODEL_RUN_ID"
    DUPLICATE_REQUEST_ID = "DUPLICATE_REQUEST_ID"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    INVALID_FAILURE_CODE = "INVALID_FAILURE_CODE"
    EMPTY_SESSION = "EMPTY_SESSION"
    SESSION_FINALIZED = "SESSION_FINALIZED"


class RecordingCaptureError(ValueError):
    """Controlled capture failure whose message never contains private payloads."""

    def __init__(self, code: RecordingCaptureErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


_JSON_VALUE_ADAPTER = TypeAdapter(JsonValue)


class RecordingCaptureSession:
    """Collect caller-supplied outcomes without invoking or interpreting a model."""

    def __init__(self, model_run_id: str) -> None:
        if not isinstance(model_run_id, str) or re.fullmatch(
            MODEL_RUN_ID_PATTERN, model_run_id
        ) is None:
            raise RecordingCaptureError(
                RecordingCaptureErrorCode.INVALID_MODEL_RUN_ID
            )
        self._model_run_id = model_run_id
        self._records: list[RecordedEntry] = []
        self._request_ids: set[str] = set()
        self._finalized: ModelRecording | None = None

    def record_success(
        self,
        request: HarnessRequest,
        mapping: MappingProfile,
        response: Any,
    ) -> None:
        self._ensure_can_record(request.request_id)
        try:
            copied = copy.deepcopy(response)
            validated = _JSON_VALUE_ADAPTER.validate_python(copied, strict=True)
            json.dumps(validated, ensure_ascii=False, allow_nan=False)
        except Exception:
            raise RecordingCaptureError(
                RecordingCaptureErrorCode.INVALID_RESPONSE
            ) from None
        self._append(
            request,
            RecordedSuccess(
                **self._identity(request, mapping),
                status="SUCCEEDED",
                response=validated,
            ),
        )

    def record_failure(
        self,
        request: HarnessRequest,
        mapping: MappingProfile,
        failure_code: RecordedFailureCode,
    ) -> None:
        self._ensure_can_record(request.request_id)
        if not isinstance(failure_code, RecordedFailureCode):
            raise RecordingCaptureError(
                RecordingCaptureErrorCode.INVALID_FAILURE_CODE
            )
        self._append(
            request,
            RecordedFailure(
                **self._identity(request, mapping),
                status="FAILED",
                failure_code=failure_code,
            ),
        )

    def finalize(self) -> ModelRecording:
        if self._finalized is None:
            if not self._records:
                raise RecordingCaptureError(RecordingCaptureErrorCode.EMPTY_SESSION)
            self._finalized = ModelRecording(
                model_run_id=self._model_run_id,
                records=copy.deepcopy(self._records),
            )
        return self._finalized.model_copy(deep=True)

    def _ensure_can_record(self, request_id: str) -> None:
        if self._finalized is not None:
            raise RecordingCaptureError(RecordingCaptureErrorCode.SESSION_FINALIZED)
        if request_id in self._request_ids:
            raise RecordingCaptureError(
                RecordingCaptureErrorCode.DUPLICATE_REQUEST_ID
            )

    @staticmethod
    def _identity(
        request: HarnessRequest,
        mapping: MappingProfile,
    ) -> dict[str, str]:
        return {
            "request_id": request.request_id,
            "database_id": request.database_id,
            "question_sha256": question_fingerprint(request.question),
            "mapping_sha256": mapping_fingerprint(mapping),
        }

    def _append(
        self,
        request: HarnessRequest,
        record: RecordedEntry,
    ) -> None:
        self._records.append(record)
        self._request_ids.add(request.request_id)
