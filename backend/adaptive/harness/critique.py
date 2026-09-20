"""Deterministic critique disposition for bounded-repair summaries.

The critique classifies a `RepairSummary` into a fixed follow-up bucket.
It never judges answer correctness, model accuracy, or user satisfaction:
success only means a safe execution finished, including empty results.
Failures are classified by the final (code, stage) pair only; raw error
text is never reflected. No user confirmation or admin queue action is
taken here; the result is a follow-up suggestion, not an approval,
publish, or retry instruction.

Synthetic use only: pure function over `RepairSummary`. No question,
QueryIR, SQL, rows, exceptions, traces, paths, or secrets are accepted,
returned, or logged. No file, DB, or network access.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .contracts import HarnessErrorCode, HarnessStage, HarnessStatus
from .repair import REPAIR_PROVIDER_ERROR, RepairSummary, is_repairable


CRITIQUE_DISPOSITION_VERSION = "critique-disposition-v1"

CRITIQUE_INVALID_SUMMARY = "CRITIQUE_INVALID_SUMMARY"


class CritiqueDisposition(str, Enum):
    EXECUTED_UNVERIFIED = "EXECUTED_UNVERIFIED"
    REQUEST_INVALID = "REQUEST_INVALID"
    MODEL_REVIEW = "MODEL_REVIEW"
    CONTEXT_REVIEW = "CONTEXT_REVIEW"
    SAFETY_BLOCKED = "SAFETY_BLOCKED"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"
    INTERNAL_REVIEW = "INTERNAL_REVIEW"


class CritiqueError(Exception):
    """Fail-closed error with a fixed code only; never reflects input."""

    def __init__(self) -> None:
        super().__init__(CRITIQUE_INVALID_SUMMARY)
        self.code = CRITIQUE_INVALID_SUMMARY


class CritiqueResult(BaseModel):
    """Fixed disposition contract; codes and repair flags only.

    `repair_attempted` mirrors whether the coordinator ran its single
    repair; `recovered_after_repair` is true only for a success reached
    through that repair, distinguishing initial success from recovery
    and from a repair that still failed.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    critique_version: str = Field(min_length=1)
    disposition: CritiqueDisposition = Field(strict=False)
    final_code: str | None = None
    final_stage: HarnessStage | None = Field(default=None, strict=False)
    repair_attempted: bool
    recovered_after_repair: bool


