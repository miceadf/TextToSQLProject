"""Private mapping review storage and strict published activation (Phase 6-19).

Review state lives outside the immutable Phase 6-17 registration directory::

    .schema-adaptive/registration-reviews/<database_id>/
        draft.json
        mapping-vNNNN.json
        current.json      (latest published version)
        activation.json   (sole execution authority; may name an older version)

The registration directory is never modified. All failures surface as
:class:`ReviewError` carrying only a fixed code string; source/private
paths, rows, SQL, and exception text are never attached.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..mapping import (
    MappingDraft,
    MappingReviewService,
    PublishedMappingProfile,
)
from ..mapping.errors import MappingPublishError, MappingWorkflowError
from ..ontology import load_default_ontology
from . import storage as registration_storage
from .contracts import DATABASE_ID_PATTERN, WINDOWS_RESERVED_NAMES


REVIEW_ROOT_NAME = "registration-reviews"
REVIEW_SCHEMA_VERSION = "private-mapping-review-v1"
ACTIVATION_SCHEMA_VERSION = "private-mapping-activation-v1"

DRAFT_FILENAME = "draft.json"
CURRENT_FILENAME = "current.json"
ACTIVATION_FILENAME = "activation.json"

MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
MAX_REVIEWER_LENGTH = 64
MAX_REASON_LENGTH = 500
MAX_NAME_LENGTH = 200


class ReviewErrorCode(str, Enum):
    INVALID_INPUT = "INVALID_INPUT"
    REVIEW_CONFLICT = "REVIEW_CONFLICT"
    ARTIFACT_MISMATCH = "ARTIFACT_MISMATCH"
    PUBLISH_BLOCKED = "PUBLISH_BLOCKED"
    PRIVATE_STORAGE_INVALID = "PRIVATE_STORAGE_INVALID"
    STORAGE_ERROR = "STORAGE_ERROR"


class ReviewError(ValueError):
    def __init__(self, code: ReviewErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


def _fail(code: ReviewErrorCode) -> ReviewError:
    return ReviewError(code)


class PublishedVersionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: int = Field(ge=1)
    mapping_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mapping_bytes: int = Field(ge=1)


MAX_RECORDED_VERSIONS = 1000


class ReviewPointer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: str = Field(pattern=r"^private-mapping-review-v1$")
    database_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    version: int = Field(ge=1)
    mapping_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mapping_bytes: int = Field(ge=1)
    schema_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    ontology_version: int = Field(ge=1)
    # Phase 8-01: digests of every version this pointer has published, oldest
    # first and ending with ``version``. Absent in pre-8-01 pointers, whose
    # only provably published version is the pointer itself.
    published_versions: list[PublishedVersionRecord] | None = Field(
        default=None, min_length=1, max_length=MAX_RECORDED_VERSIONS
    )

    @model_validator(mode="after")
    def _history_ends_at_pointer(self) -> "ReviewPointer":
        records = self.published_versions
        if records is None:
            return self
        numbers = [item.version for item in records]
        if numbers != sorted(set(numbers)):
            raise ValueError("published versions must be strictly increasing")
        last = records[-1]
        if (
            last.version != self.version
            or last.mapping_sha256 != self.mapping_sha256
            or last.mapping_bytes != self.mapping_bytes
        ):
            raise ValueError("published versions must end at the pointer")
        return self

    def published_records(self) -> dict[int, PublishedVersionRecord]:
        if self.published_versions is None:
            return {
                self.version: PublishedVersionRecord(
                    version=self.version,
                    mapping_sha256=self.mapping_sha256,
                    mapping_bytes=self.mapping_bytes,
                )
            }
        return {item.version: item for item in self.published_versions}


class ActivationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: str = Field(pattern=r"^private-mapping-activation-v1$")
    database_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    mapping_version: int = Field(ge=1)
    schema_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    ontology_version: int = Field(ge=1)
    mapping_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mapping_bytes: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_bytes: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=64)
    # Optional so manifests written before Phase 8-01 still load unchanged.
    activated_by: str | None = Field(default=None, min_length=1, max_length=MAX_REVIEWER_LENGTH)
    activated_at: str | None = Field(
        default=None, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
    )


MAX_LISTED_VERSIONS = 50
MAX_MAPPING_VERSION = 9999


@dataclass(frozen=True)
class MappingVersionInfo:
    version: int
    published_by: str
    published_at: str
    active: bool
    latest: bool
    activatable: bool


@dataclass(frozen=True)
class LoadedReview:
    database_id: str
    draft: MappingDraft
    published: PublishedMappingProfile | None
    mapping_version: int | None


@dataclass(frozen=True)
class LoadedActivation:
    database_id: str
    label: str
    database_path: Path
    catalog: Any
    profile: PublishedMappingProfile
    schema_fingerprint: str
    ontology_version: int
    mapping_version: int


def validate_reviewer(value: Any) -> str:
    if not isinstance(value, str):
        raise _fail(ReviewErrorCode.INVALID_INPUT)
    if not 1 <= len(value) <= MAX_REVIEWER_LENGTH:
        raise _fail(ReviewErrorCode.INVALID_INPUT)
    if value != value.strip() or not value.strip():
        raise _fail(ReviewErrorCode.INVALID_INPUT)
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise _fail(ReviewErrorCode.INVALID_INPUT)
    return value


def validate_reason(value: Any) -> str:
    if not isinstance(value, str):
        raise _fail(ReviewErrorCode.INVALID_INPUT)
    if not 1 <= len(value) <= MAX_REASON_LENGTH:
        raise _fail(ReviewErrorCode.INVALID_INPUT)
    if not value.strip():
        raise _fail(ReviewErrorCode.INVALID_INPUT)
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise _fail(ReviewErrorCode.INVALID_INPUT)
    return value


def validate_physical_name(value: Any) -> str:
    if not isinstance(value, str):
        raise _fail(ReviewErrorCode.INVALID_INPUT)
    if not 1 <= len(value) <= MAX_NAME_LENGTH:
        raise _fail(ReviewErrorCode.INVALID_INPUT)
    if not value.strip():
        raise _fail(ReviewErrorCode.INVALID_INPUT)
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise _fail(ReviewErrorCode.INVALID_INPUT)
    return value


def validate_semantic_field(value: Any) -> str:
    return validate_physical_name(value)


def _validate_database_id(value: Any) -> str:
    if not isinstance(value, str):
        raise _fail(ReviewErrorCode.INVALID_INPUT)
    if DATABASE_ID_PATTERN.fullmatch(value) is None:
        raise _fail(ReviewErrorCode.INVALID_INPUT)
    if value != value.strip(" .") or value.endswith((".", " ")):
        raise _fail(ReviewErrorCode.INVALID_INPUT)
    if value.lower() in WINDOWS_RESERVED_NAMES:
        raise _fail(ReviewErrorCode.INVALID_INPUT)
    if value in registration_storage.RESERVED_SYNTHETIC_DATABASE_IDS:
        raise _fail(ReviewErrorCode.INVALID_INPUT)
    return value


def _is_redirect(path: Path) -> bool:
    if path.is_symlink():
        return True
    junction_check = getattr(path, "is_junction", None)
    if junction_check is not None and junction_check():
        return True
    if not path.exists():
        return False
    try:
        attributes = path.lstat().st_file_attributes  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False
    return bool(attributes & 0x400)


def _validate_repository(repository_root: str | Path) -> Path:
    try:
        supplied = Path(repository_root)
        if _is_redirect(supplied):
            raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID)
        root = supplied.resolve(strict=True)
    except ReviewError:
        raise
    except (OSError, RuntimeError, ValueError, TypeError):
        raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID) from None
    if not root.is_dir():
        raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID)
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID) from None
    if result.returncode != 0:
        raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID) from None
    try:
        discovered = Path(result.stdout.strip()).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID) from None
    if discovered != root:
        raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID) from None
    return root


def _review_root(repository_root: str | Path) -> tuple[Path, Path]:
    root = _validate_repository(repository_root)
    return root, root / ".schema-adaptive" / REVIEW_ROOT_NAME


def _validate_review_target(root: Path, review_root: Path, database_id: str) -> Path:
    for anchor in (root / ".schema-adaptive", review_root):
        try:
            if _is_redirect(anchor):
                raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID)
        except ReviewError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID) from None
    lexical = Path(os.path.abspath(review_root / database_id))
    resolved = (review_root / database_id).resolve(strict=False)
    for candidate in (lexical, resolved):
        try:
            relative = candidate.relative_to(review_root)
        except ValueError:
            raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID) from None
        if not relative.parts or len(relative.parts) != 1 or relative.parts[0] != database_id:
            raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID) from None
    current = root / ".schema-adaptive"
    for part in (REVIEW_ROOT_NAME, database_id):
        current = current / part
        if current.exists() and _is_redirect(current):
            raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID) from None
    if lexical.exists() and _is_redirect(lexical):
        raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID) from None
    relative_target = lexical.relative_to(root).as_posix()
    try:
        tracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--", relative_target],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID) from None
    if tracked.returncode != 0:
        raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID) from None
    if tracked.stdout:
        raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID) from None
    try:
        ignored = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "-q", "--", relative_target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID) from None
    if ignored.returncode == 1:
        raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID) from None
    if ignored.returncode != 0:
        raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID) from None
    return resolved


def _ensure_review_root(review_root: Path) -> None:
    parent = review_root.parent
    if _is_redirect(parent) or (parent.exists() and not parent.is_dir()):
        raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID)
    try:
        parent.mkdir(exist_ok=True)
    except (OSError, RuntimeError):
        raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
    if _is_redirect(parent) or not parent.is_dir():
        raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID)
    if review_root.exists():
        if _is_redirect(review_root) or not review_root.is_dir():
            raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID)
        return
    try:
        review_root.mkdir(exist_ok=True)
    except (OSError, RuntimeError):
        raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
    if _is_redirect(review_root) or not review_root.is_dir():
        raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID)


def _json_bytes(payload: Any) -> bytes:
    try:
        text = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        )
    except (TypeError, ValueError):
        raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
    return (text + "\n").encode("utf-8")


def _parse_json(raw: bytes) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        return json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, ValueError, TypeError):
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH) from None


def _read_review_file(path: Path, expected_parent: Path, *, max_bytes: int) -> bytes:
    try:
        if _is_redirect(path) or not path.is_file():
            raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
        if path.resolve(strict=True).parent != expected_parent.resolve(strict=True):
            raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    except ReviewError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH) from None
    try:
        with path.open("rb") as stream:
            try:
                metadata = os.fstat(stream.fileno())
            except (OSError, RuntimeError):
                raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH) from None
            try:
                link_count = metadata.st_nlink
            except AttributeError:
                link_count = 1
            if link_count != 1:
                raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
            if metadata.st_size < 0 or metadata.st_size > max_bytes:
                raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
            content = stream.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
            return content
    except ReviewError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH) from None


def _strict_model(raw: bytes, model_type: type[Any]) -> Any:
    payload = _parse_json(raw)
    try:
        model = model_type.model_validate(payload)
    except ValidationError:
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH) from None
    if payload != model.model_dump(mode="json"):
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH) from None
    return model


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except (OSError, RuntimeError):
        return
    try:
        os.fsync(descriptor)
    except (OSError, RuntimeError):
        pass
    finally:
        try:
            os.close(descriptor)
        except (OSError, RuntimeError):
            pass


def _write_new_file(path: Path, content: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise _fail(ReviewErrorCode.REVIEW_CONFLICT) from None
    except (OSError, RuntimeError):
        raise _fail(ReviewErrorCode.STORAGE_ERROR) from None


def _atomic_replace(path: Path, content: bytes) -> None:
    try:
        descriptor, temp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=path.parent
        )
    except (OSError, RuntimeError):
        raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
    temp_path = Path(temp_name)
    try:
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except (OSError, RuntimeError):
            raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
        try:
            os.replace(temp_path, path)
        except (OSError, RuntimeError):
            raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
        _fsync_directory(path.parent)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except (OSError, RuntimeError):
            pass


def _acquire_lock(review_root: Path, database_id: str) -> Path:
    lock_path = review_root / f".lock-{database_id}.lock"
    try:
        if _is_redirect(lock_path):
            raise _fail(ReviewErrorCode.REVIEW_CONFLICT)
    except ReviewError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise _fail(ReviewErrorCode.REVIEW_CONFLICT) from None
    except (OSError, RuntimeError):
        raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
    try:
        os.close(descriptor)
    except (OSError, RuntimeError):
        pass
    return lock_path


def _release_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink(missing_ok=True)
    except (OSError, RuntimeError):
        pass


def _mapping_filename(version: int) -> str:
    return f"mapping-v{version:04d}.json"


def _existing_versions(review_dir: Path) -> list[int]:
    versions: list[int] = []
    try:
        entries = list(review_dir.iterdir())
    except (OSError, RuntimeError):
        return []
    for item in entries:
        if item.name.startswith("mapping-v") and item.name.endswith(".json"):
            body = item.name[len("mapping-v") : -len(".json")]
            if len(body) == 4 and body.isdigit():
                versions.append(int(body))
    return sorted(versions)


def _load_draft_strict(review_dir: Path) -> MappingDraft:
    raw = _read_review_file(review_dir / DRAFT_FILENAME, review_dir, max_bytes=MAX_JSON_BYTES)
    return _strict_model(raw, MappingDraft)


def _load_pointer_strict(review_dir: Path) -> ReviewPointer:
    raw = _read_review_file(
        review_dir / CURRENT_FILENAME, review_dir, max_bytes=MAX_MANIFEST_BYTES
    )
    payload = _parse_json(raw)
    try:
        model = ReviewPointer.model_validate(payload)
    except ValidationError:
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH) from None
    # Pre-8-01 pointers omit the history; an explicit null stays rejected.
    if payload != model.model_dump(mode="json", exclude_none=True):
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    return model


def _load_manifest_strict(review_dir: Path) -> ActivationManifest:
    raw = _read_review_file(
        review_dir / ACTIVATION_FILENAME, review_dir, max_bytes=MAX_MANIFEST_BYTES
    )
    payload = _parse_json(raw)
    try:
        model = ActivationManifest.model_validate(payload)
    except ValidationError:
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH) from None
    # Pre-8-01 manifests omit the optional activation fields; an explicit null
    # is never written and stays rejected.
    if payload != model.model_dump(mode="json", exclude_none=True):
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    return model


def _check_canonical_full_coverage(profile: PublishedMappingProfile) -> bool:
    from ..api.database_registry import CANONICAL_SELECT

    # Phase 8-18: every course-list field, plus any other ontology field.
    ontology = load_default_ontology()
    if not ontology.accepts_version(profile.ontology_version):
        return False
    known = set(ontology.fields_at(profile.ontology_version))
    return (set(CANONICAL_SELECT) - ontology.retired_fields) <= set(profile.fields) <= known


def _validate_canonical_executable(catalog: Any, profile: PublishedMappingProfile) -> None:
    from ..api.database_registry import CANONICAL_SELECT
    from ..compiler.harness import SchemaAdaptiveSQLiteCompiler
    from ..harness.contracts import QueryIR
    from ..mapping.bridge import to_harness_mapping
    from ..policy.harness import SchemaAdaptiveAstPolicy
    from ..relations.physical import build_physical_relation_graph

    if not _check_canonical_full_coverage(profile):
        raise _fail(ReviewErrorCode.PUBLISH_BLOCKED)
    try:
        graph = build_physical_relation_graph(catalog)
    except Exception:
        raise _fail(ReviewErrorCode.PUBLISH_BLOCKED) from None
    if graph.database_id != profile.database_id:
        raise _fail(ReviewErrorCode.PUBLISH_BLOCKED)
    if graph.schema_fingerprint != profile.schema_fingerprint:
        raise _fail(ReviewErrorCode.PUBLISH_BLOCKED)
    try:
        harness_mapping = to_harness_mapping(profile)
        compiler = SchemaAdaptiveSQLiteCompiler(graph, profile)
        query_ir = QueryIR.model_validate(
            {
                "entity": "CourseOffering",
                "select": [name for name in CANONICAL_SELECT if name not in load_default_ontology().retired_fields],
                "filters": [],
                "limit": 200,
            }
        )
        compiled = compiler.compile(query_ir, harness_mapping)
        SchemaAdaptiveAstPolicy(graph, profile).validate(
            compiled, query_ir=query_ir, mapping=harness_mapping
        )
    except ReviewError:
        raise
    except Exception:
        raise _fail(ReviewErrorCode.PUBLISH_BLOCKED) from None


def init_review(*, repository_root: str | Path, database_id: str, reviewer: str) -> MappingDraft:
    database_id = _validate_database_id(database_id)
    reviewer = validate_reviewer(reviewer)
    root, review_root = _review_root(repository_root)
    review_dir = _validate_review_target(root, review_root, database_id)
    try:
        loaded = registration_storage.load_registration(
            repository_root=root, database_id=database_id
        )
    except Exception:
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH) from None
    try:
        _ensure_review_root(review_root)
    except ReviewError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID) from None
    review_dir = _validate_review_target(root, review_root, database_id)
    lock_path = _acquire_lock(review_root, database_id)
    staging: Path | None = None
    try:
        if review_dir.exists() or review_dir.is_symlink():
            if _is_redirect(review_dir) or not review_dir.is_dir():
                raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
            try:
                names = sorted(item.name for item in review_dir.iterdir())
            except (OSError, RuntimeError):
                raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
            allowed_prefix = ("mapping-v",)
            for name in names:
                if name in _REVIEW_FILENAMES or _is_staging_leftover(name):
                    continue
                if name.startswith(allowed_prefix) and name.endswith(".json"):
                    continue
                raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
            if DRAFT_FILENAME in names:
                existing = _load_draft_strict(review_dir)
                if (
                    existing.database_id != database_id
                    or existing.schema_fingerprint != loaded.manifest.schema_fingerprint
                    or existing.ontology_version != loaded.manifest.ontology_version
                ):
                    raise _fail(ReviewErrorCode.REVIEW_CONFLICT)
                return existing
        else:
            try:
                review_dir.mkdir(parents=False, exist_ok=False)
            except FileExistsError:
                raise _fail(ReviewErrorCode.REVIEW_CONFLICT) from None
            except (OSError, RuntimeError):
                raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
            _fsync_directory(review_root)
            review_dir = _validate_review_target(root, review_root, database_id)
        draft = MappingReviewService().create_draft(loaded.proposal)
        if (
            draft.database_id != database_id
            or draft.schema_fingerprint != loaded.manifest.schema_fingerprint
            or draft.ontology_version != loaded.manifest.ontology_version
        ):
            raise _fail(ReviewErrorCode.STORAGE_ERROR)
        try:
            content = _json_bytes(draft.model_dump(mode="json"))
        except Exception:
            raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
        try:
            staging_descriptor, staging_name = tempfile.mkstemp(
                prefix=DRAFT_FILENAME + ".", suffix=".tmp", dir=review_dir
            )
        except (OSError, RuntimeError):
            raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
        staging = Path(staging_name)
        try:
            with os.fdopen(staging_descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except (OSError, RuntimeError):
            raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
        try:
            drafted = _load_draft_strict(review_dir) if (review_dir / DRAFT_FILENAME).exists() else None
        except ReviewError:
            drafted = None
        except (OSError, RuntimeError):
            drafted = None
        if drafted is not None:
            if (
                drafted.database_id != database_id
                or drafted.schema_fingerprint != loaded.manifest.schema_fingerprint
                or drafted.ontology_version != loaded.manifest.ontology_version
            ):
                raise _fail(ReviewErrorCode.REVIEW_CONFLICT)
            return drafted
        try:
            os.rename(staging, review_dir / DRAFT_FILENAME)
        except FileExistsError:
            raise _fail(ReviewErrorCode.REVIEW_CONFLICT) from None
        except (OSError, RuntimeError):
            raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
        staging = None
        _fsync_directory(review_dir)
        return _load_draft_strict(review_dir)
    finally:
        if staging is not None:
            try:
                staging.unlink(missing_ok=True)
            except (OSError, RuntimeError):
                pass
        _release_lock(lock_path)


def _mutate_draft(
    *,
    repository_root: str | Path,
    database_id: str,
    reviewer: str,
    kind: str,
    semantic_field: str,
    candidate_index: int | None = None,
    table: str | None = None,
    column: str | None = None,
    reason: str | None = None,
) -> MappingDraft:
    database_id = _validate_database_id(database_id)
    reviewer = validate_reviewer(reviewer)
    semantic_field = validate_semantic_field(semantic_field)
    root, review_root = _review_root(repository_root)
    review_dir = _validate_review_target(root, review_root, database_id)
    try:
        loaded = registration_storage.load_registration(
            repository_root=root, database_id=database_id
        )
    except Exception:
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH) from None
    if not review_dir.is_dir() or _is_redirect(review_dir):
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    lock_path = _acquire_lock(review_root, database_id)
    try:
        draft = _load_draft_strict(review_dir)
        if (
            draft.database_id != database_id
            or draft.schema_fingerprint != loaded.manifest.schema_fingerprint
            or draft.ontology_version != loaded.manifest.ontology_version
        ):
            raise _fail(ReviewErrorCode.REVIEW_CONFLICT)
        service = MappingReviewService()
        try:
            if kind == "APPROVE":
                if candidate_index is None:
                    raise _fail(ReviewErrorCode.INVALID_INPUT)
                new_draft = service.approve(
                    draft,
                    loaded.proposal,
                    semantic_field,
                    reviewer=reviewer,
                    candidate_index=candidate_index,
                )
            elif kind == "IGNORE":
                if reason is None:
                    raise _fail(ReviewErrorCode.INVALID_INPUT)
                new_draft = service.ignore(
                    draft, semantic_field, reviewer=reviewer, reason=reason
                )
            elif kind == "REMAP":
                if table is None or column is None or reason is None:
                    raise _fail(ReviewErrorCode.INVALID_INPUT)
                physical = {(t.name, c.name) for t in loaded.catalog.tables for c in t.columns}
                if (table, column) not in physical:
                    raise _fail(ReviewErrorCode.INVALID_INPUT)
                new_draft = service.remap(
                    draft,
                    semantic_field,
                    table=table,
                    column=column,
                    reviewer=reviewer,
                    reason=reason,
                )
            else:
                raise _fail(ReviewErrorCode.INVALID_INPUT)
        except ReviewError:
            raise
        except MappingWorkflowError as exc:
            from ..mapping.errors import MappingWorkflowErrorCode as _Code

            if exc.code is _Code.CANDIDATE_NOT_FOUND or exc.code is _Code.UNKNOWN_FIELD:
                raise _fail(ReviewErrorCode.INVALID_INPUT) from None
            if exc.code is _Code.PROPOSAL_MISMATCH:
                raise _fail(ReviewErrorCode.REVIEW_CONFLICT) from None
            raise _fail(ReviewErrorCode.INVALID_INPUT) from None
        except ValueError:
            raise _fail(ReviewErrorCode.INVALID_INPUT) from None
        except Exception:
            raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
        try:
            content = _json_bytes(new_draft.model_dump(mode="json"))
        except Exception:
            raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
        _atomic_replace(review_dir / DRAFT_FILENAME, content)
        return _load_draft_strict(review_dir)
    finally:
        _release_lock(lock_path)


def approve_field(
    *,
    repository_root: str | Path,
    database_id: str,
    semantic_field: str,
    candidate_index: int,
    reviewer: str,
) -> MappingDraft:
    if not isinstance(candidate_index, int) or isinstance(candidate_index, bool):
        raise _fail(ReviewErrorCode.INVALID_INPUT)
    if candidate_index < 0 or candidate_index > 64:
        raise _fail(ReviewErrorCode.INVALID_INPUT)
    return _mutate_draft(
        repository_root=repository_root,
        database_id=database_id,
        reviewer=reviewer,
        kind="APPROVE",
        semantic_field=semantic_field,
        candidate_index=candidate_index,
    )


def remap_field(
    *,
    repository_root: str | Path,
    database_id: str,
    semantic_field: str,
    table: str,
    column: str,
    reviewer: str,
    reason: str,
) -> MappingDraft:
    table = validate_physical_name(table)
    column = validate_physical_name(column)
    reason = validate_reason(reason)
    return _mutate_draft(
        repository_root=repository_root,
        database_id=database_id,
        reviewer=reviewer,
        kind="REMAP",
        semantic_field=semantic_field,
        table=table,
        column=column,
        reason=reason,
    )


def ignore_field(
    *,
    repository_root: str | Path,
    database_id: str,
    semantic_field: str,
    reviewer: str,
    reason: str,
) -> MappingDraft:
    """Phase 8-15: mark a field as absent from this database (reason required).

    SQLite publishing still needs every course-list field, so ignoring one of
    those blocks an SQLite publish until it is approved or remapped; other
    fields (Phase 8-18) may stay ignored.
    """
    reason = validate_reason(reason)
    return _mutate_draft(
        repository_root=repository_root,
        database_id=database_id,
        reviewer=reviewer,
        kind="IGNORE",
        semantic_field=semantic_field,
        reason=reason,
    )


def load_review(*, repository_root: str | Path, database_id: str) -> LoadedReview:
    database_id = _validate_database_id(database_id)
    root, review_root = _review_root(repository_root)
    review_dir = _validate_review_target(root, review_root, database_id)
    if not review_dir.is_dir() or _is_redirect(review_dir):
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    draft = _load_draft_strict(review_dir)
    published: PublishedMappingProfile | None = None
    version: int | None = None
    if (review_dir / CURRENT_FILENAME).exists():
        pointer = _load_pointer_strict(review_dir)
        if pointer.database_id != database_id:
            raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
        mapping_raw = _read_review_file(
            review_dir / _mapping_filename(pointer.version),
            review_dir,
            max_bytes=MAX_JSON_BYTES,
        )
        if (
            len(mapping_raw) != pointer.mapping_bytes
            or hashlib.sha256(mapping_raw).hexdigest() != pointer.mapping_sha256
        ):
            raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
        published = _strict_model(mapping_raw, PublishedMappingProfile)
        if (
            published.database_id != database_id
            or published.version != pointer.version
            or published.schema_fingerprint != pointer.schema_fingerprint
            or published.ontology_version != pointer.ontology_version
        ):
            raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
        version = pointer.version
    return LoadedReview(
        database_id=database_id, draft=draft, published=published, mapping_version=version
    )


def publish_review(
    *, repository_root: str | Path, database_id: str, published_by: str
) -> LoadedActivation:
    database_id = _validate_database_id(database_id)
    published_by = validate_reviewer(published_by)
    root, review_root = _review_root(repository_root)
    review_dir = _validate_review_target(root, review_root, database_id)
    try:
        loaded = registration_storage.load_registration(
            repository_root=root, database_id=database_id
        )
    except Exception:
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH) from None
    try:
        ontology = load_default_ontology()
    except Exception:
        raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
    if not review_dir.is_dir() or _is_redirect(review_dir):
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    lock_path = _acquire_lock(review_root, database_id)
    try:
        draft = _load_draft_strict(review_dir)
        if (
            draft.database_id != database_id
            or draft.schema_fingerprint != loaded.manifest.schema_fingerprint
            or draft.ontology_version != loaded.manifest.ontology_version
        ):
            raise _fail(ReviewErrorCode.REVIEW_CONFLICT)
        if not ontology.accepts_version(draft.ontology_version):
            raise _fail(ReviewErrorCode.PUBLISH_BLOCKED)
        service = MappingReviewService()
        try:
            issues = service.validate_for_publish(draft, loaded.catalog, ontology)
        except Exception:
            raise _fail(ReviewErrorCode.PUBLISH_BLOCKED) from None
        if issues:
            raise _fail(ReviewErrorCode.PUBLISH_BLOCKED)
        versions = _existing_versions(review_dir)
        next_version = max(versions, default=0) + 1
        try:
            candidate = service.publish(
                draft,
                loaded.catalog,
                ontology,
                version=next_version,
                published_by=published_by,
            )
        except MappingPublishError:
            raise _fail(ReviewErrorCode.PUBLISH_BLOCKED) from None
        except ValueError:
            raise _fail(ReviewErrorCode.INVALID_INPUT) from None
        except Exception:
            raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
        if not _check_canonical_full_coverage(candidate):
            raise _fail(ReviewErrorCode.PUBLISH_BLOCKED)
        _validate_canonical_executable(loaded.catalog, candidate)
        if (review_dir / CURRENT_FILENAME).exists():
            try:
                pointer = _load_pointer_strict(review_dir)
                existing_raw = _read_review_file(
                    review_dir / _mapping_filename(pointer.version),
                    review_dir,
                    max_bytes=MAX_JSON_BYTES,
                )
                existing = _strict_model(existing_raw, PublishedMappingProfile)
                if (
                    existing.database_id == candidate.database_id
                    and existing.schema_fingerprint == candidate.schema_fingerprint
                    and existing.ontology_version == candidate.ontology_version
                    and existing.fields == candidate.fields
                ):
                    # Publishing the latest reviewed content must make it the
                    # executing version, even after a rollback or an
                    # interrupted activation write.
                    if _active_version_or_none(review_dir) != pointer.version:
                        _activate_locked(
                            review_dir,
                            database_id=database_id,
                            version=pointer.version,
                            loaded=loaded,
                            ontology=ontology,
                            activated_by=published_by,
                            record=pointer.published_records().get(pointer.version),
                        )
                    return load_activation(repository_root=root, database_id=database_id)
            except ReviewError:
                raise
            except Exception:
                raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH) from None
        try:
            mapping_bytes = _json_bytes(candidate.model_dump(mode="json"))
        except Exception:
            raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
        mapping_name = _mapping_filename(next_version)
        if (review_dir / mapping_name).exists():
            raise _fail(ReviewErrorCode.REVIEW_CONFLICT)
        previous_records: list[PublishedVersionRecord] = []
        if (review_dir / CURRENT_FILENAME).exists():
            previous_pointer = _load_pointer_strict(review_dir)
            if previous_pointer.database_id != database_id:
                raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
            previous_records = sorted(
                previous_pointer.published_records().values(), key=lambda item: item.version
            )
        _write_new_file(review_dir / mapping_name, mapping_bytes)
        digest = hashlib.sha256(mapping_bytes).hexdigest()
        records = [
            *previous_records,
            PublishedVersionRecord(
                version=next_version, mapping_sha256=digest, mapping_bytes=len(mapping_bytes)
            ),
        ][-MAX_RECORDED_VERSIONS:]
        pointer = ReviewPointer(
            schema_version=REVIEW_SCHEMA_VERSION,
            database_id=database_id,
            version=next_version,
            mapping_sha256=digest,
            mapping_bytes=len(mapping_bytes),
            schema_fingerprint=candidate.schema_fingerprint,
            ontology_version=candidate.ontology_version,
            published_versions=records,
        )
        try:
            pointer_bytes = _json_bytes(pointer.model_dump(mode="json", exclude_none=True))
        except Exception:
            try:
                (review_dir / mapping_name).unlink(missing_ok=True)
            except (OSError, RuntimeError):
                pass
            raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
        manifest = ActivationManifest(
            schema_version=ACTIVATION_SCHEMA_VERSION,
            database_id=database_id,
            mapping_version=next_version,
            schema_fingerprint=loaded.manifest.schema_fingerprint,
            ontology_version=loaded.manifest.ontology_version,
            mapping_sha256=digest,
            mapping_bytes=len(mapping_bytes),
            content_sha256=loaded.manifest.content_sha256,
            content_bytes=loaded.manifest.content_bytes,
            label=loaded.manifest.label,
            activated_by=published_by,
            activated_at=_utc_now_text(),
        )
        try:
            manifest_bytes = _json_bytes(manifest.model_dump(mode="json"))
        except Exception:
            try:
                (review_dir / mapping_name).unlink(missing_ok=True)
            except (OSError, RuntimeError):
                pass
            raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
        _atomic_replace(review_dir / CURRENT_FILENAME, pointer_bytes)
        _atomic_replace(review_dir / ACTIVATION_FILENAME, manifest_bytes)
        return load_activation(repository_root=root, database_id=database_id)
    finally:
        _release_lock(lock_path)


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_version_profile(review_dir: Path, version: int) -> tuple[bytes, PublishedMappingProfile]:
    raw = _read_review_file(
        review_dir / _mapping_filename(version), review_dir, max_bytes=MAX_JSON_BYTES
    )
    return raw, _strict_model(raw, PublishedMappingProfile)


def _check_activatable(
    profile: PublishedMappingProfile,
    *,
    database_id: str,
    version: int,
    loaded: Any,
    ontology: Any,
) -> None:
    """Apply the publish-time execution checks to an existing immutable version."""
    if (
        profile.database_id != database_id
        or profile.version != version
        or profile.status != "PUBLISHED"
        or profile.schema_fingerprint != loaded.manifest.schema_fingerprint
        or not ontology.accepts_version(profile.ontology_version)
        or profile.ontology_version != loaded.manifest.ontology_version
    ):
        raise _fail(ReviewErrorCode.PUBLISH_BLOCKED)
    if not _check_canonical_full_coverage(profile):
        raise _fail(ReviewErrorCode.PUBLISH_BLOCKED)
    _validate_canonical_executable(loaded.catalog, profile)


def _active_version_or_none(review_dir: Path) -> int | None:
    try:
        return _load_manifest_strict(review_dir).mapping_version
    except Exception:
        return None


def _activate_locked(
    review_dir: Path,
    *,
    database_id: str,
    version: int,
    loaded: Any,
    ontology: Any,
    activated_by: str,
    record: "PublishedVersionRecord | None",
) -> None:
    """Point activation.json at an existing immutable version; caller holds the lock.

    ``record`` is the digest the pointer stored when the version was published;
    a version without one (never published) or with different bytes is refused.
    """
    if record is None or record.version != version:
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    raw, profile = _read_version_profile(review_dir, version)
    if len(raw) != record.mapping_bytes or hashlib.sha256(raw).hexdigest() != record.mapping_sha256:
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    _check_activatable(
        profile, database_id=database_id, version=version, loaded=loaded, ontology=ontology
    )
    manifest = ActivationManifest(
        schema_version=ACTIVATION_SCHEMA_VERSION,
        database_id=database_id,
        mapping_version=version,
        schema_fingerprint=loaded.manifest.schema_fingerprint,
        ontology_version=loaded.manifest.ontology_version,
        mapping_sha256=hashlib.sha256(raw).hexdigest(),
        mapping_bytes=len(raw),
        content_sha256=loaded.manifest.content_sha256,
        content_bytes=loaded.manifest.content_bytes,
        label=loaded.manifest.label,
        activated_by=activated_by,
        activated_at=_utc_now_text(),
    )
    try:
        manifest_bytes = _json_bytes(manifest.model_dump(mode="json"))
    except Exception:
        raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
    _atomic_replace(review_dir / ACTIVATION_FILENAME, manifest_bytes)


def _validate_mapping_version(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(ReviewErrorCode.INVALID_INPUT)
    if value < 1 or value > MAX_MAPPING_VERSION:
        raise _fail(ReviewErrorCode.INVALID_INPUT)
    return value


def _load_published_context(review_dir: Path, database_id: str, loaded: Any) -> ReviewPointer:
    """Require a strict draft and latest pointer for this registration."""
    draft = _load_draft_strict(review_dir)
    if (
        draft.database_id != database_id
        or draft.schema_fingerprint != loaded.manifest.schema_fingerprint
        or draft.ontology_version != loaded.manifest.ontology_version
    ):
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    if not (review_dir / CURRENT_FILENAME).exists():
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    pointer = _load_pointer_strict(review_dir)
    if pointer.database_id != database_id:
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    return pointer


def activate_mapping_version(
    *,
    repository_root: str | Path,
    database_id: str,
    mapping_version: int,
    activated_by: str,
) -> LoadedActivation:
    """Re-activate an earlier (or the latest) immutable published version (Phase 8-01).

    Only versions up to the latest published pointer are eligible, each is
    re-validated like a publish, and only ``activation.json`` changes.
    """
    database_id = _validate_database_id(database_id)
    activated_by = validate_reviewer(activated_by)
    mapping_version = _validate_mapping_version(mapping_version)
    root, review_root = _review_root(repository_root)
    review_dir = _validate_review_target(root, review_root, database_id)
    try:
        loaded = registration_storage.load_registration(
            repository_root=root, database_id=database_id
        )
    except Exception:
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH) from None
    try:
        ontology = load_default_ontology()
    except Exception:
        raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
    if not review_dir.is_dir() or _is_redirect(review_dir):
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    lock_path = _acquire_lock(review_root, database_id)
    try:
        pointer = _load_published_context(review_dir, database_id, loaded)
        if not (review_dir / ACTIVATION_FILENAME).exists():
            raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
        record = pointer.published_records().get(mapping_version)
        if record is None:
            raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
        if _active_version_or_none(review_dir) == mapping_version:
            try:
                return load_activation(repository_root=root, database_id=database_id)
            except ReviewError:
                pass  # rewrite a damaged record for the same version below
        _activate_locked(
            review_dir,
            database_id=database_id,
            version=mapping_version,
            loaded=loaded,
            ontology=ontology,
            activated_by=activated_by,
            record=record,
        )
        return load_activation(repository_root=root, database_id=database_id)
    finally:
        _release_lock(lock_path)


def list_mapping_versions(
    *, repository_root: str | Path, database_id: str
) -> list[MappingVersionInfo]:
    """Metadata of published versions (newest last); no mapping content or paths."""
    database_id = _validate_database_id(database_id)
    root, review_root = _review_root(repository_root)
    review_dir = _validate_review_target(root, review_root, database_id)
    if not review_dir.is_dir() or _is_redirect(review_dir):
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    try:
        loaded = registration_storage.load_registration(
            repository_root=root, database_id=database_id
        )
        ontology = load_default_ontology()
    except Exception:
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH) from None
    if not (review_dir / CURRENT_FILENAME).exists():
        return []
    pointer = _load_pointer_strict(review_dir)
    if pointer.database_id != database_id:
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    try:
        active: int | None = load_activation(
            repository_root=root, database_id=database_id
        ).mapping_version
    except ReviewError:
        active = None
    # Only versions the pointer recorded as published; orphan files from an
    # interrupted publish never appear. The active version is always listed.
    records = pointer.published_records()
    numbers = sorted(records)
    shown = numbers[-MAX_LISTED_VERSIONS:]
    if active is not None and active in records and active not in shown:
        shown = [active, *shown]
    results: list[MappingVersionInfo] = []
    for version in shown:
        try:
            raw, profile = _read_version_profile(review_dir, version)
        except ReviewError:
            continue
        record = records[version]
        if len(raw) != record.mapping_bytes or hashlib.sha256(raw).hexdigest() != record.mapping_sha256:
            continue
        if profile.database_id != database_id or profile.version != version:
            continue
        try:
            validate_reviewer(profile.published_by)
        except ReviewError:
            continue
        try:
            _check_activatable(
                profile,
                database_id=database_id,
                version=version,
                loaded=loaded,
                ontology=ontology,
            )
            activatable = True
        except ReviewError:
            activatable = False
        results.append(
            MappingVersionInfo(
                version=version,
                published_by=profile.published_by,
                published_at=_utc_text(profile.published_at),
                active=version == active,
                latest=version == pointer.version,
                activatable=activatable,
            )
        )
    return results

def load_activation(*, repository_root: str | Path, database_id: str) -> LoadedActivation:
    database_id = _validate_database_id(database_id)
    root, review_root = _review_root(repository_root)
    review_dir = _validate_review_target(root, review_root, database_id)
    try:
        if _is_redirect(review_dir) or not review_dir.is_dir():
            raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
        if review_dir.resolve(strict=True).parent != review_root.resolve(strict=True):
            raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
        names = sorted(item.name for item in review_dir.iterdir())
    except ReviewError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH) from None
    for name in names:
        if name in _REVIEW_FILENAMES or _is_staging_leftover(name):
            continue
        if name.startswith("mapping-v") and name.endswith(".json") and len(name) == len("mapping-v0000.json"):
            body = name[len("mapping-v") : -len(".json")]
            if body.isdigit():
                continue
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    if (
        CURRENT_FILENAME not in names
        or ACTIVATION_FILENAME not in names
        or DRAFT_FILENAME not in names
    ):
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    # A tampered, missing, or stale draft hides the activation as well.
    try:
        draft = _load_draft_strict(review_dir)
    except ReviewError:
        raise
    except Exception:
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH) from None
    if draft.database_id != database_id:
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    manifest = _load_manifest_strict(review_dir)
    if manifest.database_id != database_id:
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    # activation.json is the sole execution-authority commit record. current.json
    # may advance first during a re-publish; until activation is replaced, the
    # previous immutable mapping remains executable across failures/restarts.
    mapping_raw = _read_review_file(
        review_dir / _mapping_filename(manifest.mapping_version),
        review_dir,
        max_bytes=MAX_JSON_BYTES,
    )
    if (
        len(mapping_raw) != manifest.mapping_bytes
        or hashlib.sha256(mapping_raw).hexdigest() != manifest.mapping_sha256
    ):
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    profile = _strict_model(mapping_raw, PublishedMappingProfile)
    if (
        profile.database_id != database_id
        or profile.version != manifest.mapping_version
        or profile.status != "PUBLISHED"
        or profile.schema_fingerprint != manifest.schema_fingerprint
        or profile.ontology_version != manifest.ontology_version
    ):
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    try:
        loaded = registration_storage.load_registration(
            repository_root=root, database_id=database_id
        )
    except Exception:
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH) from None
    try:
        ontology = load_default_ontology()
    except Exception:
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH) from None
    if (
        manifest.schema_fingerprint != loaded.manifest.schema_fingerprint
        or manifest.ontology_version != loaded.manifest.ontology_version
        or manifest.content_sha256 != loaded.manifest.content_sha256
        or manifest.content_bytes != loaded.manifest.content_bytes
        or manifest.label != loaded.manifest.label
    ):
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    if profile.schema_fingerprint != loaded.manifest.schema_fingerprint:
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    if not ontology.accepts_version(profile.ontology_version):
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    if profile.ontology_version != loaded.manifest.ontology_version:
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    if (
        draft.schema_fingerprint != loaded.manifest.schema_fingerprint
        or draft.ontology_version != loaded.manifest.ontology_version
    ):
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    if not _check_canonical_full_coverage(profile):
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    _validate_canonical_executable(loaded.catalog, profile)
    try:
        registration_storage.validate_label(manifest.label)
    except Exception:
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH) from None
    db_path = root / ".schema-adaptive" / "registered-databases" / database_id / "database.sqlite3"
    return LoadedActivation(
        database_id=database_id,
        label=manifest.label,
        database_path=db_path,
        catalog=loaded.catalog,
        profile=profile,
        schema_fingerprint=profile.schema_fingerprint,
        ontology_version=profile.ontology_version,
        mapping_version=profile.version,
    )


def list_activations(*, repository_root: str | Path) -> list[LoadedActivation]:
    try:
        root, review_root = _review_root(repository_root)
    except ReviewError:
        return []
    try:
        if _is_redirect(review_root) or not review_root.is_dir():
            return []
    except (OSError, RuntimeError, ValueError):
        return []
    try:
        entries = sorted(item.name for item in review_root.iterdir())
    except (OSError, RuntimeError):
        return []
    results: list[LoadedActivation] = []
    for name in entries:
        if name.startswith(".lock-"):
            continue
        if DATABASE_ID_PATTERN.fullmatch(name) is None:
            continue
        if name.lower() in WINDOWS_RESERVED_NAMES:
            continue
        try:
            _validate_review_target(root, review_root, name)
        except ReviewError:
            continue
        try:
            results.append(load_activation(repository_root=root, database_id=name))
        except ReviewError:
            continue
        except Exception:
            continue
    results.sort(key=lambda item: item.database_id)
    return results


def incomplete_field_ids(*, repository_root: str | Path, database_id: str) -> list[str]:
    try:
        root, review_root = _review_root(repository_root)
        review_dir = _validate_review_target(root, review_root, database_id)
        draft = _load_draft_strict(review_dir)
    except Exception:
        return []
    from ..api.database_registry import CANONICAL_SELECT
    from ..mapping import ReviewDecision

    # Phase 8-18: an unreviewed field, or a course-list field left unmapped;
    # other fields may be absent from an SQLite upload.
    pending = sorted(
        name
        for name, entry in draft.fields.items()
        if name not in load_default_ontology().retired_fields
        and (entry.decision is ReviewDecision.PENDING or (
            name in CANONICAL_SELECT
            and entry.decision not in {ReviewDecision.APPROVED, ReviewDecision.REMAPPED}
        ))
    )
    return pending[:64]


@dataclass(frozen=True)
class RegistrationComparison:
    base_database_id: str
    candidate_database_id: str
    base_mapping_version: int
    diff: Any
    field_impacts: list[Any]
    ontology_version_match: bool
    carry_over_executable: bool


def compare_registrations(
    *, repository_root: str | Path, base_database_id: str, candidate_database_id: str
) -> RegistrationComparison:
    """Read-only schema comparison against the base's active mapping (Phase 8-02).

    Writes nothing and grants nothing: the carry-over check stops at compile
    and AST policy validation and never executes SQL.
    """
    from ..catalog.compare import FieldImpactStatus, compare_catalogs, mapping_impact

    base_database_id = _validate_database_id(base_database_id)
    candidate_database_id = _validate_database_id(candidate_database_id)
    if base_database_id == candidate_database_id:
        raise _fail(ReviewErrorCode.INVALID_INPUT)
    root, _review_root_path = _review_root(repository_root)
    base = load_activation(repository_root=root, database_id=base_database_id)
    try:
        candidate = registration_storage.load_registration(
            repository_root=root, database_id=candidate_database_id
        )
    except registration_storage.RegistrationError as exc:
        # Storage faults are internal errors, not a missing registration.
        if exc.code in (
            registration_storage.RegistrationErrorCode.STORAGE_ERROR,
            registration_storage.RegistrationErrorCode.PRIVATE_STORAGE_INVALID,
        ):
            raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH) from None
    except Exception:
        raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
    diff = compare_catalogs(base.catalog, candidate.catalog)
    # Only the target version's active fields participate in comparison or
    # carry-over. A historical year mapping must not be remapped into v3.
    target_fields = load_default_ontology().fields_at(candidate.manifest.ontology_version)
    mapped = {
        name: (entry.table, entry.column)
        for name, entry in base.profile.fields.items() if name in target_fields
    }
    impacts = mapping_impact(mapped, base.catalog, candidate.catalog)
    # Older bases carry active fields; newly added fields are NOT_IN_BASE.
    ontology_match = 1 <= base.ontology_version <= candidate.manifest.ontology_version
    executable = False
    if ontology_match and all(
        item.status in (FieldImpactStatus.UNCHANGED, FieldImpactStatus.TYPE_CHANGED)
        for item in impacts
    ):
        carried = base.profile.model_copy(update={
            "database_id": candidate_database_id,
            "schema_fingerprint": candidate.manifest.schema_fingerprint,
        })
        try:
            _validate_canonical_executable(candidate.catalog, carried)
            executable = _check_canonical_full_coverage(carried)
        except ReviewError:
            executable = False
    return RegistrationComparison(
        base_database_id=base_database_id,
        candidate_database_id=candidate_database_id,
        base_mapping_version=base.mapping_version,
        diff=diff,
        field_impacts=impacts,
        ontology_version_match=ontology_match,
        carry_over_executable=executable,
    )


SUCCESSION_FILENAME = "succession.json"
SUCCESSION_SCHEMA_VERSION = "mapping-succession-v1"
_STALE_STATUSES = ("TYPE_CHANGED", "COLUMN_MISSING", "TABLE_MISSING")


class StaleField(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    semantic_field: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    # Phase 8-18: NOT_IN_BASE is a field the base mapping never had (a newer
    # ontology field, or one the base left out); it waits for review.
    status: str = Field(pattern=r"^(TYPE_CHANGED|COLUMN_MISSING|TABLE_MISSING|NOT_IN_BASE)$")


class MappingSuccession(BaseModel):
    """Record of a carry-over review start (Phase 8-03); grants no execution."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: str = Field(pattern=r"^mapping-succession-v1$")
    database_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    base_database_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    base_mapping_version: int = Field(ge=1)
    base_mapping_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_schema_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    carried_fields: list[str] = Field(max_length=64)
    stale_fields: list[StaleField] = Field(max_length=64)
    initiated_by: str = Field(min_length=1, max_length=MAX_REVIEWER_LENGTH)
    initiated_at: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")

    @model_validator(mode="after")
    def _consistent(self) -> "MappingSuccession":
        stale = [item.semantic_field for item in self.stale_fields]
        if self.carried_fields != sorted(set(self.carried_fields)) or stale != sorted(set(stale)):
            raise ValueError("succession fields must be sorted and unique")
        if set(self.carried_fields) & set(stale):
            raise ValueError("a field cannot be both carried and stale")
        if self.database_id == self.base_database_id:
            raise ValueError("succession needs two registrations")
        return self


