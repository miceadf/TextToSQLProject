"""Gold benchmark contracts; these are not model predictions or SQL strings."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..harness.contracts import QueryIR


EVALUATION_SET_SCHEMA_VERSION = "fixture-evaluation-set-v1"


class OfferingKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    year: int = Field(ge=2000, le=2100)
    semester: str = Field(min_length=1)
    course_code: str = Field(min_length=1)
    section: str = Field(min_length=1)

    @property
    def sort_key(self) -> tuple[int, str, str, str]:
        return self.year, self.semester, self.course_code, self.section


class Answerability(str, Enum):
    MATCHES = "MATCHES"
    NO_MATCH = "NO_MATCH"


class ReviewStatus(str, Enum):
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
    HUMAN_APPROVED = "HUMAN_APPROVED"


Scalar = str | int | float | bool


class ExpectedSemanticResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offering_keys: list[OfferingKey]
    rows: list[dict[str, Scalar]]
    cardinality: int = Field(ge=0)
    answerability: Answerability

    @model_validator(mode="after")
    def validate_counts(self) -> "ExpectedSemanticResult":
        if len(self.offering_keys) != self.cardinality or len(self.rows) != self.cardinality:
            raise ValueError("gold key, row, and cardinality counts must match")
        keys = [key.sort_key for key in self.offering_keys]
        if keys != sorted(set(keys)):
            raise ValueError("gold offering keys must be unique and sorted")
        if (self.cardinality == 0) != (self.answerability is Answerability.NO_MATCH):
            raise ValueError("gold answerability does not match cardinality")
        return self


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^P2-[0-9]{3}$")
    question_ko: str = Field(min_length=2, max_length=500)
    gold_query_ir: QueryIR
    expected: ExpectedSemanticResult
    intent_family: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    paraphrase_family: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]*$")
    review_status: ReviewStatus = ReviewStatus.NEEDS_HUMAN_REVIEW
    review_rationale: str = Field(min_length=8)
    author_id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    reviewer_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]*$")

    @model_validator(mode="after")
    def validate_review_provenance(self) -> "EvaluationCase":
        if self.review_status is ReviewStatus.HUMAN_APPROVED and not self.reviewer_id:
            raise ValueError("human-approved gold requires a reviewer id")
        if self.review_status is ReviewStatus.NEEDS_HUMAN_REVIEW and self.reviewer_id:
            raise ValueError("unapproved gold cannot claim a reviewer")
        if self.expected.cardinality >= self.gold_query_ir.limit:
            raise ValueError("gold result must not be truncated by LIMIT")
        return self


class EvaluationSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["fixture-evaluation-set-v1"] = EVALUATION_SET_SCHEMA_VERSION
    benchmark_id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    version: int = Field(ge=1)
    seed_version: int = Field(ge=1)
    seed_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    ontology_version: int = Field(default=2, ge=1)
    ontology_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    query_ir_contract_version: Literal[1] = 1
    query_ir_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    cases: list[EvaluationCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_order(self) -> "EvaluationSet":
        ids = [case.case_id for case in self.cases]
        if ids != sorted(set(ids)):
            raise ValueError("evaluation cases must have unique sorted IDs")
        return self
