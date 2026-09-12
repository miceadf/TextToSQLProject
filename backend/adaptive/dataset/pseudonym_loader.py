"""Read the opt-in private conversion artifact without leaking its contents."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .conversion_contracts import PrivateConvertedRecord


class PrivateLoadIssueCode(str, Enum):
    INVALID_UTF8 = "INVALID_UTF8"
    EMPTY_LINE = "EMPTY_LINE"
    MALFORMED_JSON = "MALFORMED_JSON"
    NON_OBJECT = "NON_OBJECT"
    INPUT_SCHEMA_VERSION_MISMATCH = "INPUT_SCHEMA_VERSION_MISMATCH"
    INVALID_RECORD = "INVALID_RECORD"


class PrivateLoadIssue(BaseModel):
    code: PrivateLoadIssueCode
    line_number: int = Field(ge=1)
    message: str


class PrivateArtifactMetadata(BaseModel):
    dataset_id: str
    dataset_content_sha256: str
    profile_id: str
    profile_version: int
    ontology_version: int


class PrivateLoadResult(BaseModel):
    records: list[PrivateConvertedRecord] = Field(default_factory=list)
    issues: list[PrivateLoadIssue] = Field(default_factory=list)
    total_lines: int = Field(ge=0)
    source_name: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metadata: PrivateArtifactMetadata | None = None


class PrivateLoadFatalError(Exception):
    """Artifact-level failure; the message never contains source values."""


_EXPECTED_VERSION: Literal["private-query-ir-record-v1"] = "private-query-ir-record-v1"
_BOM = b"\xef\xbb\xbf"


def load_private_converted_jsonl(path: str | Path) -> PrivateLoadResult:
    """Stream UTF-8 JSONL, continuing after bad records but not mixed metadata."""

    source_path = Path(path)
    digest = hashlib.sha256()
    records: list[PrivateConvertedRecord] = []
    issues: list[PrivateLoadIssue] = []
    total_lines = 0
    metadata: PrivateArtifactMetadata | None = None

    try:
        with source_path.open("rb") as source:
            for total_lines, raw in enumerate(source, start=1):
                digest.update(raw)
                if total_lines == 1 and raw.startswith(_BOM):
                    raw = raw[len(_BOM):]
                try:
                    line = raw.decode("utf-8")
                except UnicodeDecodeError:
                    issues.append(_issue(PrivateLoadIssueCode.INVALID_UTF8, total_lines))
                    continue
                if not line.strip():
                    issues.append(_issue(PrivateLoadIssueCode.EMPTY_LINE, total_lines))
                    continue
                try:
                    data = json.loads(line)
                except (json.JSONDecodeError, UnicodeError):
                    issues.append(_issue(PrivateLoadIssueCode.MALFORMED_JSON, total_lines))
                    continue
                if not isinstance(data, dict):
                    issues.append(_issue(PrivateLoadIssueCode.NON_OBJECT, total_lines))
                    continue
                if data.get("schema_version") != _EXPECTED_VERSION:
                    issues.append(
                        _issue(PrivateLoadIssueCode.INPUT_SCHEMA_VERSION_MISMATCH, total_lines)
                    )
                    continue
                try:
                    record = PrivateConvertedRecord.model_validate_json(line, strict=True)
                except (ValidationError, ValueError, TypeError):
                    # Pydantic errors may quote private input, so never propagate them.
                    issues.append(_issue(PrivateLoadIssueCode.INVALID_RECORD, total_lines))
                    continue
                current_metadata = PrivateArtifactMetadata(
                    dataset_id=record.dataset_id,
                    dataset_content_sha256=record.dataset_content_sha256,
                    profile_id=record.profile_id,
                    profile_version=record.profile_version,
                    ontology_version=record.ontology_version,
                )
                if metadata is None:
                    metadata = current_metadata
                elif metadata != current_metadata:
                    raise PrivateLoadFatalError("private artifact metadata mismatch")
                records.append(record)
    except OSError:
        raise PrivateLoadFatalError("private artifact cannot be read") from None

    return PrivateLoadResult(
        records=records,
        issues=issues,
        total_lines=total_lines,
        source_name=source_path.name,
        content_sha256=digest.hexdigest(),
        metadata=metadata,
    )


def _issue(code: PrivateLoadIssueCode, line: int) -> PrivateLoadIssue:
    return PrivateLoadIssue(
        code=code,
        line_number=line,
        message={
            PrivateLoadIssueCode.INVALID_UTF8: "line is not valid UTF-8",
            PrivateLoadIssueCode.EMPTY_LINE: "line is empty",
            PrivateLoadIssueCode.MALFORMED_JSON: "line is not valid JSON",
            PrivateLoadIssueCode.NON_OBJECT: "line must contain a JSON object",
            PrivateLoadIssueCode.INPUT_SCHEMA_VERSION_MISMATCH: "unsupported record schema version",
            PrivateLoadIssueCode.INVALID_RECORD: "private record does not match its contract",
        }[code],
    )