def _carried_reason(base_database_id: str, version: int) -> str:
    return f"carried over from {base_database_id} mapping-v{version}; schema unchanged"


def _same_review_state(left: MappingDraft, right: MappingDraft) -> bool:
    """Compare review decisions while ignoring reviewer and timestamps."""
    if (
        left.database_id != right.database_id
        or left.schema_fingerprint != right.schema_fingerprint
        or left.ontology_version != right.ontology_version
        or set(left.fields) != set(right.fields)
    ):
        return False
    return all(
        (a.decision, a.table, a.column, a.reason) == (b.decision, b.table, b.column, b.reason)
        for a, b in ((left.fields[name], right.fields[name]) for name in left.fields)
    )


def _link_new_file(path: Path, content: bytes) -> None:
    """Publish a complete file at ``path`` or nothing; never overwrite."""
    try:
        descriptor, temp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=path.parent
        )
    except (OSError, RuntimeError):
        raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
    temp_path = Path(temp_name)
    try:
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except (OSError, RuntimeError):
            raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
        try:
            os.link(temp_path, path)
        except FileExistsError:
            raise _fail(ReviewErrorCode.REVIEW_CONFLICT) from None
        except (OSError, RuntimeError):
            raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
        _fsync_directory(path.parent)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except (OSError, RuntimeError):
            pass