# Allowed (code, stage) pairs by disposition. Code alone never classifies;
# a known code with any other stage is rejected as invalid input.
_DISPOSITION_TABLE: dict[CritiqueDisposition, dict[str, set[HarnessStage | None]]] = {
    CritiqueDisposition.REQUEST_INVALID: {
        HarnessErrorCode.INVALID_REQUEST.value: {HarnessStage.REQUEST_VALIDATED},
    },
    CritiqueDisposition.MODEL_REVIEW: {
        HarnessErrorCode.MODEL_INVALID_OUTPUT.value: {HarnessStage.IR_GENERATED},
        HarnessErrorCode.UNKNOWN_ENTITY.value: {HarnessStage.IR_VALIDATED},
        HarnessErrorCode.UNKNOWN_FIELD.value: {HarnessStage.IR_VALIDATED},
        HarnessErrorCode.INVALID_OPERATOR.value: {HarnessStage.IR_VALIDATED},
        HarnessErrorCode.INVALID_VALUE_TYPE.value: {HarnessStage.IR_VALIDATED},
        REPAIR_PROVIDER_ERROR: {None},
    },
    CritiqueDisposition.CONTEXT_REVIEW: {
        HarnessErrorCode.DATABASE_NOT_FOUND.value: {HarnessStage.EXECUTED},
        HarnessErrorCode.MAPPING_NOT_PUBLISHED.value: {HarnessStage.CONTEXT_READY},
        HarnessErrorCode.MAPPING_MISSING.value: {
            HarnessStage.CONTEXT_READY,
            HarnessStage.IR_VALIDATED,
            HarnessStage.SQL_COMPILED,
        },
        HarnessErrorCode.MULTI_TABLE_NOT_SUPPORTED.value: {HarnessStage.SQL_COMPILED},
        HarnessErrorCode.AMBIGUOUS_JOIN_PATH.value: {
            HarnessStage.SQL_COMPILED,
            HarnessStage.SQL_VALIDATED,
        },
        HarnessErrorCode.INVALID_JOIN_PLAN.value: {
            HarnessStage.SQL_COMPILED,
            HarnessStage.SQL_VALIDATED,
        },
        HarnessErrorCode.SELF_JOIN_NOT_SUPPORTED.value: {HarnessStage.SQL_COMPILED},
    },
    CritiqueDisposition.SAFETY_BLOCKED: {
        HarnessErrorCode.UNSAFE_SQL.value: {HarnessStage.SQL_VALIDATED},
        HarnessErrorCode.SQL_PARSE_ERROR.value: {HarnessStage.SQL_VALIDATED},
        HarnessErrorCode.MULTIPLE_STATEMENTS.value: {HarnessStage.SQL_VALIDATED},
        HarnessErrorCode.UNSAFE_SQL_NODE.value: {HarnessStage.SQL_VALIDATED},
        HarnessErrorCode.UNAUTHORIZED_TABLE.value: {HarnessStage.SQL_VALIDATED},
        HarnessErrorCode.UNAUTHORIZED_COLUMN.value: {HarnessStage.SQL_VALIDATED},
        HarnessErrorCode.INVALID_JOIN_PREDICATE.value: {HarnessStage.SQL_VALIDATED},
        HarnessErrorCode.INVALID_PROJECTION.value: {HarnessStage.SQL_VALIDATED},
        HarnessErrorCode.INVALID_PLACEHOLDER.value: {HarnessStage.SQL_VALIDATED},
        HarnessErrorCode.INVALID_LIMIT.value: {HarnessStage.SQL_VALIDATED},
    },
    CritiqueDisposition.EXECUTION_FAILURE: {
        HarnessErrorCode.EXECUTION_TIMEOUT.value: {HarnessStage.EXECUTED},
        HarnessErrorCode.EXECUTION_ERROR.value: {HarnessStage.EXECUTED},
        HarnessErrorCode.RESULT_TOO_LARGE.value: {HarnessStage.EXECUTED},
        HarnessErrorCode.RESULT_SCHEMA_MISMATCH.value: {HarnessStage.RESULT_VALIDATED},
    },
    CritiqueDisposition.INTERNAL_REVIEW: {
        HarnessErrorCode.SQL_COMPILE_ERROR.value: {HarnessStage.SQL_COMPILED},
    },
}

_KNOWN_CODES: frozenset[str] = frozenset(
    code for bucket in _DISPOSITION_TABLE.values() for code in bucket
)


def _disposition_of(code: str, stage: HarnessStage | None) -> CritiqueDisposition:
    for disposition, bucket in _DISPOSITION_TABLE.items():
        allowed = bucket.get(code)
        if allowed is not None and stage in allowed:
            return disposition
    raise CritiqueError()


def disposition_for(code: str, stage: HarnessStage | None) -> CritiqueDisposition:
    """Return the fixed disposition for one (code, stage) pair.

    Single public entry point over the critique disposition table, so
    follow-up stores (e.g. review-queue snapshots) can verify a stored
    code/stage/disposition triple without duplicating the taxonomy.
    Unknown codes, unknown stages, and pairs outside the table raise
    `CritiqueError` with the fixed code only.
    """
    if not isinstance(code, str) or not isinstance(stage, (HarnessStage, type(None))):
        raise CritiqueError()
    return _disposition_of(code, stage)


