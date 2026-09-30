"""Admin routes to register PostgreSQL schemas from server-owned connections (Phase 8-15).

- ``GET  /api/adaptive/v1/postgres/sources``: connection IDs and labels only.
- ``GET  /api/adaptive/v1/postgres/schemas?source_id=``: user schemas.
- ``POST /api/adaptive/v1/postgres/registrations``: register one schema.

Review goes through the existing review route, which hands PostgreSQL
registrations to :class:`PostgresRegistrationService`. A successful publish
adds the validated context to the running server's live set, so the database
is listed and answerable without a restart. DSNs never leave the server.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ..registration import postgres_registrations as pg
from .registration_upload import _summarize_mapping, _summarize_schema

SOURCES_PATH = "/api/adaptive/v1/postgres/sources"
SCHEMAS_PATH = "/api/adaptive/v1/postgres/schemas"
REGISTER_PATH = "/api/adaptive/v1/postgres/registrations"
SOURCES_VERSION = "postgres-sources-v1"
REGISTER_VERSION = "private-postgres-registration-v1"
_MAX_BODY_BYTES = 16 * 1024

_STATUS = {
    "INVALID_INPUT": 400,
    "NOT_FOUND": 404,
    "REGISTRATION_CONFLICT": 409,
    "SOURCE_UNAVAILABLE": 502,
    "INSPECTION_FAILED": 502,
}


def _error(code: str) -> JSONResponse:
    public = f"POSTGRES_REGISTRATION_{code}" if code in _STATUS else "POSTGRES_REGISTRATION_INTERNAL_ERROR"
    return JSONResponse(status_code=_STATUS.get(code, 500), content={"error": {"code": public}})


def _json(payload: dict[str, Any]) -> JSONResponse:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return JSONResponse(status_code=200, content=json.loads(text))


class PostgresRegistrationService:
    """Server-side registration, review and publish for PostgreSQL schemas."""

    def __init__(
        self,
        *,
        repository_root: str | Path,
        sources: dict[str, pg.PostgresSource],
        live_contexts: Any,
        reserved_ids: frozenset[str],
        expected_ontology_version: int,
        max_databases: int = 8,
        value_reader: Any | None = None,
        mapping_proposer: Callable[..., Any] | None = None,
        adapter_factory: Callable[[str, Any], Any] | None = None,
        executor_factory: Callable[[str, Any], Any] | None = None,
        connector: Callable[[Any], Any] | None = None,
    ) -> None:
        self.repository_root = Path(repository_root)
        self._sources = dict(sources)
        self._live = live_contexts
        self._reserved = frozenset(reserved_ids)
        self._max_databases = int(max_databases)
        self._ontology_version = int(expected_ontology_version)
        self._reader = value_reader
        self._proposer = mapping_proposer
        self._adapter_factory = adapter_factory
        self._executor_factory = executor_factory
        self._connector = connector
        self._lock = threading.Lock()

    # -- helpers -----------------------------------------------------------------------

    def handles(self, root: Path, database_id: Any) -> bool:
        return isinstance(database_id, str) and pg.registration_exists(root, database_id)

    def source(self, source_id: Any) -> pg.PostgresSource | None:
        return self._sources.get(source_id) if isinstance(source_id, str) else None

    def _adapter(self, database_id: str, config: Any) -> Any:
        if self._adapter_factory is not None:
            return self._adapter_factory(database_id, config)
        from ..catalog.postgres import PostgresAdapter

        return PostgresAdapter({database_id: config})

    def _executor(self, database_id: str, config: Any) -> Any:
        if self._executor_factory is not None:
            return self._executor_factory(database_id, config)
        from ..harness.postgres import PostgresReadOnlyExecutor

        return PostgresReadOnlyExecutor({database_id: config})

    def _local_reader(self, database_id: str, config: Any) -> Any:
        """A reader bound to this one schema, so the shared chat reader changes only on publish."""
        from ..harness.postgres_values import PostgresDistinctValueReader

        return PostgresDistinctValueReader({database_id: config}, connector=self._connector)

    def _register_reader(self, database_id: str, config: Any) -> None:
        register = getattr(self._reader, "register", None)
        if callable(register):
            register(database_id, config)

    # -- sources and registration ---------------------------------------------------------

    def source_list(self) -> list[dict[str, str]]:
        return [{"source_id": s.source_id, "label": s.label} for s in self._sources.values()]

    def schemas(self, source_id: Any) -> list[str]:
        source = self.source(source_id)
        if source is None:
            raise pg.PostgresRegistrationError("NOT_FOUND")
        return pg.list_schemas(source, connector=self._connector)

    def register(self, body: dict[str, Any]) -> dict[str, Any]:
        if set(body) != {"source_id", "schema", "database_id", "label"} or not all(
            isinstance(value, str) for value in body.values()
        ):
            raise pg.PostgresRegistrationError("INVALID_INPUT")
        source = self.source(body["source_id"])
        if source is None:
            raise pg.PostgresRegistrationError("NOT_FOUND")
        database_id = body["database_id"]
        reader = None
        if self._proposer is not None:
            try:
                reader = self._local_reader(database_id, source.config(body["schema"]))
            except Exception:
                reader = None  # no examples: the rule proposal stands
        with self._lock:
            loaded = pg.register_postgres_database(
                repository_root=self.repository_root,
                source=source,
                schema=body["schema"],
                database_id=database_id,
                label=body["label"],
                reserved_ids=self._reserved | frozenset(self._live),
                adapter_factory=self._adapter,
                value_reader=reader,
                mapping_proposer=self._proposer,
            )
        schema_summary = _summarize_schema(loaded.catalog)
        mapping_summary = _summarize_mapping(loaded.proposal, model_configured=self._proposer is not None)
        if schema_summary is None or mapping_summary is None:
            raise pg.PostgresRegistrationError("STORAGE_ERROR")
        return {
            "version": REGISTER_VERSION,
            "database_id": loaded.manifest.database_id,
            "label": loaded.manifest.label,
            "status": "PENDING_REVIEW",
            "schema_fingerprint": loaded.manifest.schema_fingerprint,
            "schema": schema_summary,
            "mapping": mapping_summary,
        }

    # -- review (same shapes as the SQLite review route) ---------------------------------

    def review_detail(self, root: Path, database_id: str) -> JSONResponse:
        from .review_publish import MappingReviewErrorCode, _detail_payload, _error_response

        try:
            loaded = pg.load_postgres_registration(repository_root=root, database_id=database_id)
            draft = pg.load_draft(repository_root=root, database_id=database_id)
            published = pg.load_published(repository_root=root, database_id=database_id)
        except pg.PostgresRegistrationError as exc:
            code = MappingReviewErrorCode.CONFLICT if exc.code == "REVIEW_CONFLICT" else MappingReviewErrorCode.NOT_FOUND
            return _error_response(code.value)
        if draft is None:
            return _error_response(MappingReviewErrorCode.NOT_FOUND.value)
        history = []
        if published is not None:
            history = [{
                "version": published.mapping_version,
                "published_by": published.published_by,
                "published_at": published.published_at,
                "active": True,
                "latest": True,
                "activatable": False,
            }]
        payload = _detail_payload(
            database_id=database_id,
            loaded_registration=loaded,
            draft=draft,
            published=published,
            mapping_version=None if published is None else published.mapping_version,
            history=history,
        )
        if payload is None:
            return _error_response(MappingReviewErrorCode.INTERNAL_ERROR.value)
        return JSONResponse(status_code=200, content=payload)

    def review_action(self, root: Path, payload: dict[str, Any]) -> JSONResponse:
        from .review_publish import (
            REVIEW_VERSION,
            MappingReviewErrorCode,
            _ApproveBody,
            _error_response,
            _IgnoreBody,
            _InitBody,
            _publish_blocked_response,
            _PublishBody,
            _RemapBody,
        )

        models = {"INIT": _InitBody, "APPROVE": _ApproveBody, "REMAP": _RemapBody, "IGNORE": _IgnoreBody, "PUBLISH": _PublishBody}
        model = models.get(payload.get("action"))  # type: ignore[arg-type]
        if model is None:
            return _error_response(MappingReviewErrorCode.INVALID_REQUEST.value)
        try:
            body = model.model_validate(payload)
        except Exception:
            return _error_response(MappingReviewErrorCode.INVALID_REQUEST.value)
        if payload != body.model_dump(mode="json"):
            return _error_response(MappingReviewErrorCode.INVALID_REQUEST.value)
        database_id = body.database_id
        try:
            with self._lock:
                if body.action == "INIT":
                    pg.init_review(repository_root=root, database_id=database_id, reviewer=body.reviewer)
                elif body.action == "PUBLISH":
                    return self._publish(root, database_id, body.published_by, REVIEW_VERSION)
                else:
                    pg.mutate_review(
                        repository_root=root,
                        database_id=database_id,
                        kind=body.action,
                        semantic_field=body.semantic_field,
                        reviewer=body.reviewer,
                        candidate_index=getattr(body, "candidate_index", None),
                        table=getattr(body, "table", None),
                        column=getattr(body, "column", None),
                        reason=getattr(body, "reason", None),
                    )
        except pg.PostgresRegistrationError as exc:
            if exc.code == "PUBLISH_BLOCKED":
                return _publish_blocked_response([])
            code = {
                "LIMIT_REACHED": MappingReviewErrorCode.CONFLICT,
                "INVALID_INPUT": MappingReviewErrorCode.INVALID_REQUEST,
                "REVIEW_CONFLICT": MappingReviewErrorCode.CONFLICT,
                "NOT_FOUND": MappingReviewErrorCode.NOT_FOUND,
                "ARTIFACT_MISMATCH": MappingReviewErrorCode.NOT_FOUND,
            }.get(exc.code, MappingReviewErrorCode.INTERNAL_ERROR)
            return _error_response(code.value)
        return self.review_detail(root, database_id)

    def _publish(self, root: Path, database_id: str, published_by: str, review_version: str) -> JSONResponse:
        from ..api.postgres_runtime import build_validated_contexts
        from .review_publish import _publish_blocked_response

        loaded = pg.load_postgres_registration(repository_root=root, database_id=database_id)
        source = self.source(loaded.manifest.source_id)
        if source is None:
            raise pg.PostgresRegistrationError("SOURCE_UNAVAILABLE")
        # The same cap startup applies: fixed databases plus served PostgreSQL ones.
        if database_id not in self._live and len(self._reserved) + len(self._live) >= self._max_databases:
            raise pg.PostgresRegistrationError("LIMIT_REACHED")
        config = source.config(loaded.manifest.postgres_schema)
        reader = self._local_reader(database_id, config)

        def read_values(table: str, column: str) -> list[str]:
            values = reader.read(database_id, table, column, pg.TRANSFORM_SAMPLE_LIMIT)
            return [value for value in values if isinstance(value, str)] if isinstance(values, list) else []

        def validate(profile: Any) -> Any:
            return build_validated_contexts(
                [profile],
                adapters_by_id={database_id: self._adapter(database_id, profile.connection_config)},
                executors_by_id={database_id: self._executor(database_id, profile.connection_config)},
                expected_ontology_version=self._ontology_version,
            )[database_id]

        context, blocking = pg.publish_review(
            repository_root=root,
            database_id=database_id,
            published_by=published_by,
            source=source,
            validate=validate,
            read_values=read_values,
        )
        if context is None:
            return _publish_blocked_response(blocking)
        self._live.add(context)
        self._register_reader(database_id, config)  # chat value hints for the new database
        record = pg.load_published(repository_root=root, database_id=database_id)
        return _json({
            "version": review_version,
            "database_id": database_id,
            "status": "PUBLISHED",
            "mapping_version": record.mapping_version if record is not None else 1,
            "registry_eligible": True,
        })


def register_postgres_registration_routes(app: FastAPI, service: PostgresRegistrationService) -> None:
    app.state.postgres_registration_service = service

    @app.get(SOURCES_PATH)
    async def list_sources(_request: Request) -> JSONResponse:
        try:
            return _json({"version": SOURCES_VERSION, "sources": service.source_list()})
        except Exception:
            return _error("INTERNAL_ERROR")

    @app.get(SCHEMAS_PATH)
    async def list_source_schemas(http_request: Request) -> JSONResponse:
        try:
            params = dict(http_request.query_params)
            if set(params) != {"source_id"}:
                return _error("INVALID_INPUT")
            schemas = await run_in_threadpool(service.schemas, params["source_id"])
            return _json({"version": SOURCES_VERSION, "source_id": params["source_id"], "schemas": schemas})
        except pg.PostgresRegistrationError as exc:
            return _error(exc.code)
        except Exception:
            return _error("INTERNAL_ERROR")

    @app.post(REGISTER_PATH)
    async def register(http_request: Request) -> JSONResponse:
        try:
            raw = await http_request.body()
            if not raw or len(raw) > _MAX_BODY_BYTES:
                return _error("INVALID_INPUT")
            from .review_publish import _parse_strict_body

            body = _parse_strict_body(bytes(raw))
            if not isinstance(body, dict):
                return _error("INVALID_INPUT")
            return _json(await run_in_threadpool(service.register, body))
        except pg.PostgresRegistrationError as exc:
            return _error(exc.code)
        except Exception:
            return _error("INTERNAL_ERROR")
