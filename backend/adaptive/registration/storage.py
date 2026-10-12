"""Private SQLite registration storage and strict loading.

The boundary copies one caller-supplied standalone SQLite file into the
repository private root (``.schema-adaptive/registered-databases``) as an
immutable registration directory holding exactly four files::

    database.sqlite3
    catalog.json
    proposal.json
    registration.json

``registration.json`` is the manifest and commit marker and is written
last inside staging; the staging directory is published with a single
rename. Registrations land in ``PENDING_REVIEW`` and never touch the
Phase 6-16 fixed synthetic registry. All failures surface as
:class:`RegistrationError` carrying only a fixed code string.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from ..catalog import DatabaseCatalog, SQLiteAdapter
from ..catalog.errors import SchemaInspectionError
from ..mapping import MappingProposal, RuleBasedSchemaAligner
from ..ontology import load_default_ontology
from .contracts import (
    ARTIFACT_FILENAMES,
    CATALOG_FILENAME,
    DATABASE_FILENAME,
    DATABASE_ID_PATTERN,
    MAX_LABEL_LENGTH,
    MAX_SOURCE_BYTES,
    PROPOSAL_FILENAME,
    REGISTRATION_FILENAME,
    REGISTRATION_SCHEMA_VERSION,
    REGISTRATION_STATUS,
    ALLOWED_FILES,
    WINDOWS_RESERVED_NAMES,
    DatabaseRegistration,
    RegistrationArtifact,
)
from .errors import RegistrationError, RegistrationErrorCode


PRIVATE_ROOT_NAME = "registered-databases"
SQLITE_HEADER = b"SQLite format 3\x00"
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
MAX_MANIFEST_BYTES = 256 * 1024
MAX_JSON_ARTIFACT_BYTES = 8 * 1024 * 1024

# Phase 6-16 fixed synthetic registry IDs. Kept as literals so importing
# this module never executes the synthetic prototype app factory (which
# builds temp databases at import time); drift is pinned by tests.
RESERVED_SYNTHETIC_DATABASE_IDS = frozenset(
    {"synth-clarification-prototype", "synth-clarification-legacy"}
)
# Internal UI selection IDs, distinct from registered databases. Loading old
# records must remain possible; only new registrations use this restriction.
RESERVED_SELECTION_DATABASE_IDS = frozenset({"legacy"})


@dataclass(frozen=True)
class LoadedRegistration:
    manifest: DatabaseRegistration
    catalog: DatabaseCatalog
    proposal: MappingProposal


def _fail(code: RegistrationErrorCode) -> RegistrationError:
    return RegistrationError(code)


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
    return bool(attributes & 0x400)  # Windows FILE_ATTRIBUTE_REPARSE_POINT


def _validate_repository(repository_root: str | Path) -> Path:
    try:
        supplied = Path(repository_root)
        if _is_redirect(supplied):
            raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID)
        root = supplied.resolve(strict=True)
    except RegistrationError:
        raise
    except (OSError, RuntimeError, ValueError, TypeError):
        raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID) from None
    if not root.is_dir():
        raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID)
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID) from None
    if result.returncode != 0:
        raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID)
    try:
        discovered = Path(result.stdout.strip()).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID) from None
    if discovered != root:
        raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID)
    return root


def _private_root(repository_root: str | Path) -> tuple[Path, Path]:
    root = _validate_repository(repository_root)
    return root, root / ".schema-adaptive" / PRIVATE_ROOT_NAME


def _validate_registration_target(root: Path, private_root: Path, database_id: str) -> Path:
    for anchor in (root / ".schema-adaptive", private_root):
        try:
            if _is_redirect(anchor):
                raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID)
        except RegistrationError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID) from None
    lexical = Path(os.path.abspath(private_root / database_id))
    resolved = (private_root / database_id).resolve(strict=False)
    for candidate in (lexical, resolved):
        try:
            relative = candidate.relative_to(private_root)
        except ValueError:
            raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID) from None
        if not relative.parts or len(relative.parts) != 1 or relative.parts[0] != database_id:
            raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID) from None
    current = root / ".schema-adaptive"
    for part in (PRIVATE_ROOT_NAME, database_id):
        current = current / part
        if current.exists() and _is_redirect(current):
            raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID) from None
    if lexical.exists() and _is_redirect(lexical):
        raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID) from None
    relative_target = lexical.relative_to(root).as_posix()
    try:
        tracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--", relative_target],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID) from None
    if tracked.returncode != 0:
        raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID) from None
    if tracked.stdout:
        raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID) from None
    try:
        ignored = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "-q", "--", relative_target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID) from None
    if ignored.returncode == 1:
        raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID) from None
    if ignored.returncode != 0:
        raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID) from None
    return resolved


def validate_database_id(value: Any) -> str:
    if not isinstance(value, str):
        raise _fail(RegistrationErrorCode.INVALID_INPUT)
    if DATABASE_ID_PATTERN.fullmatch(value) is None:
        raise _fail(RegistrationErrorCode.INVALID_INPUT)
    if value != value.strip(" .") or value.endswith((".", " ")):
        raise _fail(RegistrationErrorCode.INVALID_INPUT)
    if value.lower() in WINDOWS_RESERVED_NAMES:
        raise _fail(RegistrationErrorCode.INVALID_INPUT)
    if value in RESERVED_SYNTHETIC_DATABASE_IDS:
        raise _fail(RegistrationErrorCode.REGISTRATION_CONFLICT)
    return value


def validate_new_database_id(value: Any) -> str:
    database_id = validate_database_id(value)
    if database_id in RESERVED_SELECTION_DATABASE_IDS:
        raise _fail(RegistrationErrorCode.INVALID_INPUT)
    return database_id


def validate_label(value: Any) -> str:
    if not isinstance(value, str):
        raise _fail(RegistrationErrorCode.INVALID_INPUT)
    if not 1 <= len(value) <= MAX_LABEL_LENGTH:
        raise _fail(RegistrationErrorCode.INVALID_INPUT)
    if not value.strip():
        raise _fail(RegistrationErrorCode.INVALID_INPUT)
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise _fail(RegistrationErrorCode.INVALID_INPUT)
    return value


def _sidecar_paths(source: Path) -> list[Path]:
    name = source.name
    return [source.parent / f"{name}{suffix}" for suffix in SIDECAR_SUFFIXES]


def _lexists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _check_sidecars_absent(source: Path) -> None:
    for sidecar in _sidecar_paths(source):
        try:
            if _lexists(sidecar):
                raise _fail(RegistrationErrorCode.UNSAFE_SOURCE)
        except RegistrationError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise _fail(RegistrationErrorCode.UNSAFE_SOURCE) from None


def _copy_source_single_fd(source: Path, destination: Path) -> tuple[str, int]:
    try:
        if _is_redirect(source):
            raise _fail(RegistrationErrorCode.UNSAFE_SOURCE)
        path_metadata = source.lstat()
    except RegistrationError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise _fail(RegistrationErrorCode.UNSAFE_SOURCE) from None
    try:
        exists = source.exists()
    except (OSError, RuntimeError, ValueError):
        raise _fail(RegistrationErrorCode.STORAGE_ERROR) from None
    if not exists:
        raise _fail(RegistrationErrorCode.INVALID_INPUT)
    try:
        descriptor = os.open(source, os.O_RDONLY)
    except FileNotFoundError:
        raise _fail(RegistrationErrorCode.INVALID_INPUT) from None
    except (OSError, RuntimeError):
        raise _fail(RegistrationErrorCode.STORAGE_ERROR) from None
    try:
        try:
            metadata = os.fstat(descriptor)
        except (OSError, RuntimeError):
            raise _fail(RegistrationErrorCode.STORAGE_ERROR) from None
        if not stat.S_ISREG(metadata.st_mode):
            raise _fail(RegistrationErrorCode.UNSAFE_SOURCE)
        try:
            reopened_metadata = source.lstat()
        except (OSError, RuntimeError, ValueError):
            raise _fail(RegistrationErrorCode.UNSAFE_SOURCE) from None
        if _is_redirect(source) or (
            metadata.st_dev,
            metadata.st_ino,
        ) != (
            path_metadata.st_dev,
            path_metadata.st_ino,
        ) or (
            metadata.st_dev,
            metadata.st_ino,
        ) != (
            reopened_metadata.st_dev,
            reopened_metadata.st_ino,
        ):
            raise _fail(RegistrationErrorCode.UNSAFE_SOURCE)
        try:
            source_links = metadata.st_nlink
        except AttributeError:
            source_links = 1
        if source_links != 1:
            raise _fail(RegistrationErrorCode.UNSAFE_SOURCE)
        if metadata.st_size <= 0:
            raise _fail(RegistrationErrorCode.INVALID_SQLITE)
        if metadata.st_size > MAX_SOURCE_BYTES:
            raise _fail(RegistrationErrorCode.SOURCE_TOO_LARGE)
        header = os.read(descriptor, len(SQLITE_HEADER))
        if header != SQLITE_HEADER:
            raise _fail(RegistrationErrorCode.INVALID_SQLITE)
        digest = hashlib.sha256(header)
        total = len(header)
        try:
            writer = open(destination, "xb")  # noqa: PTH123
        except FileExistsError:
            raise _fail(RegistrationErrorCode.STORAGE_ERROR) from None
        except (OSError, RuntimeError):
            raise _fail(RegistrationErrorCode.STORAGE_ERROR) from None
        with writer:
            writer.write(header)
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_SOURCE_BYTES:
                    raise _fail(RegistrationErrorCode.SOURCE_TOO_LARGE)
                digest.update(chunk)
                writer.write(chunk)
            writer.flush()
            try:
                os.fsync(writer.fileno())
            except (OSError, RuntimeError):
                pass
        if total <= 0 or total > MAX_SOURCE_BYTES:
            raise _fail(
                RegistrationErrorCode.SOURCE_TOO_LARGE
                if total > MAX_SOURCE_BYTES
                else RegistrationErrorCode.INVALID_SQLITE
            )
        return digest.hexdigest(), total
    except RegistrationError:
        try:
            destination.unlink(missing_ok=True)
        except (OSError, RuntimeError):
            pass
        raise
    except (OSError, RuntimeError):
        try:
            destination.unlink(missing_ok=True)
        except (OSError, RuntimeError):
            pass
        raise _fail(RegistrationErrorCode.STORAGE_ERROR) from None
    finally:
        try:
            os.close(descriptor)
        except (OSError, RuntimeError):
            pass


def _verify_copied_database(database_path: Path) -> None:
    for sidecar in _sidecar_paths(database_path):
        try:
            if _lexists(sidecar):
                raise _fail(RegistrationErrorCode.UNSAFE_SOURCE)
        except RegistrationError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise _fail(RegistrationErrorCode.UNSAFE_SOURCE) from None
    connection: sqlite3.Connection | None = None
    try:
        try:
            connection = sqlite3.connect(
                database_path.as_uri() + "?mode=ro&immutable=1",
                uri=True,
            )
            connection.execute("PRAGMA query_only = ON")
            quick_check = connection.execute("PRAGMA quick_check").fetchall()
            journal_mode = connection.execute("PRAGMA journal_mode").fetchall()
        except (sqlite3.Error, OSError, RuntimeError, ValueError):
            raise _fail(RegistrationErrorCode.INVALID_SQLITE) from None
        if (
            len(quick_check) != 1
            or len(quick_check[0]) != 1
            or str(quick_check[0][0]).lower() != "ok"
        ):
            raise _fail(RegistrationErrorCode.INVALID_SQLITE)
        if journal_mode:
            mode = str(journal_mode[0][0]).lower()
            if mode == "wal":
                raise _fail(RegistrationErrorCode.UNSAFE_SOURCE)
    finally:
        if connection is not None:
            try:
                connection.close()
            except (sqlite3.Error, OSError, RuntimeError):
                pass
    for sidecar in _sidecar_paths(database_path):
        try:
            if _lexists(sidecar):
                raise _fail(RegistrationErrorCode.UNSAFE_SOURCE)
        except RegistrationError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise _fail(RegistrationErrorCode.UNSAFE_SOURCE) from None


def _json_bytes(payload: Any) -> bytes:
    try:
        text = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise _fail(RegistrationErrorCode.STORAGE_ERROR) from None
    return (text + "\n").encode("utf-8")


def _parse_json(raw: bytes, code: RegistrationErrorCode) -> Any:
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
        raise _fail(code) from None


def _read_private_file(path: Path, expected_parent: Path, *, max_bytes: int) -> bytes:
    try:
        if _is_redirect(path) or not path.is_file():
            raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH)
        if path.resolve(strict=True).parent != expected_parent.resolve(strict=True):
            raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH)
    except RegistrationError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH) from None
    try:
        with path.open("rb") as stream:
            try:
                metadata = os.fstat(stream.fileno())
            except (OSError, RuntimeError):
                raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH) from None
            try:
                link_count = metadata.st_nlink
            except AttributeError:
                link_count = 1
            if link_count != 1:
                raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH)
            if metadata.st_size < 0 or metadata.st_size > max_bytes:
                raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH)
            content = stream.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH)
            return content
    except RegistrationError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH) from None


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
    except (OSError, RuntimeError):
        raise _fail(RegistrationErrorCode.STORAGE_ERROR) from None


def _strict_model_payload(raw: bytes, model_type: type[Any]) -> Any:
    payload = _parse_json(raw, RegistrationErrorCode.ARTIFACT_MISMATCH)
    try:
        model = model_type.model_validate(payload)
    except ValidationError:
        raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH) from None
    if payload != model.model_dump(mode="json"):
        raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH)
    return model


def _ensure_private_root(private_root: Path) -> None:
    private_parent = private_root.parent
    if _is_redirect(private_parent) or (
        private_parent.exists() and not private_parent.is_dir()
    ):
        raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID)
    try:
        private_parent.mkdir(exist_ok=True)
    except (OSError, RuntimeError):
        raise _fail(RegistrationErrorCode.STORAGE_ERROR) from None
    if _is_redirect(private_parent) or not private_parent.is_dir():
        raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID)
    if private_root.exists():
        if _is_redirect(private_root) or not private_root.is_dir():
            raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID)
        return
    try:
        private_root.mkdir(exist_ok=True)
    except (OSError, RuntimeError):
        raise _fail(RegistrationErrorCode.STORAGE_ERROR) from None
    if _is_redirect(private_root) or not private_root.is_dir():
        raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID)


# (catalog with in-memory samples, ontology, rule proposal) -> proposal or None
MappingProposer = Callable[[DatabaseCatalog, Any, MappingProposal], "MappingProposal | None"]
PROPOSER_SAMPLE_LIMIT = 10


def _proposal_is_consistent(
    candidate: Any, rules: MappingProposal, catalog: DatabaseCatalog
) -> bool:
    if not isinstance(candidate, MappingProposal):
        return False
    if (
        candidate.database_id != rules.database_id
        or candidate.schema_fingerprint != rules.schema_fingerprint
        or candidate.ontology_version != rules.ontology_version
        or set(candidate.candidates) != set(rules.candidates)
        or not set(candidate.unmapped_fields) <= set(rules.candidates)
    ):
        return False
    physical = {(table.name, column.name) for table in catalog.tables for column in table.columns}
    return all(
        (option.table, option.column) in physical
        for options in candidate.candidates.values()
        for option in options
    )


def _proposal_with_model(
    proposer: MappingProposer,
    database_id: str,
    staged_db: Path,
    catalog: DatabaseCatalog,
    ontology: Any,
    rules: MappingProposal,
) -> MappingProposal:
    """Model proposal when it is usable, else the rule proposal.

    Samples are read into memory for the prompt only; the stored catalog and
    proposal stay sample-free.
    """
    try:
        sampled = SQLiteAdapter(
            {database_id: staged_db}, immutable=True, sample_limit=PROPOSER_SAMPLE_LIMIT
        ).inspect(database_id, include_samples=True)
        if sampled.schema_fingerprint != catalog.schema_fingerprint:
            return rules
        candidate = proposer(sampled, ontology, rules)
    except Exception:
        return rules
    if candidate is None or not _proposal_is_consistent(candidate, rules, catalog):
        return rules
    try:
        # Round-trip so nothing but the contract's own fields is kept.
        return MappingProposal.model_validate(candidate.model_dump(mode="json"))
    except Exception:
        return rules


def register_database(
    *,
    repository_root: str | Path,
    source_path: str | Path,
    database_id: str,
    label: str,
    mapping_proposer: MappingProposer | None = None,
) -> Path:
    """Validate, copy, and publish one standalone SQLite file.

    Returns the path of the published ``registration.json`` commit marker.
    The new registration is ``PENDING_REVIEW`` and is not selectable from
    the Phase 6-16 fixed registry. With ``mapping_proposer`` (Phase 8-12) a
    local model may reorder the review candidates; any failure keeps the
    rule proposal.
    """
    database_id = validate_new_database_id(database_id)
    label = validate_label(label)
    if not isinstance(source_path, (str, Path)):
        raise _fail(RegistrationErrorCode.INVALID_INPUT)
    source = Path(source_path)
    root, private_root = _private_root(repository_root)
    final_dir = _validate_registration_target(root, private_root, database_id)
    try:
        _ensure_private_root(private_root)
    except RegistrationError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise _fail(RegistrationErrorCode.PRIVATE_STORAGE_INVALID) from None
    final_dir = _validate_registration_target(root, private_root, database_id)
    if final_dir.exists() or final_dir.is_symlink():
        raise _fail(RegistrationErrorCode.REGISTRATION_CONFLICT)
    _check_sidecars_absent(source)

    lock_path = private_root / f".lock-{database_id}.lock"
    try:
        if _is_redirect(lock_path):
            raise _fail(RegistrationErrorCode.REGISTRATION_CONFLICT)
    except RegistrationError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise _fail(RegistrationErrorCode.STORAGE_ERROR) from None
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise _fail(RegistrationErrorCode.REGISTRATION_CONFLICT) from None
    except (OSError, RuntimeError):
        raise _fail(RegistrationErrorCode.STORAGE_ERROR) from None
    try:
        os.close(lock_fd)
    except (OSError, RuntimeError):
        pass

    staging: Path | None = None
    try:
        if final_dir.exists() or final_dir.is_symlink():
            raise _fail(RegistrationErrorCode.REGISTRATION_CONFLICT)
        try:
            staging_name = tempfile.mkdtemp(
                prefix=f".staging-{database_id}-", dir=private_root
            )
        except (OSError, RuntimeError):
            raise _fail(RegistrationErrorCode.STORAGE_ERROR) from None
        staging = Path(staging_name)
        staged_db = staging / DATABASE_FILENAME
        content_sha256, content_bytes = _copy_source_single_fd(source, staged_db)
        _check_sidecars_absent(source)
        _verify_copied_database(staged_db)

        try:
            adapter = SQLiteAdapter({database_id: staged_db}, immutable=True)
            catalog = adapter.inspect(database_id, include_samples=False)
        except SchemaInspectionError:
            raise _fail(RegistrationErrorCode.INSPECTION_FAILED) from None
        except (OSError, RuntimeError, ValueError):
            raise _fail(RegistrationErrorCode.INSPECTION_FAILED) from None
        try:
            ontology = load_default_ontology()
        except (OSError, RuntimeError, ValueError):
            raise _fail(RegistrationErrorCode.INSPECTION_FAILED) from None
        try:
            proposal = RuleBasedSchemaAligner().align(catalog, ontology)
        except (OSError, RuntimeError, ValueError):
            raise _fail(RegistrationErrorCode.MAPPING_FAILED) from None
        if mapping_proposer is not None:
            proposal = _proposal_with_model(
                mapping_proposer, database_id, staged_db, catalog, ontology, proposal
            )
        for table in catalog.tables:
            for column in table.columns:
                if column.sample_values:
                    raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH)

        try:
            catalog_payload = catalog.model_dump(mode="json")
            proposal_payload = proposal.model_dump(mode="json")
            catalog_bytes = _json_bytes(catalog_payload)
            proposal_bytes = _json_bytes(proposal_payload)
        except (ValidationError, TypeError, ValueError):
            raise _fail(RegistrationErrorCode.STORAGE_ERROR) from None
        _write_new_file(staging / CATALOG_FILENAME, catalog_bytes)
        _write_new_file(staging / PROPOSAL_FILENAME, proposal_bytes)
        artifacts = [
            RegistrationArtifact(
                path=DATABASE_FILENAME,
                sha256=content_sha256,
                bytes=content_bytes,
            ),
            RegistrationArtifact(
                path=CATALOG_FILENAME,
                sha256=hashlib.sha256(catalog_bytes).hexdigest(),
                bytes=len(catalog_bytes),
            ),
            RegistrationArtifact(
                path=PROPOSAL_FILENAME,
                sha256=hashlib.sha256(proposal_bytes).hexdigest(),
                bytes=len(proposal_bytes),
            ),
        ]
        try:
            manifest = DatabaseRegistration(
                schema_version=REGISTRATION_SCHEMA_VERSION,
                status=REGISTRATION_STATUS,
                database_id=database_id,
                label=label,
                content_sha256=content_sha256,
                content_bytes=content_bytes,
                schema_fingerprint=catalog.schema_fingerprint,
                ontology_version=ontology.version,
                artifacts=artifacts,
            )
            manifest_bytes = _json_bytes(manifest.model_dump(mode="json"))
        except (ValidationError, TypeError, ValueError):
            raise _fail(RegistrationErrorCode.STORAGE_ERROR) from None
        _write_new_file(staging / REGISTRATION_FILENAME, manifest_bytes)
        _fsync_directory(staging)

        if final_dir.exists() or final_dir.is_symlink():
            raise _fail(RegistrationErrorCode.REGISTRATION_CONFLICT)
        try:
            os.rename(staging, final_dir)
        except FileExistsError:
            raise _fail(RegistrationErrorCode.REGISTRATION_CONFLICT) from None
        except (OSError, RuntimeError):
            raise _fail(RegistrationErrorCode.STORAGE_ERROR) from None
        _fsync_directory(private_root)
        staging = None
        return final_dir / REGISTRATION_FILENAME
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        try:
            lock_path.unlink(missing_ok=True)
        except (OSError, RuntimeError):
            pass


def load_registration(
    *,
    repository_root: str | Path,
    database_id: str,
) -> LoadedRegistration:
    """Strict-load one published private SQLite registration."""
    database_id = validate_database_id(database_id)
    root, private_root = _private_root(repository_root)
    final_dir = _validate_registration_target(root, private_root, database_id)
    try:
        if _is_redirect(final_dir) or not final_dir.is_dir():
            raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH)
        if final_dir.resolve(strict=True).parent != private_root.resolve(strict=True):
            raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH)
        names = sorted(item.name for item in final_dir.iterdir())
    except RegistrationError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH) from None
    if set(names) != set(ALLOWED_FILES):
        raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH)

    manifest_raw = _read_private_file(
        final_dir / REGISTRATION_FILENAME,
        final_dir,
        max_bytes=MAX_MANIFEST_BYTES,
    )
    database_raw = _read_private_file(
        final_dir / DATABASE_FILENAME,
        final_dir,
        max_bytes=MAX_SOURCE_BYTES,
    )
    catalog_raw = _read_private_file(
        final_dir / CATALOG_FILENAME,
        final_dir,
        max_bytes=MAX_JSON_ARTIFACT_BYTES,
    )
    proposal_raw = _read_private_file(
        final_dir / PROPOSAL_FILENAME,
        final_dir,
        max_bytes=MAX_JSON_ARTIFACT_BYTES,
    )
    manifest = _strict_model_payload(manifest_raw, DatabaseRegistration)
    catalog = _strict_model_payload(catalog_raw, DatabaseCatalog)
    proposal = _strict_model_payload(proposal_raw, MappingProposal)
    if (
        manifest.database_id != database_id
        or catalog.database_id != database_id
        or proposal.database_id != database_id
    ):
        raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH)
    if manifest.schema_version != REGISTRATION_SCHEMA_VERSION:
        raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH)
    if manifest.status != REGISTRATION_STATUS:
        raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH)
    try:
        validate_label(manifest.label)
    except RegistrationError:
        raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH) from None
    expected = {artifact.path: artifact for artifact in manifest.artifacts}
    if set(expected) != set(ARTIFACT_FILENAMES):
        raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH)
    actual = {
        DATABASE_FILENAME: database_raw,
        CATALOG_FILENAME: catalog_raw,
        PROPOSAL_FILENAME: proposal_raw,
    }
    for filename, raw in actual.items():
        artifact = expected[filename]
        if len(raw) != artifact.bytes or hashlib.sha256(raw).hexdigest() != artifact.sha256:
            raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH)
    if (
        len(database_raw) != manifest.content_bytes
        or hashlib.sha256(database_raw).hexdigest() != manifest.content_sha256
    ):
        raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH)
    if catalog.schema_fingerprint != manifest.schema_fingerprint:
        raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH)
    if (
        proposal.schema_fingerprint != manifest.schema_fingerprint
        or proposal.ontology_version != manifest.ontology_version
    ):
        raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH)
    for table in catalog.tables:
        for column in table.columns:
            if column.sample_values:
                raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH)

    stored_copy: Path | None = None
    try:
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".verify-{database_id}-", dir=private_root
            )
        except (OSError, RuntimeError):
            raise _fail(RegistrationErrorCode.STORAGE_ERROR) from None
        stored_copy = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(database_raw)
                stream.flush()
                os.fsync(stream.fileno())
        except (OSError, RuntimeError):
            raise _fail(RegistrationErrorCode.STORAGE_ERROR) from None
        try:
            _verify_copied_database(stored_copy)
            reinspected = SQLiteAdapter(
                {database_id: stored_copy}, immutable=True
            ).inspect(database_id, include_samples=False)
        except SchemaInspectionError:
            raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH) from None
        except RegistrationError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH) from None
        if reinspected.schema_fingerprint != manifest.schema_fingerprint:
            raise _fail(RegistrationErrorCode.ARTIFACT_MISMATCH)
    finally:
        if stored_copy is not None:
            try:
                stored_copy.unlink(missing_ok=True)
            except (OSError, RuntimeError):
                pass
    return LoadedRegistration(manifest=manifest, catalog=catalog, proposal=proposal)