def _load_succession_strict(review_dir: Path) -> MappingSuccession:
    raw = _read_review_file(
        review_dir / SUCCESSION_FILENAME, review_dir, max_bytes=MAX_MANIFEST_BYTES
    )
    return _strict_model(raw, MappingSuccession)


def carry_over_review(
    *,
    repository_root: str | Path,
    database_id: str,
    base_database_id: str,
    reviewer: str,
) -> MappingDraft:
    """Start the first review of a registration from a base's active mapping.

    Fields whose mapped column is unchanged are remapped to the base column
    with a recorded reason; affected fields stay PENDING and are recorded as
    stale. Publishing still requires every field reviewed and executable.
    """
    from ..catalog.compare import FieldImpactStatus

    database_id = _validate_database_id(database_id)
    base_database_id = _validate_database_id(base_database_id)
    reviewer = validate_reviewer(reviewer)
    if database_id == base_database_id:
        raise _fail(ReviewErrorCode.INVALID_INPUT)
    comparison = compare_registrations(
        repository_root=repository_root,
        base_database_id=base_database_id,
        candidate_database_id=database_id,
    )
    if not comparison.ontology_version_match:
        raise _fail(ReviewErrorCode.PUBLISH_BLOCKED)
    root, review_root = _review_root(repository_root)
    base = load_activation(repository_root=root, database_id=base_database_id)
    base_manifest = _load_manifest_strict(_validate_review_target(root, review_root, base_database_id))
    # The comparison and the copied mapping must describe the same active version.
    if (
        base_manifest.mapping_version != base.mapping_version
        or comparison.base_mapping_version != base.mapping_version
    ):
        raise _fail(ReviewErrorCode.REVIEW_CONFLICT)
    try:
        loaded = registration_storage.load_registration(
            repository_root=root, database_id=database_id
        )
    except Exception:
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH) from None
    if comparison.diff.candidate_fingerprint != loaded.manifest.schema_fingerprint:
        raise _fail(ReviewErrorCode.REVIEW_CONFLICT)

    carried = sorted(
        item.semantic_field for item in comparison.field_impacts
        if item.status is FieldImpactStatus.UNCHANGED
    )
    physical = {(t.name, c.name) for t in loaded.catalog.tables for c in t.columns}
    for name in carried:
        entry = base.profile.fields.get(name)
        if entry is None or (entry.table, entry.column) not in physical:
            raise _fail(ReviewErrorCode.REVIEW_CONFLICT)
    stale = [
        StaleField(semantic_field=item.semantic_field, status=item.status.value)
        for item in comparison.field_impacts
        if item.status is not FieldImpactStatus.UNCHANGED
    ]
    service = MappingReviewService()
    try:
        planned = service.create_draft(loaded.proposal)
        reason = _carried_reason(base_database_id, base.mapping_version)
        for name in carried:
            entry = base.profile.fields[name]
            planned = service.remap(
                planned, name, table=entry.table, column=entry.column,
                reviewer=reviewer, reason=reason,
            )
    except Exception:
        raise _fail(ReviewErrorCode.REVIEW_CONFLICT) from None
    classified = set(carried) | {item.semantic_field for item in stale}
    stale = sorted(
        [*stale, *(StaleField(semantic_field=name, status="NOT_IN_BASE") for name in set(planned.fields) - classified)],
        key=lambda item: item.semantic_field,
    )
    if (
        planned.database_id != database_id
        or planned.schema_fingerprint != loaded.manifest.schema_fingerprint
        or planned.ontology_version != loaded.manifest.ontology_version
    ):
        raise _fail(ReviewErrorCode.STORAGE_ERROR)
    record = MappingSuccession(
        schema_version=SUCCESSION_SCHEMA_VERSION,
        database_id=database_id,
        base_database_id=base_database_id,
        base_mapping_version=base.mapping_version,
        base_mapping_sha256=base_manifest.mapping_sha256,
        base_schema_fingerprint=base.schema_fingerprint,
        schema_fingerprint=loaded.manifest.schema_fingerprint,
        carried_fields=carried,
        stale_fields=stale,
        initiated_by=reviewer,
        initiated_at=_utc_now_text(),
    )

    try:
        _ensure_review_root(review_root)
    except ReviewError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise _fail(ReviewErrorCode.PRIVATE_STORAGE_INVALID) from None
    review_dir = _validate_review_target(root, review_root, database_id)
    lock_path = _acquire_lock(review_root, database_id)
    try:
        if review_dir.exists() or review_dir.is_symlink():
            if _is_redirect(review_dir) or not review_dir.is_dir():
                raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
        else:
            try:
                review_dir.mkdir(parents=False, exist_ok=False)
            except FileExistsError:
                raise _fail(ReviewErrorCode.REVIEW_CONFLICT) from None
            except (OSError, RuntimeError):
                raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
            _fsync_directory(review_root)
        try:
            names = sorted(item.name for item in review_dir.iterdir())
        except (OSError, RuntimeError):
            raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
        if any(
            name not in (DRAFT_FILENAME, SUCCESSION_FILENAME) and not _is_staging_leftover(name)
            for name in names
        ):
            # Already published (or unknown files): carry-over is for the first review only.
            raise _fail(ReviewErrorCode.REVIEW_CONFLICT)
        existing = _load_draft_strict(review_dir) if DRAFT_FILENAME in names else None
        if SUCCESSION_FILENAME in names:
            previous = _load_succession_strict(review_dir)
            same_plan = (
                previous.database_id == record.database_id
                and previous.base_database_id == record.base_database_id
                and previous.base_mapping_version == record.base_mapping_version
                and previous.base_mapping_sha256 == record.base_mapping_sha256
                and previous.schema_fingerprint == record.schema_fingerprint
                and previous.carried_fields == record.carried_fields
                and previous.stale_fields == record.stale_fields
            )
            if not same_plan or existing is None:
                raise _fail(ReviewErrorCode.REVIEW_CONFLICT)
            # Same request again: return the current draft, including later edits.
            return existing
        if existing is not None and not _same_review_state(existing, planned):
            fresh = service.create_draft(loaded.proposal)
            if not _same_review_state(existing, fresh):
                raise _fail(ReviewErrorCode.REVIEW_CONFLICT)
            existing = None
        if existing is None:
            try:
                content = _json_bytes(planned.model_dump(mode="json"))
            except Exception:
                raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
            _atomic_replace(review_dir / DRAFT_FILENAME, content)
        try:
            record_bytes = _json_bytes(record.model_dump(mode="json"))
        except Exception:
            raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
        _link_new_file(review_dir / SUCCESSION_FILENAME, record_bytes)
        return _load_draft_strict(review_dir)
    finally:
        _release_lock(lock_path)


