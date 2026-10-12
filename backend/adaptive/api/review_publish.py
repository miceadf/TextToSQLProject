"""Private mapping review + publish boundary (Phase 6-19, synthetic-only).

Minimal surface on the loopback synthetic prototype:

- ``GET /api/adaptive/v1/registrations/review?database_id=...``
- ``POST /api/adaptive/v1/registrations/review`` with a strict JSON body
  carrying one of ``INIT`` / ``APPROVE`` / ``REMAP`` / ``PUBLISH`` /
  ``ACTIVATE`` (Phase 8-01: re-activate an earlier immutable published version) /
  ``CARRY_OVER`` (Phase 8-03: start the first review from a base's active mapping).
- ``GET /api/adaptive/v1/registrations/compare?base_database_id=...&candidate_database_id=...``
  (Phase 8-02: read-only schema comparison against the base's active mapping).

All mutations reload the server-owned registration / proposal / catalog /
draft on every call; callers cannot inject drafts, proposals, catalogs,
statuses, versions, mappings, paths, or SQL. Success payloads carry
read-only metadata only (no sample values, rows, SQL, paths, SQLite
details, or tracebacks). Failures use fixed public codes only.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import unquote_plus

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from ..mapping.ai_proposal import is_ai_candidate
from ..registration import reviews as review_storage
from ..registration.reviews import ReviewError, ReviewErrorCode


# v2 (Phase 8-12): review candidates carry ``source`` (AI or RULES).
REVIEW_VERSION = "private-mapping-review-v2"
REVIEW_PATH = "/api/adaptive/v1/registrations/review"
COMPARE_VERSION = "registration-compare-v1"
COMPARE_PATH = "/api/adaptive/v1/registrations/compare"

_MAX_BODY_BYTES = 64 * 1024


class MappingReviewErrorCode(str, Enum):
    INVALID_REQUEST = "MAPPING_REVIEW_INVALID_REQUEST"
    NOT_FOUND = "MAPPING_REVIEW_NOT_FOUND"
    CONFLICT = "MAPPING_REVIEW_CONFLICT"
    PUBLISH_BLOCKED = "MAPPING_REVIEW_PUBLISH_BLOCKED"
    INTERNAL_ERROR = "MAPPING_REVIEW_INTERNAL_ERROR"


def _status_for(code: str) -> int:
    if code == MappingReviewErrorCode.INVALID_REQUEST.value:
        return 400
    if code == MappingReviewErrorCode.NOT_FOUND.value:
        return 404
    if code == MappingReviewErrorCode.CONFLICT.value:
        return 409
    if code == MappingReviewErrorCode.PUBLISH_BLOCKED.value:
        return 422
    return 500


def _error_response(code: str) -> JSONResponse:
    return JSONResponse(status_code=_status_for(code), content={"error": {"code": code}})


def _publish_blocked_response(fields: list[str], reasons: list[dict[str, str]] | None = None) -> JSONResponse:
    """``fields`` as before; Phase 9-07 adds why each one blocks (PostgreSQL publish)."""
    safe = [item for item in fields if isinstance(item, str)][:64]
    error: dict[str, Any] = {"code": MappingReviewErrorCode.PUBLISH_BLOCKED.value, "fields": safe}
    if reasons is not None:
        error["reasons"] = [item for item in reasons if item.get("field") in safe][:64]
    return JSONResponse(status_code=_status_for(MappingReviewErrorCode.PUBLISH_BLOCKED.value), content={"error": error})


def _review_to_public_code(exc: ReviewError) -> str:
    code = exc.code
    if code is ReviewErrorCode.INVALID_INPUT:
        return MappingReviewErrorCode.INVALID_REQUEST.value
    if code is ReviewErrorCode.REVIEW_CONFLICT:
        return MappingReviewErrorCode.CONFLICT.value
    if code is ReviewErrorCode.PUBLISH_BLOCKED:
        return MappingReviewErrorCode.PUBLISH_BLOCKED.value
    if code is ReviewErrorCode.ARTIFACT_MISMATCH:
        return MappingReviewErrorCode.NOT_FOUND.value
    return MappingReviewErrorCode.INTERNAL_ERROR.value


class _InitBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    action: Literal["INIT"] = "INIT"
    database_id: str = Field(min_length=1, max_length=64)
    reviewer: str = Field(min_length=1, max_length=64)


class _ApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    action: Literal["APPROVE"] = "APPROVE"
    database_id: str = Field(min_length=1, max_length=64)
    semantic_field: str = Field(min_length=1, max_length=200)
    candidate_index: int = Field(ge=0, le=64)
    reviewer: str = Field(min_length=1, max_length=64)


class _RemapBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    action: Literal["REMAP"] = "REMAP"
    database_id: str = Field(min_length=1, max_length=64)
    semantic_field: str = Field(min_length=1, max_length=200)
    table: str = Field(min_length=1, max_length=200)
    column: str = Field(min_length=1, max_length=200)
    reviewer: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=500)


class _IgnoreBody(BaseModel):
    """Phase 8-15: the database does not hold this field (reason required)."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    action: Literal["IGNORE"] = "IGNORE"
    database_id: str = Field(min_length=1, max_length=64)
    semantic_field: str = Field(min_length=1, max_length=200)
    reviewer: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=500)


