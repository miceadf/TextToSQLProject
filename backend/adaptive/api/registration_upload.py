"""Standalone SQLite loopback upload boundary (Phase 6-18, synthetic-only).

Exposes one registration-only POST endpoint on the synthetic prototype
app. The endpoint streams a raw SQLite body to an exclusive temporary
regular file (32 MiB enforced at the HTTP boundary), then delegates to
the Phase 6-17 ``register_database()`` / ``load_registration()`` pair
without any additional SQLite/schema/mapping inspection.

Success responses are built only from the strict reload result and
carry sample-free schema/mapping summaries. All failures use fixed
public codes with a fixed status mapping. Temporary paths, original
filenames, source/private paths, rows/samples, SQL, SQLite details,
and tracebacks are never reflected.
"""

from __future__ import annotations

import json
import os
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote_plus

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ..mapping.ai_proposal import is_ai_candidate
from ..registration import MAX_SOURCE_BYTES, REGISTRATION_STATUS
from ..registration.errors import RegistrationError, RegistrationErrorCode
from ..registration.storage import RESERVED_SELECTION_DATABASE_IDS


# v2 (Phase 8-12): ``mapping.proposer`` and the ``AI_PROPOSAL`` reason signal.
SQLITE_UPLOAD_VERSION = "private-sqlite-upload-v2"
SQLITE_UPLOAD_PATH = "/api/adaptive/v1/registrations/upload"
SQLITE_UPLOAD_CONTENT_TYPE = "application/x-sqlite3"

_CHUNK_BYTES = 64 * 1024
_FINGERPRINT_PATTERN_LEN = 64


class SqliteUploadErrorCode(str, Enum):
    INVALID_REQUEST = "SQLITE_UPLOAD_INVALID_REQUEST"
    CONFLICT = "SQLITE_UPLOAD_CONFLICT"
    TOO_LARGE = "SQLITE_UPLOAD_TOO_LARGE"
    INTERNAL_ERROR = "SQLITE_UPLOAD_INTERNAL_ERROR"


def _status_for(code: str) -> int:
    if code == SqliteUploadErrorCode.INVALID_REQUEST.value:
        return 400
    if code == SqliteUploadErrorCode.CONFLICT.value:
        return 409
    if code == SqliteUploadErrorCode.TOO_LARGE.value:
        return 413
    return 500


def _error_response(code: str) -> JSONResponse:
    return JSONResponse(status_code=_status_for(code), content={"error": {"code": code}})


def _registration_to_public_code(exc: RegistrationError) -> str:
    code = exc.code
    if code is RegistrationErrorCode.REGISTRATION_CONFLICT:
        return SqliteUploadErrorCode.CONFLICT.value
    if code is RegistrationErrorCode.SOURCE_TOO_LARGE:
        return SqliteUploadErrorCode.TOO_LARGE.value
    if code in (
        RegistrationErrorCode.INVALID_INPUT,
        RegistrationErrorCode.UNSAFE_SOURCE,
        RegistrationErrorCode.INVALID_SQLITE,
    ):
        return SqliteUploadErrorCode.INVALID_REQUEST.value
    return SqliteUploadErrorCode.INTERNAL_ERROR.value


def _parse_query_params(request: Request) -> dict[str, str] | None:
    try:
        raw = request.url.query
    except Exception:
        return None
    if not isinstance(raw, str) or raw == "":
        return None
    params: dict[str, str] = {}
    try:
        for part in raw.split("&"):
            if not part:
                return None
            if "=" in part:
                key, _, value = part.partition("=")
            else:
                key, value = part, ""
            key = unquote_plus(key)
            value = unquote_plus(value)
            if key in params:
                return None
            params[key] = value
    except Exception:
        return None
    return params


def _summarize_schema(catalog: Any) -> dict[str, Any] | None:
    try:
        tables = list(catalog.tables)
    except Exception:
        return None
    table_summaries: list[dict[str, Any]] = []
    column_count = 0
    foreign_key_count = 0
    try:
        for table in tables:
            if not isinstance(table.name, str) or not table.name:
                return None
            columns = list(table.columns)
            column_entries: list[dict[str, Any]] = []
            for column in columns:
                if not isinstance(column.name, str) or not column.name:
                    return None
                if column.sample_values:
                    return None
                column_entries.append(
                    {
                        "name": column.name,
                        "declared_type": str(column.declared_type),
                        "affinity": str(column.affinity.value if isinstance(column.affinity, Enum) else column.affinity),
                        "nullable": bool(column.nullable),
                        "primary_key_position": int(column.primary_key_position),
                    }
                )
            column_count += len(column_entries)
            foreign_keys = list(table.foreign_keys)
            foreign_key_count += len(foreign_keys)
            table_summaries.append(
                {
                    "name": table.name,
                    "column_count": len(column_entries),
                    "columns": column_entries,
                    "primary_key": [str(item) for item in list(table.primary_key)],
                    "foreign_key_count": len(foreign_keys),
                }
            )
        table_summaries.sort(key=lambda item: item["name"])
        return {
            "table_count": len(table_summaries),
            "column_count": column_count,
            "foreign_key_count": foreign_key_count,
            "tables": table_summaries,
        }
    except Exception:
        return None