def load_succession(*, repository_root: str | Path, database_id: str) -> MappingSuccession | None:
    """Display-only record; a missing, damaged, or mismatched file yields None."""
    try:
        database_id = _validate_database_id(database_id)
        root, review_root = _review_root(repository_root)
        review_dir = _validate_review_target(root, review_root, database_id)
        if not (review_dir / SUCCESSION_FILENAME).exists():
            return None
        record = _load_succession_strict(review_dir)
        loaded = registration_storage.load_registration(repository_root=root, database_id=database_id)
        draft = _load_draft_strict(review_dir)
    except Exception:
        return None
    classified = set(record.carried_fields) | {item.semantic_field for item in record.stale_fields}
    if (
        record.database_id != database_id
        or record.schema_fingerprint != loaded.manifest.schema_fingerprint
        or classified != set(draft.fields)
    ):
        return None
    return record


VERIFIED_FILENAME = "verified-queries.json"
VERIFIED_SCHEMA_VERSION = "verified-queries-v1"


class VerifiedQueryEntry(BaseModel):
    """Admin-confirmed result hash of one fixed test query (Phase 8-05)."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    query_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")
    mapping_version: int = Field(ge=1)
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    row_count: int = Field(ge=0, le=200)
    verified_by: str = Field(min_length=1, max_length=MAX_REVIEWER_LENGTH)
    verified_at: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


class VerifiedQueries(BaseModel):
    """Baselines only: hashes and counts, never row values; grants nothing."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: str = Field(pattern=r"^verified-queries-v1$")
    database_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    entries: list[VerifiedQueryEntry] = Field(max_length=64)

    @model_validator(mode="after")
    def _one_per_query(self) -> "VerifiedQueries":
        ids = [item.query_id for item in self.entries]
        if ids != sorted(set(ids)):
            raise ValueError("verified entries must be sorted and unique by query")
        return self


