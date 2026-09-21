"""Durable synthetic review-queue lifecycle (Phase 6-07).

One narrow flow: strict-load a `review-queue-snapshot-v1` file, restore a
new `InMemoryReviewQueue` from it (or start explicitly empty), run one
existing `AgentWorkflowCoordinator` execution against that queue, and --
only when the workflow result is `ENQUEUED` -- publish the changed queue
to a new immutable snapshot. Existing snapshot bytes are never
overwritten and no current-pointer swap, in-place update, or automatic
retry exists here.

Contract (`durable-review-workflow-v1`):

- Restore keeps snapshot entry order, idempotency keys, and the
  `(model_run_id_sha256, request_id_sha256)` identity exactly. Raw run /
  request IDs, questions, QueryIR, SQL, rows, physical mappings, paths,
  and secrets are never reconstructed: the snapshot carries hashes and
  fixed codes only, and so does the restored queue.
- Restore never trusts a caller-built Pydantic instance. Instance input
  is dumped to plain JSON and revalidated through the public snapshot
  validator (`validate_review_queue_snapshot`), which rechecks the
  code / stage / disposition / idempotency / count / hash contract.
  Forged entries, duplicate identities / keys, and wrong versions fail
  closed with a fixed code. A failed restore returns no partial queue.
- The coordinator takes the current snapshot path (`None` means an
  explicit empty start), the next snapshot path, the repository root,
  and the existing `AgentWorkflowCoordinator.run()` inputs. It exposes
  no API that accepts a caller-supplied queue, `CritiqueResult`,
  disposition, or restored entry; the queue is always built internally
  by restore-or-empty and injected into the wrapped workflow.
- Before any model, repair-provider, or executor call, the coordinator
  checks: current / next path presence and difference, the shared
  private / Git-ignored / tracked / symlink / junction / reparse /
  hardlink policy (via `preflight_review_queue_snapshot`), and next-path
  no-overwrite. Path, restore, and type errors fail closed with a fixed
  code and zero model / provider / executor calls.
- After execution, only `ENQUEUED` publishes the changed queue to the
  next **new** snapshot via the atomic no-overwrite writer.
  `ALREADY_PRESENT` and `NOT_ELIGIBLE` write nothing.
- The result carries the contract version, the existing
  `AgentWorkflowResult` (defensively revalidated from plain JSON), the
  persistence state (`WRITTEN` or `UNCHANGED`), and -- when written --
  the new snapshot content hash plus entry count. Raw local paths,
  questions, SQL, rows, physical mappings, secrets, and raw IDs are
  never copied into the result, errors, stdout, or module logs. The
  embedded `HarnessResult` keeps its existing contract as an explicit
  caller return value. This module emits no logs.
- A post-execution publish failure fails closed with a fixed storage
  code. Residual boundary: read-only model / execution side effects
  have already happened, and in-memory queue changes not returned to
  the caller may remain process-local; the existing source snapshot
  bytes and any pre-existing target bytes are preserved (the writer
  never overwrites).

Synthetic use only: injected models / providers, read-only SQLite
execution, private snapshots under `.schema-adaptive`. No network,
Ollama, PostgreSQL, CNU data, UI, or approval flow.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .agent_workflow import (
    AGENT_WORKFLOW_VERSION,
    AgentWorkflowCoordinator,
    AgentWorkflowError,
    AgentWorkflowErrorCode,
    AgentWorkflowResult,
)
from .review_queue import InMemoryReviewQueue
from .review_queue_storage import (
    ReviewQueueSnapshot,
    ReviewQueueSnapshotError,
    build_review_queue_snapshot,
    load_review_queue_snapshot,
    preflight_review_queue_snapshot,
    validate_review_queue_snapshot,
    write_review_queue_snapshot,
)

DURABLE_REVIEW_WORKFLOW_VERSION = "durable-review-workflow-v1"

DURABLE_REVIEW_INVALID_INPUT = "DURABLE_REVIEW_INVALID_INPUT"
DURABLE_REVIEW_CONFLICT = "DURABLE_REVIEW_CONFLICT"
DURABLE_REVIEW_INTERNAL_ERROR = "DURABLE_REVIEW_INTERNAL_ERROR"
DURABLE_REVIEW_STORAGE_ERROR = "DURABLE_REVIEW_STORAGE_ERROR"

_HEX64_PATTERN = r"^[0-9a-f]{64}$"


class DurableReviewWorkflowErrorCode(str, Enum):
    INVALID_INPUT = DURABLE_REVIEW_INVALID_INPUT
    CONFLICT = DURABLE_REVIEW_CONFLICT
    INTERNAL_ERROR = DURABLE_REVIEW_INTERNAL_ERROR
    STORAGE_ERROR = DURABLE_REVIEW_STORAGE_ERROR


class DurableReviewWorkflowError(ValueError):
    """Fail-closed lifecycle error with a fixed code only."""

    def __init__(self, code: DurableReviewWorkflowErrorCode) -> None:
        self.code = code.value
        super().__init__(code.value)


class DurableReviewPersistence(str, Enum):
    WRITTEN = "WRITTEN"
    UNCHANGED = "UNCHANGED"


def _invalid_input() -> DurableReviewWorkflowError:
    return DurableReviewWorkflowError(
        DurableReviewWorkflowErrorCode.INVALID_INPUT
    )


def _storage_error() -> DurableReviewWorkflowError:
    return DurableReviewWorkflowError(
        DurableReviewWorkflowErrorCode.STORAGE_ERROR
    )


def _internal_error() -> DurableReviewWorkflowError:
    return DurableReviewWorkflowError(
        DurableReviewWorkflowErrorCode.INTERNAL_ERROR
    )


def restore_review_queue(
    snapshot: ReviewQueueSnapshot | dict[str, Any],
) -> InMemoryReviewQueue:
    """Restore a new queue from a strict-loaded snapshot document.

    Caller-built instances are dumped to plain JSON and revalidated
    through the single snapshot document validator, so forged entries,
    duplicate identities / keys, count / hash mismatches, and wrong
    versions fail closed with a fixed code and no partial queue.
    Entry order, idempotency keys, and identity pairs are preserved.
    """
    if isinstance(snapshot, BaseModel):
        try:
            plain = snapshot.model_dump(mode="json", warnings=False)
        except Exception:
            raise _invalid_input() from None
    elif isinstance(snapshot, dict):
        plain = snapshot
    else:
        raise _invalid_input()
    if not isinstance(plain, dict):
        raise _invalid_input()
    try:
        validated = validate_review_queue_snapshot(plain)
    except ReviewQueueSnapshotError:
        raise _invalid_input() from None
    except Exception:
        raise _invalid_input() from None
    try:
        return InMemoryReviewQueue._from_validated_entries(validated.entries)
    except Exception:
        raise _invalid_input() from None


class DurableReviewWorkflowResult(BaseModel):
    """One durable lifecycle execution; fixed metadata plus workflow result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_version: str = Field(pattern=r"^durable-review-workflow-v1$")
    workflow_result: AgentWorkflowResult
    persistence: DurableReviewPersistence = Field(strict=False)
    snapshot_hash: str | None = Field(default=None, pattern=_HEX64_PATTERN)
    entry_count: int = Field(ge=0)

    @model_validator(mode="before")
    @classmethod
    def _revalidate_nested(cls, data: Any) -> Any:
        # Forged nested instances (`model_copy(update=...)`,
        # `model_construct`) may skip validation, so revalidate the
        # embedded workflow result from plain JSON data first.
        if isinstance(data, BaseModel):
            try:
                data = data.model_dump(mode="json", warnings=False)
            except Exception:
                raise ValueError("invalid durable result") from None
            if not isinstance(data, dict):
                raise ValueError("invalid durable result")
        if isinstance(data, dict):
            value = data.get("workflow_result")
            if isinstance(value, BaseModel):
                try:
                    normalized = dict(data)
                    normalized["workflow_result"] = value.model_dump(
                        mode="json", warnings=False
                    )
                except Exception:
                    raise ValueError("invalid durable result") from None
                return normalized
        return data

    @field_validator("workflow_result", mode="before")
    @classmethod
    def _revalidate_workflow_result(cls, value: Any) -> Any:
        if isinstance(value, BaseModel):
            try:
                plain = value.model_dump(mode="json", warnings=False)
            except Exception:
                raise ValueError("invalid durable result") from None
            try:
                return AgentWorkflowResult.model_validate(plain)
            except Exception:
                raise ValueError("invalid durable result") from None
        return value

    @model_validator(mode="after")
    def _validate_consistency(self) -> DurableReviewWorkflowResult:
        from .review_queue import ReviewQueueStatus

        status = self.workflow_result.queue_status
        if self.persistence is DurableReviewPersistence.WRITTEN:
            if status is not ReviewQueueStatus.ENQUEUED:
                raise ValueError("written result must follow ENQUEUED")
            if (
                self.snapshot_hash is None
                or self.workflow_result.queue_entry is None
                or self.entry_count < 1
            ):
                raise ValueError("written result must carry a snapshot hash")
        else:
            if status not in (
                ReviewQueueStatus.ALREADY_PRESENT,
                ReviewQueueStatus.NOT_ELIGIBLE,
            ):
                raise ValueError("unchanged result must not follow ENQUEUED")
            if self.snapshot_hash is not None:
                raise ValueError("unchanged result must not carry a hash")
            if (
                status is ReviewQueueStatus.ALREADY_PRESENT
                and (
                    self.workflow_result.queue_entry is None
                    or self.entry_count < 1
                )
            ):
                raise ValueError("existing result must carry an entry")
        if self.workflow_result.workflow_version != AGENT_WORKFLOW_VERSION:
            raise ValueError("workflow version mismatch")
        return self


