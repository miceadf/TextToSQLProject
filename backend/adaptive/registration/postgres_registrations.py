"""Register PostgreSQL schemas from server-owned connections (Phase 8-15).

Connections come from the server's own settings (``DATABASE_URL`` and
``PG_SOURCE_<NAME>`` in ``backend/.env``); the browser only ever sees a
connection ID and a label. A registration inspects one schema read-only,
proposes a mapping (rules, optionally the Phase 8-12 local model), and is
stored immutably under ``.schema-adaptive/postgres-registrations/<id>``.

Review reuses the draft model of the SQLite flow (INIT, APPROVE, REMAP,
IGNORE). Publishing decides the per-field transforms and the row anchor with
fixed rules, validates the result with the unchanged runtime checks over the
real connection, and writes the same private profile pair the startup loader
reads (``.schema-adaptive/postgres-profiles/<id>``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..catalog import DatabaseCatalog
from ..catalog.postgres import PostgresConnectionConfig
from ..mapping import (
    MappingDraft,
    MappingProposal,
    MappingReviewService,
    PublishedFieldMapping,
    PublishedMappingProfile,
    ReviewDecision,
    RuleBasedSchemaAligner,
)
from ..mapping.extensions import ExtensionField
from ..mapping.transforms import FieldTransform, column_type_supports, transform_supports_data_type
from ..ontology import DataType, load_default_ontology
from . import storage as sqlite_storage

REGISTRATION_VERSION = "private-postgres-registration-v1"
PUBLISHED_VERSION = "private-postgres-published-v1"
# Phase 8-19: v5 adds the database's extension fields.
PROFILE_VERSION = "postgres-runtime-profile-v5"
REGISTRATION_ROOT = ("postgres-registrations",)
PROFILE_ROOT = ("postgres-profiles",)
SOURCE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
DSN_ENV_PREFIX = "SCHEMA_ADAPTIVE_POSTGRES_DSN_REG_"
MAX_SAMPLED_COLUMNS = 300
SAMPLE_LIMIT = 10
TRANSFORM_SAMPLE_LIMIT = 50
MAX_JSON_BYTES = 8 * 1024 * 1024
WEEKDAYS = frozenset("월화수목금토일")
# Numbers may omit the leading zero ("1/.5/1" in the team's legacy data).
_SLASH_VALUE = re.compile(r"^(?:\d+(?:\.\d+)?|\.\d+)(?:/(?:\d+(?:\.\d+)?|\.\d+))+$")
_HHMM_VALUE = re.compile(r"^(?:[01]\d|2[0-3])[0-5]\d$")
# Phase 9-05: a time field left as raw text is compared as text, so it must
# already hold zero-padded "HH:MM" ("09:00-10:15" would silently misorder).
# Phase 9-06: column types a number field may be compared on as is.
_NUMERIC_TYPE = re.compile(
    r"^(?:smallint|integer|int|int2|int4|int8|bigint|numeric|decimal|real|double precision|float4|float8)"
    r"(?:\s*\([0-9,\s]*\))?$"
)
# Phase 9-30: text fields a numeric column may still hold (codes and sections).
_NUMERIC_IDENTITY_FIELDS = frozenset({"Course.code", "CourseOffering.section", "Department.code"})
_CLOCK_TEXT = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")


class PostgresRegistrationError(Exception):
    """Fixed-code failure; the code is the only public detail."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> PostgresRegistrationError:
    return PostgresRegistrationError(code)


# --- connections ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PostgresSource:
    source_id: str
    label: str
    _dsn: str

    def config(self, schema: str) -> PostgresConnectionConfig:
        return PostgresConnectionConfig(self._dsn, schema=schema)

    def identity(self) -> str:
        """SHA-256 of host, port, database and user (no password): which database this is."""
        return connection_identity(self._dsn)

    def __repr__(self) -> str:
        return f"PostgresSource(source_id='{self.source_id}')"

    __str__ = __repr__