def _check_pair(code: str | None, stage: HarnessStage | None, *, succeeded: bool) -> None:
    if succeeded:
        if code is not None or stage is not HarnessStage.RESULT_VALIDATED:
            raise CritiqueError()
        return
    if code is None or code not in _KNOWN_CODES:
        raise CritiqueError()
    _disposition_of(code, stage)


def _is_retryable_initial(code: str, stage: HarnessStage | None) -> bool:
    """Initial failures must be Phase 6-01 retryable pairs to allow repair."""
    if code == REPAIR_PROVIDER_ERROR or stage is None:
        return False
    try:
        parsed = HarnessErrorCode(code)
    except ValueError:
        return False
    return is_repairable(parsed, stage)


def critique_repair_summary(summary: RepairSummary) -> CritiqueResult:
    """Classify a repair summary without touching models, DB, or files."""
    try:
        if not isinstance(summary, RepairSummary):
            raise TypeError()
        # model_copy(update=...) and model_construct() skip validation.
        # Suppress serializer warnings because they can contain forged input.
        summary = RepairSummary.model_validate(
            summary.model_dump(mode="json", warnings=False)
        )
    except Exception:
        raise CritiqueError() from None
    if summary.repair_attempted != (summary.repair_attempts > 0):
        raise CritiqueError()
    if summary.succeeded != (summary.final_status is HarnessStatus.SUCCEEDED):
        raise CritiqueError()

    if not summary.repair_attempted:
        # Without repair the final outcome must equal the initial outcome;
        # a coordinator that ran zero repairs cannot change code or stage.
        if (
            summary.final_status is not summary.initial_status
            or summary.final_stage is not summary.initial_stage
            or summary.final_code != summary.initial_code
        ):
            raise CritiqueError()
        if summary.succeeded:
            _check_pair(summary.final_code, summary.final_stage, succeeded=True)
            return CritiqueResult(
                critique_version=CRITIQUE_DISPOSITION_VERSION,
                disposition=CritiqueDisposition.EXECUTED_UNVERIFIED,
                final_code=None,
                final_stage=HarnessStage.RESULT_VALIDATED,
                repair_attempted=False,
                recovered_after_repair=False,
            )
        if summary.final_code is None:
            raise CritiqueError()
        # This code is produced only when a repair provider was called.
        if summary.final_code == REPAIR_PROVIDER_ERROR:
            raise CritiqueError()
        disposition = _disposition_of(summary.final_code, summary.final_stage)
        return CritiqueResult(
            critique_version=CRITIQUE_DISPOSITION_VERSION,
            disposition=disposition,
            final_code=summary.final_code,
            final_stage=summary.final_stage,
            repair_attempted=False,
            recovered_after_repair=False,
        )

    # A repair ran, so the initial outcome must be a Phase 6-01 retryable
    # failure; the coordinator never repairs input, policy, context,
    # execution, or internal failures.
    if summary.initial_status is not HarnessStatus.FAILED:
        raise CritiqueError()
    if summary.initial_code is None:
        raise CritiqueError()
    _check_pair(summary.initial_code, summary.initial_stage, succeeded=False)
    if not _is_retryable_initial(summary.initial_code, summary.initial_stage):
        raise CritiqueError()
    if summary.succeeded:
        _check_pair(summary.final_code, summary.final_stage, succeeded=True)
        return CritiqueResult(
            critique_version=CRITIQUE_DISPOSITION_VERSION,
            disposition=CritiqueDisposition.EXECUTED_UNVERIFIED,
            final_code=None,
            final_stage=HarnessStage.RESULT_VALIDATED,
            repair_attempted=True,
            recovered_after_repair=True,
        )
    if summary.final_code is None:
        raise CritiqueError()
    disposition = _disposition_of(summary.final_code, summary.final_stage)
    return CritiqueResult(
        critique_version=CRITIQUE_DISPOSITION_VERSION,
        disposition=disposition,
        final_code=summary.final_code,
        final_stage=summary.final_stage,
        repair_attempted=True,
        recovered_after_repair=False,
    )
