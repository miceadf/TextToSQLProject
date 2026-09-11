from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.adaptive.harness.contracts import QueryIR
from backend.adaptive.ontology.contracts import Operator

from .contracts import DatasetIssue
from .sql_contracts import IrCompatibilityStatus, IrReasonCode


CONVERSION_REPORT_SCHEMA_VERSION = "query-ir-conversion-report-v1"
CONVERSION_TOOL_VERSION = "sql-to-query-ir-converter-v1"
PRIVATE_RECORD_SCHEMA_VERSION = "private-query-ir-record-v1"


class SourceProfileStatus(str, Enum):
    APPROVED = "APPROVED"


class SourceLiteralType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"


class SourceColumnMapping(BaseModel):
    table: str = Field(min_length=1)
    column: str = Field(min_length=1)
    semantic_field: str = Field(min_length=1)
    allowed_literal_types: list[SourceLiteralType] = Field(min_length=1)
    evidence: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_literal_types(self) -> "SourceColumnMapping":
        if len(self.allowed_literal_types) != len(set(self.allowed_literal_types)):
            raise ValueError("allowed literal types must be unique")
        if self.allowed_literal_types != sorted(
            self.allowed_literal_types,
            key=lambda item: item.value,
        ):
            raise ValueError("allowed literal types must be sorted")
        return self


class DatasetSchemaProfile(BaseModel):
    schema_version: Literal["dataset-schema-profile-v1"] = (
        "dataset-schema-profile-v1"
    )
    profile_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    status: SourceProfileStatus
    dialect: str = Field(min_length=1)
    table_allowlist: list[str] = Field(min_length=1)
    root_entity: str = Field(min_length=1)
    ontology_version: int = Field(ge=1)
    reviewed_by: str = Field(min_length=1)
    mappings: list[SourceColumnMapping] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_deterministic_unique_mappings(self) -> "DatasetSchemaProfile":
        if self.table_allowlist != sorted(set(self.table_allowlist)):
            raise ValueError("table allowlist must be unique and sorted")
        physical = [(item.table, item.column) for item in self.mappings]
        semantic = [item.semantic_field for item in self.mappings]
        if len(physical) != len(set(physical)):
            raise ValueError("source profile has duplicate physical columns")
        if len(semantic) != len(set(semantic)):
            raise ValueError("source profile has duplicate semantic fields")
        if physical != sorted(physical):
            raise ValueError("source profile mappings must be sorted by table and column")
        if any(item.table not in self.table_allowlist for item in self.mappings):
            raise ValueError("source mapping table is not allowlisted")
        return self

    @property
    def mappings_by_physical(self) -> dict[tuple[str, str], SourceColumnMapping]:
        return {(item.table, item.column): item for item in self.mappings}


class ConversionStatus(str, Enum):
    CONVERTED_EXACT = "CONVERTED_EXACT"
    CONVERTED_WITH_EXECUTION_POLICY = "CONVERTED_WITH_EXECUTION_POLICY"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNSUPPORTED = "UNSUPPORTED"


class ConversionReasonCode(str, Enum):
    ANALYZER_NOT_CURRENT_IR_CANDIDATE = "ANALYZER_NOT_CURRENT_IR_CANDIDATE"
    QUESTION_SQL_CONFLICT = "QUESTION_SQL_CONFLICT"
    SOURCE_PROFILE_MISMATCH = "SOURCE_PROFILE_MISMATCH"
    UNKNOWN_SOURCE_TABLE = "UNKNOWN_SOURCE_TABLE"
    UNMAPPED_SOURCE_COLUMN = "UNMAPPED_SOURCE_COLUMN"
    AMBIGUOUS_QUOTED_IDENTIFIER = "AMBIGUOUS_QUOTED_IDENTIFIER"
    UNSUPPORTED_PROJECTION = "UNSUPPORTED_PROJECTION"
    DUPLICATE_SEMANTIC_PROJECTION = "DUPLICATE_SEMANTIC_PROJECTION"
    UNSUPPORTED_BOOLEAN_STRUCTURE = "UNSUPPORTED_BOOLEAN_STRUCTURE"
    UNSUPPORTED_OPERATOR = "UNSUPPORTED_OPERATOR"
    OPERATOR_NOT_ALLOWED_FOR_FIELD = "OPERATOR_NOT_ALLOWED_FOR_FIELD"
    NON_LITERAL_FILTER_VALUE = "NON_LITERAL_FILTER_VALUE"
    LITERAL_TYPE_MISMATCH = "LITERAL_TYPE_MISMATCH"
    UNSAFE_LITERAL_COERCION = "UNSAFE_LITERAL_COERCION"
    UNSUPPORTED_LIKE_PATTERN = "UNSUPPORTED_LIKE_PATTERN"
    EMPTY_CONTAINS_VALUE = "EMPTY_CONTAINS_VALUE"
    INVALID_SOURCE_LIMIT = "INVALID_SOURCE_LIMIT"
    DEFAULT_LIMIT_APPLIED = "DEFAULT_LIMIT_APPLIED"
    QUERY_IR_VALIDATION_FAILED = "QUERY_IR_VALIDATION_FAILED"
    INTENT_SIGNATURE_MISMATCH = "INTENT_SIGNATURE_MISMATCH"
    CONVERSION_ERROR = "CONVERSION_ERROR"