def _summarize_mapping(proposal: Any, *, model_configured: bool = False) -> dict[str, Any] | None:
    try:
        candidates = dict(proposal.candidates)
        unmapped = [str(item) for item in list(proposal.unmapped_fields)]
        ontology_version = int(proposal.ontology_version)
    except Exception:
        return None
    fields: list[dict[str, Any]] = []
    try:
        for semantic_field in sorted(candidates):
            options = list(candidates[semantic_field])
            if not options:
                fields.append(
                    {
                        "semantic_field": str(semantic_field),
                        "review_status": "UNMAPPED",
                        "top_candidate": None,
                    }
                )
                continue
            top = options[0]
            if getattr(top, "semantic_field", None) != semantic_field:
                return None
            evidence: list[dict[str, Any]] = []
            for item in list(getattr(top, "evidence", [])):
                signal = getattr(item.signal, "value", item.signal)
                evidence.append(
                    {
                        "signal": str(signal),
                        "score": float(item.score),
                        "detail": str(item.detail),
                    }
                )
            review_status = getattr(top.review_status, "value", top.review_status)
            fields.append(
                {
                    "semantic_field": str(semantic_field),
                    "review_status": str(review_status),
                    "top_candidate": {
                        "table": str(top.table),
                        "column": str(top.column),
                        "confidence": float(top.confidence),
                        "reasons": evidence,
                    },
                }
            )
        has_ai = any(
            is_ai_candidate(option) for options in candidates.values() for option in options
        )
        if has_ai:
            proposer = "AI"
        else:
            proposer = "AI_UNAVAILABLE" if model_configured else "RULES"
        return {
            "ontology_version": ontology_version,
            "proposer": proposer,
            "field_count": len(fields),
            "unmapped_count": len(unmapped),
            "unmapped_fields": sorted(unmapped),
            "fields": fields,
        }
    except Exception:
        return None


def _discover_repository_root() -> Path | None:
    try:
        here = Path(__file__).resolve()
    except Exception:
        return None
    for parent in (here, *here.parents):
        try:
            if (parent / ".git").exists():
                return parent
        except Exception:
            continue
    return None


