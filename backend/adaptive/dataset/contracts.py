from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class DatasetIssueCode(str, Enum):
    EMPTY_LINE = "EMPTY_LINE"
    INVALID_UTF8 = "INVALID_UTF8"
    INVALID_UNICODE = "INVALID_UNICODE"
    MALFORMED_JSON = "MALFORMED_JSON"
    NON_OBJECT = "NON_OBJECT"
    MISSING_FIELD = "MISSING_FIELD"
    INVALID_FIELD_TYPE = "INVALID_FIELD_TYPE"


class DatasetRecord(BaseModel):
    schema_version: Literal["dataset-record-v1"] = "dataset-record-v1"
    source_line: int = Field(ge=1)
    instruction: str
    question: str
    sql: str


class DatasetIssue(BaseModel):
    code: DatasetIssueCode
    line_number: int = Field(ge=1)
    field: str | None
    message: str


class DatasetLoadResult(BaseModel):
    records: list[DatasetRecord] = Field(default_factory=list)
    issues: list[DatasetIssue] = Field(default_factory=list)
    total_lines: int = Field(ge=0)
    source_name: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