def connection_identity(dsn: str) -> str:
    """Host, port, database and user of a DSN (URL or key=value form), hashed."""
    from urllib.parse import unquote, urlsplit

    text = dsn.strip()
    if "://" in text:
        split = urlsplit(text)
        parts = {
            "host": split.hostname or "",
            "port": str(split.port or ""),
            "dbname": unquote(split.path.lstrip("/")),
            "user": unquote(split.username or ""),
        }
    else:
        pairs = dict(token.partition("=")[::2] for token in text.split() if "=" in token)
        parts = {
            "host": pairs.get("host") or pairs.get("hostaddr") or "",
            "port": pairs.get("port", ""),
            "dbname": pairs.get("dbname", ""),
            "user": pairs.get("user", ""),
        }
    parts = {key: value.strip("'\"") for key, value in parts.items()}
    parts["host"] = parts["host"].lower()
    canonical = "|".join(f"{key}={parts[key]}" for key in ("host", "port", "dbname", "user"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sources_from_env(env: dict[str, str]) -> dict[str, PostgresSource]:
    """``DATABASE_URL`` → ``default``; ``PG_SOURCE_<NAME>`` → ``<name>``."""
    sources: dict[str, PostgresSource] = {}
    default = (env.get("DATABASE_URL") or "").strip()
    if default:
        sources["default"] = PostgresSource("default", "기본 PostgreSQL 서버 (DATABASE_URL)", default)
    for key in sorted(env):
        if not key.startswith("PG_SOURCE_"):
            continue
        source_id = key[len("PG_SOURCE_"):].lower().replace("_", "-")
        dsn = (env.get(key) or "").strip()
        if dsn and SOURCE_ID_PATTERN.fullmatch(source_id) and source_id not in sources:
            sources[source_id] = PostgresSource(source_id, source_id, dsn)
    return sources


def _default_connector(config: PostgresConnectionConfig) -> Any:
    from ..harness.postgres import _production_connector

    return _production_connector(config)


def list_schemas(
    source: PostgresSource, *, connector: Callable[[PostgresConnectionConfig], Any] | None = None
) -> list[str]:
    """User schemas of the connection (read-only); raises a fixed code on failure."""
    connection = None
    try:
        connection = (connector or _default_connector)(source.config("public"))
        cursor = connection.cursor()
        cursor.execute(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast') "
            "AND schema_name NOT LIKE 'pg_temp_%%' AND schema_name NOT LIKE 'pg_toast_temp_%%' "
            "ORDER BY schema_name LIMIT 200",
            (),
        )
        rows = cursor.fetchall()
    except Exception:
        raise _fail("SOURCE_UNAVAILABLE") from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
    return [
        row[0]
        for row in rows
        if isinstance(row, (tuple, list)) and isinstance(row[0], str) and SCHEMA_PATTERN.fullmatch(row[0])
    ]


def dsn_env_for(database_id: str) -> str:
    return DSN_ENV_PREFIX + database_id.upper().replace("-", "_")


# --- stored documents --------------------------------------------------------------------------


class PostgresRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["private-postgres-registration-v1"] = REGISTRATION_VERSION
    status: Literal["PENDING_REVIEW"] = "PENDING_REVIEW"
    database_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=32)
    # Phase 8-16: which database the source pointed to when this was registered.
    source_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    postgres_schema: str = Field(min_length=1, max_length=63)
    schema_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    ontology_version: int = Field(ge=1)
    catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PublishedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["private-postgres-published-v1"] = PUBLISHED_VERSION
    database_id: str
    mapping_version: int = Field(ge=1)
    published_by: str
    published_at: str
    mapping_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class LoadedPostgresRegistration:
    manifest: PostgresRegistration
    catalog: DatabaseCatalog
    proposal: MappingProposal


# --- private storage ------------------------------------------------------------------------


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode(
        "utf-8"
    )


def _strict_json(raw: bytes) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    return json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)


def _read(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise _fail("ARTIFACT_MISMATCH")
    raw = path.read_bytes()
    if len(raw) > MAX_JSON_BYTES:
        raise _fail("ARTIFACT_MISMATCH")
    return raw


def _strict_model(raw: bytes, model: type[Any]) -> Any:
    try:
        payload = _strict_json(raw)
        loaded = model.model_validate(payload)
    except Exception:
        raise _fail("ARTIFACT_MISMATCH") from None
    if payload != loaded.model_dump(mode="json"):
        raise _fail("ARTIFACT_MISMATCH")
    return loaded


def _private_dir(repository_root: str | Path, parts: tuple[str, ...], database_id: str) -> tuple[Path, Path]:
    """(repository root, target directory); the target must be Git-ignored and untracked."""
    try:
        root = sqlite_storage._validate_repository(repository_root)  # noqa: SLF001
    except Exception:
        raise _fail("PRIVATE_STORAGE_INVALID") from None
    target = root.joinpath(".schema-adaptive", *parts, database_id)
    relative = target.relative_to(root).as_posix()
    for anchor in (root / ".schema-adaptive", root.joinpath(".schema-adaptive", *parts), target):
        if anchor.is_symlink():
            raise _fail("PRIVATE_STORAGE_INVALID")
    try:
        tracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--", relative],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
        )
        ignored = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "-q", "--", relative],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
    except Exception:
        raise _fail("PRIVATE_STORAGE_INVALID") from None
    if tracked.returncode != 0 or tracked.stdout or ignored.returncode != 0:
        raise _fail("PRIVATE_STORAGE_INVALID")
    return root, target


def _write_atomic(path: Path, content: bytes) -> None:
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    except Exception:
        try:
            Path(name).unlink(missing_ok=True)
        except Exception:
            pass
        raise _fail("STORAGE_ERROR") from None


def _write_pair(directory: Path, contents: dict[str, bytes]) -> None:
    """Replace several files as one unit: on any failure the previous files return."""
    previous = {name: (directory / name).read_bytes() if (directory / name).is_file() else None for name in contents}
    written: list[str] = []
    try:
        for name, content in contents.items():
            _write_atomic(directory / name, content)
            written.append(name)
    except Exception:
        for name in written:
            try:
                if previous[name] is None:
                    (directory / name).unlink(missing_ok=True)
                else:
                    _write_atomic(directory / name, previous[name])  # type: ignore[arg-type]
            except Exception:
                pass
        raise _fail("STORAGE_ERROR") from None


