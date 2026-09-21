"""Private snapshot storage for the synthetic review queue (Phase 6-05).

Synthetic boundary only: persist the fixed metadata of an
`InMemoryReviewQueue` (`queue.list_entries()`) to one explicit private
file under `.schema-adaptive`, and strict-load it back. There is no
operational queue, admin UI, approval workflow, collection/enqueue path,
model call, or DB execution here. A persisted snapshot is not an admin
approval or a correction result.

Contract (`review-queue-snapshot-v1`):

- Source is `queue.list_entries()` fixed fields only. Questions, SQL,
  rows, raw exceptions, physical mappings, raw run/request IDs, paths,
  and secrets are never written to file content, stdout, logs, or
  controlled errors.
- Single versioned JSON file with queue version, entry count, ordered
  entries, and SHA-256 over the canonical JSON bytes of the payload
  excluding the hash field. Canonical rule: UTF-8,
  `sort_keys=True`, compact separators `(",", ":")`,
  `ensure_ascii=False`, `allow_nan=False`. The file itself is
  pretty-printed JSON (`indent=2`, `sort_keys=True`,
  `ensure_ascii=False`, `allow_nan=False`) plus one trailing newline,
  UTF-8 encoded. The content hash is tamper detection only, not
  authorship authentication.
- Path policy reuses the existing recording-bundle validator
  (`preflight_private_snapshot_path` / `read_private_snapshot_bytes`).
  No parallel weaker path check exists in this module. File and parent
  redirect/escape are blocked; path/OS exceptions map to fixed codes.
- No-overwrite by default: an existing snapshot is preserved and a
  fixed error is raised. Overwrite, in-place update, and operational
  concurrency/locking are not implemented. An empty-queue snapshot is
  allowed when explicitly saved, with fixed metadata only.
- Strict load returns defensive copies of the validated contract and
  never mutates an internal queue. File-internal paths are never
  trusted: the caller supplies the snapshot path.
- Entry semantics reuse the single critique code-stage-disposition
  table (`disposition_for`): the stored disposition must equal the
  table result for the stored (code, stage) pair. A known code with a
  wrong but eligible disposition, `INVALID_REQUEST` stored as an
  operational failure, `REPAIR_PROVIDER_ERROR` with a non-None stage,
  and impossible failure flags (`recovered_after_repair=True`, which
  the critique yields only for `EXECUTED_UNVERIFIED` success that is
  never stored) are all fixed `INVALID_CONTRACT` rejections. The same
  semantic check runs before writing, not only on load.
- The private path is rechecked after parent-directory creation and
  immediately before atomic publish to narrow redirect-swap exposure.
  Check-then-use races are reduced, not guaranteed away: no
  operational concurrency or locking is implemented.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .contracts import HarnessErrorCode, HarnessStage
from .critique import CritiqueDisposition, CritiqueError, disposition_for
from .recording_bundle_storage import (
    RecordingBundleError,
    RecordingBundleErrorCode,
    preflight_private_snapshot_path,
    read_private_snapshot_bytes,
)
from .repair import REPAIR_PROVIDER_ERROR
from .review_queue import (
    REVIEW_QUEUE_VERSION,
    InMemoryReviewQueue,
    ReviewQueueEntry,
)

REVIEW_QUEUE_SNAPSHOT_VERSION = "review-queue-snapshot-v1"
REVIEW_QUEUE_STORAGE_TOOL_VERSION = "review-queue-storage-v1"

_SNAPSHOT_EXISTS_MESSAGE = "review queue snapshot already exists"

_HEX64_PATTERN = r"^[0-9a-f]{64}$"

_ELIGIBLE_DISPOSITIONS: frozenset[CritiqueDisposition] = frozenset({
    CritiqueDisposition.MODEL_REVIEW,
    CritiqueDisposition.CONTEXT_REVIEW,
    CritiqueDisposition.SAFETY_BLOCKED,
    CritiqueDisposition.EXECUTION_FAILURE,
    CritiqueDisposition.INTERNAL_REVIEW,
})

_KNOWN_FINAL_CODES: frozenset[str] = frozenset(
    {code.value for code in HarnessErrorCode} | {REPAIR_PROVIDER_ERROR}
)

_TOP_LEVEL_KEYS: frozenset[str] = frozenset({
    "schema_version",
    "storage_tool_version",
    "queue_version",
    "entry_count",
    "entries",
    "payload_sha256",
})


class ReviewQueueSnapshotErrorCode(str, Enum):
    INVALID_PATH = "INVALID_PATH"
    PRIVATE_PATH_NOT_IGNORED = "PRIVATE_PATH_NOT_IGNORED"
    GIT_TRACKING_CHECK_FAILED = "GIT_TRACKING_CHECK_FAILED"
    GIT_TRACKED_TARGET = "GIT_TRACKED_TARGET"
    INVALID_CONTRACT = "INVALID_CONTRACT"
    COUNT_MISMATCH = "COUNT_MISMATCH"
    HASH_MISMATCH = "HASH_MISMATCH"
    STORAGE_ERROR = "STORAGE_ERROR"


class ReviewQueueSnapshotError(ValueError):
    """Controlled snapshot failure with a fixed code only."""

    def __init__(self, code: ReviewQueueSnapshotErrorCode) -> None:
        self.code = code.value
        super().__init__(code.value)


class ReviewQueueSnapshot(BaseModel):
    """Validated versioned snapshot; fixed metadata only."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["review-queue-snapshot-v1"] = REVIEW_QUEUE_SNAPSHOT_VERSION
    storage_tool_version: Literal["review-queue-storage-v1"] = (
        REVIEW_QUEUE_STORAGE_TOOL_VERSION
    )
    queue_version: Literal["review-queue-v1"] = REVIEW_QUEUE_VERSION
    entry_count: int = Field(ge=0)
    entries: list[ReviewQueueEntry] = Field(default_factory=list)
    payload_sha256: str = Field(pattern=_HEX64_PATTERN)


