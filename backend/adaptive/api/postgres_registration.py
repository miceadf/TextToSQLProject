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

from ..registration import postgres_dictionaries as dictionaries_store
from ..registration import postgres_extensions as extensions_store
from ..registration import postgres_registrations as pg
from .registration_upload import _summarize_mapping, _summarize_schema

SOURCES_PATH = "/api/adaptive/v1/postgres/sources"
SCHEMAS_PATH = "/api/adaptive/v1/postgres/schemas"
REGISTER_PATH = "/api/adaptive/v1/postgres/registrations"
DICTIONARIES_PATH = "/api/adaptive/v1/postgres/dictionaries"
# Phase 8-19: this database's extension fields.
EXTENSIONS_PATH = "/api/adaptive/v1/postgres/extensions"
EXTENSIONS_VERSION = "postgres-extensions-v1"
DICTIONARIES_VERSION = "postgres-dictionaries-v1"
SOURCES_VERSION = "postgres-sources-v1"
REGISTER_VERSION = "private-postgres-registration-v1"
REGISTRATIONS_VERSION = "postgres-registrations-v1"
_MAX_BODY_BYTES = 16 * 1024

_STATUS = {
    "INVALID_INPUT": 400,
    "INVALID_LABEL": 400,
    "NOT_FOUND": 404,
    "REGISTRATION_CONFLICT": 409,
    "SOURCE_UNAVAILABLE": 502,
    "INSPECTION_FAILED": 502,
    "LIMIT_REACHED": 409,
    "ALIAS_CONFLICT": 409,
    "ALIAS_LENGTH": 400,
    "SOURCE_CHANGED": 409,
    "EXTENSION_CONFLICT": 409,
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
        alias_model: Callable[[str, dict[str, Any]], Any] | None = None,
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
        self._alias_model = alias_model
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

    def registrations(self) -> dict[str, Any]:
        return {"version": REGISTRATIONS_VERSION, "registrations": pg.list_registrations(self.repository_root)}

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
                "SOURCE_CHANGED": MappingReviewErrorCode.CONFLICT,
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

        # Phase 8-07: the edited dictionaries, re-checked against the stored values.
        draft = pg.load_draft(repository_root=root, database_id=database_id)
        entries = dictionaries_store.load_entries(repository_root=root, database_id=database_id)
        editable = dictionaries_store.editable_fields(loaded, draft)
        stale = []
        for field_name, field_entries in entries.items():
            if field_name not in editable:
                stale.append(field_name)
                continue
            if dictionaries_store.is_period_field(field_name):
                # Phase 8-21: a period table may list periods no class uses.
                if not all(
                    dictionaries_store.valid_period_entry(entry.stored, entry.label, list(entry.aliases))
                    for entry in field_entries
                ):
                    stale.append(field_name)
                continue
            table, column = editable[field_name]
            stored = [entry.stored for entry in field_entries]
            present = reader.present(database_id, table, column, stored)
            if present is None or set(stored) - present:
                stale.append(field_name)  # missing, or not verifiable now
        if stale:
            return _publish_blocked_response(sorted(stale))
        value_dictionaries = dictionaries_store.build_dictionaries(entries, published_by)

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
            dictionaries=value_dictionaries,
            extensions=extensions_store.load_extensions(repository_root=root, database_id=database_id),
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


    # -- extension fields (Phase 8-19) ----------------------------------------------------

    def extensions(self, database_id: Any) -> dict[str, Any]:
        if not isinstance(database_id, str):
            raise pg.PostgresRegistrationError("INVALID_INPUT")
        loaded = pg.load_postgres_registration(repository_root=self.repository_root, database_id=database_id)
        stored = extensions_store.load_extensions(repository_root=self.repository_root, database_id=database_id)
        return {
            "version": EXTENSIONS_VERSION,
            "database_id": database_id,
            "fields": [
                {key: getattr(item, key) for key in ("key", "label", "entity", "table", "column", "data_type")}
                for item in stored
            ],
            "tables": extensions_store.columns(loaded),
        }

    def extension_action(self, body: dict[str, Any]) -> dict[str, Any]:
        action = body.get("action")
        allowed = {
            "SET": {"action", "database_id", "key", "label", "entity", "table", "column", "data_type", "reviewer"},
            "REMOVE": {"action", "database_id", "key", "reviewer"},
        }
        if action not in allowed or set(body) != allowed[action]:
            raise pg.PostgresRegistrationError("INVALID_INPUT")
        database_id = body["database_id"]
        reviewer = body["reviewer"]
        if not isinstance(database_id, str) or not isinstance(reviewer, str) or not 1 <= len(reviewer.strip()) <= 64 or len(reviewer) > 64:
            raise pg.PostgresRegistrationError("INVALID_INPUT")
        with self._lock:
            if action == "SET":
                extensions_store.set_extension(
                    repository_root=self.repository_root, database_id=database_id, key=body["key"],
                    label=body["label"], entity=body["entity"], table=body["table"], column=body["column"],
                    data_type=body["data_type"], reviewer=reviewer,
                )
            else:
                extensions_store.remove_extension(
                    repository_root=self.repository_root, database_id=database_id, key=body["key"]
                )
        return self.extensions(database_id)

    # -- value dictionaries (Phase 8-07) ---------------------------------------------------

    def _dictionary_context(self, database_id: Any) -> tuple[Any, Any, dict[str, tuple[str, str]], Any]:
        if not isinstance(database_id, str):
            raise pg.PostgresRegistrationError("INVALID_INPUT")
        root = self.repository_root
        loaded = pg.load_postgres_registration(repository_root=root, database_id=database_id)
        draft = pg.load_draft(repository_root=root, database_id=database_id)
        if draft is None:
            raise pg.PostgresRegistrationError("NOT_FOUND")
        source = self.source(loaded.manifest.source_id)
        if source is None:
            raise pg.PostgresRegistrationError("SOURCE_UNAVAILABLE")
        reader = self._local_reader(database_id, source.config(loaded.manifest.postgres_schema))
        return loaded, draft, dictionaries_store.editable_fields(loaded, draft), reader

    def _values(self, reader: Any, database_id: str, table: str, column: str, limit: int) -> list[str] | None:
        values = reader.read(database_id, table, column, limit)
        return [value for value in values if isinstance(value, str)] if isinstance(values, list) else None

    def dictionaries(self, database_id: Any) -> dict[str, Any]:
        if not isinstance(database_id, str):
            raise pg.PostgresRegistrationError("INVALID_INPUT")
        pg.load_postgres_registration(repository_root=self.repository_root, database_id=database_id)
        if pg.load_draft(repository_root=self.repository_root, database_id=database_id) is None:
            # Review not started: nothing is editable yet.
            return {"version": DICTIONARIES_VERSION, "database_id": database_id, "fields": []}
        _loaded, _draft, editable, reader = self._dictionary_context(database_id)
        entries = dictionaries_store.load_entries(repository_root=self.repository_root, database_id=database_id)
        from ..ontology import load_default_ontology

        labels = {name: field.label for name, field in load_default_ontology().fields.items()}
        fields = []
        for name, (table, column) in editable.items():
            if dictionaries_store.is_period_field(name):
                values = None  # Phase 8-21: any period of the timetable, typed freely
            else:
                values = self._values(reader, database_id, table, column, dictionaries_store.MAX_LISTED_VALUES)
            fields.append({
                "field": name,
                "label": labels.get(name, name),
                "values": values,
                "entries": [entry.model_dump(mode="json") for entry in entries.get(name, [])],
            })
        for name in sorted(set(entries) - set(editable)):
            # No longer editable (remapped or ignored): shown so it can be removed.
            fields.append({
                "field": name,
                "label": labels.get(name, name),
                "values": None,
                "entries": [entry.model_dump(mode="json") for entry in entries[name]],
            })
        return {"version": DICTIONARIES_VERSION, "database_id": database_id, "fields": fields}

    def dictionary_action(self, body: dict[str, Any]) -> dict[str, Any]:
        action = body.get("action")
        allowed = {
            "SET": {"action", "database_id", "field", "stored", "label", "aliases", "reviewer"},
            "REMOVE": {"action", "database_id", "field", "stored", "reviewer"},
            "SUGGEST": {"action", "database_id", "field", "ai"},
        }
        if action not in allowed or set(body) != allowed[action]:
            raise pg.PostgresRegistrationError("INVALID_INPUT")
        database_id = body["database_id"]
        loaded, _draft, editable, reader = self._dictionary_context(database_id)
        field_name = body["field"]
        reviewer = body.get("reviewer", "admin")
        if not isinstance(reviewer, str) or not 1 <= len(reviewer.strip()) <= 64 or len(reviewer) > 64:
            raise pg.PostgresRegistrationError("INVALID_INPUT")
        if action == "REMOVE" and isinstance(field_name, str):
            stored_fields = dictionaries_store.load_entries(repository_root=self.repository_root, database_id=database_id)
            if field_name in stored_fields and field_name not in editable:
                if not isinstance(body["stored"], str):
                    raise pg.PostgresRegistrationError("INVALID_INPUT")
                with self._lock:
                    dictionaries_store.remove_entry(
                        repository_root=self.repository_root, database_id=database_id, field=field_name,
                        stored=body["stored"], reviewer=reviewer,
                    )
                return self.dictionaries(database_id)
        if not isinstance(field_name, str) or field_name not in editable:
            raise pg.PostgresRegistrationError("INVALID_INPUT")
        with self._lock:
            if action == "SET":
                aliases = body["aliases"]
                if not isinstance(aliases, list) or not all(isinstance(item, str) for item in aliases):
                    raise pg.PostgresRegistrationError("INVALID_INPUT")
                if not all(isinstance(body[key], str) for key in ("stored", "label", "reviewer")):
                    raise pg.PostgresRegistrationError("INVALID_INPUT")
                table, column = editable[field_name]
                if dictionaries_store.is_period_field(field_name):
                    # Phase 8-21: a period table lists the school's whole
                    # timetable, including periods no class uses yet.
                    if not dictionaries_store.valid_period_entry(body["stored"], body["label"], aliases):
                        raise pg.PostgresRegistrationError("INVALID_INPUT")
                    known = None
                else:
                    known = self._values(reader, database_id, table, column, dictionaries_store.MAX_LISTED_VALUES)
                if known is None and not dictionaries_store.is_period_field(field_name):
                    present = reader.present(database_id, table, column, [body["stored"]])
                    if present is None or body["stored"] not in present:
                        raise pg.PostgresRegistrationError("INVALID_INPUT")
                    known = [body["stored"]]
                context = self._check_context(loaded, editable, reader, database_id)
                warnings = []
                for alias in aliases:
                    blocked, warning = dictionaries_store.check_alias(
                        alias, body["stored"], field=field_name, entries=context[1], **context[0]
                    )
                    if blocked is None:
                        blocked = self._big_field_collision(editable, context[0], reader, database_id, alias, body["stored"])
                    if blocked == dictionaries_store.ALIAS_LENGTH_REASON:
                        raise pg.PostgresRegistrationError("ALIAS_LENGTH")  # not a collision (Phase 8-17)
                    if blocked is not None:
                        raise pg.PostgresRegistrationError("ALIAS_CONFLICT")
                    if warning is not None:
                        warnings.append({"alias": alias, "warning": warning})
                dictionaries_store.set_entry(
                    repository_root=self.repository_root, database_id=database_id, field=field_name,
                    stored=body["stored"], label=body["label"], aliases=aliases, reviewer=body["reviewer"],
                    allowed_fields=set(editable), known_values=known,
                )
                result = self.dictionaries(database_id)
                result["warnings"] = warnings
                return result
            if action == "REMOVE":
                if not isinstance(body["stored"], str) or not isinstance(body["reviewer"], str):
                    raise pg.PostgresRegistrationError("INVALID_INPUT")
                dictionaries_store.remove_entry(
                    repository_root=self.repository_root, database_id=database_id, field=field_name,
                    stored=body["stored"], reviewer=body["reviewer"],
                )
                return self.dictionaries(database_id)
        # SUGGEST: candidates are returned, never stored.
        if not isinstance(body["ai"], bool):
            raise pg.PostgresRegistrationError("INVALID_INPUT")
        table, column = editable[field_name]
        values = self._values(reader, database_id, table, column, dictionaries_store.MAX_LISTED_VALUES) or []
        candidates: dict[str, list[tuple[str, str]]] = {}
        for value in values:
            for alias in dictionaries_store.rule_candidates(value):
                candidates.setdefault(value, []).append((alias, "RULE"))
        if body["ai"] and self._alias_model is not None and values:
            from ..ontology import load_default_ontology

            label = load_default_ontology().fields[field_name].label
            for value, aliases in dictionaries_store.ai_candidates(self._alias_model, label, values).items():
                for alias in aliases:
                    if all(alias != existing for existing, _source in candidates.get(value, [])):
                        candidates.setdefault(value, []).append((alias, "AI"))
        context = self._check_context(loaded, editable, reader, database_id)
        out = []
        for value in values:
            for alias, source in candidates.get(value, []):
                blocked, warning = dictionaries_store.check_alias(
                    alias, value, field=field_name, entries=context[1], **context[0]
                )
                out.append({"stored": value, "alias": alias, "source": source, "blocked": blocked, "warning": warning})
        return {
            "version": DICTIONARIES_VERSION,
            "database_id": database_id,
            "field": field_name,
            "ai_used": bool(body["ai"] and self._alias_model is not None),
            "candidates": out[:200],
        }

    def _big_field_collision(
        self,
        editable: dict[str, tuple[str, str]],
        context: dict[str, Any],
        reader: Any,
        database_id: str,
        alias: str,
        stored: str,
    ) -> str | None:
        """Exact matches in fields too large to list (their word pieces cannot be checked)."""
        for name, (table, column) in editable.items():
            if name in context["values_by_field"]:
                continue
            present = reader.present(database_id, table, column, [alias])
            if present is None:
                return "확인할 수 없음(값 조회 실패)"
            if alias in present and alias != stored:
                return f"다른 값과 겹침: {alias}"
        return None

    def _check_context(
        self, loaded: Any, editable: dict[str, tuple[str, str]], reader: Any, database_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        values_by_field = {}
        for name, (table, column) in editable.items():
            values = self._values(reader, database_id, table, column, dictionaries_store.MAX_LISTED_VALUES)
            if values is not None:
                values_by_field[name] = values
        edited = {pair for pair in editable.values()}
        other_texts: list[str] = []
        for table, column in dictionaries_store.text_columns(loaded):
            if (table, column) in edited or len(other_texts) >= dictionaries_store.MAX_OTHER_TEXT_VALUES:
                continue
            values = self._values(reader, database_id, table, column, dictionaries_store.MAX_OTHER_TEXT_VALUES)
            other_texts.extend(values or [])
        entries = dictionaries_store.load_entries(repository_root=self.repository_root, database_id=database_id)
        return {"values_by_field": values_by_field, "other_texts": other_texts[: dictionaries_store.MAX_OTHER_TEXT_VALUES]}, entries


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

    @app.get(DICTIONARIES_PATH)
    async def get_dictionaries(http_request: Request) -> JSONResponse:
        try:
            params = dict(http_request.query_params)
            if set(params) != {"database_id"}:
                return _error("INVALID_INPUT")
            return _json(await run_in_threadpool(service.dictionaries, params["database_id"]))
        except pg.PostgresRegistrationError as exc:
            return _error(exc.code)
        except Exception:
            return _error("INTERNAL_ERROR")

    @app.post(DICTIONARIES_PATH)
    async def post_dictionaries(http_request: Request) -> JSONResponse:
        try:
            raw = await http_request.body()
            if not raw or len(raw) > _MAX_BODY_BYTES:
                return _error("INVALID_INPUT")
            from .review_publish import _parse_strict_body

            body = _parse_strict_body(bytes(raw))
            if not isinstance(body, dict):
                return _error("INVALID_INPUT")
            return _json(await run_in_threadpool(service.dictionary_action, body))
        except pg.PostgresRegistrationError as exc:
            return _error(exc.code)
        except Exception:
            return _error("INTERNAL_ERROR")

    @app.get(EXTENSIONS_PATH)
    async def get_extensions(http_request: Request) -> JSONResponse:
        try:
            params = dict(http_request.query_params)
            if set(params) != {"database_id"}:
                return _error("INVALID_INPUT")
            return _json(await run_in_threadpool(service.extensions, params["database_id"]))
        except pg.PostgresRegistrationError as exc:
            return _error(exc.code)
        except Exception:
            return _error("INTERNAL_ERROR")

    @app.post(EXTENSIONS_PATH)
    async def post_extensions(http_request: Request) -> JSONResponse:
        try:
            raw = await http_request.body()
            if not raw or len(raw) > _MAX_BODY_BYTES:
                return _error("INVALID_INPUT")
            from .review_publish import _parse_strict_body

            body = _parse_strict_body(bytes(raw))
            if not isinstance(body, dict):
                return _error("INVALID_INPUT")
            return _json(await run_in_threadpool(service.extension_action, body))
        except pg.PostgresRegistrationError as exc:
            return _error(exc.code)
        except Exception:
            return _error("INTERNAL_ERROR")

    @app.get(REGISTER_PATH)
    async def list_registrations(http_request: Request) -> JSONResponse:
        try:
            if dict(http_request.query_params):
                return _error("INVALID_INPUT")
            return _json(await run_in_threadpool(service.registrations))
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
