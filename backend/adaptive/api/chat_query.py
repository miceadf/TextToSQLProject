"""QueryIR chatbot query endpoint for server-owned PostgreSQL profiles (Phase 7-13).

One natural-language question becomes exactly one model call that returns
QueryIR. The server keeps the model's entity, filters, and limit, replaces
the projection with the fixed course-card fields the database maps, and
runs it through the existing validation, compiler, AST policy, and
read-only executor. Questions about canonical fields a profile declares
unsupported are answered as UNSUPPORTED without execution, and so are
questions naming a concept outside the common ontology that the database
declares no extension field for (Phase 8-20).

Responses carry canonical course-card keys and semantic labels. Phase 8-14:
the development SQL display option also includes the executed SQL with
physical identifiers and values filled in for inspection. DSNs and raw
exception text are not returned. Nothing is logged.
"""

from __future__ import annotations

import inspect
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
from ..mapping.transforms import FieldTransform
from ..mapping.values import ValueDictionary, bind_filter_value, time_label_values
from ..ontology.contracts import Operator
from ..ontology.loader import load_default_ontology
from ..ontology.outside_concepts import default_outside_concepts
from .postgres_runtime import DatabaseRoutingExecutor, is_postgres_context_current
from .unknown_conditions import mentioned_fields, outside_concepts, output_filter_values
from .time_hints import apply_time_conditions, find_time_conditions
from .unmatched_terms import find_unmatched_terms
from .value_hints import MAX_HINTS, ChatValueIndexes, repair_output
from .review_publish import _parse_strict_body, _read_limited_body
from ..mapping.extensions import DATA_TYPES as EXTENSION_TYPES
from ..mapping.extensions import dto_key as extension_dto_key
from ..mapping.extensions import operators_for as extension_operators
from .database_registry import _REF_TO_DTO_KEY, CARD_FIELDS, is_valid_database_id
from .chat_models import (
    CHAT_MODELS_PATH,
    CHAT_MODELS_VERSION,
    DEFAULT_MODEL_ID,
    MODEL_ID_PATTERN,
    MODEL_LABELS,
    ChatModelChoice,
    validate_choices,
)

CHAT_QUERY_PATH = "/api/adaptive/v1/chat/query"
CHAT_QUERY_VERSION = "adaptive-chat-query-v1"
MAX_QUESTION_LENGTH = 500
# Longest text cell a response may carry; the frontend parser uses the same cap.
MAX_TEXT_LENGTH = 1000
# Phase 7-16 server policies (user decision after the 7-15 comparison):
# the row cap is a display rule, so the server fixes it at the executor
# maximum instead of the model's (always prompt-example) limit; and
# name-like strings are matched by containment, since users type partial
# names ("수학과" for "자연과학대학 수학과").
CHAT_ROW_LIMIT = 200
CONTAINS_FIELDS = frozenset({"Course.name", "Department.name", "Instructor.name", "Course.category"})
# Phase 8-14 (user decision): a development option shows the executed SQL in
# the chat, like the legacy chatbot. Off unless the server enables it.
MAX_SQL_PREVIEW = 20000
# Phase 8-20: an empty result re-runs each equality or containment condition
# alone (one row, same compiler, policy and read-only executor) to say which
# value the database does not hold.
MAX_DIAGNOSIS_PROBES = 4
_PROBED_OPERATORS = frozenset({Operator.EQUALS, Operator.CONTAINS})

_REQUEST_KEYS = frozenset({"database_id", "query"})
# Phase 9-08: the chat model the user picked, by the server's choice id.
_OPTIONAL_REQUEST_KEYS = frozenset({"model"})
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


def _domain_ontology() -> Any:
    return load_default_ontology()


def _runtime_ontology(extensions: dict[str, Any] | None = None) -> tuple[Ontology, dict[str, str]]:
    """Common fields plus this database's extension fields (Phase 8-19)."""
    domain = load_default_ontology()
    fields = {
        name: OntologyField(
            name=name, data_type=field.data_type, allowed_operators=field.allowed_operators
        )
        for name, field in domain.active_fields.items()
    }
    labels = {name: field.label for name, field in domain.fields.items()}
    for name, extension in (extensions or {}).items():
        # Only extension fields carry a label into the prompt: the model has
        # no other way to tie "강의실" to Schedule.ext_room.
        fields[name] = OntologyField(
            name=name,
            data_type=EXTENSION_TYPES[extension.data_type],
            allowed_operators=set(extension_operators(extension.data_type)),
            label=extension.label,
        )
        labels[name] = extension.label
    ontology = Ontology(entities={entity.name for entity in domain.entities}, fields=fields)
    return ontology, labels


