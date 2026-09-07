from __future__ import annotations

from enum import Enum


class MappingWorkflowErrorCode(str, Enum):
    INVALID_DATABASE_ID = "INVALID_DATABASE_ID"
    NOT_FOUND = "NOT_FOUND"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    CANDIDATE_NOT_FOUND = "CANDIDATE_NOT_FOUND"
    PROPOSAL_MISMATCH = "PROPOSAL_MISMATCH"
    PUBLISH_BLOCKED = "PUBLISH_BLOCKED"


class MappingWorkflowError(Exception):
    def __init__(self, code: MappingWorkflowErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class MappingPublishError(MappingWorkflowError):
    def __init__(self, issues: list[str]) -> None:
        super().__init__(
            MappingWorkflowErrorCode.PUBLISH_BLOCKED,
            "mapping publish blocked: " + "; ".join(issues),
        )
        self.issues = issues