class _PublishBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    action: Literal["PUBLISH"] = "PUBLISH"
    database_id: str = Field(min_length=1, max_length=64)
    published_by: str = Field(min_length=1, max_length=64)


class _CarryOverBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    action: Literal["CARRY_OVER"] = "CARRY_OVER"
    database_id: str = Field(min_length=1, max_length=64)
    base_database_id: str = Field(min_length=1, max_length=64)
    reviewer: str = Field(min_length=1, max_length=64)


class _ActivateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    action: Literal["ACTIVATE"] = "ACTIVATE"
    database_id: str = Field(min_length=1, max_length=64)
    mapping_version: int = Field(ge=1, le=review_storage.MAX_MAPPING_VERSION)
    activated_by: str = Field(min_length=1, max_length=64)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _parse_strict_body(raw: bytes) -> dict[str, Any] | None:
    if len(raw) > _MAX_BODY_BYTES:
        return None
    try:
        payload = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


async def _read_limited_body(request: Request) -> bytes | None:
    """Read at most the review boundary limit without buffering an oversized body."""
    body = bytearray()
    try:
        async for chunk in request.stream():
            if not isinstance(chunk, bytes):
                return None
            if len(body) + len(chunk) > _MAX_BODY_BYTES:
                return None
            body.extend(chunk)
    except Exception:
        return None
    return bytes(body)


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


def _summarize_review(loaded_registration: Any, draft: Any) -> dict[str, Any] | None:
    try:
        candidates = dict(loaded_registration.proposal.candidates)
        catalog_tables = list(loaded_registration.catalog.tables)
    except Exception:
        return None
    try:
        allowed: list[dict[str, Any]] = []
        for table in catalog_tables:
            columns = [str(column.name) for column in list(table.columns)]
            allowed.append({"name": str(table.name), "columns": sorted(columns)})
        allowed.sort(key=lambda item: item["name"])
        fields: list[dict[str, Any]] = []
        from ..ontology import load_default_ontology
        retired = load_default_ontology().retired_fields
        for semantic_field in sorted(set(draft.fields) - retired):
            entry = draft.fields[semantic_field]
            options = list(candidates.get(semantic_field, []))
            parsed_options: list[dict[str, Any]] = []
            for index, option in enumerate(options):
                if getattr(option, "semantic_field", None) != semantic_field:
                    return None
                review_status = getattr(option.review_status, "value", option.review_status)
                parsed_options.append(
                    {
                        "index": index,
                        "table": str(option.table),
                        "column": str(option.column),
                        "confidence": float(option.confidence),
                        "review_status": str(review_status),
                        # Phase 8-12: who proposed this candidate.
                        "source": "AI" if is_ai_candidate(option) else "RULES",
                    }
                )
            decision = getattr(entry.decision, "value", entry.decision)
            fields.append(
                {
                    "semantic_field": str(semantic_field),
                    "decision": str(decision),
                    "table": str(entry.table) if entry.table is not None else None,
                    "column": str(entry.column) if entry.column is not None else None,
                    "candidates": parsed_options,
                }
            )
        return {
            "field_count": len(fields),
            "fields": fields,
            "allowed_tables": allowed,
        }
    except Exception:
        return None


