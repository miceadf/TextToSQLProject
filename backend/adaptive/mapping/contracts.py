"""스키마 연결 제안·검토 초안·게시 profile의 입출력 계약.

후보와 근거는 제안에, 사람의 결정은 초안에, 실행할 연결은 게시본에 담는다.
각 기록의 ontology 버전을 보존하며 게시 가능 여부는 review 서비스가 판단한다.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class MappingSignal(str, Enum):
    COLUMN_NAME = "COLUMN_NAME"
    DATA_TYPE = "DATA_TYPE"
    TABLE_NAME = "TABLE_NAME"
    # Phase 8-12: a local model chose this column (added to the rule evidence).
    AI_PROPOSAL = "AI_PROPOSAL"


class MappingReviewStatus(str, Enum):
    AUTO_APPROVE_CANDIDATE = "AUTO_APPROVE_CANDIDATE"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    UNCERTAIN = "UNCERTAIN"


class MappingEvidence(BaseModel):
    signal: MappingSignal
    score: float = Field(ge=0.0, le=1.0)
    detail: str


class MappingCandidate(BaseModel):
    semantic_field: str = Field(min_length=1)
    table: str = Field(min_length=1)
    column: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    review_status: MappingReviewStatus
    evidence: list[MappingEvidence]


class MappingProposal(BaseModel):
    database_id: str = Field(min_length=1)
    schema_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    ontology_version: int = Field(ge=1)
    candidates: dict[str, list[MappingCandidate]]
    unmapped_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_candidate_keys(self) -> "MappingProposal":
        for semantic_field, candidates in self.candidates.items():
            if any(candidate.semantic_field != semantic_field for candidate in candidates):
                raise ValueError("mapping candidate key and semantic_field must match")
        return self


class ReviewDecision(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REMAPPED = "REMAPPED"
    REJECTED = "REJECTED"
    IGNORED = "IGNORED"


class ReviewedFieldMapping(BaseModel):
    semantic_field: str = Field(min_length=1)
    decision: ReviewDecision = ReviewDecision.PENDING
    table: str | None = None
    column: str | None = None
    source_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reviewer: str | None = None
    reason: str | None = None
    reviewed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_state(self) -> "ReviewedFieldMapping":
        has_table = self.table is not None
        has_column = self.column is not None
        if has_table != has_column:
            raise ValueError("table and column must be set together")
        if self.decision in {ReviewDecision.APPROVED, ReviewDecision.REMAPPED}:
            if not has_table:
                raise ValueError("approved mappings require table and column")
            if not self.reviewer or self.reviewed_at is None:
                raise ValueError("reviewed mappings require reviewer and reviewed_at")
        if self.decision in {ReviewDecision.REJECTED, ReviewDecision.IGNORED}:
            if has_table:
                raise ValueError("rejected or ignored mappings cannot target a column")
            if not self.reviewer or self.reviewed_at is None:
                raise ValueError("reviewed mappings require reviewer and reviewed_at")
        if self.decision in {
            ReviewDecision.REMAPPED,
            ReviewDecision.REJECTED,
            ReviewDecision.IGNORED,
        } and not self.reason:
            raise ValueError(f"{self.decision.value.lower()} mappings require reason")
        if self.decision is ReviewDecision.PENDING and (
            self.reviewer or self.reason or self.reviewed_at
        ):
            raise ValueError("pending mappings cannot contain review metadata")
        return self


class MappingDraft(BaseModel):
    database_id: str = Field(min_length=1)
    schema_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    ontology_version: int = Field(ge=1)
    fields: dict[str, ReviewedFieldMapping]

    @model_validator(mode="after")
    def validate_field_keys(self) -> "MappingDraft":
        if any(key != entry.semantic_field for key, entry in self.fields.items()):
            raise ValueError("draft field key and semantic_field must match")
        return self


class PublishedFieldMapping(BaseModel):
    table: str = Field(min_length=1)
    column: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    reason: str | None = None


class PublishedMappingProfile(BaseModel):
    database_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    status: Literal["PUBLISHED"] = "PUBLISHED"
    schema_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    ontology_version: int = Field(ge=1)
    published_by: str = Field(min_length=1)
    published_at: datetime
    fields: dict[str, PublishedFieldMapping]