def registration_exists(repository_root: str | Path, database_id: str) -> bool:
    try:
        _root, target = _private_dir(repository_root, REGISTRATION_ROOT, database_id)
    except PostgresRegistrationError:
        return False
    return (target / "registration.json").is_file()


def _is_junction(path: Path) -> bool:
    check = getattr(path, "is_junction", None)
    if callable(check):
        return check()
    if os.name == "nt":
        check = getattr(os.path, "isjunction", None)
        if callable(check):
            return check(path)
        try:
            attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        except OSError:
            return False
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return False


def delete_registration(repository_root: str | Path, database_id: Any) -> None:
    """Delete only a registered PostgreSQL DB; leave evaluation artifacts alone."""
    if isinstance(database_id, str) and database_id in sqlite_storage.RESERVED_SYNTHETIC_DATABASE_IDS:
        raise _fail("NOT_FOUND")
    try:
        database_id = sqlite_storage.validate_database_id(database_id)
    except Exception:
        raise _fail("INVALID_INPUT") from None
    registration = _private_dir(repository_root, REGISTRATION_ROOT, database_id)[1]
    if not registration.is_dir() or not (registration / "registration.json").is_file():
        raise _fail("NOT_FOUND")
    profile = _private_dir(repository_root, PROFILE_ROOT, database_id)[1]

    def check_tree(path: Path) -> None:
        # Reject Windows junctions as well as symlinks, including nested artifacts.
        if path.is_symlink() or _is_junction(path):
            raise _fail("PRIVATE_STORAGE_INVALID")
        if path.is_dir():
            for child in path.iterdir():
                check_tree(child)

    try:
        # Validate both complete trees before removing either one.
        for path in (registration, profile):
            for anchor in (path.parent.parent, path.parent):
                if anchor.is_symlink() or _is_junction(anchor):
                    raise _fail("PRIVATE_STORAGE_INVALID")
            check_tree(path)
        for path in (profile, registration):
            if path.exists():
                shutil.rmtree(path)
    except PostgresRegistrationError:
        raise
    except Exception:
        raise _fail("STORAGE_ERROR") from None


def _id_taken(root: Path, database_id: str) -> bool:
    base = root / ".schema-adaptive"
    return any(
        (base / folder / database_id).exists()
        for folder in ("registered-databases", "postgres-registrations", "postgres-profiles")
    )


# --- registration --------------------------------------------------------------------------


def _sampled_catalog(catalog: DatabaseCatalog, reader: Any, database_id: str) -> DatabaseCatalog | None:
    """A copy carrying bounded example values (prompt input only)."""
    columns = [(table, column) for table in catalog.tables for column in table.columns]
    if len(columns) > MAX_SAMPLED_COLUMNS:
        return None
    tables = []
    for table in catalog.tables:
        new_columns = []
        for column in table.columns:
            # Phase 8-23: a sample, so columns with many values (names, codes)
            # still show examples instead of none.
            try:
                values = reader.read(database_id, table.name, column.name, SAMPLE_LIMIT, sample=True)
            except TypeError:  # a reader without samples: all values or nothing, as before
                values = reader.read(database_id, table.name, column.name, SAMPLE_LIMIT)
            examples = [value for value in values if isinstance(value, str)] if isinstance(values, list) else []
            new_columns.append(column.model_copy(update={"sample_values": examples}))
        tables.append(table.model_copy(update={"columns": new_columns}))
    return catalog.model_copy(update={"tables": tables})


