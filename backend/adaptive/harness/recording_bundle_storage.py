"""Atomic private storage for exact model recordings and declared provenance."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from .recorded import (
    ModelRecording,
    RecordedFailure,
    RecordedSuccess,
    RecordingLoadError,
    parse_model_recording_bytes,
)
from .recording_bundle_contracts import (
    BUNDLE_SCHEMA_VERSION,
    STORAGE_TOOL_VERSION,
    LoadedRecordingBundle,
    RecordingArtifactMetadata,
    RecordingBundleManifest,
    RecordingProvenance,
)


RECORDING_FILENAME = "recording.json"
MANIFEST_FILENAME = "manifest.json"


class RecordingBundleErrorCode(str, Enum):
    INVALID_PATH = "INVALID_PATH"
    PRIVATE_PATH_NOT_IGNORED = "PRIVATE_PATH_NOT_IGNORED"
    GIT_TRACKING_CHECK_FAILED = "GIT_TRACKING_CHECK_FAILED"
    GIT_TRACKED_TARGET = "GIT_TRACKED_TARGET"
    INVALID_CONTRACT = "INVALID_CONTRACT"
    INCOMPLETE_BUNDLE = "INCOMPLETE_BUNDLE"
    ARTIFACT_MISMATCH = "ARTIFACT_MISMATCH"
    STORAGE_ERROR = "STORAGE_ERROR"


class RecordingBundleError(ValueError):
    """Controlled bundle failure that never includes paths or recording payloads."""

    def __init__(self, code: RecordingBundleErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


def _json_data(value: BaseModel) -> Any:
    return value.model_dump(mode="json")


def _json_bytes(value: Any, *, compact: bool = False) -> bytes:
    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":") if compact else None,
        indent=None if compact else 2,
        allow_nan=False,
    )
    return (text + "\n").encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_redirect(path: Path) -> bool:
    if path.is_symlink():
        return True
    junction_check = getattr(path, "is_junction", None)
    if junction_check is not None and junction_check():
        return True
    if not path.exists():
        return False
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & 0x400)  # Windows FILE_ATTRIBUTE_REPARSE_POINT


def _read_private_file(
    path: Path,
    expected_parent: Path,
    error_code: RecordingBundleErrorCode,
    *,
    max_bytes: int | None = None,
) -> bytes:
    if _is_redirect(path) or not path.is_file():
        raise RecordingBundleError(error_code)
    if path.resolve(strict=True).parent != expected_parent.resolve(strict=True):
        raise RecordingBundleError(error_code)
    try:
        with path.open("rb") as stream:
            metadata = os.fstat(stream.fileno())
            if metadata.st_nlink != 1:
                raise RecordingBundleError(error_code)
            if max_bytes is not None:
                if (
                    not isinstance(max_bytes, int)
                    or isinstance(max_bytes, bool)
                    or max_bytes < 1
                    or metadata.st_size > max_bytes
                ):
                    raise RecordingBundleError(error_code)
                content = stream.read(max_bytes + 1)
                if len(content) > max_bytes:
                    raise RecordingBundleError(error_code)
                return content
            return stream.read()
    except RecordingBundleError:
        raise
    except OSError:
        raise RecordingBundleError(error_code) from None


def provenance_fingerprint(provenance: RecordingProvenance) -> str:
    return _sha256(_json_bytes(_json_data(provenance), compact=True))


def _recording_counts(recording: ModelRecording) -> tuple[int, int, int]:
    successes = sum(isinstance(item, RecordedSuccess) for item in recording.records)
    failures = sum(isinstance(item, RecordedFailure) for item in recording.records)
    return len(recording.records), successes, failures


def _bundle_material(
    recording: ModelRecording,
    recording_sha256: str,
    provenance_sha256: str,
) -> dict[str, Any]:
    record_count, success_count, failure_count = _recording_counts(recording)
    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "storage_tool_version": STORAGE_TOOL_VERSION,
        "recording_schema_version": recording.schema_version,
        "model_run_id": recording.model_run_id,
        "record_count": record_count,
        "success_count": success_count,
        "failure_count": failure_count,
        "recording_sha256": recording_sha256,
        "provenance_sha256": provenance_sha256,
    }


def _build_manifest(
    recording: ModelRecording,
    provenance: RecordingProvenance,
) -> tuple[RecordingBundleManifest, bytes]:
    recording_bytes = _json_bytes(_json_data(recording))
    recording_sha256 = _sha256(recording_bytes)
    provenance_sha256 = provenance_fingerprint(provenance)
    material = _bundle_material(recording, recording_sha256, provenance_sha256)
    generation_id = _sha256(_json_bytes(material, compact=True))
    manifest = RecordingBundleManifest(
        generation_id=generation_id,
        recording_schema_version=recording.schema_version,
        model_run_id=recording.model_run_id,
        record_count=material["record_count"],
        success_count=material["success_count"],
        failure_count=material["failure_count"],
        provenance=provenance,
        provenance_sha256=provenance_sha256,
        recording=RecordingArtifactMetadata(
            path=f"generations/{generation_id}/{RECORDING_FILENAME}",
            sha256=recording_sha256,
            bytes=len(recording_bytes),
        ),
    )
    return manifest, recording_bytes


def _validate_repository(repository_root: str | Path) -> Path:
    supplied = Path(repository_root)
    try:
        if _is_redirect(supplied):
            raise RecordingBundleError(RecordingBundleErrorCode.INVALID_PATH)
        root = supplied.resolve(strict=True)
    except OSError:
        raise RecordingBundleError(RecordingBundleErrorCode.INVALID_PATH) from None
    if not root.is_dir():
        raise RecordingBundleError(RecordingBundleErrorCode.INVALID_PATH)
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
        )
    except OSError:
        raise RecordingBundleError(
            RecordingBundleErrorCode.GIT_TRACKING_CHECK_FAILED
        ) from None
    if result.returncode != 0:
        raise RecordingBundleError(
            RecordingBundleErrorCode.GIT_TRACKING_CHECK_FAILED
        )
    try:
        discovered = Path(result.stdout.strip()).resolve(strict=True)
    except OSError:
        raise RecordingBundleError(
            RecordingBundleErrorCode.GIT_TRACKING_CHECK_FAILED
        ) from None
    if discovered != root:
        raise RecordingBundleError(RecordingBundleErrorCode.INVALID_PATH)
    return root


def _validate_target(output_dir: str | Path, root: Path) -> Path:
    private_root = root / ".schema-adaptive"
    lexical = Path(os.path.abspath(output_dir))
    resolved = Path(output_dir).resolve(strict=False)
    for candidate in (lexical, resolved):
        try:
            relative = candidate.relative_to(private_root)
        except ValueError:
            raise RecordingBundleError(RecordingBundleErrorCode.INVALID_PATH) from None
        if not relative.parts:
            raise RecordingBundleError(RecordingBundleErrorCode.INVALID_PATH)
    current = private_root
    relative = lexical.relative_to(private_root)
    for part in relative.parts:
        if current.exists() and _is_redirect(current):
            raise RecordingBundleError(RecordingBundleErrorCode.INVALID_PATH)
        current = current / part
    if current.exists() and _is_redirect(current):
        raise RecordingBundleError(RecordingBundleErrorCode.INVALID_PATH)
    relative_target = lexical.relative_to(root).as_posix()
    try:
        tracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--", relative_target],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        raise RecordingBundleError(
            RecordingBundleErrorCode.GIT_TRACKING_CHECK_FAILED
        ) from None
    if tracked.returncode != 0:
        raise RecordingBundleError(
            RecordingBundleErrorCode.GIT_TRACKING_CHECK_FAILED
        )
    if tracked.stdout:
        raise RecordingBundleError(RecordingBundleErrorCode.GIT_TRACKED_TARGET)
    try:
        ignored = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "-q", "--", relative_target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        raise RecordingBundleError(
            RecordingBundleErrorCode.GIT_TRACKING_CHECK_FAILED
        ) from None
    if ignored.returncode == 1:
        raise RecordingBundleError(
            RecordingBundleErrorCode.PRIVATE_PATH_NOT_IGNORED
        )
    if ignored.returncode != 0:
        raise RecordingBundleError(
            RecordingBundleErrorCode.GIT_TRACKING_CHECK_FAILED
        )
    return resolved


def _validate_root_layout(target: Path, *, manifest_required: bool) -> None:
    if not target.exists():
        if manifest_required:
            raise RecordingBundleError(RecordingBundleErrorCode.INCOMPLETE_BUNDLE)
        return
    if _is_redirect(target) or not target.is_dir():
        raise RecordingBundleError(RecordingBundleErrorCode.INVALID_PATH)
    allowed = {"generations", MANIFEST_FILENAME}
    if any(item.name not in allowed for item in target.iterdir()):
        raise RecordingBundleError(RecordingBundleErrorCode.INVALID_PATH)
    manifest = target / MANIFEST_FILENAME
    if manifest_required and not manifest.is_file():
        raise RecordingBundleError(RecordingBundleErrorCode.INCOMPLETE_BUNDLE)
    if manifest.exists() and _is_redirect(manifest):
        raise RecordingBundleError(RecordingBundleErrorCode.INVALID_PATH)
    generations = target / "generations"
    if generations.exists() and _is_redirect(generations):
        raise RecordingBundleError(RecordingBundleErrorCode.INVALID_PATH)


def _write_new_generation(
    generation_root: Path,
    generation: Path,
    recording_bytes: bytes,
) -> None:
    temporary = Path(tempfile.mkdtemp(prefix=".pending-", dir=generation_root))
    try:
        artifact = temporary / RECORDING_FILENAME
        with artifact.open("xb") as stream:
            stream.write(recording_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        _verify_generation(temporary, recording_bytes)
        _fsync_directory(temporary)
        os.replace(temporary, generation)
        _fsync_directory(generation_root)
    except RecordingBundleError:
        raise
    except OSError:
        raise RecordingBundleError(RecordingBundleErrorCode.STORAGE_ERROR) from None
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def _verify_generation(generation: Path, expected: bytes) -> None:
    if _is_redirect(generation) or not generation.is_dir():
        raise RecordingBundleError(RecordingBundleErrorCode.ARTIFACT_MISMATCH)
    if generation.resolve(strict=True).parent != generation.parent.resolve(strict=True):
        raise RecordingBundleError(RecordingBundleErrorCode.ARTIFACT_MISMATCH)
    items = list(generation.iterdir())
    if len(items) != 1 or items[0].name != RECORDING_FILENAME:
        raise RecordingBundleError(RecordingBundleErrorCode.ARTIFACT_MISMATCH)
    artifact = items[0]
    actual = _read_private_file(
        artifact,
        generation,
        RecordingBundleErrorCode.ARTIFACT_MISMATCH,
    )
    if actual != expected:
        raise RecordingBundleError(RecordingBundleErrorCode.ARTIFACT_MISMATCH)
    try:
        parse_model_recording_bytes(actual)
    except RecordingLoadError:
        raise RecordingBundleError(RecordingBundleErrorCode.ARTIFACT_MISMATCH) from None


def _publish_manifest(path: Path, content: bytes, *, overwrite: bool) -> None:
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".manifest-", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if overwrite:
                os.replace(temporary, path)
            else:
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    raise FileExistsError(
                        "recording bundle already exists; use overwrite"
                    ) from None
                temporary.unlink()
            _fsync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)
    except FileExistsError:
        raise
    except OSError:
        raise RecordingBundleError(RecordingBundleErrorCode.STORAGE_ERROR) from None


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


def _preflight_bundle_target(
    output_dir: str | Path,
    *,
    repository_root: str | Path,
    overwrite: bool,
) -> Path:
    root = _validate_repository(repository_root)
    target = _validate_target(output_dir, root)
    _validate_root_layout(target, manifest_required=False)
    if (target / MANIFEST_FILENAME).exists() and not overwrite:
        raise FileExistsError("recording bundle already exists; use overwrite")
    return target


def preflight_recording_bundle(
    output_dir: str | Path,
    *,
    repository_root: str | Path,
    overwrite: bool = False,
) -> None:
    """Check the same private target policy used by the writer, without writes."""
    try:
        _preflight_bundle_target(
            output_dir, repository_root=repository_root, overwrite=overwrite
        )
    except (FileExistsError, RecordingBundleError):
        raise
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, TypeError):
        raise RecordingBundleError(RecordingBundleErrorCode.INVALID_PATH) from None


def _write_recording_bundle(
    output_dir: str | Path,
    recording: ModelRecording,
    provenance: RecordingProvenance,
    *,
    repository_root: str | Path,
    overwrite: bool = False,
) -> Path:
    """Publish a verified immutable recording generation and manifest commit marker."""

    if not isinstance(recording, ModelRecording) or not isinstance(
        provenance, RecordingProvenance
    ):
        raise RecordingBundleError(RecordingBundleErrorCode.INVALID_CONTRACT)
    try:
        recording = ModelRecording.model_validate(recording.model_dump(mode="json"))
        provenance = RecordingProvenance.model_validate(
            provenance.model_dump(mode="json")
        )
    except Exception:
        raise RecordingBundleError(RecordingBundleErrorCode.INVALID_CONTRACT) from None
    try:
        target = _preflight_bundle_target(
            output_dir, repository_root=repository_root, overwrite=overwrite
        )
    except (FileExistsError, RecordingBundleError):
        raise
    except OSError:
        raise RecordingBundleError(RecordingBundleErrorCode.INVALID_PATH) from None
    manifest_path = target / MANIFEST_FILENAME

    manifest, recording_bytes = _build_manifest(recording, provenance)
    manifest_bytes = _json_bytes(_json_data(manifest))
    try:
        target.mkdir(parents=True, exist_ok=True)
        generation_root = target / "generations"
        if _is_redirect(generation_root):
            raise RecordingBundleError(RecordingBundleErrorCode.INVALID_PATH)
        generation_root.mkdir(parents=True, exist_ok=True)
        if _is_redirect(generation_root) or generation_root.resolve() != generation_root:
            raise RecordingBundleError(RecordingBundleErrorCode.INVALID_PATH)
        generation = generation_root / manifest.generation_id
        if generation.exists() or generation.is_symlink():
            _verify_generation(generation, recording_bytes)
        else:
            _write_new_generation(generation_root, generation, recording_bytes)
        _verify_generation(generation, recording_bytes)
        _publish_manifest(manifest_path, manifest_bytes, overwrite=overwrite)
    except (FileExistsError, RecordingBundleError):
        raise
    except OSError:
        raise RecordingBundleError(RecordingBundleErrorCode.STORAGE_ERROR) from None
    return manifest_path


def write_recording_bundle(
    output_dir: str | Path,
    recording: ModelRecording,
    provenance: RecordingProvenance,
    *,
    repository_root: str | Path,
    overwrite: bool = False,
) -> Path:
    try:
        return _write_recording_bundle(
            output_dir,
            recording,
            provenance,
            repository_root=repository_root,
            overwrite=overwrite,
        )
    except (FileExistsError, RecordingBundleError):
        raise
    except (OSError, RuntimeError, subprocess.SubprocessError):
        raise RecordingBundleError(RecordingBundleErrorCode.STORAGE_ERROR) from None


def _parse_manifest(raw: bytes) -> RecordingBundleManifest:
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
        return RecordingBundleManifest.model_validate(payload)
    except (UnicodeError, json.JSONDecodeError, ValidationError, ValueError, TypeError):
        raise RecordingBundleError(RecordingBundleErrorCode.INVALID_CONTRACT) from None


def _load_recording_bundle(
    manifest_path: str | Path,
    *,
    repository_root: str | Path,
) -> LoadedRecordingBundle:
    """Load one committed manifest and verify every content-bound field."""

    try:
        root = _validate_repository(repository_root)
        supplied = Path(manifest_path)
        if supplied.name != MANIFEST_FILENAME or _is_redirect(supplied):
            raise RecordingBundleError(RecordingBundleErrorCode.INVALID_PATH)
        target = _validate_target(supplied.parent, root)
        expected_manifest = target / MANIFEST_FILENAME
        if supplied.resolve(strict=False) != expected_manifest:
            raise RecordingBundleError(RecordingBundleErrorCode.INVALID_PATH)
        _validate_root_layout(target, manifest_required=True)
    except RecordingBundleError:
        raise
    except OSError:
        raise RecordingBundleError(RecordingBundleErrorCode.INVALID_PATH) from None
    try:
        manifest = _parse_manifest(
            _read_private_file(
                expected_manifest,
                target,
                RecordingBundleErrorCode.INCOMPLETE_BUNDLE,
            )
        )
    except OSError:
        raise RecordingBundleError(RecordingBundleErrorCode.INCOMPLETE_BUNDLE) from None

    expected_relative = (
        f"generations/{manifest.generation_id}/{RECORDING_FILENAME}"
    )
    if manifest.recording.path != expected_relative:
        raise RecordingBundleError(RecordingBundleErrorCode.ARTIFACT_MISMATCH)
    generation_root = target / "generations"
    if _is_redirect(generation_root) or not generation_root.is_dir():
        raise RecordingBundleError(RecordingBundleErrorCode.INCOMPLETE_BUNDLE)
    generation = generation_root / manifest.generation_id
    if _is_redirect(generation) or not generation.is_dir():
        raise RecordingBundleError(RecordingBundleErrorCode.INCOMPLETE_BUNDLE)
    if generation.resolve(strict=True).parent != generation_root.resolve(strict=True):
        raise RecordingBundleError(RecordingBundleErrorCode.ARTIFACT_MISMATCH)
    artifact = generation / RECORDING_FILENAME
    recording_bytes = _read_private_file(
        artifact,
        generation,
        RecordingBundleErrorCode.INCOMPLETE_BUNDLE,
    )
    if list(item.name for item in generation.iterdir()) != [RECORDING_FILENAME]:
        raise RecordingBundleError(RecordingBundleErrorCode.ARTIFACT_MISMATCH)
    if (
        len(recording_bytes) != manifest.recording.bytes
        or _sha256(recording_bytes) != manifest.recording.sha256
    ):
        raise RecordingBundleError(RecordingBundleErrorCode.ARTIFACT_MISMATCH)
    try:
        recording = parse_model_recording_bytes(recording_bytes)
    except RecordingLoadError:
        raise RecordingBundleError(RecordingBundleErrorCode.ARTIFACT_MISMATCH) from None
    record_count, success_count, failure_count = _recording_counts(recording)
    if (
        recording.schema_version != manifest.recording_schema_version
        or recording.model_run_id != manifest.model_run_id
        or record_count != manifest.record_count
        or success_count != manifest.success_count
        or failure_count != manifest.failure_count
    ):
        raise RecordingBundleError(RecordingBundleErrorCode.ARTIFACT_MISMATCH)
    provenance_sha256 = provenance_fingerprint(manifest.provenance)
    if provenance_sha256 != manifest.provenance_sha256:
        raise RecordingBundleError(RecordingBundleErrorCode.ARTIFACT_MISMATCH)
    material = _bundle_material(
        recording, manifest.recording.sha256, provenance_sha256
    )
    if _sha256(_json_bytes(material, compact=True)) != manifest.generation_id:
        raise RecordingBundleError(RecordingBundleErrorCode.ARTIFACT_MISMATCH)
    return LoadedRecordingBundle(manifest=manifest, recording=recording)


def load_recording_bundle(
    manifest_path: str | Path,
    *,
    repository_root: str | Path,
) -> LoadedRecordingBundle:
    try:
        return _load_recording_bundle(
            manifest_path,
            repository_root=repository_root,
        )
    except RecordingBundleError:
        raise
    except (OSError, RuntimeError, subprocess.SubprocessError):
        raise RecordingBundleError(RecordingBundleErrorCode.STORAGE_ERROR) from None


def preflight_private_snapshot_path(
    snapshot_path: str | Path,
    *,
    repository_root: str | Path,
) -> Path:
    """Validate a single private snapshot file path with the bundle policy.

    This is a minimal public wrapper over the existing repository/private
    path validator (`_validate_repository` + `_validate_target`). It adds
    no parallel weaker check: symlink/junction/reparse, Git tracked/
    ignored, and private-root containment all run through the same code
    as recording bundles. The `.json` suffix guard is file-shape only.
    """
    try:
        root = _validate_repository(repository_root)
        supplied = Path(snapshot_path)
        if supplied.suffix.lower() != ".json":
            raise RecordingBundleError(RecordingBundleErrorCode.INVALID_PATH)
        return _validate_target(supplied, root)
    except RecordingBundleError:
        raise
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, TypeError):
        raise RecordingBundleError(RecordingBundleErrorCode.INVALID_PATH) from None


def read_private_snapshot_bytes(
    snapshot_path: str | Path,
    *,
    repository_root: str | Path,
    max_bytes: int | None = None,
) -> bytes:
    """Read a single private snapshot file through the shared bundle guard.

    Path policy reuses `preflight_private_snapshot_path`; content reads
    reuse `_read_private_file`, so redirect/escape and hardlink (`st_nlink`)
    checks are identical to recording bundles. File paths stored inside
    the snapshot are never trusted: the caller supplies the path.
    """
    try:
        target = preflight_private_snapshot_path(
            snapshot_path, repository_root=repository_root
        )
        parent = target.parent
        return _read_private_file(
            target,
            parent,
            RecordingBundleErrorCode.INCOMPLETE_BUNDLE,
            max_bytes=max_bytes,
        )
    except RecordingBundleError:
        raise
    except (OSError, RuntimeError, subprocess.SubprocessError):
        raise RecordingBundleError(RecordingBundleErrorCode.STORAGE_ERROR) from None