def _extensions_of(context: Any) -> dict[str, Any]:
    reader = getattr(context, "extension_fields", None)
    return dict(reader()) if callable(reader) else {}


def _card_key(field: str) -> str:
    return _REF_TO_DTO_KEY.get(field) or extension_dto_key(field)


class _FixedOutputModel:
    """Replays the already obtained model output; never calls a provider."""

    def __init__(self, output: dict[str, Any]) -> None:
        self._output = output

    def generate_ir(self, request: Any, ontology: Any, mapping: Any) -> dict[str, Any]:
        del request, ontology, mapping
        return self._output


class _RecordingExecutor:
    """Remembers the compiled query that actually reached the executor."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.compiled: Any = None

    def execute(self, database_id: str, compiled: Any) -> Any:
        self.compiled = compiled
        return self._inner.execute(database_id, compiled)


def _sql_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        if "\\" in value:
            # E'' strings read backslashes the same way under every
            # standard_conforming_strings setting (LIKE escapes use them).
            return "E'" + value.replace("\\", "\\\\").replace("'", "''") + "'"
        return "'" + value.replace("'", "''") + "'"
    raise ValueError("unsupported parameter")


def render_sql_preview(sql: str, parameters: list[Any]) -> str:
    """The executed statement with its bound values written in, for reading only.

    The database received the statement and the values separately.
    """
    out: list[str] = []
    values = list(parameters)
    index = 0
    position = 0
    while position < len(sql):
        if sql.startswith("%%", position):
            out.append("%")
            position += 2
        elif sql.startswith("%s", position):
            if index >= len(values):
                raise ValueError("too few parameters")
            out.append(_sql_literal(values[index]))
            index += 1
            position += 2
        else:
            out.append(sql[position])
            position += 1
    if index != len(values):
        raise ValueError("too many parameters")
    text = "".join(out)
    # Counted in UTF-16 code units, as the browser's parser counts it.
    if len(text.encode("utf-16-le")) // 2 > MAX_SQL_PREVIEW:
        raise ValueError("preview too long")
    return text


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


def _display_value(
    transforms: dict[str, Any], dictionaries: dict[str, ValueDictionary], field: str, value: Any
) -> Any:
    """Show the filter value the compiler actually binds.

    A dictionary value is shown by its label, so a stored code never
    reaches the user.
    """
    bound = bind_filter_value(
        field, value, field_transforms=transforms, value_dictionaries=dictionaries
    )
    dictionary = dictionaries.get(field)
    return bound if dictionary is None else dictionary.label_for(bound)


def _not_understood(database_id: str, failure_code: str) -> dict[str, Any]:
    return {
        "version": CHAT_QUERY_VERSION,
        "database_id": database_id,
        "status": "NOT_UNDERSTOOD",
        "failure_code": failure_code,
    }


def _unsupported(
    database_id: str, fields: list[str], labels: dict[str, str], concepts: list[Any]
) -> dict[str, Any]:
    return {
        "version": CHAT_QUERY_VERSION,
        "database_id": database_id,
        "status": "UNSUPPORTED",
        "unsupported": [
            *({"field": field, "label": labels.get(field, field)} for field in fields),
            *({"concept": concept.key, "label": concept.label} for concept in concepts),
        ],
    }


def _empty_answer(
    database_id: str,
    card_fields: list[str],
    executed: QueryIR,
    labels: dict[str, str],
    transforms: dict[str, Any],
    dictionaries: dict[str, ValueDictionary],
) -> dict[str, Any]:
    return {
        "version": CHAT_QUERY_VERSION,
        "database_id": database_id,
        "status": "SUCCEEDED",
        "columns": [_card_key(field) for field in card_fields],
        "rows": [],
        "row_count": 0,
        "limit": executed.limit,
        "understood": {
            "filters": [
                {
                    "field": item.field,
                    "label": labels.get(item.field, item.field),
                    "operator": item.operator.value,
                    "value": _display_value(transforms, dictionaries, item.field, item.value),
                }
                for item in executed.filters
            ]
        },
    }


def _add_extensions(answer: dict[str, Any], extension_cards: list[str], extensions: dict[str, Any]) -> None:
    if extension_cards:
        answer["extensions"] = [
            {"key": extension_dto_key(name), "label": extensions[name].label} for name in extension_cards
        ]


def _unmatched_filters(
    context: Any, request: HarnessRequest, ontology: Ontology, mapping: Any, executed: QueryIR
) -> list[Any]:
    """Conditions that match no row even alone (Phase 8-20).

    A lone condition is its own probe. Otherwise each equality or
    containment condition runs alone with ``limit 1`` through the same
    harness; any failure leaves that condition out (a diagnosis, not an
    answer).
    """
    probed = [item for item in executed.filters if item.operator in _PROBED_OPERATORS]
    if len(executed.filters) == 1:
        return probed
    if not probed or len(probed) > MAX_DIAGNOSIS_PROBES:
        return []
    executor = DatabaseRoutingExecutor(_NoSqlite(), {request.database_id: context})
    unmatched = []
    for item in probed:
        probe = {
            "entity": executed.entity,
            "select": [item.field],
            "filters": [item.model_dump(mode="json")],
            "limit": 1,
        }
        try:
            result = ExecutionHarness(
                model=_FixedOutputModel(probe),
                compiler=context.compiler(),
                sql_policy=context.policy(),
                executor=executor,
            ).run(request, ontology, mapping)
        except Exception:
            continue
        if result.status is HarnessStatus.SUCCEEDED and not result.rows:
            unmatched.append(item)
    return unmatched


# Luna 9-05: words that ask for a field without its label or aliases. When the
# question holds one, a missing field the model only selected is still
# declined: dropping it would answer "지난해 ..." over every year.
_UNLABELLED_REQUESTS = {
    "CourseOffering.year": re.compile(r"작년|재작년|지난\s*해|올해|금년|내년|연도|년도|\d{2,4}\s*년"),
}


def _drop_unasked_select(
    query_ir: QueryIR, question: str, unsupported: set[str], available: set[str]
) -> QueryIR:
    """Drop a missing field the model only selected and the question never named (Phase 9-05).

    The card replaces ``select`` anyway, so declining the whole answer for
    it was wrong: a database without sections declined most questions
    because the model often selects ``CourseOffering.section``. A field the
    question names ("분반 알려줘") or filters on is still declined.
    """
    filtered = {item.field for item in query_ir.filters}
    candidates = [field for field in query_ir.select if field in unsupported and field not in filtered]
    if not candidates:
        return query_ir
    named = set(mentioned_fields(question, _domain_ontology(), candidates))
    named |= {
        field for field in candidates if field in _UNLABELLED_REQUESTS and _UNLABELLED_REQUESTS[field].search(question)
    }
    dropped = {field for field in candidates if field not in named}
    if not dropped:
        return query_ir
    select = [field for field in query_ir.select if field not in dropped]
    if not select:
        fallback = next((field for field in CARD_FIELDS if field in available), None)
        if fallback is None:
            return query_ir
        select = [fallback]
    return query_ir.model_copy(update={"select": select})


def _takes_hints(model: Any) -> bool:
    """Whether ``generate_ir`` takes ``value_hints`` (a pre-8-13 model does not)."""
    try:
        parameters = inspect.signature(model.generate_ir).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        (item.name == "value_hints" and item.kind is not inspect.Parameter.POSITIONAL_ONLY)
        or item.kind is inspect.Parameter.VAR_KEYWORD
        for item in parameters
    )


def answer_chat_query(
    *,
    database_id: str,
    question: str,
    pg_contexts: dict[str, Any],
    model: Any,
    value_indexes: Any | None = None,
    show_sql: bool = False,
) -> dict[str, Any]:
    """Answer one question; raises ``ChatQueryError`` for non-answers.

    ``value_indexes`` (Phase 8-13) grounds the question in the database's own
    values: hints go into the one model call and closed rules repair the
    output before the unchanged validation and execution path.
    """
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
        extensions = _extensions_of(context)
        ontology, labels = _runtime_ontology(extensions)
        mapping = context.harness_mapping()
        unsupported = set(context.unsupported_fields()) | _domain_ontology().retired_fields
        request = HarnessRequest(
            request_id=f"chat-{uuid4()}", database_id=database_id, question=question
        )
    except Exception:
        raise ChatQueryError(ChatQueryErrorCode.EXECUTION_FAILED, "CONTEXT_ERROR") from None

    # A retired condition must never be silently discarded by a model.
    named_retired = mentioned_fields(question, _domain_ontology(), _domain_ontology().retired_fields)
    if _UNLABELLED_REQUESTS["CourseOffering.year"].search(question):
        named_retired.append("CourseOffering.year")
    if named_retired:
        return _unsupported(database_id, list(dict.fromkeys(named_retired)), labels, [])

    # Phase 8-25: time words are read by closed rules; a time field this
    # database lacks makes the question unsupported whatever the model says.
    try:
        timed = find_time_conditions(question)
    except Exception:
        timed = []  # the rules are optional help, never a failure
    missing_time = list(
        dict.fromkeys(
            item.field for item in timed if item.field not in mapping.fields or item.field in unsupported
        )
    )
    timed = [item for item in timed if item.field not in missing_time]

    index = None
    hints: list[Any] = []
    if value_indexes is not None:
        try:
            index = value_indexes.get(context)
            if index is not None:
                hints = index.find(question, allowed_fields=set(mapping.fields) & set(ontology.fields) - unsupported)
        except Exception:
            index, hints = None, []  # hints are optional help, never a failure

    # The single model call. Provider failures carry only a fixed code.
    time_payloads = [item.payload() for item in timed] if _takes_hints(model) else []
    payloads = [*time_payloads, *(hint.payload() for hint in hints)][:MAX_HINTS]
    try:
        if payloads:
            output = model.generate_ir(request, ontology, mapping, value_hints=payloads)
        else:
            output = model.generate_ir(request, ontology, mapping)
    except Exception as exc:
        failure = getattr(exc, "failure_code", None)
        raise ChatQueryError(
            ChatQueryErrorCode.MODEL_UNAVAILABLE,
            failure.value if isinstance(failure, RecordedFailureCode) else "PROVIDER_ERROR",
        ) from None

    try:
        output = repair_output(
            output,
            _domain_ontology(),
            index=index,
            hints=hints,
            available_fields=[
                *(field for field in CARD_FIELDS if field in mapping.fields),
                *sorted(name for name in extensions if name in mapping.fields),
            ],
        )
    except Exception:
        pass  # repairs are optional; validation below decides
    try:
        output = apply_time_conditions(output, timed)
    except Exception:
        pass

    # Phase 8-20: a concept this database has no field for is answered as
    # unsupported, whatever field the model put the condition into.
    try:
        outside = outside_concepts(
            question,
            default_outside_concepts(),
            extensions=extensions,
            filter_values=output_filter_values(output),
            hint_texts=[hint.text for hint in hints],
        )
    except Exception:
        outside = []  # the check is a guard on top; the answer path below is unchanged
    try:
        query_ir = QueryIR.model_validate(output)
    except Exception:
        if outside or missing_time:
            return _unsupported(database_id, missing_time, labels, outside)
        return _not_understood(database_id, "MODEL_INVALID_OUTPUT")


    if any(field in _domain_ontology().retired_fields for field in _referenced_fields(query_ir)):
        return _not_understood(database_id, "UNKNOWN_FIELD")
    query_ir = _drop_unasked_select(query_ir, question, unsupported, set(mapping.fields))
    requested_unsupported = [
        field for field in dict.fromkeys(_referenced_fields(query_ir)) if field in unsupported
    ]
    if outside:
        # Common fields the model added on its own are not blamed beside the concept.
        named = mentioned_fields(question, _domain_ontology(), requested_unsupported)
        return _unsupported(database_id, list(dict.fromkeys([*missing_time, *named])), labels, outside)
    if missing_time:
        return _unsupported(
            database_id, list(dict.fromkeys([*missing_time, *requested_unsupported])), labels, []
        )

    # The card projection replaces ``select``; an unknown field must not
    # disappear silently with it.
    if any(field not in ontology.fields for field in _referenced_fields(query_ir)):
        return _not_understood(database_id, "UNKNOWN_FIELD")

    if requested_unsupported:
        return _unsupported(database_id, requested_unsupported, labels, [])

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
    dictionaries = context.value_dictionaries()
    for item in query_ir.filters:
        try:
            bind_filter_value(
                item.field,
                item.value,
                field_transforms=transforms,
                value_dictionaries=dictionaries,
            )
            time_label_values(item.field, item.operator, item.value, value_dictionaries=dictionaries)
        except ValueError:
            return _not_understood(database_id, "INVALID_VALUE_TYPE")

    extension_cards = sorted(name for name in extensions if name in mapping.fields)
    card_fields = [*(field for field in CARD_FIELDS if field in mapping.fields), *extension_cards]
    text_extensions = {name for name in extension_cards if extensions[name].data_type == "string"}
    rewritten = query_ir.model_dump(mode="json")
    rewritten["select"] = card_fields
    rewritten["limit"] = CHAT_ROW_LIMIT
    for item in rewritten["filters"]:
        dictionary = dictionaries.get(item["field"])
        if dictionary is not None and dictionary.knows(item["value"]):
            # A recognised value names one stored value exactly; containment
            # would also match longer codes ("01" in "101").
            continue
        packed = transforms.get(item["field"]) == FieldTransform.PACKED_WEEKDAY  # Phase 8-21
        if (
            item["field"] in CONTAINS_FIELDS or item["field"] in text_extensions or packed
        ) and item["operator"] == Operator.EQUALS.value:
            item["operator"] = Operator.CONTAINS.value
    executed = QueryIR.model_validate(rewritten)

    # Phase 8-21: a time no period of the period table satisfies matches no
    # row; there is no SQL for an empty list, so the answer is empty as is.
    no_period = [
        item
        for item in executed.filters
        if time_label_values(item.field, item.operator, item.value, value_dictionaries=dictionaries) == ()
    ]
    if no_period:
        answer = _empty_answer(database_id, card_fields, executed, labels, transforms, dictionaries)
        terms = find_unmatched_terms(question, _domain_ontology(), executed.filters, hints=hints, timed=timed, extensions=extensions)
        if terms:
            answer["unmatched_terms"] = terms
        answer["unmatched"] = [
            {
                "field": item.field,
                "label": labels.get(item.field, item.field),
                "value": _display_value(transforms, dictionaries, item.field, item.value),
            }
            for item in no_period
        ]
        _add_extensions(answer, extension_cards, extensions)
        return answer

    recorder = _RecordingExecutor(DatabaseRoutingExecutor(_NoSqlite(), {database_id: context}))
    try:
        harness = ExecutionHarness(
            model=_FixedOutputModel(rewritten),
            compiler=context.compiler(),
            sql_policy=context.policy(),
            executor=recorder,
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
    columns = [_card_key(field) for field in card_fields]
    rows: list[dict[str, Any]] = []
    for row in result.rows:
        if not isinstance(row, dict) or set(row) != set(card_fields):
            raise ChatQueryError(ChatQueryErrorCode.EXECUTION_FAILED, "RESULT_SHAPE_MISMATCH")
        rows.append({_card_key(field): _json_value(row[field]) for field in card_fields})
    answer: dict[str, Any] = {
        "version": CHAT_QUERY_VERSION,
        "database_id": database_id,
        "status": "SUCCEEDED",
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "limit": executed.limit,
        "understood": {
            "filters": [
                {
                    "field": item.field,
                    "label": labels.get(item.field, item.field),
                    "operator": item.operator.value,
                    "value": _display_value(transforms, dictionaries, item.field, item.value),
                }
                for item in executed.filters
            ]
        },
    }
    if not rows:
        unmatched = [
            {
                "field": item.field,
                "label": labels.get(item.field, item.field),
                "value": _display_value(transforms, dictionaries, item.field, item.value),
            }
            for item in _unmatched_filters(context, request, ontology, mapping, executed)
        ]
        if unmatched:
            answer["unmatched"] = unmatched
    _add_extensions(answer, extension_cards, extensions)
    terms = find_unmatched_terms(question, _domain_ontology(), executed.filters, hints=hints, timed=timed, extensions=extensions)
    if terms:
        answer["unmatched_terms"] = terms
    if show_sql and recorder.compiled is not None:
        try:
            answer["sql"] = render_sql_preview(
                str(recorder.compiled.sql), list(recorder.compiled.parameters)
            )
        except Exception:
            pass  # a display aid; the answer stands without it
    return answer


def _parse_request(payload: dict[str, Any] | None) -> tuple[str, str, str | None]:
    if (
        payload is None
        or not _REQUEST_KEYS <= set(payload)
        or not set(payload) <= _REQUEST_KEYS | _OPTIONAL_REQUEST_KEYS
    ):
        raise ChatQueryError(ChatQueryErrorCode.INVALID_REQUEST)
    model_id = payload.get("model")
    if "model" in payload and (
        not isinstance(model_id, str) or MODEL_ID_PATTERN.fullmatch(model_id) is None
    ):
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
    return database_id, question.strip(), model_id


def _single_choice(model: Any | None) -> tuple[ChatModelChoice, ...]:
    """The pre-9-08 way: one model passed alone becomes the only choice."""
    if model is None:
        return ()
    tag = getattr(model, "_model_tag", None)
    return (
        ChatModelChoice(
            id=DEFAULT_MODEL_ID,
            label=MODEL_LABELS[DEFAULT_MODEL_ID],
            tag=tag if isinstance(tag, str) else None,  # type: ignore[arg-type]
            model=model,
        ),
    )


def register_chat_query_routes(
    app: FastAPI,
    *,
    pg_contexts: dict[str, Any],
    model: Any | None,
    value_reader: Any | None = None,
    value_indexes: ChatValueIndexes | None = None,
    show_sql: bool = False,
    models: Any = None,
) -> None:
    """``models`` (Phase 9-08) lists the choices, default first; without it
    ``model`` alone is the only choice."""
    if not isinstance(pg_contexts, dict):
        raise ValueError("pg_contexts must be a dict")
    if models is not None and model is not None:
        raise ValueError("pass model or models, not both")
    live_models = models if callable(getattr(models, "snapshot", None)) else None
    choices = _single_choice(model) if models is None else validate_choices(models.snapshot() if live_models else models)
    from .postgres_runtime import shared_or_copy

    contexts = shared_or_copy(pg_contexts)
    # Phase 8-13: value hints only with a server-owned distinct-value reader.
    value_indexes = value_indexes if value_indexes is not None else (
        None if value_reader is None else ChatValueIndexes(value_reader, load_default_ontology())
    )

    @app.get(CHAT_MODELS_PATH)
    async def chat_models() -> JSONResponse:
        current = validate_choices(live_models.snapshot()) if live_models is not None else choices
        listing = {
            "version": CHAT_MODELS_VERSION,
            "default": current[0].id if current else None,
            "models": [choice.listing() for choice in current],
        }
        return JSONResponse(status_code=200, content=listing)

    @app.post(CHAT_QUERY_PATH)
    async def chat_query(http_request: Request) -> JSONResponse:
        try:
            raw = await _read_limited_body(http_request)
            database_id, question, model_id = _parse_request(
                None if raw is None else _parse_strict_body(raw)
            )
            current = validate_choices(live_models.snapshot()) if live_models is not None else choices
            by_id = {choice.id: choice for choice in current}
            if model_id is None:
                chosen = current[0].model if current else None
            elif model_id in by_id:
                chosen = by_id[model_id].model
            else:
                raise ChatQueryError(ChatQueryErrorCode.INVALID_REQUEST)
            answer = await run_in_threadpool(
                answer_chat_query,
                database_id=database_id,
                question=question,
                pg_contexts=contexts,
                model=chosen,
                value_indexes=value_indexes,
                show_sql=bool(show_sql),
            )
            text = json.dumps(answer, ensure_ascii=False, sort_keys=True, allow_nan=False)
            return JSONResponse(status_code=200, content=json.loads(text))
        except ChatQueryError as exc:
            return _error_response(exc)
        except Exception:
            return _error_response(
                ChatQueryError(ChatQueryErrorCode.EXECUTION_FAILED, "INTERNAL_ERROR")
            )
