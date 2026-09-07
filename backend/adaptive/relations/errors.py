from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RelationErrorCode(str, Enum):
    INVALID_FOREIGN_KEY = "INVALID_FOREIGN_KEY"
    PROFILE_MISMATCH = "PROFILE_MISMATCH"
    UNKNOWN_SEMANTIC_FIELD = "UNKNOWN_SEMANTIC_FIELD"
    UNKNOWN_PHYSICAL_TABLE = "UNKNOWN_PHYSICAL_TABLE"
    DISCONNECTED_TABLES = "DISCONNECTED_TABLES"
    SEARCH_LIMIT_EXCEEDED = "SEARCH_LIMIT_EXCEEDED"


class RelationIssue(BaseModel):
    code: RelationErrorCode
    message: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)


class RelationGraphError(ValueError):
    def __init__(self, issue: RelationIssue) -> None:
        super().__init__(issue.message)
        self.issue = issue