def load_published_version(
    *, repository_root: str | Path, database_id: str, mapping_version: int
) -> LoadedActivation:
    """Strictly load a recorded published version without activating it (Phase 8-05).

    Only versions in the pointer's digest record whose bytes match, and which
    pass the re-activation checks, are returned. Nothing is written.
    """
    database_id = _validate_database_id(database_id)
    mapping_version = _validate_mapping_version(mapping_version)
    root, review_root = _review_root(repository_root)
    review_dir = _validate_review_target(root, review_root, database_id)
    if not review_dir.is_dir() or _is_redirect(review_dir):
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    try:
        loaded = registration_storage.load_registration(
            repository_root=root, database_id=database_id
        )
    except registration_storage.RegistrationError as exc:
        if exc.code in (
            registration_storage.RegistrationErrorCode.STORAGE_ERROR,
            registration_storage.RegistrationErrorCode.PRIVATE_STORAGE_INVALID,
        ):
            raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH) from None
    except Exception:
        raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
    try:
        ontology = load_default_ontology()
    except Exception:
        raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
    pointer = _load_published_context(review_dir, database_id, loaded)
    record = pointer.published_records().get(mapping_version)
    if record is None:
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    raw, profile = _read_version_profile(review_dir, mapping_version)
    if len(raw) != record.mapping_bytes or hashlib.sha256(raw).hexdigest() != record.mapping_sha256:
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    _check_activatable(
        profile, database_id=database_id, version=mapping_version, loaded=loaded, ontology=ontology
    )
    return LoadedActivation(
        database_id=database_id,
        label=loaded.manifest.label,
        database_path=root / ".schema-adaptive" / "registered-databases" / database_id / "database.sqlite3",
        catalog=loaded.catalog,
        profile=profile,
        schema_fingerprint=profile.schema_fingerprint,
        ontology_version=profile.ontology_version,
        mapping_version=profile.version,
    )