def register_postgres_database(
    *,
    repository_root: str | Path,
    source: PostgresSource,
    schema: str,
    database_id: str,
    label: str,
    reserved_ids: frozenset[str] = frozenset(),
    adapter_factory: Callable[[str, PostgresConnectionConfig], Any] | None = None,
    value_reader: Any | None = None,
    mapping_proposer: Callable[..., Any] | None = None,
) -> LoadedPostgresRegistration:
    try:
        database_id = sqlite_storage.validate_new_database_id(database_id)
        label = sqlite_storage.validate_label(label)
    except Exception:
        raise _fail("INVALID_INPUT") from None
    from ..api.postgres_runtime import _valid_label as runtime_label_ok

    if not runtime_label_ok(label):
        # The PostgreSQL runtime's label rule (letters, digits, space, ()_.-);
        # refusing here beats a registration that can never be published.
        raise _fail("INVALID_LABEL")
    if not isinstance(schema, str) or SCHEMA_PATTERN.fullmatch(schema) is None or schema in _SYSTEM_SCHEMAS:
        raise _fail("INVALID_INPUT")
    root, target = _private_dir(repository_root, REGISTRATION_ROOT, database_id)
    if database_id in reserved_ids or _id_taken(root, database_id):
        raise _fail("REGISTRATION_CONFLICT")
    config = source.config(schema)
    try:
        if adapter_factory is None:
            from ..catalog.postgres import PostgresAdapter

            adapter = PostgresAdapter({database_id: config})
        else:
            adapter = adapter_factory(database_id, config)
        catalog = adapter.inspect(database_id)
    except Exception:
        raise _fail("INSPECTION_FAILED") from None
    if not catalog.tables or any(column.sample_values for table in catalog.tables for column in table.columns):
        raise _fail("INSPECTION_FAILED")
    ontology = load_default_ontology()
    try:
        proposal = RuleBasedSchemaAligner().align(catalog, ontology)
    except Exception:
        raise _fail("MAPPING_FAILED") from None
    if mapping_proposer is not None and value_reader is not None:
        try:
            sampled = _sampled_catalog(catalog, value_reader, database_id)
            candidate = None if sampled is None else mapping_proposer(sampled, ontology, proposal)
            if candidate is not None and sqlite_storage._proposal_is_consistent(candidate, proposal, catalog):  # noqa: SLF001
                proposal = MappingProposal.model_validate(candidate.model_dump(mode="json"))
        except Exception:
            pass  # the rule proposal stands
    catalog_bytes = _json_bytes(catalog.model_dump(mode="json"))
    proposal_bytes = _json_bytes(proposal.model_dump(mode="json"))
    manifest = PostgresRegistration(
        database_id=database_id,
        label=label,
        source_id=source.source_id,
        source_identity=source.identity(),
        postgres_schema=schema,
        schema_fingerprint=catalog.schema_fingerprint,
        ontology_version=ontology.version,
        catalog_sha256=hashlib.sha256(catalog_bytes).hexdigest(),
        proposal_sha256=hashlib.sha256(proposal_bytes).hexdigest(),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".staging-{database_id}-", dir=target.parent))
    try:
        (staging / "catalog.json").write_bytes(catalog_bytes)
        (staging / "proposal.json").write_bytes(proposal_bytes)
        (staging / "registration.json").write_bytes(_json_bytes(manifest.model_dump(mode="json")))
        if target.exists():
            raise _fail("REGISTRATION_CONFLICT")
        os.rename(staging, target)
        staging = None  # type: ignore[assignment]
    except PostgresRegistrationError:
        raise
    except Exception:
        raise _fail("STORAGE_ERROR") from None
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
    return load_postgres_registration(repository_root=root, database_id=database_id)


def load_postgres_registration(*, repository_root: str | Path, database_id: str) -> LoadedPostgresRegistration:
    try:
        database_id = sqlite_storage.validate_database_id(database_id)
    except Exception:
        raise _fail("INVALID_INPUT") from None
    _root, target = _private_dir(repository_root, REGISTRATION_ROOT, database_id)
    if target.is_symlink() or not target.is_dir():
        raise _fail("NOT_FOUND")
    manifest = _strict_model(_read(target / "registration.json"), PostgresRegistration)
    catalog_raw = _read(target / "catalog.json")
    proposal_raw = _read(target / "proposal.json")
    if (
        hashlib.sha256(catalog_raw).hexdigest() != manifest.catalog_sha256
        or hashlib.sha256(proposal_raw).hexdigest() != manifest.proposal_sha256
    ):
        raise _fail("ARTIFACT_MISMATCH")
    catalog = _strict_model(catalog_raw, DatabaseCatalog)
    proposal = _strict_model(proposal_raw, MappingProposal)
    if (
        manifest.database_id != database_id
        or catalog.database_id != database_id
        or proposal.database_id != database_id
        or catalog.schema_fingerprint != manifest.schema_fingerprint
        or proposal.schema_fingerprint != manifest.schema_fingerprint
        or proposal.ontology_version != manifest.ontology_version
        or any(column.sample_values for table in catalog.tables for column in table.columns)
    ):
        raise _fail("ARTIFACT_MISMATCH")
    return LoadedPostgresRegistration(manifest=manifest, catalog=catalog, proposal=proposal)


# --- review --------------------------------------------------------------------------------


def _draft_path(repository_root: str | Path, database_id: str) -> Path:
    return _private_dir(repository_root, REGISTRATION_ROOT, database_id)[1] / "draft.json"


def load_draft(*, repository_root: str | Path, database_id: str) -> MappingDraft | None:
    loaded = load_postgres_registration(repository_root=repository_root, database_id=database_id)
    path = _draft_path(repository_root, database_id)
    if not path.exists():
        return None
    draft = _strict_model(_read(path), MappingDraft)
    if (
        draft.database_id != database_id
        or draft.schema_fingerprint != loaded.manifest.schema_fingerprint
        or draft.ontology_version != loaded.manifest.ontology_version
    ):
        raise _fail("REVIEW_CONFLICT")
    return draft


def init_review(*, repository_root: str | Path, database_id: str, reviewer: str) -> MappingDraft:
    existing = load_draft(repository_root=repository_root, database_id=database_id)
    if existing is not None:
        return existing
    loaded = load_postgres_registration(repository_root=repository_root, database_id=database_id)
    del reviewer  # recorded on each decision, as in the SQLite flow
    draft = MappingReviewService().create_draft(loaded.proposal)
    _write_atomic(_draft_path(repository_root, database_id), _json_bytes(draft.model_dump(mode="json")))
    return draft


