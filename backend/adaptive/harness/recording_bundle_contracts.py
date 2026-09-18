"""Versioned, payload-free metadata for private recording bundles."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .recorded import MODEL_RUN_ID_PATTERN, ModelRecording


PROVENANCE_SCHEMA_VERSION = "recording-provenance-v1"
BUNDLE_SCHEMA_VERSION = "recording-bundle-v1"
STORAGE_TOOL_VERSION = "recording-storage-v1"
SAFE_LABEL_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,159}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"


class ProviderKind(str, Enum):
    SYNTHETIC = "SYNTHETIC"
    OLLAMA = "OLLAMA"
    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"
    OTHER = "OTHER"


class RecordingProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["recording-provenance-v1"] = PROVENANCE_SCHEMA_VERSION
    verification_status: Literal["DECLARED_UNVERIFIED"] = "DECLARED_UNVERIFIED"
    provider_kind: ProviderKind = Field(strict=False)
    provider_id: str = Field(pattern=SAFE_LABEL_PATTERN)
    provider_version: str = Field(pattern=SAFE_LABEL_PATTERN)
    model_id: str = Field(pattern=SAFE_LABEL_PATTERN)
    model_artifact_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    prompt_template_sha256: str = Field(pattern=SHA256_PATTERN)
    generation_config_sha256: str = Field(pattern=SHA256_PATTERN)
    collector_version: str = Field(pattern=SAFE_LABEL_PATTERN)


class RecordingArtifactMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    path: str = Field(min_length=1, max_length=300)
    sha256: str = Field(pattern=SHA256_PATTERN)
    bytes: int = Field(ge=1)


class RecordingBundleManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["recording-bundle-v1"] = BUNDLE_SCHEMA_VERSION
    storage_tool_version: Literal["recording-storage-v1"] = STORAGE_TOOL_VERSION
    generation_id: str = Field(pattern=SHA256_PATTERN)
    recording_schema_version: Literal["recorded-model-v1"]
    model_run_id: str = Field(pattern=MODEL_RUN_ID_PATTERN)
    record_count: int = Field(ge=1)
    success_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    provenance: RecordingProvenance
    provenance_sha256: str = Field(pattern=SHA256_PATTERN)
    recording: RecordingArtifactMetadata

    @model_validator(mode="after")
    def counts_are_coherent(self) -> "RecordingBundleManifest":
        if self.success_count + self.failure_count != self.record_count:
            raise ValueError("recording manifest counts must sum to record count")
        return self


class LoadedRecordingBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest: RecordingBundleManifest
    recording: ModelRecording
