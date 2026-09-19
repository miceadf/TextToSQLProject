"""Provider-neutral orchestration for deterministic in-memory recordings."""

from __future__ import annotations

from enum import Enum
from typing import Sequence

from pydantic import BaseModel, ConfigDict

from .contracts import HarnessRequest, MappingProfile, Ontology
from .model import QueryModel
from .recorded import ModelRecording, RecordedFailureCode
from .recording_capture import (
    RecordingCaptureError,
    RecordingCaptureErrorCode,
    RecordingCaptureSession,
)


class CaptureTarget(BaseModel):
    """One exact request and runtime context to send to a provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request: HarnessRequest
    ontology: Ontology
    mapping: MappingProfile


class RecordingOrchestrationErrorCode(str, Enum):
    EMPTY_TARGETS = "EMPTY_TARGETS"
    INVALID_PROVIDER = "INVALID_PROVIDER"
    INVALID_TARGET = "INVALID_TARGET"
    DUPLICATE_REQUEST_ID = "DUPLICATE_REQUEST_ID"
    DATABASE_MISMATCH = "DATABASE_MISMATCH"


class RecordingOrchestrationError(ValueError):
    """Fatal input-contract failure that occurs before provider invocation."""

    def __init__(self, code: RecordingOrchestrationErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


class RecordingProviderError(RuntimeError):
    """Typed, payload-free failure raised by a provider adapter."""

    def __init__(self, failure_code: RecordedFailureCode) -> None:
        if not isinstance(failure_code, RecordedFailureCode):
            raise TypeError("INVALID_PROVIDER_FAILURE_CODE")
        self.failure_code = failure_code
        super().__init__(failure_code.value)


def preflight_capture(provider: QueryModel, targets: Sequence[CaptureTarget]) -> None:
    """Validate a complete batch before provider invocation."""
    try:
        provider_call = getattr(provider, "generate_ir", None)
    except Exception:
        raise RecordingOrchestrationError(
            RecordingOrchestrationErrorCode.INVALID_PROVIDER
        ) from None
    if not callable(provider_call):
        raise RecordingOrchestrationError(
            RecordingOrchestrationErrorCode.INVALID_PROVIDER
        )
    if not targets:
        raise RecordingOrchestrationError(
            RecordingOrchestrationErrorCode.EMPTY_TARGETS
        )

    request_ids: set[str] = set()
    for target in targets:
        if not isinstance(target, CaptureTarget) or not all(
            (
                isinstance(target.request, HarnessRequest),
                isinstance(target.ontology, Ontology),
                isinstance(target.mapping, MappingProfile),
            )
        ):
            raise RecordingOrchestrationError(
                RecordingOrchestrationErrorCode.INVALID_TARGET
            )
        if target.request.request_id in request_ids:
            raise RecordingOrchestrationError(
                RecordingOrchestrationErrorCode.DUPLICATE_REQUEST_ID
            )
        request_ids.add(target.request.request_id)
        if target.request.database_id != target.mapping.database_id:
            raise RecordingOrchestrationError(
                RecordingOrchestrationErrorCode.DATABASE_MISMATCH
            )


def capture_recording(
    model_run_id: str,
    provider: QueryModel,
    targets: Sequence[CaptureTarget],
) -> ModelRecording:
    """Invoke a provider once per target and retain every attempt in order."""

    materialized_targets = list(targets)
    preflight_capture(provider, materialized_targets)
    session = RecordingCaptureSession(model_run_id)

    for target in materialized_targets:
        request = target.request.model_copy(deep=True)
        ontology = target.ontology.model_copy(deep=True)
        mapping = target.mapping.model_copy(deep=True)
        try:
            response = provider.generate_ir(request, ontology, mapping)
        except RecordingProviderError as exc:
            session.record_failure(
                target.request,
                target.mapping,
                exc.failure_code,
            )
            continue
        except Exception:
            session.record_failure(
                target.request,
                target.mapping,
                RecordedFailureCode.PROVIDER_ERROR,
            )
            continue

        try:
            session.record_success(target.request, target.mapping, response)
        except RecordingCaptureError as exc:
            if exc.code is not RecordingCaptureErrorCode.INVALID_RESPONSE:
                raise
            session.record_failure(
                target.request,
                target.mapping,
                RecordedFailureCode.PROVIDER_ERROR,
            )

    return session.finalize()