def mutate_review(
    *,
    repository_root: str | Path,
    database_id: str,
    kind: str,
    semantic_field: str,
    reviewer: str,
    candidate_index: int | None = None,
    table: str | None = None,
    column: str | None = None,
    reason: str | None = None,
) -> MappingDraft:
    loaded = load_postgres_registration(repository_root=repository_root, database_id=database_id)
    draft = load_draft(repository_root=repository_root, database_id=database_id)
    if draft is None:
        raise _fail("NOT_FOUND")
    service = MappingReviewService()
    try:
        if kind == "APPROVE" and candidate_index is not None:
            new = service.approve(draft, loaded.proposal, semantic_field, reviewer=reviewer, candidate_index=candidate_index)
        elif kind == "REMAP" and table and column and reason:
            physical = {(t.name, c.name) for t in loaded.catalog.tables for c in t.columns}
            if (table, column) not in physical:
                raise _fail("INVALID_INPUT")
            new = service.remap(draft, semantic_field, table=table, column=column, reviewer=reviewer, reason=reason)
        elif kind == "IGNORE" and reason:
            new = service.ignore(draft, semantic_field, reviewer=reviewer, reason=reason)
        else:
            raise _fail("INVALID_INPUT")
    except PostgresRegistrationError:
        raise
    except Exception:
        raise _fail("INVALID_INPUT") from None
    _write_atomic(_draft_path(repository_root, database_id), _json_bytes(new.model_dump(mode="json")))
    return new


def load_published(*, repository_root: str | Path, database_id: str) -> PublishedRecord | None:
    path = _private_dir(repository_root, REGISTRATION_ROOT, database_id)[1] / "published.json"
    if not path.exists():
        return None
    record = _strict_model(_read(path), PublishedRecord)
    if record.database_id != database_id:
        raise _fail("ARTIFACT_MISMATCH")
    return record


# Phase 8-21: "월 09:00-10:15, 수 09:00-10:15" or "월1-2/수3": weekdays with
# times or periods, and nothing else (a course name never matches).
_PACKED_SLOT = r"[월화수목금토일]\s*[0-9:~\-\s]*"
_PACKED_VALUE = re.compile(rf"^\s*{_PACKED_SLOT}(?:[,/]\s*{_PACKED_SLOT})*$")


class SampledValues(list):
    """Some of a column's values, not all of them (Phase 8-21).

    Only ``PACKED_WEEKDAY`` may be chosen from a sample: it searches by
    ``LIKE`` and cannot fail on an unseen value, while a cast such as
    ``SLASH_FIRST_NUMERIC`` would (Luna 8-21 C review).
    """


def packed_weekdays_fit(values: list[str]) -> bool:
    """Every value is a packed timetable, and not all are bare weekdays (those are KOREAN_WEEKDAY)."""
    return (
        bool(values)
        and all(_PACKED_VALUE.fullmatch(value) for value in values)
        and any(value not in WEEKDAYS for value in values)
    )


def transform_fits(transform: FieldTransform, declared_type: str, data_type: DataType, values: list[str]) -> bool:
    """The fixed publish rule: type rules plus stored examples in the transform's format."""
    if not transform_supports_data_type(transform, data_type) or not column_type_supports(transform, declared_type):
        return False
    if isinstance(values, SampledValues) and transform is not FieldTransform.PACKED_WEEKDAY:
        return False
    if transform is FieldTransform.KOREAN_WEEKDAY:
        return bool(values) and all(value in WEEKDAYS for value in values)
    if transform is FieldTransform.PACKED_WEEKDAY:
        return packed_weekdays_fit(values)
    if transform is FieldTransform.SLASH_FIRST_NUMERIC:
        return bool(values) and all(_SLASH_VALUE.fullmatch(value) for value in values)
    if transform is FieldTransform.HHMM_TEXT_TO_HHMM:
        return bool(values) and all(_HHMM_VALUE.fullmatch(value) for value in values)
    return True  # TIME_TO_HHMM: the column type decides


def decide_transforms(
    mapped: dict[str, PublishedFieldMapping],
    catalog: DatabaseCatalog,
    read_values: Callable[[str, str], list[str]],
) -> dict[str, FieldTransform]:
    fields = load_default_ontology().fields
    declared = {(t.name, c.name): str(c.declared_type or "") for t in catalog.tables for c in t.columns}
    transforms: dict[str, FieldTransform] = {}
    for name, target in mapped.items():
        field = fields.get(name)
        if field is None:
            continue
        values: list[str] | None = None
        fitting = []
        for transform in FieldTransform:
            if not transform_supports_data_type(transform, field.data_type) or not column_type_supports(
                transform, declared.get((target.table, target.column), "")
            ):
                continue
            if values is None:
                values = read_values(target.table, target.column)
            if transform_fits(transform, declared.get((target.table, target.column), ""), field.data_type, values):
                fitting.append(transform)
        if len(fitting) == 1:
            transforms[name] = fitting[0]
    return transforms


