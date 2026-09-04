from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class MappingSignal(str, Enum):
    COLUMN_NAME = "COLUMN_NAME"
    DATA_TYPE = "DATA_TYPE"
    TABLE_NAME = "TABLE_NAME"


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
