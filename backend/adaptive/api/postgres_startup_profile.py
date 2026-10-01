"""Private PostgreSQL startup profile loader (Phase 7-05, synthetic-only).

Reads Git-ignored private metadata and a separately configured DSN from
the process environment, then injects only approved immutable mappings
into the server-owned runtime registry. No frontend, SQLite upload,
mapping edit/approve/publish, Ollama, real data, external PostgreSQL,
or legacy change happens here.

All public failures use one fixed redacted message. DSN, host, port,
user, password, schema, paths, mapping physical identifiers, and driver
exception text never appear in exceptions, repr, logs, or app state.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

_FIXED_FAILURE_MESSAGE = "postgres startup profile unavailable"

_MAX_FILE_BYTES = 256 * 1024

_PROFILE_VERSION = "postgres-runtime-profile-v1"
# Phase 7-12: v2 adds view inspection, declared value transforms, and an
# explicit list of canonical fields the database cannot answer.
_PROFILE_VERSION_V2 = "postgres-runtime-profile-v2"
# Phase 7-17: v3 is v2 plus a declared row anchor table (LEFT JOIN from it).
_PROFILE_VERSION_V3 = "postgres-runtime-profile-v3"
# Phase 8-06: v4 is v2 plus a nullable anchor and reviewed value dictionaries.
_PROFILE_VERSION_V4 = "postgres-runtime-profile-v4"
# Phase 8-19: v4 plus this database's extension fields.
_PROFILE_VERSION_V5 = "postgres-runtime-profile-v5"
_MAX_PROFILE_FIELDS = 64
_TABLE_NAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_SEMANTIC_FIELD_PATTERN = re.compile(r"^[A-Z][A-Za-z]{0,63}\.[a-z][a-z_]{0,63}$")

_DB_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _().-]{0,63}$")
_SCHEMA_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_$]{0,62}$")
_DSN_ENV_PATTERN = re.compile(r"^SCHEMA_ADAPTIVE_POSTGRES_DSN_[A-Z0-9_]{1,64}$")
_HEX64_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_PROFILE_KEYS = frozenset(
    {"version", "database_id", "label", "schema", "dsn_env", "mapping_sha256"}
)
_PROFILE_V2_EXTRA_KEYS = frozenset(
    {"include_views", "field_transforms", "unsupported_fields"}
)
_MAPPING_KEYS = frozenset(
    {
        "database_id",
        "version",
        "status",
        "schema_fingerprint",
        "ontology_version",
        "published_by",
        "published_at",
        "fields",
    }
)
_FIELD_KEYS = frozenset({"table", "column", "reviewer", "reason"})

__all__ = [
    "PostgresStartupProfileError",
    "load_postgres_startup_profiles",
]


class PostgresStartupProfileError(Exception):
    """Fixed-message startup failure (no sensitive details)."""

    def __init__(self) -> None:
        super().__init__(_FIXED_FAILURE_MESSAGE)


def _fail() -> PostgresStartupProfileError:
    return PostgresStartupProfileError()


def _valid_database_id(value: Any) -> bool:
    return isinstance(value, str) and _DB_ID_PATTERN.fullmatch(value) is not None


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _parse_strict_json(raw: bytes) -> Any:
    try:
        text = raw.decode("utf-8")
    except Exception:
        raise _fail() from None
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _v: (_ for _ in ()).throw(ValueError()),
        )
    except Exception:
        raise _fail() from None


def _check_sized(raw: Any) -> bytes:
    if not isinstance(raw, (bytes, bytearray)):
        raise _fail()
    content = bytes(raw)
    if len(content) < 1 or len(content) > _MAX_FILE_BYTES:
        raise _fail()
    return content


def _parse_v2_extras(payload: dict[str, Any]) -> None:
    from ..mapping.transforms import FieldTransform

    if not isinstance(payload["include_views"], bool):
        raise _fail()
    transforms = payload["field_transforms"]
    if not isinstance(transforms, dict) or len(transforms) > _MAX_PROFILE_FIELDS:
        raise _fail()
    for semantic, transform in transforms.items():
        if not isinstance(semantic, str) or _SEMANTIC_FIELD_PATTERN.fullmatch(semantic) is None:
            raise _fail()
        if not isinstance(transform, str) or transform not in FieldTransform.__members__:
            raise _fail()
    unsupported = payload["unsupported_fields"]
    if not isinstance(unsupported, list) or len(unsupported) > _MAX_PROFILE_FIELDS:
        raise _fail()
    for semantic in unsupported:
        if not isinstance(semantic, str) or _SEMANTIC_FIELD_PATTERN.fullmatch(semantic) is None:
            raise _fail()
    # One canonical spelling: sorted and unique.
    if unsupported != sorted(set(unsupported)):
        raise _fail()
    if set(unsupported) & set(transforms):
        raise _fail()


def _parse_value_dictionaries(payload: dict[str, Any]) -> None:
    from ..mapping.values import ValueDictionary

    dictionaries = payload["value_dictionaries"]
    if not isinstance(dictionaries, dict) or len(dictionaries) > _MAX_PROFILE_FIELDS:
        raise _fail()
    parsed: dict[str, Any] = {}
    for semantic, raw in dictionaries.items():
        if not isinstance(semantic, str) or _SEMANTIC_FIELD_PATTERN.fullmatch(semantic) is None:
            raise _fail()
        try:
            dictionary = ValueDictionary.model_validate(raw)
        except Exception:
            raise _fail() from None
        # One canonical spelling: the document must equal its own dump.
        if dictionary.model_dump(mode="json") != raw:
            raise _fail()
        parsed[semantic] = dictionary
    if set(parsed) & set(payload["field_transforms"]) or set(parsed) & set(
        payload["unsupported_fields"]
    ):
        raise _fail()
    payload["value_dictionaries"] = parsed


def _parse_extension_fields(payload: dict[str, Any]) -> None:
    from ..mapping.extensions import ExtensionField, valid_extension

    extensions = payload["extension_fields"]
    if not isinstance(extensions, dict):
        raise _fail()
    parsed: dict[str, Any] = {}
    for name, raw in extensions.items():
        if not isinstance(raw, dict) or set(raw) != {"label", "data_type"}:
            raise _fail()
        field = ExtensionField(label=raw["label"], data_type=raw["data_type"])
        if not valid_extension(name, field):
            raise _fail()
        parsed[name] = field
    payload["extension_fields"] = parsed


def _parse_profile_payload(raw: bytes, *, expected_id: str) -> dict[str, Any]:
    content = _check_sized(raw)
    payload = _parse_strict_json(content)
    try:
        if not isinstance(payload, dict):
            raise _fail()
        version = payload.get("version")
        if version == _PROFILE_VERSION:
            expected_keys = set(_PROFILE_KEYS)
        elif version == _PROFILE_VERSION_V2:
            expected_keys = set(_PROFILE_KEYS | _PROFILE_V2_EXTRA_KEYS)
        elif version == _PROFILE_VERSION_V3:
            expected_keys = set(_PROFILE_KEYS | _PROFILE_V2_EXTRA_KEYS | {"anchor_table"})
        elif version == _PROFILE_VERSION_V4:
            expected_keys = set(
                _PROFILE_KEYS | _PROFILE_V2_EXTRA_KEYS | {"anchor_table", "value_dictionaries"}
            )
        elif version == _PROFILE_VERSION_V5:
            expected_keys = set(
                _PROFILE_KEYS
                | _PROFILE_V2_EXTRA_KEYS
                | {"anchor_table", "value_dictionaries", "extension_fields"}
            )
        else:
            raise _fail()
        if set(payload.keys()) != expected_keys:
            raise _fail()
        for key in _PROFILE_KEYS:
            if not isinstance(payload[key], str):
                raise _fail()
        if version != _PROFILE_VERSION:
            _parse_v2_extras(payload)
        if version in (_PROFILE_VERSION_V3, _PROFILE_VERSION_V4, _PROFILE_VERSION_V5):
            anchor = payload["anchor_table"]
            nullable = version in (_PROFILE_VERSION_V4, _PROFILE_VERSION_V5)
            if not (anchor is None and nullable) and (
                not isinstance(anchor, str) or _TABLE_NAME_PATTERN.fullmatch(anchor) is None
            ):
                raise _fail()
        if version in (_PROFILE_VERSION_V4, _PROFILE_VERSION_V5):
            _parse_value_dictionaries(payload)
        if version == _PROFILE_VERSION_V5:
            _parse_extension_fields(payload)
        if payload["database_id"] != expected_id:
            raise _fail()
        if not _valid_database_id(payload["database_id"]):
            raise _fail()
        label = payload["label"]
        if not (1 <= len(label) <= 64 and _LABEL_PATTERN.fullmatch(label) is not None):
            raise _fail()
        if _SCHEMA_PATTERN.fullmatch(payload["schema"]) is None:
            raise _fail()
        dsn_env = payload["dsn_env"]
        if len(dsn_env) > 128 or _DSN_ENV_PATTERN.fullmatch(dsn_env) is None:
            raise _fail()
        if _HEX64_PATTERN.fullmatch(payload["mapping_sha256"]) is None:
            raise _fail()
        return dict(payload)
    except PostgresStartupProfileError:
        raise
    except Exception:
        raise _fail() from None


def _parse_published_at(value: Any) -> datetime:
    try:
        if not isinstance(value, str) or not value:
            raise _fail()
        text = value
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            raise _fail()
        return parsed
    except PostgresStartupProfileError:
        raise
    except Exception:
        raise _fail() from None


def _payload_matches_dump(payload: dict[str, Any], dumped: dict[str, Any]) -> bool:
    try:
        if set(payload.keys()) != set(dumped.keys()):
            return False
        for key, value in payload.items():
            if key == "published_at":
                continue
            if value != dumped.get(key):
                return False
        expected = _parse_published_at(payload.get("published_at"))
        actual_raw = dumped.get("published_at")
        if isinstance(actual_raw, datetime):
            actual = actual_raw
            if actual.tzinfo is None:
                return False
        elif isinstance(actual_raw, str):
            actual = _parse_published_at(actual_raw)
        else:
            return False
        return expected.timestamp() == actual.timestamp()
    except Exception:
        return False


def _parse_mapping_profile(raw: bytes, *, expected_id: str) -> Any:
    from ..mapping.contracts import PublishedMappingProfile

    content = _check_sized(raw)
    payload = _parse_strict_json(content)
    try:
        if not isinstance(payload, dict):
            raise _fail()
        if set(payload.keys()) != set(_MAPPING_KEYS):
            raise _fail()
        if payload.get("status") != "PUBLISHED":
            raise _fail()
        if payload.get("database_id") != expected_id:
            raise _fail()
        if not _valid_database_id(payload.get("database_id")):
            raise _fail()
        for key in ("version", "ontology_version"):
            value = payload.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise _fail()
        fingerprint = payload.get("schema_fingerprint")
        if not isinstance(fingerprint, str) or _HEX64_PATTERN.fullmatch(fingerprint) is None:
            raise _fail()
        published_by = payload.get("published_by")
        if (
            not isinstance(published_by, str)
            or not published_by
            or not published_by.strip()
        ):
            raise _fail()
        _parse_published_at(payload.get("published_at"))
        fields = payload.get("fields")
        if not isinstance(fields, dict):
            raise _fail()
        for semantic, entry in fields.items():
            if (
                not isinstance(semantic, str)
                or not semantic
                or not semantic.strip()
            ):
                raise _fail()
            if not isinstance(entry, dict):
                raise _fail()
            if set(entry.keys()) != set(_FIELD_KEYS):
                raise _fail()
            for key in ("table", "column", "reviewer"):
                value = entry.get(key)
                if (
                    not isinstance(value, str)
                    or not value
                    or not value.strip()
                ):
                    raise _fail()
            reason = entry.get("reason")
            if reason is not None and not isinstance(reason, str):
                raise _fail()
        try:
            model = PublishedMappingProfile.model_validate(payload)
        except Exception:
            raise _fail() from None
        try:
            dumped = model.model_dump(mode="json")
        except Exception:
            raise _fail() from None
        if not _payload_matches_dump(payload, dumped):
            raise _fail()
        if model.database_id != expected_id:
            raise _fail()
        try:
            if model.status != "PUBLISHED":  # type: ignore[comparison-overlap]
                raise _fail()
        except PostgresStartupProfileError:
            raise
        except Exception:
            raise _fail() from None
        try:
            published_at = model.published_at
            if not isinstance(published_at, datetime) or published_at.tzinfo is None:
                raise _fail()
        except PostgresStartupProfileError:
            raise
        except Exception:
            raise _fail() from None
        return model
    except PostgresStartupProfileError:
        raise
    except Exception:
        raise _fail() from None


def load_postgres_startup_profiles(
    *,
    repository_root: str | Path,
    database_ids: list[str] | tuple[str, ...],
) -> list[Any]:
    """Strict-load private startup profiles in deterministic order."""
    from ..catalog.postgres import PostgresConnectionConfig
    from ..harness.recording_bundle_storage import read_private_snapshot_bytes
    from .postgres_runtime import PostgresRuntimeProfile

    try:
        if not isinstance(repository_root, (str, Path)) or not str(repository_root):
            raise _fail()
        if not isinstance(database_ids, (list, tuple)) or not database_ids:
            raise _fail()
        requested: list[str] = []
        for item in database_ids:
            if not _valid_database_id(item):
                raise _fail()
            requested.append(item)
        if len(set(requested)) != len(requested):
            raise _fail()
        ordered_ids = sorted(requested)
        seen_envs: set[str] = set()
        seen_mappings: set[str] = set()
        profiles: list[Any] = []
        for database_id in ordered_ids:
            profile_rel = Path(
                ".schema-adaptive/postgres-profiles/" + database_id + "/profile.json"
            )
            mapping_rel = Path(
                ".schema-adaptive/postgres-profiles/" + database_id + "/mapping.json"
            )
            try:
                profile_raw = read_private_snapshot_bytes(
                    Path(repository_root) / profile_rel,
                    repository_root=repository_root,
                    max_bytes=_MAX_FILE_BYTES,
                )
                mapping_raw = read_private_snapshot_bytes(
                    Path(repository_root) / mapping_rel,
                    repository_root=repository_root,
                    max_bytes=_MAX_FILE_BYTES,
                )
            except Exception:
                raise _fail() from None
            profile_doc = _parse_profile_payload(profile_raw, expected_id=database_id)
            try:
                actual_sha = hashlib.sha256(bytes(mapping_raw)).hexdigest()
            except Exception:
                raise _fail() from None
            if not hmac.compare_digest(str(profile_doc["mapping_sha256"]), actual_sha):
                raise _fail()
            mapping = _parse_mapping_profile(mapping_raw, expected_id=database_id)
            try:
                if mapping.database_id != database_id:
                    raise _fail()
            except PostgresStartupProfileError:
                raise
            except Exception:
                raise _fail() from None
            dsn_env = str(profile_doc["dsn_env"])
            if dsn_env in seen_envs:
                raise _fail()
            if mapping.database_id in seen_mappings:
                raise _fail()
            seen_envs.add(dsn_env)
            seen_mappings.add(mapping.database_id)
            try:
                dsn = os.environ.get(dsn_env)
            except Exception:
                raise _fail() from None
            if not isinstance(dsn, str) or not dsn or not dsn.strip():
                raise _fail()
            is_v5 = profile_doc["version"] == _PROFILE_VERSION_V5
            is_v4 = profile_doc["version"] == _PROFILE_VERSION_V4 or is_v5
            is_v3 = profile_doc["version"] == _PROFILE_VERSION_V3 or is_v4
            is_v2 = profile_doc["version"] == _PROFILE_VERSION_V2 or is_v3
            try:
                connection = PostgresConnectionConfig(
                    dsn,
                    schema=str(profile_doc["schema"]),
                    include_views=bool(profile_doc["include_views"]) if is_v2 else False,
                )
            except Exception:
                raise _fail() from None
            try:
                profile = PostgresRuntimeProfile(
                    database_id=database_id,
                    label=str(profile_doc["label"]),
                    connection_config=connection,
                    published_mapping=mapping,
                    field_transforms=dict(profile_doc["field_transforms"]) if is_v2 else None,
                    unsupported_fields=list(profile_doc["unsupported_fields"]) if is_v2 else (),
                    anchor_table=(
                        str(profile_doc["anchor_table"])
                        if is_v3 and profile_doc["anchor_table"] is not None
                        else None
                    ),
                    value_dictionaries=dict(profile_doc["value_dictionaries"]) if is_v4 else None,
                    extension_fields=dict(profile_doc["extension_fields"]) if is_v5 else None,
                )
            except Exception:
                raise _fail() from None
            profiles.append(profile)
        profiles.sort(key=lambda item: item.database_id)
        return profiles
    except PostgresStartupProfileError:
        raise
    except Exception:
        raise _fail() from None