# Phase 9-07: why a field blocks publish, shown to the admin all at once.
BLOCK_REASONS = (
    "PENDING",  # not reviewed yet
    "COLUMN_MISSING",  # the chosen column is not in this schema
    "COLUMN_SHARED",  # another field (``other``) already uses the column
    "NOTHING_MAPPED",  # no field is connected at all
    "DICTIONARY_UNUSED",  # a value dictionary for a field that is not connected
    "PERIOD_TABLE_NEEDED",  # a time field on a period-number column needs its period table
    "TIME_FORMAT",  # raw text that is not zero-padded HH:MM (Phase 9-05)
    "NUMBER_FORMAT",  # a number field on a non-numeric column (Phase 9-06)
    "BLANK_VALUES",  # empty text under a text-to-number/time transform (Phase 9-06)
    "TEXT_ON_NUMBER",  # text compared with a numeric column fails (Phase 9-30)
    "JOIN_AMBIGUOUS",  # multiple anchored join paths block publish (Phase 9-30)
    "DICTIONARY_STALE",  # a dictionary entry the column no longer holds (checked before planning)
)


@dataclass(frozen=True, order=True)
class BlockedField:
    name: str
    reason: str
    other: str | None = None


@dataclass(frozen=True)
class PublishPlan:
    mapping: PublishedMappingProfile
    transforms: dict[str, FieldTransform]
    unsupported: tuple[str, ...]
    anchor_table: str | None
    dictionaries: dict[str, Any] = field(default_factory=dict)
    extensions: dict[str, Any] = field(default_factory=dict)


