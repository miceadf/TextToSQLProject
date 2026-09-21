"""In-memory operational-failure review candidate queue (synthetic).

Phase 6-04 boundary: connect Phase 6-01 `RepairSummary` with the Phase 6-02
`critique_repair_summary()` classifier and register only operational
failure candidates in memory. This is not operational storage, an admin
UI, a human-approval flow, or a retry/publish instruction.

Contract (`review-queue-v1`):

- Input is the original `RepairSummary` plus explicit raw `model_run_id`
  and `request_id` strings. The queue calls
  `critique_repair_summary()` internally; there is no API that accepts a
  caller-supplied `CritiqueResult` or a caller-chosen disposition.
- `EXECUTED_UNVERIFIED` and `REQUEST_INVALID` are never stored and
  return `NOT_ELIGIBLE`. Only `MODEL_REVIEW`, `CONTEXT_REVIEW`,
  `SAFETY_BLOCKED`, `EXECUTION_FAILURE`, and `INTERNAL_REVIEW` are
  operational-failure candidates. `REPAIR_PROVIDER_ERROR` follows the
  existing critique classification (currently `MODEL_REVIEW`).
- Entries carry fixed metadata only: contract version, domain-separated
  SHA-256 of the raw run/request IDs, final fixed code/stage/disposition,
  repair flags, and a stable idempotency key. Questions, QueryIR, SQL,
  rows, raw exceptions, physical mappings, paths, secrets, and raw
  run/request IDs are never stored, returned, or logged.
- Hash rule: `SHA-256(domain + raw_id)` over canonical UTF-8 bytes, where
  the domains are `review-queue-v1:model-run-id:` and
  `review-queue-v1:request-id:`. Raw IDs are hashed exactly as given
  (no stripping or case folding).
- The same run/request combination with the same classified result
  returns `ALREADY_PRESENT` and keeps one entry. The same combination
  with a different classified result raises a fixed `REVIEW_QUEUE_CONFLICT`
  error and leaves the stored entry unchanged.
- Input-ID validation and critique failures leave the queue unchanged and
  use fixed codes that never reflect caller content.
- Memory only: no disk, DB, network, Ollama, or private-bundle access.
  No persistence, concurrency, cross-process dedupe, or admin-workflow
  guarantees.

Synthetic use only: pure in-memory structure over `RepairSummary`.
"""

from __future__ import annotations

import hashlib
from enum import Enum
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .contracts import HarnessStage
from .critique import (
    CritiqueDisposition,
    CritiqueResult,
    critique_repair_summary,
)
from .repair import RepairSummary


REVIEW_QUEUE_VERSION = "review-queue-v1"

REVIEW_QUEUE_INVALID_INPUT = "REVIEW_QUEUE_INVALID_INPUT"
REVIEW_QUEUE_CONFLICT = "REVIEW_QUEUE_CONFLICT"

_MODEL_RUN_ID_DOMAIN = "review-queue-v1:model-run-id:"
_REQUEST_ID_DOMAIN = "review-queue-v1:request-id:"

_HEX64_PATTERN = r"^[0-9a-f]{64}$"
_MAX_ID_LENGTH = 200

# Operational-failure candidates only. Success and invalid input are
# review candidates for nobody here: success needs no review, and invalid
# input is a caller contract problem, not an operational failure.
_ELIGIBLE_DISPOSITIONS: frozenset[CritiqueDisposition] = frozenset({
    CritiqueDisposition.MODEL_REVIEW,
    CritiqueDisposition.CONTEXT_REVIEW,
    CritiqueDisposition.SAFETY_BLOCKED,
    CritiqueDisposition.EXECUTION_FAILURE,
    CritiqueDisposition.INTERNAL_REVIEW,
})


class ReviewQueueErrorCode(str, Enum):
    INVALID_INPUT = REVIEW_QUEUE_INVALID_INPUT
    CONFLICT = REVIEW_QUEUE_CONFLICT


class ReviewQueueError(Exception):
    """Fail-closed queue error with a fixed code only; never reflects input."""

    def __init__(self, code: ReviewQueueErrorCode) -> None:
        self.code = code.value
        super().__init__(self.code)