def _version_history(root: Path, database_id: str) -> list[dict[str, Any]]:
    """Published version metadata; the active flag comes from the strict activation load."""
    try:
        versions = review_storage.list_mapping_versions(
            repository_root=root, database_id=database_id
        )
    except Exception:
        return []
    return [
        {
            "version": int(item.version),
            "published_by": str(item.published_by),
            "published_at": str(item.published_at),
            "active": bool(item.active),
            "latest": bool(item.latest),
            "activatable": bool(item.activatable),
        }
        for item in versions
    ]


def _succession_summary(root: Path, database_id: str) -> dict[str, Any] | None:
    """Display-only carry-over record (Phase 8-03); absent when missing or damaged."""
    try:
        record = review_storage.load_succession(repository_root=root, database_id=database_id)
    except Exception:
        return None
    if record is None:
        return None
    return {
        "base_database_id": str(record.base_database_id),
        "base_mapping_version": int(record.base_mapping_version),
        "carried_fields": [str(name) for name in record.carried_fields],
        "stale_fields": [
            {"semantic_field": str(item.semantic_field), "status": str(item.status)}
            for item in record.stale_fields
        ],
    }


def _detail_payload(
    *,
    database_id: str,
    loaded_registration: Any,
    draft: Any,
    published: Any,
    mapping_version: Any,
    history: list[dict[str, Any]] | None = None,
    succession: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    try:
        manifest = loaded_registration.manifest
        summary = _summarize_review(loaded_registration, draft)
        if summary is None:
            return None
        fingerprint = str(manifest.schema_fingerprint)
        if len(fingerprint) != 64:
            return None
        payload: dict[str, Any] = {
            "version": REVIEW_VERSION,
            "database_id": str(manifest.database_id),
            "label": str(manifest.label),
            "status": "PUBLISHED" if published is not None else "PENDING_REVIEW",
            "schema_fingerprint": fingerprint,
            "ontology_version": int(manifest.ontology_version),
            "review": summary,
        }
        if published is not None and mapping_version is not None:
            payload["mapping_version"] = int(mapping_version)
            entries = list(history or [])
            active = [item["version"] for item in entries if item["active"]]
            if len(active) > 1:
                return None
            payload["active_mapping_version"] = active[0] if active else None
            payload["mapping_versions"] = entries
        if succession is not None:
            payload["succession"] = succession
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        return json.loads(text)
    except Exception:
        return None


def _activation_response(root: Path, database_id: str, activation: Any) -> JSONResponse:
    """Re-load the committed activation and answer with fixed metadata only."""
    try:
        reloaded = review_storage.load_activation(repository_root=root, database_id=database_id)
    except Exception:
        return _error_response(MappingReviewErrorCode.INTERNAL_ERROR.value)
    if (
        reloaded.mapping_version != activation.mapping_version
        or reloaded.database_id != database_id
    ):
        return _error_response(MappingReviewErrorCode.INTERNAL_ERROR.value)
    try:
        content = json.dumps(
            {
                "version": REVIEW_VERSION,
                "database_id": reloaded.database_id,
                "status": "PUBLISHED",
                "mapping_version": reloaded.mapping_version,
                "registry_eligible": True,
            },
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        return JSONResponse(status_code=200, content=json.loads(content))
    except Exception:
        return _error_response(MappingReviewErrorCode.INTERNAL_ERROR.value)


class SchemaCompareErrorCode(str, Enum):
    INVALID_REQUEST = "SCHEMA_COMPARE_INVALID_REQUEST"
    NOT_FOUND = "SCHEMA_COMPARE_NOT_FOUND"
    INTERNAL_ERROR = "SCHEMA_COMPARE_INTERNAL_ERROR"


def _compare_error(code: SchemaCompareErrorCode) -> JSONResponse:
    status = {
        SchemaCompareErrorCode.INVALID_REQUEST: 400,
        SchemaCompareErrorCode.NOT_FOUND: 404,
    }.get(code, 500)
    return JSONResponse(status_code=status, content={"error": {"code": code.value}})


def _compare_payload(comparison: Any) -> dict[str, Any] | None:
    """Structural metadata only; no samples, defaults, rows, SQL, or paths."""
    try:
        diff = comparison.diff
        payload: dict[str, Any] = {
            "version": COMPARE_VERSION,
            "base_database_id": str(comparison.base_database_id),
            "candidate_database_id": str(comparison.candidate_database_id),
            "base_mapping_version": int(comparison.base_mapping_version),
            "base_fingerprint": str(diff.base_fingerprint),
            "candidate_fingerprint": str(diff.candidate_fingerprint),
            "identical": bool(diff.identical),
            "ontology_version_match": bool(comparison.ontology_version_match),
            "carry_over_executable": bool(comparison.carry_over_executable),
            "truncated": bool(diff.truncated),
            "tables_added": [str(name) for name in diff.tables_added],
            "tables_removed": [str(name) for name in diff.tables_removed],
            "column_changes": [
                item.model_dump(mode="json") for item in diff.column_changes
            ],
            "foreign_key_changes": [
                item.model_dump(mode="json") for item in diff.foreign_key_changes
            ],
            "field_impacts": [
                item.model_dump(mode="json") for item in comparison.field_impacts
            ],
        }
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        return json.loads(text)
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


def register_mapping_review_routes(
    app: FastAPI,
    *,
    repository_root: str | Path | None = None,
    postgres_service: Any | None = None,
) -> None:
    if repository_root is None:
        resolved_root = _discover_repository_root()
    else:
        resolved_root = Path(repository_root)
    if resolved_root is None:
        raise RuntimeError("synthetic prototype unavailable")
    try:
        app.state.mapping_review_repository_root = str(resolved_root)
    except Exception:
        raise RuntimeError("synthetic prototype unavailable") from None

    @app.get(REVIEW_PATH)
    async def get_review(http_request: Request) -> JSONResponse:
        try:
            params = _parse_query_params(http_request)
            if params is None or set(params) != {"database_id"}:
                return _error_response(MappingReviewErrorCode.INVALID_REQUEST.value)
            database_id = params["database_id"]
            if not isinstance(database_id, str) or not database_id:
                return _error_response(MappingReviewErrorCode.INVALID_REQUEST.value)
            try:
                root = Path(str(http_request.app.state.mapping_review_repository_root))
            except Exception:
                return _error_response(MappingReviewErrorCode.INTERNAL_ERROR.value)
            if postgres_service is not None and postgres_service.handles(root, database_id):
                return postgres_service.review_detail(root, database_id)
            try:
                from ..registration.storage import load_registration as _load_registration

                loaded_registration = _load_registration(
                    repository_root=root, database_id=database_id
                )
            except Exception:
                return _error_response(MappingReviewErrorCode.NOT_FOUND.value)
            try:
                review = review_storage.load_review(
                    repository_root=root, database_id=database_id
                )
            except ReviewError as exc:
                return _error_response(_review_to_public_code(exc))
            except Exception:
                return _error_response(MappingReviewErrorCode.INTERNAL_ERROR.value)
            payload = _detail_payload(
                database_id=database_id,
                loaded_registration=loaded_registration,
                draft=review.draft,
                published=review.published,
                mapping_version=review.mapping_version,
                history=_version_history(root, database_id),
                succession=_succession_summary(root, database_id),
            )
            if payload is None:
                return _error_response(MappingReviewErrorCode.INTERNAL_ERROR.value)
            if payload.get("database_id") != database_id:
                return _error_response(MappingReviewErrorCode.INTERNAL_ERROR.value)
            return JSONResponse(status_code=200, content=payload)
        except Exception:
            return _error_response(MappingReviewErrorCode.INTERNAL_ERROR.value)

    @app.get(COMPARE_PATH)
    async def get_compare(http_request: Request) -> JSONResponse:
        try:
            params = _parse_query_params(http_request)
            if params is None or set(params) != {"base_database_id", "candidate_database_id"}:
                return _compare_error(SchemaCompareErrorCode.INVALID_REQUEST)
            base_id = params["base_database_id"]
            candidate_id = params["candidate_database_id"]
            if not base_id or not candidate_id:
                return _compare_error(SchemaCompareErrorCode.INVALID_REQUEST)
            try:
                root = Path(str(http_request.app.state.mapping_review_repository_root))
            except Exception:
                return _compare_error(SchemaCompareErrorCode.INTERNAL_ERROR)
            try:
                comparison = review_storage.compare_registrations(
                    repository_root=root,
                    base_database_id=base_id,
                    candidate_database_id=candidate_id,
                )
            except ReviewError as exc:
                if exc.code is ReviewErrorCode.INVALID_INPUT:
                    return _compare_error(SchemaCompareErrorCode.INVALID_REQUEST)
                if exc.code is ReviewErrorCode.ARTIFACT_MISMATCH:
                    return _compare_error(SchemaCompareErrorCode.NOT_FOUND)
                return _compare_error(SchemaCompareErrorCode.INTERNAL_ERROR)
            except Exception:
                return _compare_error(SchemaCompareErrorCode.INTERNAL_ERROR)
            payload = _compare_payload(comparison)
            if (
                payload is None
                or payload["base_database_id"] != base_id
                or payload["candidate_database_id"] != candidate_id
            ):
                return _compare_error(SchemaCompareErrorCode.INTERNAL_ERROR)
            return JSONResponse(status_code=200, content=payload)
        except Exception:
            return _compare_error(SchemaCompareErrorCode.INTERNAL_ERROR)

    @app.post(REVIEW_PATH)
    async def post_review(http_request: Request) -> JSONResponse:
        try:
            raw = await _read_limited_body(http_request)
            if not isinstance(raw, (bytes, bytearray)) or len(raw) == 0:
                return _error_response(MappingReviewErrorCode.INVALID_REQUEST.value)
            payload = _parse_strict_body(bytes(raw))
            if payload is None:
                return _error_response(MappingReviewErrorCode.INVALID_REQUEST.value)
            action = payload.get("action")
            if action not in ("INIT", "APPROVE", "REMAP", "IGNORE", "PUBLISH", "ACTIVATE", "CARRY_OVER"):
                return _error_response(MappingReviewErrorCode.INVALID_REQUEST.value)
            try:
                root = Path(str(http_request.app.state.mapping_review_repository_root))
            except Exception:
                return _error_response(MappingReviewErrorCode.INTERNAL_ERROR.value)
            if postgres_service is not None and postgres_service.handles(root, payload.get("database_id")):
                if action in ("ACTIVATE", "CARRY_OVER"):
                    return _error_response(MappingReviewErrorCode.INVALID_REQUEST.value)
                return await run_in_threadpool(postgres_service.review_action, root, payload)
            try:
                if action == "INIT":
                    body = _InitBody.model_validate(payload)
                    if payload != body.model_dump(mode="json"):
                        return _error_response(MappingReviewErrorCode.INVALID_REQUEST.value)
                    review_storage.init_review(
                        repository_root=root,
                        database_id=body.database_id,
                        reviewer=body.reviewer,
                    )
                    database_id = body.database_id
                elif action == "APPROVE":
                    body = _ApproveBody.model_validate(payload)
                    if payload != body.model_dump(mode="json"):
                        return _error_response(MappingReviewErrorCode.INVALID_REQUEST.value)
                    review_storage.approve_field(
                        repository_root=root,
                        database_id=body.database_id,
                        semantic_field=body.semantic_field,
                        candidate_index=body.candidate_index,
                        reviewer=body.reviewer,
                    )
                    database_id = body.database_id
                elif action == "REMAP":
                    body = _RemapBody.model_validate(payload)
                    if payload != body.model_dump(mode="json"):
                        return _error_response(MappingReviewErrorCode.INVALID_REQUEST.value)
                    review_storage.remap_field(
                        repository_root=root,
                        database_id=body.database_id,
                        semantic_field=body.semantic_field,
                        table=body.table,
                        column=body.column,
                        reviewer=body.reviewer,
                        reason=body.reason,
                    )
                    database_id = body.database_id
                elif action == "IGNORE":
                    body = _IgnoreBody.model_validate(payload)
                    if payload != body.model_dump(mode="json"):
                        return _error_response(MappingReviewErrorCode.INVALID_REQUEST.value)
                    review_storage.ignore_field(
                        repository_root=root,
                        database_id=body.database_id,
                        semantic_field=body.semantic_field,
                        reviewer=body.reviewer,
                        reason=body.reason,
                    )
                    database_id = body.database_id
                elif action == "CARRY_OVER":
                    body = _CarryOverBody.model_validate(payload)
                    if payload != body.model_dump(mode="json"):
                        return _error_response(MappingReviewErrorCode.INVALID_REQUEST.value)
                    try:
                        review_storage.carry_over_review(
                            repository_root=root,
                            database_id=body.database_id,
                            base_database_id=body.base_database_id,
                            reviewer=body.reviewer,
                        )
                    except ReviewError as exc:
                        # Ontology mismatch has no draft field list to report.
                        return _error_response(_review_to_public_code(exc))
                    database_id = body.database_id
                elif action == "ACTIVATE":
                    body = _ActivateBody.model_validate(payload)
                    if payload != body.model_dump(mode="json"):
                        return _error_response(MappingReviewErrorCode.INVALID_REQUEST.value)
                    try:
                        activation = review_storage.activate_mapping_version(
                            repository_root=root,
                            database_id=body.database_id,
                            mapping_version=body.mapping_version,
                            activated_by=body.activated_by,
                        )
                    except ReviewError as exc:
                        # No draft field list here: the target is an immutable version.
                        return _error_response(_review_to_public_code(exc))
                    return _activation_response(root, body.database_id, activation)
                else:
                    body = _PublishBody.model_validate(payload)
                    if payload != body.model_dump(mode="json"):
                        return _error_response(MappingReviewErrorCode.INVALID_REQUEST.value)
                    try:
                        activation = review_storage.publish_review(
                            repository_root=root,
                            database_id=body.database_id,
                            published_by=body.published_by,
                        )
                    except ReviewError as exc:
                        if exc.code is ReviewErrorCode.PUBLISH_BLOCKED:
                            fields = review_storage.incomplete_field_ids(
                                repository_root=root, database_id=body.database_id
                            )
                            return _publish_blocked_response(fields)
                        return _error_response(_review_to_public_code(exc))
                    return _activation_response(root, body.database_id, activation)
            except ReviewError as exc:
                if action == "PUBLISH" and exc.code is ReviewErrorCode.PUBLISH_BLOCKED:
                    try:
                        fields = review_storage.incomplete_field_ids(
                            repository_root=root, database_id=str(payload.get("database_id", ""))
                        )
                    except Exception:
                        fields = []
                    return _publish_blocked_response(fields)
                return _error_response(_review_to_public_code(exc))
            except Exception:
                return _error_response(MappingReviewErrorCode.INVALID_REQUEST.value)
            try:
                from ..registration.storage import load_registration as _load_registration

                loaded_registration = _load_registration(
                    repository_root=root, database_id=database_id
                )
                review = review_storage.load_review(
                    repository_root=root, database_id=database_id
                )
            except ReviewError as exc:
                return _error_response(_review_to_public_code(exc))
            except Exception:
                return _error_response(MappingReviewErrorCode.INTERNAL_ERROR.value)
            detail = _detail_payload(
                database_id=database_id,
                loaded_registration=loaded_registration,
                draft=review.draft,
                published=review.published,
                mapping_version=review.mapping_version,
                history=_version_history(root, database_id),
                succession=_succession_summary(root, database_id),
            )
            if detail is None:
                return _error_response(MappingReviewErrorCode.INTERNAL_ERROR.value)
            return JSONResponse(status_code=200, content=detail)
        except Exception:
            return _error_response(MappingReviewErrorCode.INTERNAL_ERROR.value)