def plan_publish(
    loaded: LoadedPostgresRegistration,
    draft: MappingDraft,
    *,
    version: int,
    published_by: str,
    read_values: Callable[[str, str], list[str]],
    dictionaries: dict[str, Any] | None = None,
    extensions: list[Any] | None = None,
    has_blank: Callable[[str, str], bool | None] | None = None,
    preblocked: list[BlockedField] | None = None,
) -> tuple[PublishPlan | None, list[BlockedField]]:
    """The publishable mapping, or every field that blocks it, with the reason.

    A field with a value dictionary (Phase 8-07) gets no transform: the
    runtime allows only one of the two per field. Extension fields
    (Phase 8-19) join the mapping as declared, never sharing a column.
    """
    dictionaries = dict(dictionaries or {})
    extensions = list(extensions or [])
    # Phase 9-07: every check runs and every blocked field comes back with its
    # reason, so an admin fixes them in one pass instead of publishing again
    # and again (the 9-04 rehearsal needed four publishes).
    # ``preblocked``: found before planning (a stale value dictionary), so they
    # come back with every other blocker instead of hiding them (Luna 9-07).
    blocked: dict[str, BlockedField] = {item.name: item for item in preblocked or []}

    def block(name: str, reason: str, other: str | None = None) -> None:
        blocked.setdefault(name, BlockedField(name, reason, other))

    for name, entry in sorted(draft.fields.items()):
        if name in load_default_ontology().retired_fields:
            continue
        if entry.decision is ReviewDecision.PENDING:
            block(name, "PENDING")
    physical = {(t.name, c.name) for t in loaded.catalog.tables for c in t.columns}
    mapped: dict[str, PublishedFieldMapping] = {}
    owner: dict[tuple[str, str], str] = {}
    for name, entry in sorted(draft.fields.items()):
        if name in load_default_ontology().retired_fields:
            continue
        if entry.decision in (ReviewDecision.APPROVED, ReviewDecision.REMAPPED):
            target = (entry.table or "", entry.column or "")
            if target not in physical:
                block(name, "COLUMN_MISSING")
                continue
            if target in owner:
                block(name, "COLUMN_SHARED", owner[target])
                continue
            owner[target] = name
            mapped[name] = PublishedFieldMapping(
                table=target[0], column=target[1], reviewer=entry.reviewer or published_by, reason=entry.reason
            )
    if not mapped:
        for name in sorted(draft.fields):
            if name not in load_default_ontology().retired_fields:
                block(name, "NOTHING_MAPPED")
        return None, sorted(blocked.values())
    for extension in extensions:
        target = (extension.table, extension.column)
        if target not in physical:
            block(extension.name, "COLUMN_MISSING")
            continue
        if target in owner or extension.name in mapped:
            block(extension.name, "COLUMN_SHARED", owner.get(target))
            continue
        owner[target] = extension.name
        mapped[extension.name] = PublishedFieldMapping(
            table=target[0], column=target[1], reviewer=extension.reviewer, reason="Phase 8-19 extension field"
        )
    for name in sorted(dictionaries):
        if name not in mapped:
            block(name, "DICTIONARY_UNUSED")
    # Phase 8-21: a time field on a period-number column needs its period table.
    from .postgres_dictionaries import INTEGER_TYPE

    declared = {(t.name, c.name): str(c.declared_type or "").lower() for t in loaded.catalog.tables for c in t.columns}
    ontology_fields = load_default_ontology().fields

    def kind(name: str) -> DataType | None:
        return ontology_fields[name].data_type if name in ontology_fields else None

    def column_type(name: str) -> str:
        target = mapped[name]
        return declared.get((target.table, target.column), "").strip()

    for name in sorted(mapped):
        if kind(name) is DataType.TIME and INTEGER_TYPE.fullmatch(column_type(name)) and name not in dictionaries:
            block(name, "PERIOD_TABLE_NEEDED")
    transforms = {
        name: transform
        for name, transform in decide_transforms(mapped, loaded.catalog, read_values).items()
        if name not in dictionaries
    }
    # Phase 9-05: a time field kept as raw text needs "HH:MM" values; a range
    # such as "09:00-10:15" compared as text gave wrong rows without a warning.
    # The value read leaves out empty text, which sorts before every time, so
    # it is asked for on its own; an unreadable column blocks too (Luna 9-05).
    def unreadable_time(target: PublishedFieldMapping) -> bool:
        values = read_values(target.table, target.column)
        if not values or not all(_CLOCK_TEXT.fullmatch(value) for value in values):
            return True
        return has_blank is not None and has_blank(target.table, target.column) is not False

    for name in sorted(mapped):
        if name in blocked:
            continue
        if (
            kind(name) is DataType.TIME
            and name not in transforms
            and name not in dictionaries
            and not column_type(name).startswith("time")
            and unreadable_time(mapped[name])
        ):
            block(name, "TIME_FORMAT")
        # Phase 9-30: chat compares text as text ("토요일" or LIKE for
        # contains), which fails on numeric columns; dictionaries cannot serve them.
        # Numeric codes and sections stay allowed (Luna 8-23, Luna 9-30): "1041" = 1041 works.
        elif (
            kind(name) is DataType.STRING
            and name not in _NUMERIC_IDENTITY_FIELDS
            and _NUMERIC_TYPE.fullmatch(column_type(name))
            and name not in transforms
            and name not in dictionaries
        ):
            block(name, "TEXT_ON_NUMBER")
        # Phase 9-06: a number field on a text column without a validated
        # text-to-number transform fails in SQL ("3-2-2" or even "3" = 3). A
        # value dictionary never serves a number field (the runtime refuses it).
        elif (
            kind(name) in (DataType.NUMBER, DataType.INTEGER)
            and name not in transforms
            and not _NUMERIC_TYPE.fullmatch(column_type(name))
        ):
            block(name, "NUMBER_FORMAT")
        # Luna 9-06: the value read leaves out empty text, so a slash or
        # four-digit transform could be chosen over a column that also holds ""
        # and then fail in SQL (CAST('' AS NUMERIC)) or misorder.
        elif (
            transforms.get(name) in (FieldTransform.SLASH_FIRST_NUMERIC, FieldTransform.HHMM_TEXT_TO_HHMM)
            and has_blank is not None
            and has_blank(mapped[name].table, mapped[name].column) is not False
        ):
            block(name, "BLANK_VALUES")
    if blocked:
        return None, sorted(blocked.values())
    unsupported = tuple(sorted(name for name in draft.fields if name not in mapped))
    mapping = PublishedMappingProfile(
        database_id=draft.database_id,
        version=version,
        schema_fingerprint=draft.schema_fingerprint,
        ontology_version=draft.ontology_version,
        published_by=published_by,
        published_at=datetime.now(timezone.utc),
        fields=mapped,
    )
    section = mapped.get("CourseOffering.section")
    return (
        PublishPlan(
            mapping=mapping,
            transforms=transforms,
            unsupported=unsupported,
            anchor_table=section.table if section is not None else None,
            dictionaries=dictionaries,
            extensions={
                extension.name: ExtensionField(label=extension.label, data_type=extension.data_type)
                for extension in extensions
            },
        ),
        [],
    )