class ReviewQueueStatus(str, Enum):
    ENQUEUED = "ENQUEUED"
    ALREADY_PRESENT = "ALREADY_PRESENT"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"


class ReviewQueueEntry(BaseModel):
    """Fixed review-candidate metadata; hashes and codes only."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    queue_version: str = Field(pattern=r"^review-queue-v1$")
    model_run_id_sha256: str = Field(pattern=_HEX64_PATTERN)
    request_id_sha256: str = Field(pattern=_HEX64_PATTERN)
    disposition: CritiqueDisposition = Field(strict=False)
    final_code: str = Field(min_length=1, max_length=100)
    final_stage: HarnessStage | None = Field(default=None, strict=False)
    repair_attempted: bool
    recovered_after_repair: bool
    idempotency_key: str = Field(min_length=1, max_length=400)


class ReviewQueueRegistration(BaseModel):
    """Enqueue outcome; entry is None only for NOT_ELIGIBLE."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    status: ReviewQueueStatus = Field(strict=False)
    entry: ReviewQueueEntry | None = None


def _domain_fingerprint(domain: str, value: str) -> str:
    return hashlib.sha256((domain + value).encode("utf-8")).hexdigest()


def model_run_id_fingerprint(model_run_id: str) -> str:
    """Domain-separated hash of a raw model run identifier (never stored raw)."""
    return _domain_fingerprint(_MODEL_RUN_ID_DOMAIN, model_run_id)


def review_request_id_fingerprint(request_id: str) -> str:
    """Domain-separated hash of a raw request identifier (never stored raw)."""
    return _domain_fingerprint(_REQUEST_ID_DOMAIN, request_id)


def _validate_id(value: Any) -> str:
    if not isinstance(value, str):
        raise ReviewQueueError(ReviewQueueErrorCode.INVALID_INPUT)
    if not (1 <= len(value) <= _MAX_ID_LENGTH) or not value.strip():
        raise ReviewQueueError(ReviewQueueErrorCode.INVALID_INPUT)
    return value


def validate_queue_identifier(value: Any) -> str:
    """Validate one queue identifier with the single shared rule.

    Public helper so follow-up layers (e.g. the synthetic agent
    workflow) reuse this exact rule instead of copying it. Accepts a
    1..200 character non-blank string; raises fixed
    `REVIEW_QUEUE_INVALID_INPUT` otherwise without reflecting content.
    """
    return _validate_id(value)


def validate_review_queue_identity(
    model_run_id: Any, request_id: Any
) -> tuple[str, str]:
    """Validate a (model_run_id, request_id) pair with the shared rule."""
    return (validate_queue_identifier(model_run_id), validate_queue_identifier(request_id))


def _idempotency_key(
    *,
    model_run_hash: str,
    request_hash: str,
    result: CritiqueResult,
) -> str:
    stage_name = result.final_stage.value if result.final_stage is not None else "NONE"
    return (
        f"{REVIEW_QUEUE_VERSION}:{model_run_hash}:{request_hash}"
        f":{result.disposition.value}:{result.final_code}:{stage_name}"
        f":{int(result.repair_attempted)}:{int(result.recovered_after_repair)}"
    )


