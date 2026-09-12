"""Versioned contracts for private IR pseudonymization artifacts."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.adaptive.harness.contracts import QueryIR

from .conversion_contracts import ConversionStatus, LimitProvenance


PSEUDONYM_PROFILE_SCHEMA_VERSION = "pseudonym-profile-v1"
PSEUDONYM_RECORD_SCHEMA_VERSION = "pseudonymized-query-ir-record-v1"
PSEUDONYM_REPORT_SCHEMA_VERSION = "pseudonymization-summary-v1"
PSEUDONYM_TOOL_VERSION = "ir-pseudonymizer-v1"
PSEUDONYM_ALGORITHM_VERSION = "hmac-sha256-v1"


class PseudonymPolicy(str, Enum):
    PSEUDONYMIZE = "PSEUDONYMIZE"
    PRESERVE = "PRESERVE"
    REVIEW = "REVIEW"


class PseudonymFieldPolicy(BaseModel):
    field: str = Field(min_length=1)
    policy: PseudonymPolicy
    namespace: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")

    @model_validator(mode="after")
    def validate_namespace(self) -> "PseudonymFieldPolicy":
        if self.policy is PseudonymPolicy.PSEUDONYMIZE and self.namespace is None:
            raise ValueError("pseudonymized field requires a namespace")
        if self.policy is not PseudonymPolicy.PSEUDONYMIZE and self.namespace is not None:
            raise ValueError("non-pseudonymized field cannot have a namespace")
        return self


class PseudonymizationProfile(BaseModel):
    schema_version: Literal["pseudonym-profile-v1"] = PSEUDONYM_PROFILE_SCHEMA_VERSION
    profile_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
    version: int = Field(ge=1)
    ontology_version: int = Field(ge=1)
    field_policies: list[PseudonymFieldPolicy] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_sorted_fields(self) -> "PseudonymizationProfile":
        names = [item.field for item in self.field_policies]
        if names != sorted(set(names)):
            raise ValueError("field policies must be unique and sorted")
        return self

    @property
    def policies_by_field(self) -> dict[str, PseudonymFieldPolicy]:
        return {item.field: item for item in self.field_policies}


class PseudonymStatus(str, Enum):
    PSEUDONYMIZED_EXACT = "PSEUDONYMIZED_EXACT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNSUPPORTED = "UNSUPPORTED"


class PseudonymReasonCode(str, Enum):
    INPUT_SCHEMA_VERSION_MISMATCH = "INPUT_SCHEMA_VERSION_MISMATCH"
    INPUT_METADATA_MISMATCH = "INPUT_METADATA_MISMATCH"
    NON_CONVERTED_INPUT = "NON_CONVERTED_INPUT"
    QUESTION_SQL_CONFLICT_PRESENT = "QUESTION_SQL_CONFLICT_PRESENT"
    PROFILE_VERSION_MISMATCH = "PROFILE_VERSION_MISMATCH"
    ONTOLOGY_VERSION_MISMATCH = "ONTOLOGY_VERSION_MISMATCH"
    UNKNOWN_SEMANTIC_FIELD = "UNKNOWN_SEMANTIC_FIELD"
    FIELD_POLICY_MISSING = "FIELD_POLICY_MISSING"
    FIELD_REQUIRES_REVIEW = "FIELD_REQUIRES_REVIEW"
    NON_STRING_SENSITIVE_VALUE = "NON_STRING_SENSITIVE_VALUE"
    EMPTY_SENSITIVE_VALUE = "EMPTY_SENSITIVE_VALUE"
    NORMALIZATION_COLLISION = "NORMALIZATION_COLLISION"
    AMBIGUOUS_SURFACE_NAMESPACE = "AMBIGUOUS_SURFACE_NAMESPACE"
    OVERLAPPING_TEXT_MATCH = "OVERLAPPING_TEXT_MATCH"
    SENSITIVE_VALUE_NOT_FOUND_IN_TEXT = "SENSITIVE_VALUE_NOT_FOUND_IN_TEXT"
    ORIGINAL_VALUE_REMAINS = "ORIGINAL_VALUE_REMAINS"
    PSEUDONYM_COLLISION_RESOLVED = "PSEUDONYM_COLLISION_RESOLVED"
    QUERY_IR_STRUCTURE_CHANGED = "QUERY_IR_STRUCTURE_CHANGED"
    QUESTION_IR_TOKEN_MISMATCH = "QUESTION_IR_TOKEN_MISMATCH"
    OUTPUT_VALIDATION_FAILED = "OUTPUT_VALIDATION_FAILED"
    PSEUDONYMIZATION_ERROR = "PSEUDONYMIZATION_ERROR"


class PseudonymizedRecord(BaseModel):
    """Private artifact. Original source identifiers are intentionally absent."""

    schema_version: Literal["pseudonymized-query-ir-record-v1"] = PSEUDONYM_RECORD_SCHEMA_VERSION
    dataset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    profile_id: str = Field(min_length=1)
    profile_version: int = Field(ge=1)
    ontology_version: int = Field(ge=1)
    key_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    record_id: str = Field(pattern=r"^LIN_[0-9A-F]{64}$")
    canonical_lineage: str = Field(pattern=r"^LIN_[0-9A-F]{64}$")
    shape_lineage: str = Field(pattern=r"^LIN_[0-9A-F]{64}$")
    exact_duplicate_lineage: str | None = Field(default=None, pattern=r"^LIN_[0-9A-F]{64}$")
    question_sql_duplicate_lineage: str | None = Field(default=None, pattern=r"^LIN_[0-9A-F]{64}$")
    instruction: str
    question: str
    query_ir: QueryIR
    conversion_status: ConversionStatus
    limit_provenance: LimitProvenance
    normalization_changed: bool = False
    collision_resolved: bool = False

    @model_validator(mode="after")
    def validate_conversion_status(self) -> "PseudonymizedRecord":
        if self.conversion_status not in {
            ConversionStatus.CONVERTED_EXACT,
            ConversionStatus.CONVERTED_WITH_EXECUTION_POLICY,
        }:
            raise ValueError("only converted records can be pseudonymized")
        expected_provenance = (
            LimitProvenance.SOURCE
            if self.conversion_status is ConversionStatus.CONVERTED_EXACT
            else LimitProvenance.EXECUTION_POLICY_DEFAULT
        )
        if self.limit_provenance is not expected_provenance:
            raise ValueError("conversion status and limit provenance disagree")
        return self


class PseudonymReviewItem(BaseModel):
    """Local private review reference; never part of the public summary."""

    source_line: int | None = Field(default=None, ge=1)
    input_line: int | None = Field(default=None, ge=1)
    reason_codes: list[PseudonymReasonCode] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_reason_order(self) -> "PseudonymReviewItem":
        if self.source_line is None and self.input_line is None:
            raise ValueError("review item needs a source or input line")
        order = {reason: index for index, reason in enumerate(PseudonymReasonCode)}
        if self.reason_codes != sorted(set(self.reason_codes), key=order.__getitem__):
            raise ValueError("review reason codes must be unique and enum ordered")
        return self


class PseudonymSummaryReport(BaseModel):
    """Public, deterministic aggregate only; no record references or tokens."""

    schema_version: Literal["pseudonymization-summary-v1"] = PSEUDONYM_REPORT_SCHEMA_VERSION
    tool_version: Literal["ir-pseudonymizer-v1"] = PSEUDONYM_TOOL_VERSION
    algorithm_version: Literal["hmac-sha256-v1"] = PSEUDONYM_ALGORITHM_VERSION
    dataset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    dataset_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    profile_id: str = Field(min_length=1)
    profile_version: int = Field(ge=1)
    ontology_version: int = Field(ge=1)
    key_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    input_record_count: int = Field(ge=0)
    output_record_count: int = Field(ge=0)
    review_record_count: int = Field(ge=0)
    unsupported_record_count: int = Field(ge=0)
    status_counts: dict[str, int] = Field(default_factory=dict)
    reason_code_counts: dict[str, int] = Field(default_factory=dict)
    field_unique_value_counts: dict[str, int] = Field(default_factory=dict)
    field_length_bucket_counts: dict[str, dict[str, int]] = Field(default_factory=dict)
    known_value_leak_count: int = Field(ge=0)
    residual_risk_note: str = (
        "Known-value checks cannot establish anonymity or detect unknown identifiers."
    )