def _canonical_payload_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def snapshot_payload_sha256(payload_without_hash: dict[str, Any]) -> str:
    """SHA-256 over canonical JSON bytes (tamper detection, not auth)."""
    return hashlib.sha256(_canonical_payload_bytes(payload_without_hash)).hexdigest()


def _snapshot_file_bytes(snapshot: dict[str, Any]) -> bytes:
    text = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )
    return (text + "\n").encode("utf-8")


def _convert_bundle_error(error: RecordingBundleError) -> ReviewQueueSnapshotError:
    mapping = {
        RecordingBundleErrorCode.INVALID_PATH: (
            ReviewQueueSnapshotErrorCode.INVALID_PATH
        ),
        RecordingBundleErrorCode.PRIVATE_PATH_NOT_IGNORED: (
            ReviewQueueSnapshotErrorCode.PRIVATE_PATH_NOT_IGNORED
        ),
        RecordingBundleErrorCode.GIT_TRACKING_CHECK_FAILED: (
            ReviewQueueSnapshotErrorCode.GIT_TRACKING_CHECK_FAILED
        ),
        RecordingBundleErrorCode.GIT_TRACKED_TARGET: (
            ReviewQueueSnapshotErrorCode.GIT_TRACKED_TARGET
        ),
        RecordingBundleErrorCode.INVALID_CONTRACT: (
            ReviewQueueSnapshotErrorCode.INVALID_CONTRACT
        ),
        RecordingBundleErrorCode.INCOMPLETE_BUNDLE: (
            ReviewQueueSnapshotErrorCode.INVALID_CONTRACT
        ),
        RecordingBundleErrorCode.ARTIFACT_MISMATCH: (
            ReviewQueueSnapshotErrorCode.INVALID_CONTRACT
        ),
        RecordingBundleErrorCode.STORAGE_ERROR: (
            ReviewQueueSnapshotErrorCode.STORAGE_ERROR
        ),
    }
    return ReviewQueueSnapshotError(
        mapping.get(error.code, ReviewQueueSnapshotErrorCode.INVALID_PATH)
        if isinstance(error.code, RecordingBundleErrorCode)
        else ReviewQueueSnapshotErrorCode.INVALID_PATH
    )


