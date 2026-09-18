"""Strict, privacy-aware replay of externally captured JSON model outputs."""

from __future__ import annotations

import copy
import hashlib
import json
from enum import Enum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from .contracts import HarnessRequest, MappingProfile, Ontology


RECORDING_SCHEMA_VERSION = "recorded-model-v1"
MODEL_RUN_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$"


class RecordedFailureCode(str, Enum):
    PROVIDER_ERROR = "PROVIDER_ERROR"
    TIMEOUT = "TIMEOUT"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    CANCELLED = "CANCELLED"


class RecordedReplayErrorCode(str, Enum):
    RESPONSE_MISSING = "RECORDED_RESPONSE_MISSING"
    DATABASE_MISMATCH = "RECORDED_DATABASE_MISMATCH"
    QUESTION_MISMATCH = "RECORDED_QUESTION_MISMATCH"
    MAPPING_MISMATCH = "RECORDED_MAPPING_MISMATCH"
    PROVIDER_ERROR = "RECORDED_PROVIDER_ERROR"
    TIMEOUT = "RECORDED_TIMEOUT"
    EMPTY_RESPONSE = "RECORDED_EMPTY_RESPONSE"
    CANCELLED = "RECORDED_CANCELLED"


class RecordingLoadError(ValueError):
    """A recording cannot be loaded without exposing its private payload."""


class RecordedModelReplayError(RuntimeError):
    def __init__(self, code: RecordedReplayErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


class _RecordedIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    request_id: str = Field(min_length=1, max_length=200)
    database_id: str = Field(min_length=1, max_length=200)
    question_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mapping_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RecordedSuccess(_RecordedIdentity):
    status: Literal["SUCCEEDED"]
    response: JsonValue


class RecordedFailure(_RecordedIdentity):
    status: Literal["FAILED"]
    failure_code: RecordedFailureCode = Field(strict=False)


RecordedEntry = Annotated[RecordedSuccess | RecordedFailure, Field(discriminator="status")]


class ModelRecording(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["recorded-model-v1"] = RECORDING_SCHEMA_VERSION
    model_run_id: str = Field(pattern=MODEL_RUN_ID_PATTERN)
    records: list[RecordedEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def request_ids_are_unique(self) -> "ModelRecording":
        request_ids = [record.request_id for record in self.records]
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("recording request IDs must be unique")
        return self


def question_fingerprint(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()


def mapping_fingerprint(mapping: MappingProfile) -> str:
    encoded = json.dumps(
        mapping.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_non_finite_constant(_: str) -> None:
    raise ValueError("non-finite JSON number")


def parse_model_recording_bytes(raw: bytes) -> ModelRecording:
    """Validate exact recording bytes without exposing a rejected payload."""
    try:
        text = raw.decode("utf-8-sig")
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_constant,
        )
        return ModelRecording.model_validate(payload)
    except (UnicodeError, json.JSONDecodeError, ValidationError, ValueError, TypeError):
        raise RecordingLoadError("recording file does not match the required JSON contract") from None


def load_model_recording(path: str | Path) -> ModelRecording:
    """Read one strict UTF-8 JSON recording without changing the source file."""

    try:
        raw = Path(path).read_bytes()
    except OSError:
        raise RecordingLoadError("recording file could not be read as UTF-8") from None
    return parse_model_recording_bytes(raw)


_FAILURE_TO_REPLAY = {
    RecordedFailureCode.PROVIDER_ERROR: RecordedReplayErrorCode.PROVIDER_ERROR,
    RecordedFailureCode.TIMEOUT: RecordedReplayErrorCode.TIMEOUT,
    RecordedFailureCode.EMPTY_RESPONSE: RecordedReplayErrorCode.EMPTY_RESPONSE,
    RecordedFailureCode.CANCELLED: RecordedReplayErrorCode.CANCELLED,
}


class RecordedModel:
    """Replay exact JSON values only when request and mapping context match."""

    def __init__(self, recording: ModelRecording) -> None:
        self.model_run_id = recording.model_run_id
        self._records = {
            record.request_id: record.model_copy(deep=True) for record in recording.records
        }

    @classmethod
    def from_path(cls, path: str | Path) -> "RecordedModel":
        return cls(load_model_recording(path))

    def generate_ir(
        self,
        request: HarnessRequest,
        ontology: Ontology,
        mapping: MappingProfile,
    ) -> JsonValue:
        del ontology
        record = self._records.get(request.request_id)
        if record is None:
            raise RecordedModelReplayError(RecordedReplayErrorCode.RESPONSE_MISSING)
        if record.database_id != request.database_id:
            raise RecordedModelReplayError(RecordedReplayErrorCode.DATABASE_MISMATCH)
        if record.question_sha256 != question_fingerprint(request.question):
            raise RecordedModelReplayError(RecordedReplayErrorCode.QUESTION_MISMATCH)
        if record.mapping_sha256 != mapping_fingerprint(mapping):
            raise RecordedModelReplayError(RecordedReplayErrorCode.MAPPING_MISMATCH)
        if isinstance(record, RecordedFailure):
            raise RecordedModelReplayError(_FAILURE_TO_REPLAY[record.failure_code])
        return copy.deepcopy(record.response)