def load_verified_queries(*, repository_root: str | Path, database_id: str) -> VerifiedQueries | None:
    """Baselines for display and regression; missing or damaged yields None."""
    try:
        database_id = _validate_database_id(database_id)
        root, review_root = _review_root(repository_root)
        review_dir = _validate_review_target(root, review_root, database_id)
        if not (review_dir / VERIFIED_FILENAME).exists():
            return None
        raw = _read_review_file(review_dir / VERIFIED_FILENAME, review_dir, max_bytes=MAX_MANIFEST_BYTES)
        record = _strict_model(raw, VerifiedQueries)
    except Exception:
        return None
    return record if record.database_id == database_id else None


def record_verified_query(
    *, repository_root: str | Path, database_id: str, entry: VerifiedQueryEntry
) -> VerifiedQueries:
    """Replace this query's baseline; caller supplies a server-computed entry."""
    database_id = _validate_database_id(database_id)
    validate_reviewer(entry.verified_by)
    root, review_root = _review_root(repository_root)
    review_dir = _validate_review_target(root, review_root, database_id)
    if not review_dir.is_dir() or _is_redirect(review_dir):
        raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
    lock_path = _acquire_lock(review_root, database_id)
    try:
        if not (review_dir / ACTIVATION_FILENAME).exists():
            raise _fail(ReviewErrorCode.ARTIFACT_MISMATCH)
        existing = load_verified_queries(repository_root=root, database_id=database_id)
        # A damaged baseline is admin metadata only; an explicit verify replaces it.
        kept = [item for item in (existing.entries if existing else []) if item.query_id != entry.query_id]
        record = VerifiedQueries(
            schema_version=VERIFIED_SCHEMA_VERSION,
            database_id=database_id,
            entries=sorted([*kept, entry], key=lambda item: item.query_id),
        )
        try:
            content = _json_bytes(record.model_dump(mode="json"))
        except Exception:
            raise _fail(ReviewErrorCode.STORAGE_ERROR) from None
        _atomic_replace(review_dir / VERIFIED_FILENAME, content)
        return record
    finally:
        _release_lock(lock_path)


# Every fixed file name a review directory may hold besides mapping-vNNNN.json.
_REVIEW_FILENAMES = (
    DRAFT_FILENAME,
    CURRENT_FILENAME,
    ACTIVATION_FILENAME,
    SUCCESSION_FILENAME,
    VERIFIED_FILENAME,
)

# ``tempfile.mkstemp(prefix=<name>., suffix=.tmp)`` staging files that a killed
# process can leave behind. They are never read, so they must not disable an
# activation; any other unknown file still fails closed.
_STAGING_LEFTOVER = re.compile(
    r"^(?:draft|current|activation|succession|verified-queries)\.json\.[A-Za-z0-9_]{1,32}\.tmp$"
)


def _is_staging_leftover(name: str) -> bool:
    return _STAGING_LEFTOVER.fullmatch(name) is not None
