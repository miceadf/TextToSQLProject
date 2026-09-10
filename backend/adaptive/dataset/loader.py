from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .contracts import (
    DatasetIssue,
    DatasetIssueCode,
    DatasetLoadResult,
    DatasetRecord,
)


_UTF8_BOM = b"\xef\xbb\xbf"
_SOURCE_FIELDS = (
    ("instruction", "instruction"),
    ("input", "question"),
    ("output", "sql"),
)


class DatasetLoadFatalError(Exception):
    """Raised when the dataset file cannot be read to completion."""

    def __init__(self, source_name: str, message: str) -> None:
        super().__init__(message)
        self.source_name = source_name
        self.message = message


def load_jsonl_dataset(path: str | Path) -> DatasetLoadResult:
    """Load an UTF-8 JSONL dataset without modifying source field values."""

    source_path = Path(path)
    source_name = source_path.name
    content_hasher = hashlib.sha256()
    records: list[DatasetRecord] = []
    issues: list[DatasetIssue] = []
    total_lines = 0

    try:
        with source_path.open("rb") as source:
            for line_number, raw_line in enumerate(source, start=1):
                total_lines = line_number
                content_hasher.update(raw_line)
                decoded_line = _decode_line(raw_line, line_number, issues)
                if decoded_line is None:
                    continue

                if not decoded_line.strip():
                    issues.append(
                        DatasetIssue(
                            code=DatasetIssueCode.EMPTY_LINE,
                            line_number=line_number,
                            field=None,
                            message="line is empty or contains only whitespace",
                        )
                    )
                    continue

                parsed = _parse_object(decoded_line, line_number, issues)
                if parsed is None:
                    continue

                values = _validate_source_fields(parsed, line_number, issues)
                if values is None:
                    continue

                records.append(
                    DatasetRecord(
                        source_line=line_number,
                        instruction=values["instruction"],
                        question=values["question"],
                        sql=values["sql"],
                    )
                )
    except OSError as exc:
        raise DatasetLoadFatalError(
            source_name=source_name,
            message=f"failed to read dataset file {source_name!r}: {exc}",
        ) from exc

    return DatasetLoadResult(
        records=records,
        issues=issues,
        total_lines=total_lines,
        source_name=source_name,
        content_sha256=content_hasher.hexdigest(),
    )


def _decode_line(
    raw_line: bytes,
    line_number: int,
    issues: list[DatasetIssue],
) -> str | None:
    encoded_line = raw_line
    if line_number == 1 and encoded_line.startswith(_UTF8_BOM):
        encoded_line = encoded_line[len(_UTF8_BOM) :]

    try:
        return encoded_line.decode("utf-8")
    except UnicodeDecodeError as exc:
        issues.append(
            DatasetIssue(
                code=DatasetIssueCode.INVALID_UTF8,
                line_number=line_number,
                field=None,
                message=(
                    "line is not valid UTF-8 "
                    f"(invalid bytes at offsets {exc.start}-{exc.end})"
                ),
            )
        )
        return None


def _parse_object(
    decoded_line: str,
    line_number: int,
    issues: list[DatasetIssue],
) -> dict[str, Any] | None:
    try:
        parsed = json.loads(decoded_line)
    except json.JSONDecodeError as exc:
        issues.append(
            DatasetIssue(
                code=DatasetIssueCode.MALFORMED_JSON,
                line_number=line_number,
                field=None,
                message=f"malformed JSON at column {exc.colno}: {exc.msg}",
            )
        )
        return None

    if not isinstance(parsed, dict):
        issues.append(
            DatasetIssue(
                code=DatasetIssueCode.NON_OBJECT,
                line_number=line_number,
                field=None,
                message="JSON value must be an object",
            )
        )
        return None
    return parsed


def _validate_source_fields(
    parsed: dict[str, Any],
    line_number: int,
    issues: list[DatasetIssue],
) -> dict[str, str] | None:
    values: dict[str, str] = {}
    has_error = False

    for source_field, record_field in _SOURCE_FIELDS:
        if source_field not in parsed:
            issues.append(
                DatasetIssue(
                    code=DatasetIssueCode.MISSING_FIELD,
                    line_number=line_number,
                    field=source_field,
                    message=f"required field {source_field!r} is missing",
                )
            )
            has_error = True
            continue

        value = parsed[source_field]
        if not isinstance(value, str):
            issues.append(
                DatasetIssue(
                    code=DatasetIssueCode.INVALID_FIELD_TYPE,
                    line_number=line_number,
                    field=source_field,
                    message=f"field {source_field!r} must be a string",
                )
            )
            has_error = True
            continue
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            issues.append(
                DatasetIssue(
                    code=DatasetIssueCode.INVALID_UNICODE,
                    line_number=line_number,
                    field=source_field,
                    message=f"field {source_field!r} contains an invalid Unicode scalar",
                )
            )
            has_error = True
            continue
        values[record_field] = value

    return None if has_error else values
