from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class Operator(str, Enum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    CONTAINS = "contains"


class DataType(str, Enum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"


class MappingStatus(str, Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    STALE = "STALE"


class HarnessStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class HarnessStage(str, Enum):
    REQUEST_VALIDATED = "REQUEST_VALIDATED"
    CONTEXT_READY = "CONTEXT_READY"
    IR_GENERATED = "IR_GENERATED"
    IR_VALIDATED = "IR_VALIDATED"
    SQL_COMPILED = "SQL_COMPILED"
    SQL_VALIDATED = "SQL_VALIDATED"
    EXECUTED = "EXECUTED"
    RESULT_VALIDATED = "RESULT_VALIDATED"


class HarnessErrorCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    DATABASE_NOT_FOUND = "DATABASE_NOT_FOUND"
    MAPPING_NOT_PUBLISHED = "MAPPING_NOT_PUBLISHED"
    MODEL_INVALID_OUTPUT = "MODEL_INVALID_OUTPUT"
    UNKNOWN_ENTITY = "UNKNOWN_ENTITY"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    INVALID_OPERATOR = "INVALID_OPERATOR"
    INVALID_VALUE_TYPE = "INVALID_VALUE_TYPE"
    MAPPING_MISSING = "MAPPING_MISSING"
    MULTI_TABLE_NOT_SUPPORTED = "MULTI_TABLE_NOT_SUPPORTED"
    SQL_COMPILE_ERROR = "SQL_COMPILE_ERROR"
    UNSAFE_SQL = "UNSAFE_SQL"
    EXECUTION_TIMEOUT = "EXECUTION_TIMEOUT"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    RESULT_TOO_LARGE = "RESULT_TOO_LARGE"
    RESULT_SCHEMA_MISMATCH = "RESULT_SCHEMA_MISMATCH"


class FilterIR(BaseModel):
    field: str = Field(min_length=1)
    operator: Operator
    value: str | int | float | bool


class QueryIR(BaseModel):
    entity: str = Field(min_length=1)
    select: list[str] = Field(min_length=1)
    filters: list[FilterIR] = Field(default_factory=list)
    limit: int = Field(default=50, ge=1, le=200)

    @field_validator("select")
    @classmethod
    def unique_select_fields(cls, fields: list[str]) -> list[str]:
        if any(not field.strip() for field in fields):
            raise ValueError("select fields must not be blank")
        if len(fields) != len(set(fields)):
            raise ValueError("select fields must be unique")
        return fields


class OntologyField(BaseModel):
    name: str = Field(min_length=1)
    data_type: DataType
    allowed_operators: set[Operator]


class Ontology(BaseModel):
    entities: set[str]
    fields: dict[str, OntologyField]


class FieldMapping(BaseModel):
    table: str = Field(min_length=1)
    column: str = Field(min_length=1)


class MappingProfile(BaseModel):
    database_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    status: MappingStatus
    fields: dict[str, FieldMapping]


class HarnessRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    database_id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def question_must_have_content(cls, question: str) -> str:
        normalized = question.strip()
        if not normalized:
            raise ValueError("question must not be blank")
        return normalized


class TraceEvent(BaseModel):
    stage: HarnessStage
    success: bool
    elapsed_ms: int = Field(ge=0)
    details: dict[str, Any] = Field(default_factory=dict)


class HarnessError(BaseModel):
    code: HarnessErrorCode
    stage: HarnessStage | None = None
    message: str


class HarnessResult(BaseModel):
    request_id: str
    status: HarnessStatus
    query_ir: QueryIR | None = None
    sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    error: HarnessError | None = None
    trace: list[TraceEvent] = Field(default_factory=list)


class CompiledQuery(BaseModel):
    sql: str
    parameters: list[str | int | float | bool]
    expected_columns: list[str]