def _expected_idempotency_key(entry: ReviewQueueEntry) -> str:
    stage_name = entry.final_stage.value if entry.final_stage is not None else "NONE"
    return (
        f"{REVIEW_QUEUE_VERSION}:{entry.model_run_id_sha256}"
        f":{entry.request_id_sha256}:{entry.disposition.value}"
        f":{entry.final_code}:{stage_name}"
        f":{int(entry.repair_attempted)}:{int(entry.recovered_after_repair)}"
    )


def _payload_without_hash(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {key: snapshot[key] for key in snapshot if key != "payload_sha256"}


def _validate_entry_semantics(entry: ReviewQueueEntry) -> None:
    """Enforce stored-entry semantics from the single critique table.

    The stored (code, stage) pair must classify to the stored
    disposition via `disposition_for`, which owns the code-stage
    taxonomy. This rejects mismatched triples even when every field
    is individually well-formed, `INVALID_REQUEST` kept as an
    operational failure, and `REPAIR_PROVIDER_ERROR` with a stage.
    `recovered_after_repair=True` is impossible for stored failures:
    the critique yields it only for `EXECUTED_UNVERIFIED` success,
    which the queue never stores.
    """
    if entry.queue_version != REVIEW_QUEUE_VERSION:
        raise ReviewQueueSnapshotError(
            ReviewQueueSnapshotErrorCode.INVALID_CONTRACT
        )
    if entry.final_code not in _KNOWN_FINAL_CODES:
        raise ReviewQueueSnapshotError(
            ReviewQueueSnapshotErrorCode.INVALID_CONTRACT
        )
    if entry.disposition not in _ELIGIBLE_DISPOSITIONS:
        raise ReviewQueueSnapshotError(
            ReviewQueueSnapshotErrorCode.INVALID_CONTRACT
        )
    if not isinstance(entry.final_stage, (HarnessStage, type(None))):
        raise ReviewQueueSnapshotError(
            ReviewQueueSnapshotErrorCode.INVALID_CONTRACT
        )
    try:
        if disposition_for(entry.final_code, entry.final_stage) != entry.disposition:
            raise ReviewQueueSnapshotError(
                ReviewQueueSnapshotErrorCode.INVALID_CONTRACT
            )
    except ReviewQueueSnapshotError:
        raise
    except (CritiqueError, ValueError, TypeError):
        raise ReviewQueueSnapshotError(
            ReviewQueueSnapshotErrorCode.INVALID_CONTRACT
        ) from None
    if entry.recovered_after_repair:
        raise ReviewQueueSnapshotError(
            ReviewQueueSnapshotErrorCode.INVALID_CONTRACT
        )


def _build_snapshot_document(queue: InMemoryReviewQueue) -> tuple[dict[str, Any], bytes]:
    if not isinstance(queue, InMemoryReviewQueue):
        raise ReviewQueueSnapshotError(
            ReviewQueueSnapshotErrorCode.INVALID_CONTRACT
        )
    try:
        stored = queue.list_entries()
        entries: list[dict[str, Any]] = []
        for item in stored:
            checked = ReviewQueueEntry.model_validate(item.model_dump(mode="json"))
            _validate_entry_semantics(checked)
            entries.append(checked.model_dump(mode="json"))
    except ReviewQueueSnapshotError:
        raise
    except Exception:
        raise ReviewQueueSnapshotError(
            ReviewQueueSnapshotErrorCode.INVALID_CONTRACT
        ) from None
    payload = {
        "schema_version": REVIEW_QUEUE_SNAPSHOT_VERSION,
        "storage_tool_version": REVIEW_QUEUE_STORAGE_TOOL_VERSION,
        "queue_version": REVIEW_QUEUE_VERSION,
        "entry_count": len(entries),
        "entries": entries,
    }
    digest = snapshot_payload_sha256(payload)
    document = dict(payload)
    document["payload_sha256"] = digest
    try:
        validated = ReviewQueueSnapshot.model_validate(document)
    except (ValidationError, ValueError, TypeError):
        raise ReviewQueueSnapshotError(
            ReviewQueueSnapshotErrorCode.INVALID_CONTRACT
        ) from None
    return validated.model_dump(mode="json"), _snapshot_file_bytes(
        validated.model_dump(mode="json")
    )


def build_review_queue_snapshot(queue: InMemoryReviewQueue) -> ReviewQueueSnapshot:
    """Build and validate snapshot metadata without touching the filesystem."""
    document, _ = _build_snapshot_document(queue)
    return ReviewQueueSnapshot.model_validate(document)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def preflight_review_queue_snapshot(
    snapshot_path: str | Path,
    *,
    repository_root: str | Path,
) -> None:
    """Check the shared private path policy without writes."""
    try:
        preflight_private_snapshot_path(
            snapshot_path, repository_root=repository_root
        )
    except RecordingBundleError as exc:
        raise _convert_bundle_error(exc) from None
    except (OSError, RuntimeError, ValueError, TypeError):
        raise ReviewQueueSnapshotError(
            ReviewQueueSnapshotErrorCode.INVALID_PATH
        ) from None


def _recheck_target(
    snapshot_path: str | Path,
    *,
    repository_root: str | Path,
) -> Path:
    """Re-run the shared private path check; narrows redirect-swap windows."""
    try:
        return preflight_private_snapshot_path(
            snapshot_path, repository_root=repository_root
        )
    except RecordingBundleError as exc:
        raise _convert_bundle_error(exc) from None
    except (OSError, RuntimeError, ValueError, TypeError):
        raise ReviewQueueSnapshotError(
            ReviewQueueSnapshotErrorCode.INVALID_PATH
        ) from None


def write_review_queue_snapshot(
    snapshot_path: str | Path,
    queue: InMemoryReviewQueue,
    *,
    repository_root: str | Path,
) -> Path:
    """Persist fixed queue metadata to one private file (no overwrite).

    The private path is checked before parent creation, rechecked after
    parent creation, and rechecked immediately before atomic publish to
    narrow redirect-swap exposure. Residual check-then-use races are not
    guaranteed away: no operational concurrency or locking exists.
    """
    _, file_bytes = _build_snapshot_document(queue)
    target = _recheck_target(snapshot_path, repository_root=repository_root)
    if target.exists() or target.is_symlink():
        raise FileExistsError(_SNAPSHOT_EXISTS_MESSAGE)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise ReviewQueueSnapshotError(
            ReviewQueueSnapshotErrorCode.STORAGE_ERROR
        ) from None
    # Parent creation can change which components exist, so re-resolve the
    # same shared policy before touching the filesystem further.
    target = _recheck_target(snapshot_path, repository_root=repository_root)
    if target.exists() or target.is_symlink():
        raise FileExistsError(_SNAPSHOT_EXISTS_MESSAGE)
    descriptor = None
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".snapshot-", suffix=".tmp", dir=target.parent
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(file_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        # Final recheck immediately before publish; `os.link` below stays
        # the atomic no-overwrite guard. A swap between this check and the
        # link call is not guaranteed away.
        target = _recheck_target(snapshot_path, repository_root=repository_root)
        try:
            os.link(temporary, target)
        except FileExistsError:
            raise FileExistsError(_SNAPSHOT_EXISTS_MESSAGE) from None
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            # A strict-loaded snapshot must have exactly one link. If the
            # temporary link cannot be removed, roll back the new target
            # before reporting a fixed storage failure.
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        _fsync_directory(target.parent)
    except FileExistsError:
        raise
    except OSError:
        raise ReviewQueueSnapshotError(
            ReviewQueueSnapshotErrorCode.STORAGE_ERROR
        ) from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # Cleanup failure must not replace a fixed storage error
                # with an OS exception containing the private temp path.
                pass
    return target


def _parse_snapshot_bytes(raw: bytes) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        payload = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, json.JSONDecodeError, ValidationError, ValueError, TypeError):
        raise ReviewQueueSnapshotError(
            ReviewQueueSnapshotErrorCode.INVALID_CONTRACT
        ) from None
    if not isinstance(payload, dict):
        raise ReviewQueueSnapshotError(
            ReviewQueueSnapshotErrorCode.INVALID_CONTRACT
        )
    return payload


def _validate_snapshot_document(payload: dict[str, Any]) -> ReviewQueueSnapshot:
    if set(payload.keys()) != set(_TOP_LEVEL_KEYS):
        raise ReviewQueueSnapshotError(
            ReviewQueueSnapshotErrorCode.INVALID_CONTRACT
        )
    try:
        snapshot = ReviewQueueSnapshot.model_validate(payload)
    except (ValidationError, ValueError, TypeError):
        raise ReviewQueueSnapshotError(
            ReviewQueueSnapshotErrorCode.INVALID_CONTRACT
        ) from None
    if snapshot.entry_count != len(snapshot.entries):
        raise ReviewQueueSnapshotError(
            ReviewQueueSnapshotErrorCode.COUNT_MISMATCH
        )
    seen_pairs: set[tuple[str, str]] = set()
    seen_keys: set[str] = set()
    for entry in snapshot.entries:
        _validate_entry_semantics(entry)
        pair = (entry.model_run_id_sha256, entry.request_id_sha256)
        if pair in seen_pairs:
            raise ReviewQueueSnapshotError(
                ReviewQueueSnapshotErrorCode.INVALID_CONTRACT
            )
        seen_pairs.add(pair)
        if entry.idempotency_key in seen_keys:
            raise ReviewQueueSnapshotError(
                ReviewQueueSnapshotErrorCode.INVALID_CONTRACT
            )
        seen_keys.add(entry.idempotency_key)
        if not hmac.compare_digest(
            entry.idempotency_key, _expected_idempotency_key(entry)
        ):
            raise ReviewQueueSnapshotError(
                ReviewQueueSnapshotErrorCode.INVALID_CONTRACT
            )
    expected = snapshot_payload_sha256(_payload_without_hash(payload))
    if not hmac.compare_digest(expected, snapshot.payload_sha256):
        raise ReviewQueueSnapshotError(
            ReviewQueueSnapshotErrorCode.HASH_MISMATCH
        )
    return snapshot


def validate_review_queue_snapshot(
    snapshot: ReviewQueueSnapshot | dict[str, Any],
) -> ReviewQueueSnapshot:
    """Revalidate caller data through the complete snapshot contract."""
    try:
        if isinstance(snapshot, BaseModel):
            payload = snapshot.model_dump(mode="json", warnings=False)
        elif isinstance(snapshot, dict):
            payload = snapshot
        else:
            raise TypeError()
        if not isinstance(payload, dict):
            raise TypeError()
        validated = _validate_snapshot_document(payload)
        return ReviewQueueSnapshot.model_validate(
            validated.model_dump(mode="json", warnings=False)
        )
    except ReviewQueueSnapshotError:
        raise
    except Exception:
        raise ReviewQueueSnapshotError(
            ReviewQueueSnapshotErrorCode.INVALID_CONTRACT
        ) from None


def load_review_queue_snapshot(
    snapshot_path: str | Path,
    *,
    repository_root: str | Path,
) -> ReviewQueueSnapshot:
    """Strict-load one private snapshot; defensive copy, no queue mutation."""
    try:
        raw = read_private_snapshot_bytes(
            snapshot_path, repository_root=repository_root
        )
    except RecordingBundleError as exc:
        raise _convert_bundle_error(exc) from None
    except (OSError, RuntimeError, ValueError, TypeError):
        raise ReviewQueueSnapshotError(
            ReviewQueueSnapshotErrorCode.STORAGE_ERROR
        ) from None
    payload = _parse_snapshot_bytes(raw)
    return validate_review_queue_snapshot(payload)


__all__ = [
    "REVIEW_QUEUE_SNAPSHOT_VERSION",
    "REVIEW_QUEUE_STORAGE_TOOL_VERSION",
    "ReviewQueueSnapshot",
    "ReviewQueueSnapshotError",
    "ReviewQueueSnapshotErrorCode",
    "build_review_queue_snapshot",
    "load_review_queue_snapshot",
    "preflight_review_queue_snapshot",
    "snapshot_payload_sha256",
    "validate_review_queue_snapshot",
    "write_review_queue_snapshot",
]