def profile_documents(plan: PublishPlan, manifest: PostgresRegistration) -> tuple[bytes, bytes]:
    """``mapping.json`` and the ``profile.json`` that pins it (startup loader format)."""
    mapping_bytes = (
        json.dumps(plan.mapping.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    profile = {
        "version": PROFILE_VERSION,
        "database_id": manifest.database_id,
        "label": manifest.label,
        "schema": manifest.postgres_schema,
        "dsn_env": dsn_env_for(manifest.database_id),
        "mapping_sha256": hashlib.sha256(mapping_bytes).hexdigest(),
        "include_views": False,
        "field_transforms": {name: transform.value for name, transform in sorted(plan.transforms.items())},
        "unsupported_fields": list(plan.unsupported),
        "anchor_table": plan.anchor_table,
        "value_dictionaries": {
            name: dictionary.model_dump(mode="json") for name, dictionary in sorted(plan.dictionaries.items())
        },
        "extension_fields": {
            name: {"label": field.label, "data_type": field.data_type}
            for name, field in sorted(plan.extensions.items())
        },
    }
    profile_bytes = (json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return profile_bytes, mapping_bytes


def runtime_profile(plan: PublishPlan, manifest: PostgresRegistration, source: PostgresSource) -> Any:
    from ..api.postgres_runtime import PostgresRuntimeProfile

    return PostgresRuntimeProfile(
        database_id=manifest.database_id,
        label=manifest.label,
        connection_config=source.config(manifest.postgres_schema),
        published_mapping=plan.mapping,
        field_transforms=plan.transforms,
        unsupported_fields=list(plan.unsupported),
        anchor_table=plan.anchor_table,
        value_dictionaries=dict(plan.dictionaries),
        extension_fields=dict(plan.extensions),
    )


def publish_review(
    *,
    repository_root: str | Path,
    database_id: str,
    published_by: str,
    source: PostgresSource,
    validate: Callable[[Any], Any],
    read_values: Callable[[str, str], list[str]],
    dictionaries: dict[str, Any] | None = None,
    extensions: list[Any] | None = None,
    has_blank: Callable[[str, str], bool | None] | None = None,
    preblocked: list[BlockedField] | None = None,
) -> tuple[Any, list[BlockedField]]:
    """(validated runtime context or ``None``, blocking fields).

    Nothing is written unless the runtime accepts the profile.
    """
    loaded = load_postgres_registration(repository_root=repository_root, database_id=database_id)
    draft = load_draft(repository_root=repository_root, database_id=database_id)
    if draft is None:
        raise _fail("NOT_FOUND")
    if loaded.manifest.source_id != source.source_id or loaded.manifest.source_identity != source.identity():
        raise _fail("SOURCE_CHANGED")  # the connection now points at another database
    previous = load_published(repository_root=repository_root, database_id=database_id)
    version = 1 if previous is None else previous.mapping_version + 1
    plan, blocking = plan_publish(
        loaded,
        draft,
        version=version,
        published_by=published_by,
        read_values=read_values,
        dictionaries=dictionaries,
        extensions=extensions,
        has_blank=has_blank,
        preblocked=preblocked,
    )
    if plan is None:
        return None, blocking
    try:
        context = validate(runtime_profile(plan, loaded.manifest, source))
    except Exception as exc:
        fields = getattr(exc, "fields", None)
        if isinstance(fields, tuple) and fields and all(isinstance(name, str) for name in fields):
            return None, [BlockedField(name, "JOIN_AMBIGUOUS") for name in fields]
        raise _fail("PUBLISH_BLOCKED") from None
    profile_bytes, mapping_bytes = profile_documents(plan, loaded.manifest)
    _root, profile_dir = _private_dir(repository_root, PROFILE_ROOT, database_id)
    profile_dir.mkdir(parents=True, exist_ok=True)
    _write_pair(profile_dir, {"mapping.json": mapping_bytes, "profile.json": profile_bytes})
    record = PublishedRecord(
        database_id=database_id,
        mapping_version=version,
        published_by=published_by,
        published_at=plan.mapping.published_at.isoformat().replace("+00:00", "Z"),
        mapping_sha256=hashlib.sha256(mapping_bytes).hexdigest(),
    )
    registration_dir = _private_dir(repository_root, REGISTRATION_ROOT, database_id)[1]
    _write_atomic(registration_dir / "published.json", _json_bytes(record.model_dump(mode="json")))
    return context, []


def list_registrations(repository_root: str | Path) -> list[dict[str, Any]]:
    """Phase 8-16: every readable registration, for reopening its review."""
    try:
        root = sqlite_storage._validate_repository(repository_root)  # noqa: SLF001
    except Exception:
        return []
    base = root.joinpath(".schema-adaptive", *REGISTRATION_ROOT)
    if not base.is_dir():
        return []
    found = []
    for entry in sorted(base.iterdir()):
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        try:
            loaded = load_postgres_registration(repository_root=root, database_id=entry.name)
            load_draft(repository_root=root, database_id=entry.name)  # a damaged draft cannot reopen
            published = load_published(repository_root=root, database_id=entry.name)
        except Exception:
            continue  # a damaged registration is left out, not fatal
        from ..api.postgres_runtime import _valid_label as runtime_label_ok

        if not runtime_label_ok(loaded.manifest.label):
            continue  # could never be published
        found.append({
            "database_id": loaded.manifest.database_id,
            "label": loaded.manifest.label,
            "source_id": loaded.manifest.source_id,
            "schema": loaded.manifest.postgres_schema,
            "status": "PUBLISHED" if published is not None else "PENDING_REVIEW",
            "mapping_version": None if published is None else published.mapping_version,
        })
    return found


def published_registrations(repository_root: str | Path) -> list[tuple[str, str, str]]:
    """(database_id, source_id, source identity) of every published registration, for startup."""
    try:
        root = sqlite_storage._validate_repository(repository_root)  # noqa: SLF001
    except Exception:
        return []
    base = root.joinpath(".schema-adaptive", *REGISTRATION_ROOT)
    if not base.is_dir():
        return []
    found = []
    for entry in sorted(base.iterdir()):
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        try:
            loaded = load_postgres_registration(repository_root=root, database_id=entry.name)
            if load_published(repository_root=root, database_id=entry.name) is not None:
                found.append((entry.name, loaded.manifest.source_id, loaded.manifest.source_identity))
        except Exception:
            continue
    return found