class LimitProvenance(str, Enum):
    SOURCE = "SOURCE"
    EXECUTION_POLICY_DEFAULT = "EXECUTION_POLICY_DEFAULT"


class IntentFilterSignature(BaseModel):
    field: str = Field(min_length=1)
    operator: Operator
    value: str | int | float | bool


class IntentSignature(BaseModel):
    root_entity: str
    select: list[str]
    filters: list[IntentFilterSignature] = Field(default_factory=list)
    limit: int = Field(ge=1, le=200)
    limit_provenance: LimitProvenance


class DuplicateProvenance(BaseModel):
    exact_duplicate_group_id: str | None = None
    question_sql_duplicate_group_id: str | None = None
    question_conflict_group_id: str | None = None


class DatasetConversionResult(BaseModel):
    source_line: int = Field(ge=1)
    canonical_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    shape_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    status: ConversionStatus
    reason_codes: list[ConversionReasonCode] = Field(default_factory=list)
    analyzer_status: IrCompatibilityStatus
    analyzer_reason_codes: list[IrReasonCode] = Field(default_factory=list)
    duplicate_provenance: DuplicateProvenance = Field(
        default_factory=DuplicateProvenance
    )
    query_ir: QueryIR | None = None
    intent_signature: IntentSignature | None = None


class ConversionRecordSummary(BaseModel):
    source_line: int = Field(ge=1)
    canonical_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    shape_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    status: ConversionStatus
    reason_codes: list[ConversionReasonCode] = Field(default_factory=list)
    analyzer_status: IrCompatibilityStatus
    analyzer_reason_codes: list[IrReasonCode] = Field(default_factory=list)
    duplicate_provenance: DuplicateProvenance = Field(
        default_factory=DuplicateProvenance
    )
    preview: str | None = None


class ConversionReviewItem(BaseModel):
    source_line: int = Field(ge=1)
    canonical_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    shape_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reason_codes: list[ConversionReasonCode] = Field(min_length=1)
    duplicate_provenance: DuplicateProvenance = Field(
        default_factory=DuplicateProvenance
    )
    preview: str | None = None


class ConversionSummary(BaseModel):
    total_lines: int = Field(ge=0)
    valid_dataset_records: int = Field(ge=0)
    invalid_dataset_records: int = Field(ge=0)
    status_counts: dict[str, int] = Field(default_factory=dict)
    reason_code_counts: dict[str, int] = Field(default_factory=dict)
    analyzer_status_counts: dict[str, int] = Field(default_factory=dict)
    exact_duplicate_record_count: int = Field(ge=0)
    question_sql_duplicate_record_count: int = Field(ge=0)
    question_conflict_record_count: int = Field(ge=0)


class QueryIrConversionReport(BaseModel):
    schema_version: Literal["query-ir-conversion-report-v1"] = (
        CONVERSION_REPORT_SCHEMA_VERSION
    )
    tool_version: Literal["sql-to-query-ir-converter-v1"] = (
        CONVERSION_TOOL_VERSION
    )
    dataset_id: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_id: str = Field(min_length=1)
    profile_version: int = Field(ge=1)
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ontology_version: int = Field(ge=1)
    dialect: str = Field(min_length=1)
    default_limit: int = Field(ge=1, le=200)
    summary: ConversionSummary
    dataset_issues: list[DatasetIssue] = Field(default_factory=list)
    records: list[ConversionRecordSummary] = Field(default_factory=list)
    review_items: list[ConversionReviewItem] = Field(default_factory=list)


class PrivateConvertedRecord(BaseModel):
    schema_version: Literal["private-query-ir-record-v1"] = (
        PRIVATE_RECORD_SCHEMA_VERSION
    )
    dataset_id: str = Field(min_length=1)
    dataset_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_id: str = Field(min_length=1)
    profile_version: int = Field(ge=1)
    ontology_version: int = Field(ge=1)
    source_line: int = Field(ge=1)
    canonical_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    shape_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    duplicate_provenance: DuplicateProvenance = Field(
        default_factory=DuplicateProvenance
    )
    instruction: str
    question: str
    query_ir: QueryIR
    conversion_status: ConversionStatus
    limit_provenance: LimitProvenance
