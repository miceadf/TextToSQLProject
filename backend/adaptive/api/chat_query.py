"""QueryIR chatbot query endpoint for server-owned PostgreSQL profiles (Phase 7-13).

One natural-language question becomes exactly one model call that returns
QueryIR. The server keeps the model's entity, filters, and limit, replaces
the projection with the fixed course-card fields the database maps, and
runs it through the existing validation, compiler, AST policy, and
read-only executor. Questions about canonical fields a profile declares
unsupported are answered as UNSUPPORTED without execution.

Responses carry canonical course-card keys and semantic labels only: no
SQL, physical identifier, DSN, or exception text. Nothing is logged.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ..harness.contracts import (
    HarnessRequest,
    HarnessStage,
    HarnessStatus,
    Ontology,
    OntologyField,
    QueryIR,
)
from ..harness.engine import ExecutionHarness
from ..harness.recorded import RecordedFailureCode
from ..mapping.transforms import normalize_filter_value
from ..ontology.loader import load_default_ontology
from .postgres_runtime import DatabaseRoutingExecutor, is_postgres_context_current
from .review_publish import _parse_strict_body, _read_limited_body
from .synthetic_registry import _REF_TO_DTO_KEY, CANONICAL_SELECT, is_valid_database_id

CHAT_QUERY_PATH = "/api/adaptive/v1/chat/query"
CHAT_QUERY_VERSION = "adaptive-chat-query-v1"
MAX_QUESTION_LENGTH = 500
# Longest text cell a response may carry; the frontend parser uses the same cap.
MAX_TEXT_LENGTH = 1000

_REQUEST_KEYS = frozenset({"database_id", "query"})
_NOT_UNDERSTOOD_STAGES = frozenset({HarnessStage.IR_GENERATED, HarnessStage.IR_VALIDATED})

__all__ = [
    "CHAT_QUERY_PATH",
    "CHAT_QUERY_VERSION",
    "ChatQueryErrorCode",
    "answer_chat_query",
    "register_chat_query_routes",
]


class ChatQueryErrorCode(str, Enum):
    INVALID_REQUEST = "CHAT_INVALID_REQUEST"
    CONTEXT_UNAVAILABLE = "CHAT_CONTEXT_UNAVAILABLE"
    MODEL_UNAVAILABLE = "CHAT_MODEL_UNAVAILABLE"
    EXECUTION_FAILED = "CHAT_EXECUTION_FAILED"


_STATUS_BY_CODE = {
    ChatQueryErrorCode.INVALID_REQUEST: 400,
    ChatQueryErrorCode.CONTEXT_UNAVAILABLE: 404,
    ChatQueryErrorCode.MODEL_UNAVAILABLE: 503,
    ChatQueryErrorCode.EXECUTION_FAILED: 500,
}


class ChatQueryError(Exception):
    def __init__(self, code: ChatQueryErrorCode, failure_code: str | None = None) -> None:
        super().__init__(code.value)
        self.code = code
        self.failure_code = failure_code


def _error_response(exc: ChatQueryError) -> JSONResponse:
    error: dict[str, Any] = {"code": exc.code.value}
    if exc.failure_code is not None:
        error["failure_code"] = exc.failure_code
    return JSONResponse(status_code=_STATUS_BY_CODE[exc.code], content={"error": error})


def _runtime_ontology() -> tuple[Ontology, dict[str, str]]:
    domain = load_default_ontology()
    ontology = Ontology(
        entities={entity.name for entity in domain.entities},
        fields={
            name: OntologyField(
                name=name, data_type=field.data_type, allowed_operators=field.allowed_operators
            )
            for name, field in domain.fields.items()
        },
    )
    labels = {name: field.label for name, field in domain.fields.items()}
    return ontology, labels


class _FixedOutputModel:
    """Replays the already obtained model output; never calls a provider."""

    def __init__(self, output: dict[str, Any]) -> None:
        self._output = output

    def generate_ir(self, request: Any, ontology: Any, mapping: Any) -> dict[str, Any]:
        del request, ontology, mapping
        return self._output


class _NoSqlite:
    """The chat route only serves PostgreSQL contexts; anything else fails closed."""

    def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("chat route has no SQLite executor")


def _referenced_fields(query_ir: QueryIR) -> list[str]:
    return [*query_ir.select, *(item.field for item in query_ir.filters)]


def _reflects_boundary(
    query_ir: QueryIR, *, question: str, exact: set[str], identifiers: set[str]
) -> bool:
    """Filter values are echoed back as "understood"; keep boundaries out.

    Rejects a value that contains the whole question, equals a request
    boundary string, or contains a physical identifier as a word.
    """
    patterns = [
        re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", re.IGNORECASE)
        for name in identifiers
    ]
    for item in query_ir.filters:
        if not isinstance(item.value, str):
            continue
        value = item.value.strip()
        if value in exact or (question and question in value):
            return True
        if any(pattern.search(value) for pattern in patterns):
            return True
    return False


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) > MAX_TEXT_LENGTH:
            raise ChatQueryError(ChatQueryErrorCode.EXECUTION_FAILED, "RESULT_SHAPE_MISMATCH")
        return value
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ChatQueryError(ChatQueryErrorCode.EXECUTION_FAILED, "RESULT_SHAPE_MISMATCH")
        return value
    if isinstance(value, Decimal) and value.is_finite():
        return int(value) if value == value.to_integral_value() else float(value)
    raise ChatQueryError(ChatQueryErrorCode.EXECUTION_FAILED, "RESULT_SHAPE_MISMATCH")


def _display_value(transforms: dict[str, Any], field: str, value: Any) -> Any:
    """Show the filter value the compiler actually binds."""
    transform = transforms.get(field)
    return value if transform is None else normalize_filter_value(transform, value)


def _not_understood(database_id: str, failure_code: str) -> dict[str, Any]:
    return {
        "version": CHAT_QUERY_VERSION,
        "database_id": database_id,
        "status": "NOT_UNDERSTOOD",
        "failure_code": failure_code,
    }


def answer_chat_query(
    *,
    database_id: str,
    question: str,
    pg_contexts: dict[str, Any],
    model: Any,
) -> dict[str, Any]:
    """Answer one question; raises ``ChatQueryError`` for non-answers."""
    context = pg_contexts.get(database_id)
    if context is None:
        raise ChatQueryError(ChatQueryErrorCode.CONTEXT_UNAVAILABLE)
    if model is None or not callable(getattr(model, "generate_ir", None)):
        raise ChatQueryError(ChatQueryErrorCode.MODEL_UNAVAILABLE)
    try:
        current = is_postgres_context_current(context)
    except Exception:
        current = False
    if current is not True:
        raise ChatQueryError(ChatQueryErrorCode.CONTEXT_UNAVAILABLE)
    try:
        ontology, labels = _runtime_ontology()
        mapping = context.harness_mapping()
        unsupported = set(context.unsupported_fields())
        request = HarnessRequest(
            request_id=f"chat-{uuid4()}", database_id=database_id, question=question
        )
    except Exception:
        raise ChatQueryError(ChatQueryErrorCode.EXECUTION_FAILED, "CONTEXT_ERROR") from None

    # The single model call. Provider failures carry only a fixed code.
    try:
        output = model.generate_ir(request, ontology, mapping)
    except Exception as exc:
        failure = getattr(exc, "failure_code", None)
        raise ChatQueryError(
            ChatQueryErrorCode.MODEL_UNAVAILABLE,
            failure.value if isinstance(failure, RecordedFailureCode) else "PROVIDER_ERROR",
        ) from None

    try:
        query_ir = QueryIR.model_validate(output)
    except Exception:
        return _not_understood(database_id, "MODEL_INVALID_OUTPUT")

    # The card projection replaces ``select``; an unknown field must not
    # disappear silently with it.
    if any(field not in ontology.fields for field in _referenced_fields(query_ir)):
        return _not_understood(database_id, "UNKNOWN_FIELD")

    requested_unsupported = [
        field for field in dict.fromkeys(_referenced_fields(query_ir)) if field in unsupported
    ]
    if requested_unsupported:
        return {
            "version": CHAT_QUERY_VERSION,
            "database_id": database_id,
            "status": "UNSUPPORTED",
            "unsupported": [
                {"field": field, "label": labels.get(field, field)}
                for field in requested_unsupported
            ],
        }

    semantic_names = {*ontology.entities, *ontology.fields}
    identifiers = {
        name
        for physical in mapping.fields.values()
        for name in (physical.table, physical.column)
        if name not in semantic_names
    }
    if _reflects_boundary(
        query_ir,
        question=question.strip(),
        exact={database_id, request.request_id},
        identifiers=identifiers,
    ):
        return _not_understood(database_id, "REFLECTED_INPUT")

    transforms = context.field_transforms()
    for item in query_ir.filters:
        if item.field in transforms:
            try:
                normalize_filter_value(transforms[item.field], item.value)
            except ValueError:
                return _not_understood(database_id, "INVALID_VALUE_TYPE")

    card_fields = [field for field in CANONICAL_SELECT if field in mapping.fields]
    rewritten = query_ir.model_dump(mode="json")
    rewritten["select"] = card_fields

    try:
        harness = ExecutionHarness(
            model=_FixedOutputModel(rewritten),
            compiler=context.compiler(),
            sql_policy=context.policy(),
            executor=DatabaseRoutingExecutor(_NoSqlite(), {database_id: context}),
        )
        result = harness.run(request, ontology, mapping)
    except Exception:
        raise ChatQueryError(ChatQueryErrorCode.EXECUTION_FAILED, "HARNESS_FAILURE") from None

    if result.status is not HarnessStatus.SUCCEEDED:
        error = result.error
        code = error.code.value if error is not None else "HARNESS_FAILURE"
        if error is not None and error.stage in _NOT_UNDERSTOOD_STAGES:
            return _not_understood(database_id, code)
        raise ChatQueryError(ChatQueryErrorCode.EXECUTION_FAILED, code)

    if list(result.columns) != card_fields:
        raise ChatQueryError(ChatQueryErrorCode.EXECUTION_FAILED, "RESULT_SHAPE_MISMATCH")
    columns = [_REF_TO_DTO_KEY[field] for field in card_fields]
    rows: list[dict[str, Any]] = []
    for row in result.rows:
        if not isinstance(row, dict) or set(row) != set(card_fields):
            raise ChatQueryError(ChatQueryErrorCode.EXECUTION_FAILED, "RESULT_SHAPE_MISMATCH")
        rows.append({_REF_TO_DTO_KEY[field]: _json_value(row[field]) for field in card_fields})
    return {
        "version": CHAT_QUERY_VERSION,
        "database_id": database_id,
        "status": "SUCCEEDED",
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "limit": query_ir.limit,
        "understood": {
            "filters": [
                {
                    "field": item.field,
                    "label": labels.get(item.field, item.field),
                    "operator": item.operator.value,
                    "value": _display_value(transforms, item.field, item.value),
                }
                for item in query_ir.filters
            ]
        },
    }


def _parse_request(payload: dict[str, Any] | None) -> tuple[str, str]:
    if payload is None or set(payload) != _REQUEST_KEYS:
        raise ChatQueryError(ChatQueryErrorCode.INVALID_REQUEST)
    database_id = payload["database_id"]
    question = payload["query"]
    if not is_valid_database_id(database_id):
        raise ChatQueryError(ChatQueryErrorCode.INVALID_REQUEST)
    if (
        not isinstance(question, str)
        or not question.strip()
        or len(question) > MAX_QUESTION_LENGTH
        or any(ord(char) < 0x20 and char not in "\t\n\r" for char in question)
    ):
        raise ChatQueryError(ChatQueryErrorCode.INVALID_REQUEST)
    return database_id, question.strip()


def register_chat_query_routes(
    app: FastAPI,
    *,
    pg_contexts: dict[str, Any],
    model: Any | None,
) -> None:
    if not isinstance(pg_contexts, dict):
        raise ValueError("pg_contexts must be a dict")
    contexts = dict(pg_contexts)

    @app.post(CHAT_QUERY_PATH)
    async def chat_query(http_request: Request) -> JSONResponse:
        try:
            raw = await _read_limited_body(http_request)
            database_id, question = _parse_request(
                None if raw is None else _parse_strict_body(raw)
            )
            answer = await run_in_threadpool(
                answer_chat_query,
                database_id=database_id,
                question=question,
                pg_contexts=contexts,
                model=model,
            )
            text = json.dumps(answer, ensure_ascii=False, sort_keys=True, allow_nan=False)
            return JSONResponse(status_code=200, content=json.loads(text))
        except ChatQueryError as exc:
            return _error_response(exc)
        except Exception:
            return _error_response(
                ChatQueryError(ChatQueryErrorCode.EXECUTION_FAILED, "INTERNAL_ERROR")
            )