def register_sqlite_upload_routes(
    app: FastAPI,
    *,
    repository_root: str | Path | None = None,
    register_database: Callable[..., Path] | None = None,
    load_registration: Callable[..., Any] | None = None,
    mapping_proposer: Callable[..., Any] | None = None,
) -> None:
    """Attach the registration-only raw-body upload endpoint.

    ``mapping_proposer`` (Phase 8-12) lets a local model order the review
    candidates; registration then runs in a worker thread so waiting for the
    model never blocks other requests.
    """
    if repository_root is None:
        resolved_root = _discover_repository_root()
    else:
        resolved_root = Path(repository_root)
    if resolved_root is None:
        raise RuntimeError("synthetic prototype unavailable")

    if register_database is None:
        from ..registration.storage import register_database as _register

        register_fn = _register
    else:
        register_fn = register_database
    if load_registration is None:
        from ..registration.storage import load_registration as _load

        load_fn = _load
    else:
        load_fn = load_registration
    if not callable(register_fn) or not callable(load_fn):
        raise RuntimeError("synthetic prototype unavailable")
    if mapping_proposer is not None and not callable(mapping_proposer):
        raise RuntimeError("synthetic prototype unavailable")
    proposer_kwargs: dict[str, Any] = (
        {} if mapping_proposer is None else {"mapping_proposer": mapping_proposer}
    )
    try:
        app.state.sqlite_upload_repository_root = str(resolved_root)
    except Exception:
        raise RuntimeError("synthetic prototype unavailable") from None

    @app.post(SQLITE_UPLOAD_PATH)
    async def upload_registration(http_request: Request) -> JSONResponse:
        try:
            params = _parse_query_params(http_request)
            if params is None or set(params) != {"database_id", "label"}:
                return _error_response(SqliteUploadErrorCode.INVALID_REQUEST.value)
            database_id = params["database_id"]
            if database_id in RESERVED_SELECTION_DATABASE_IDS:
                return _error_response(SqliteUploadErrorCode.INVALID_REQUEST.value)
            label = params["label"]
            if not isinstance(database_id, str) or not database_id:
                return _error_response(SqliteUploadErrorCode.INVALID_REQUEST.value)
            if not isinstance(label, str) or not label:
                return _error_response(SqliteUploadErrorCode.INVALID_REQUEST.value)

            content_type = http_request.headers.get("content-type")
            if not isinstance(content_type, str):
                return _error_response(SqliteUploadErrorCode.INVALID_REQUEST.value)
            if content_type.strip().lower() != SQLITE_UPLOAD_CONTENT_TYPE:
                return _error_response(SqliteUploadErrorCode.INVALID_REQUEST.value)

            declared_raw = http_request.headers.get("content-length")
            if not isinstance(declared_raw, str) or not declared_raw.strip().isdigit():
                return _error_response(SqliteUploadErrorCode.INVALID_REQUEST.value)
            try:
                declared = int(declared_raw.strip())
            except Exception:
                return _error_response(SqliteUploadErrorCode.INVALID_REQUEST.value)
            if declared <= 0:
                return _error_response(SqliteUploadErrorCode.INVALID_REQUEST.value)
            if declared > MAX_SOURCE_BYTES:
                return _error_response(SqliteUploadErrorCode.TOO_LARGE.value)

            try:
                root = Path(str(http_request.app.state.sqlite_upload_repository_root))
            except Exception:
                return _error_response(SqliteUploadErrorCode.INTERNAL_ERROR.value)

            descriptor = -1
            tmp_name: str | None = None
            try:
                try:
                    descriptor, tmp_name = tempfile.mkstemp(prefix=".upload-", suffix=".sqlite3")
                except Exception:
                    return _error_response(SqliteUploadErrorCode.INTERNAL_ERROR.value)
                total = 0
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        descriptor = -1
                        async for chunk in http_request.stream():
                            if not chunk:
                                continue
                            total += len(chunk)
                            if total > MAX_SOURCE_BYTES:
                                return _error_response(SqliteUploadErrorCode.TOO_LARGE.value)
                            try:
                                stream.write(chunk)
                            except Exception:
                                return _error_response(SqliteUploadErrorCode.INTERNAL_ERROR.value)
                        try:
                            stream.flush()
                            os.fsync(stream.fileno())
                        except Exception:
                            pass
                except Exception:
                    return _error_response(SqliteUploadErrorCode.INTERNAL_ERROR.value)
                finally:
                    if descriptor != -1:
                        try:
                            os.close(descriptor)
                        except Exception:
                            pass
                        descriptor = -1
                if total <= 0:
                    return _error_response(SqliteUploadErrorCode.INVALID_REQUEST.value)
                if total > MAX_SOURCE_BYTES:
                    return _error_response(SqliteUploadErrorCode.TOO_LARGE.value)
                if total != declared:
                    return _error_response(SqliteUploadErrorCode.INVALID_REQUEST.value)

                assert tmp_name is not None
                try:
                    await run_in_threadpool(
                        register_fn,
                        repository_root=root,
                        source_path=tmp_name,
                        database_id=database_id,
                        label=label,
                        **proposer_kwargs,
                    )
                except RegistrationError as exc:
                    return _error_response(_registration_to_public_code(exc))
                except Exception:
                    return _error_response(SqliteUploadErrorCode.INTERNAL_ERROR.value)
                try:
                    loaded = await run_in_threadpool(
                        load_fn, repository_root=root, database_id=database_id
                    )
                except RegistrationError:
                    return _error_response(SqliteUploadErrorCode.INTERNAL_ERROR.value)
                except Exception:
                    return _error_response(SqliteUploadErrorCode.INTERNAL_ERROR.value)
                try:
                    manifest = loaded.manifest
                    if manifest.status != REGISTRATION_STATUS or manifest.status != "PENDING_REVIEW":
                        return _error_response(SqliteUploadErrorCode.INTERNAL_ERROR.value)
                    if manifest.database_id != database_id or manifest.label != label:
                        return _error_response(SqliteUploadErrorCode.INTERNAL_ERROR.value)
                    fingerprint = str(manifest.schema_fingerprint)
                    if len(fingerprint) != _FINGERPRINT_PATTERN_LEN:
                        return _error_response(SqliteUploadErrorCode.INTERNAL_ERROR.value)
                    schema_summary = _summarize_schema(loaded.catalog)
                    mapping_summary = _summarize_mapping(
                        loaded.proposal, model_configured=mapping_proposer is not None
                    )
                    if schema_summary is None or mapping_summary is None:
                        return _error_response(SqliteUploadErrorCode.INTERNAL_ERROR.value)
                    payload = {
                        "version": SQLITE_UPLOAD_VERSION,
                        "database_id": manifest.database_id,
                        "label": manifest.label,
                        "status": manifest.status,
                        "schema_fingerprint": fingerprint,
                        "schema": schema_summary,
                        "mapping": mapping_summary,
                    }
                    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
                    return JSONResponse(status_code=200, content=json.loads(text))
                except Exception:
                    return _error_response(SqliteUploadErrorCode.INTERNAL_ERROR.value)
            finally:
                if tmp_name is not None:
                    try:
                        Path(tmp_name).unlink(missing_ok=True)
                    except Exception:
                        pass
        except Exception:
            return _error_response(SqliteUploadErrorCode.INTERNAL_ERROR.value)