class InMemoryReviewQueue:
    """Injectable in-memory queue; first-registration order, defensive copies."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], ReviewQueueEntry] = {}
        self._order: list[tuple[str, str]] = []

    @classmethod
    def _from_validated_entries(
        cls, entries: Iterable[ReviewQueueEntry]
    ) -> InMemoryReviewQueue:
        """Restore already snapshot-validated entries without raw IDs.

        This package-private boundary keeps mutation of queue internals in
        their owning class. Public callers must use the durable snapshot
        restore API, which validates the complete snapshot contract first.
        """
        restored: list[tuple[tuple[str, str], ReviewQueueEntry]] = []
        seen_pairs: set[tuple[str, str]] = set()
        seen_keys: set[str] = set()
        try:
            for item in entries:
                checked = ReviewQueueEntry.model_validate(
                    item.model_dump(mode="json", warnings=False)
                )
                pair = (
                    checked.model_run_id_sha256,
                    checked.request_id_sha256,
                )
                if pair in seen_pairs or checked.idempotency_key in seen_keys:
                    raise ValueError()
                seen_pairs.add(pair)
                seen_keys.add(checked.idempotency_key)
                restored.append((pair, checked.model_copy()))
        except Exception:
            raise ReviewQueueError(ReviewQueueErrorCode.INVALID_INPUT) from None

        queue = cls()
        for pair, entry in restored:
            queue._entries[pair] = entry
            queue._order.append(pair)
        return queue

    def __len__(self) -> int:
        return len(self._order)

    def enqueue(
        self,
        summary: RepairSummary,
        *,
        model_run_id: str,
        request_id: str,
    ) -> ReviewQueueRegistration:
        """Classify internally and register operational failures only."""
        raw_run_id, raw_request_id = validate_review_queue_identity(
            model_run_id, request_id
        )
        # Critique failures (including forged summaries) propagate with the
        # existing fixed CRITIQUE_INVALID_SUMMARY code; the queue is unchanged.
        result = critique_repair_summary(summary)
        if result.disposition not in _ELIGIBLE_DISPOSITIONS:
            return ReviewQueueRegistration(status=ReviewQueueStatus.NOT_ELIGIBLE, entry=None)
        model_run_hash = model_run_id_fingerprint(raw_run_id)
        request_hash = review_request_id_fingerprint(raw_request_id)
        key = (model_run_hash, request_hash)
        if result.final_code is None:
            # Only success results have no final code, and success is
            # never eligible; reaching here means classifier drift.
            raise ReviewQueueError(ReviewQueueErrorCode.INVALID_INPUT)
        candidate = ReviewQueueEntry(
            queue_version=REVIEW_QUEUE_VERSION,
            model_run_id_sha256=model_run_hash,
            request_id_sha256=request_hash,
            disposition=result.disposition,
            final_code=result.final_code,
            final_stage=result.final_stage,
            repair_attempted=result.repair_attempted,
            recovered_after_repair=result.recovered_after_repair,
            idempotency_key=_idempotency_key(
                model_run_hash=model_run_hash,
                request_hash=request_hash,
                result=result,
            ),
        )
        # Revalidate what we store so a future contract drift fails closed
        # instead of persisting a malformed entry.
        try:
            candidate = ReviewQueueEntry.model_validate(candidate.model_dump(mode="json"))
        except Exception:
            raise ReviewQueueError(ReviewQueueErrorCode.INVALID_INPUT) from None
        existing = self._entries.get(key)
        if existing is None:
            self._entries[key] = candidate.model_copy()
            self._order.append(key)
            return ReviewQueueRegistration(
                status=ReviewQueueStatus.ENQUEUED, entry=candidate.model_copy()
            )
        if existing.idempotency_key == candidate.idempotency_key:
            return ReviewQueueRegistration(
                status=ReviewQueueStatus.ALREADY_PRESENT, entry=existing.model_copy()
            )
        raise ReviewQueueError(ReviewQueueErrorCode.CONFLICT)

    def get(self, idempotency_key: str) -> ReviewQueueEntry | None:
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ReviewQueueError(ReviewQueueErrorCode.INVALID_INPUT)
        for key in self._order:
            stored = self._entries[key]
            if stored.idempotency_key == idempotency_key:
                return stored.model_copy()
        return None

    def list_entries(self) -> tuple[ReviewQueueEntry, ...]:
        return tuple(self._entries[key].model_copy() for key in self._order)


__all__ = [
    "REVIEW_QUEUE_CONFLICT",
    "REVIEW_QUEUE_INVALID_INPUT",
    "REVIEW_QUEUE_VERSION",
    "InMemoryReviewQueue",
    "ReviewQueueEntry",
    "ReviewQueueError",
    "ReviewQueueErrorCode",
    "ReviewQueueRegistration",
    "ReviewQueueStatus",
    "model_run_id_fingerprint",
    "review_request_id_fingerprint",
    "validate_queue_identifier",
    "validate_review_queue_identity",
]