class DurableReviewWorkflowCoordinator:
    """Strict-load, restore, run the wrapped workflow, publish on change."""

    def __init__(self, workflow: AgentWorkflowCoordinator) -> None:
        if not isinstance(workflow, AgentWorkflowCoordinator):
            raise _invalid_input()
        self._workflow = workflow

    def run(
        self,
        request_data: Any,
        ontology: Any,
        mapping: Any,
        *,
        model: Any,
        repair_provider: Any,
        model_run_id: Any,
        current_snapshot: str | Path | None,
        next_snapshot: str | Path,
        repository_root: str | Path,
    ) -> DurableReviewWorkflowResult:
        """Run one durable lifecycle step without caller queue injection."""
        # ---- Pre-execution checks: no model / provider / executor calls.
        if current_snapshot is not None and not isinstance(
            current_snapshot, (str, Path)
        ):
            raise _invalid_input()
        if not isinstance(next_snapshot, (str, Path)):
            raise _invalid_input()
        if not isinstance(repository_root, (str, Path)):
            raise _invalid_input()
        if isinstance(next_snapshot, str) and not next_snapshot.strip():
            raise _invalid_input()
        if (
            isinstance(current_snapshot, str)
            and not current_snapshot.strip()
        ):
            raise _invalid_input()
        if current_snapshot is not None and self._same_path(
            current_snapshot, next_snapshot
        ):
            raise _invalid_input()
        try:
            preflight_review_queue_snapshot(
                next_snapshot, repository_root=repository_root
            )
        except ReviewQueueSnapshotError:
            raise _invalid_input() from None
        except Exception:
            raise _invalid_input() from None
        if self._target_exists(next_snapshot):
            raise _invalid_input()
        if current_snapshot is None:
            queue = InMemoryReviewQueue()
        else:
            try:
                preflight_review_queue_snapshot(
                    current_snapshot, repository_root=repository_root
                )
            except ReviewQueueSnapshotError:
                raise _invalid_input() from None
            except Exception:
                raise _invalid_input() from None
            try:
                loaded = load_review_queue_snapshot(
                    current_snapshot, repository_root=repository_root
                )
            except ReviewQueueSnapshotError:
                raise _invalid_input() from None
            except Exception:
                raise _invalid_input() from None
            try:
                plain_loaded = loaded.model_dump(mode="json")
            except Exception:
                raise _invalid_input() from None
            queue = restore_review_queue(plain_loaded)

        # ---- Single wrapped workflow execution against the restored queue.
        try:
            outcome = self._workflow.run(
                request_data,
                ontology,
                mapping,
                model=model,
                repair_provider=repair_provider,
                model_run_id=model_run_id,
                queue=queue,
            )
        except AgentWorkflowError as exc:
            if exc.code == AgentWorkflowErrorCode.CONFLICT.value:
                raise DurableReviewWorkflowError(
                    DurableReviewWorkflowErrorCode.CONFLICT
                ) from None
            if exc.code == AgentWorkflowErrorCode.INVALID_INPUT.value:
                raise _invalid_input() from None
            raise _internal_error() from None
        except Exception:
            raise _internal_error() from None
        try:
            workflow_result = AgentWorkflowResult.model_validate(
                outcome.model_dump(mode="json")
            )
        except Exception:
            raise _internal_error() from None

        from .review_queue import ReviewQueueStatus

        if workflow_result.queue_status is not ReviewQueueStatus.ENQUEUED:
            try:
                return DurableReviewWorkflowResult(
                    workflow_version=DURABLE_REVIEW_WORKFLOW_VERSION,
                    workflow_result=workflow_result.model_copy(deep=True),
                    persistence=DurableReviewPersistence.UNCHANGED,
                    snapshot_hash=None,
                    entry_count=len(queue),
                )
            except Exception:
                raise _internal_error() from None

        # ---- Publish the changed queue to the next new snapshot only.
        try:
            pending_snapshot = build_review_queue_snapshot(queue)
            written_result = DurableReviewWorkflowResult(
                workflow_version=DURABLE_REVIEW_WORKFLOW_VERSION,
                workflow_result=workflow_result.model_copy(deep=True),
                persistence=DurableReviewPersistence.WRITTEN,
                snapshot_hash=pending_snapshot.payload_sha256,
                entry_count=pending_snapshot.entry_count,
            )
        except Exception:
            raise _internal_error() from None
        try:
            write_review_queue_snapshot(
                next_snapshot, queue, repository_root=repository_root
            )
        except FileExistsError:
            # A target created after the pre-execution check (or any
            # link-time collision) still never overwrites: fail closed.
            raise _storage_error() from None
        except ReviewQueueSnapshotError:
            raise _storage_error() from None
        except (OSError, RuntimeError, ValueError, TypeError):
            raise _storage_error() from None
        except Exception:
            raise _storage_error() from None
        return written_result

    @staticmethod
    def _same_path(first: str | Path, second: str | Path) -> bool:
        try:
            left = Path(os.path.abspath(first))
            right = Path(os.path.abspath(second))
            if left == right:
                return True
            return left.resolve(strict=False) == right.resolve(strict=False)
        except Exception:
            return False

    @staticmethod
    def _target_exists(candidate: str | Path) -> bool:
        try:
            target = Path(candidate)
            return target.exists() or target.is_symlink()
        except Exception:
            return True


__all__ = [
    "DURABLE_REVIEW_CONFLICT",
    "DURABLE_REVIEW_INTERNAL_ERROR",
    "DURABLE_REVIEW_INVALID_INPUT",
    "DURABLE_REVIEW_STORAGE_ERROR",
    "DURABLE_REVIEW_WORKFLOW_VERSION",
    "DurableReviewPersistence",
    "DurableReviewWorkflowCoordinator",
    "DurableReviewWorkflowError",
    "DurableReviewWorkflowErrorCode",
    "DurableReviewWorkflowResult",
    "restore_review_queue",
]
