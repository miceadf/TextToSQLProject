"""Synthetic tests for Phase 7-07 Ollama constraint-aware provider.

All Ollama responses use an injected synthetic transport. No network,
live Ollama, real DB, CNU material, frontend, or legacy changes.
"""

from __future__ import annotations

import copy
import json
import socket
from concurrent.futures import CancelledError
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.adaptive.api.clarification_http import (
    CLARIFICATION_HTTP_VERSION,
    START_PATH,
    SUBMIT_PATH,
)
from backend.adaptive.api.app_factory import (
    SYNTHETIC_PROTOTYPE_DATABASE_ID,
    create_synthetic_clarification_prototype,
)
from backend.adaptive.catalog.contracts import (
    ColumnCatalog,
    DatabaseCatalog,
    ForeignKeyCatalog,
    SQLiteAffinity,
    TableCatalog,
)
from backend.adaptive.catalog.postgres import PostgresConnectionConfig
from backend.adaptive.harness import (
    DataType,
    FieldMapping,
    HarnessRequest,
    MappingProfile,
    MappingStatus,
    OllamaQueryModel,
    Ontology,
    OntologyField,
    Operator,
)
from backend.adaptive.harness.clarification import (
    database_id_fingerprint,
    ontology_fingerprint,
    request_id_fingerprint,
)
from backend.adaptive.harness.clarification_query_constraint import (
    CLARIFICATION_QUERY_CONSTRAINT_VERSION,
    ClarificationQueryConstraint,
)
from backend.adaptive.harness.ollama_constraint import (
    OLLAMA_CONSTRAINT_PROVIDER_VERSION,
    OllamaConstraintProvider,
    OllamaConstraintProviderError,
)
from backend.adaptive.harness.recorded import mapping_fingerprint
from backend.adaptive.harness.validator import validate_query_ir
from backend.adaptive.mapping.contracts import (
    PublishedFieldMapping,
    PublishedMappingProfile,
)

QUESTION_BASE = "SYNTH_Q_P707_q8m4"
PHYSICAL_TABLE = "tbl_phys_p707"
PHYSICAL_COLUMN = "col_phys_p707"
OTHER_TABLE = "tbl_other_p707"
OTHER_COLUMN = "col_other_p707"
DSN_MARKER = "postgresql://svc-user:s3cret-pw@db-host-p707:5432/synth_p707"
SECRET_MARKER = "SECRET_P707_q8m4_TOKEN"
FAKE_PATH = "C:\\synth_p707\\private\\notes.txt"
SQL_MARKER = "SELECT * FROM tbl_phys_p707"
FIXED_CODE = "OLLAMA_CONSTRAINT_PROVIDER_ERROR"


def _ontology() -> Ontology:
    return Ontology(
        entities={"Course", "Instructor"},
        fields={
            "Course.name": OntologyField(
                name="Course.name",
                data_type=DataType.STRING,
                allowed_operators={Operator.EQUALS, Operator.CONTAINS},
            ),
            "Course.credits": OntologyField(
                name="Course.credits",
                data_type=DataType.NUMBER,
                allowed_operators={Operator.EQUALS},
            ),
            "Instructor.name": OntologyField(
                name="Instructor.name",
                data_type=DataType.STRING,
                allowed_operators={Operator.EQUALS},
            ),
        },
    )


def _mapping(
    database_id: str = "synth-p707-db",
    table: str = PHYSICAL_TABLE,
    column: str = PHYSICAL_COLUMN,
) -> MappingProfile:
    return MappingProfile(
        database_id=database_id,
        version=1,
        status=MappingStatus.PUBLISHED,
        fields={
            "Course.name": FieldMapping(table=table, column=column),
            "Course.credits": FieldMapping(table=table, column="credit_hours_p707"),
            "Instructor.name": FieldMapping(table=OTHER_TABLE, column=OTHER_COLUMN),
        },
    )


def _request(
    database_id: str = "synth-p707-db",
    question: str | None = None,
) -> HarnessRequest:
    return HarnessRequest(
        request_id="req-synth-p707",
        database_id=database_id,
        question=question or f"{QUESTION_BASE} 강의 목록 보여줘",
    )


def _constraint(
    request: HarnessRequest,
    ontology: Ontology,
    mapping: MappingProfile,
    ref: str,
) -> ClarificationQueryConstraint:
    return ClarificationQueryConstraint.model_validate(
        {
            "constraint_version": CLARIFICATION_QUERY_CONSTRAINT_VERSION,
            "request_id_sha256": request_id_fingerprint(request.request_id),
            "database_id_sha256": database_id_fingerprint(request.database_id),
            "ontology_fingerprint": ontology_fingerprint(ontology),
            "mapping_sha256": mapping_fingerprint(mapping),
            "selected_semantic_ref": ref,
        }
    )


def _ir_for_ref(ref: str) -> dict[str, Any]:
    if "." in ref:
        entity = ref.split(".", 1)[0]
        return {"entity": entity, "select": [ref], "filters": [], "limit": 10}
    if ref == "Course":
        return {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 10}
    return {"entity": "Instructor", "select": ["Instructor.name"], "filters": [], "limit": 10}


class CountingTransport:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, dict[str, Any], float]] = []

    def __call__(self, url: str, payload: dict[str, Any], timeout: float) -> Any:
        self.calls.append((url, copy.deepcopy(payload), timeout))
        outcome = self.outcomes[len(self.calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _response_ir(ref: str) -> dict[str, Any]:
    return {"response": json.dumps(_ir_for_ref(ref), ensure_ascii=False)}


def _make_model(transport: Any) -> OllamaQueryModel:
    return OllamaQueryModel(
        "http://localhost:11434/", "synthetic-p707-tag", 5.0, transport=transport
    )


def _make_bridge(transport: Any) -> tuple[OllamaConstraintProvider, Any]:
    return OllamaConstraintProvider(_make_model(transport)), transport


def _prompt_context(transport: Any) -> dict[str, Any]:
    prompt = transport.calls[0][1]["prompt"]
    assert isinstance(prompt, str)
    context_raw = prompt.split("Context: ", 1)[1].split("\nQuestion:", 1)[0]
    return json.loads(context_raw), prompt


def _assert_fixed_error(exc: OllamaConstraintProviderError) -> None:
    assert exc.code == FIXED_CODE
    assert str(exc) == FIXED_CODE
    assert exc.__context__ is None


# --- bridge restriction ---


def test_field_constraint_restricts_to_single_field() -> None:
    request, ontology, mapping = _request(), _ontology(), _mapping()
    constraint = _constraint(request, ontology, mapping, "Course.name")
    transport = CountingTransport([_response_ir("Course.name")])
    bridge, _ = _make_bridge(transport)
    out = bridge.generate_ir(request, ontology, mapping, constraint)
    assert out == _ir_for_ref("Course.name")
    assert len(transport.calls) == 1
    context, prompt = _prompt_context(transport)
    assert context["entities"] == ["Course"]
    assert context["available_fields"] == ["Course.name"]
    assert [f["name"] for f in context["fields"]] == ["Course.name"]
    assert context["fields"][0]["type"] == "string"
    assert sorted(context["fields"][0]["operators"]) == ["contains", "equals"]
    for secret in (PHYSICAL_TABLE, PHYSICAL_COLUMN, OTHER_TABLE, OTHER_COLUMN):
        assert secret not in prompt
    validate_query_ir(
        validate_query_ir.__globals__["QueryIR"].model_validate(out), ontology, mapping
    )


def test_entity_constraint_includes_only_mapped_fields() -> None:
    request, ontology, mapping = _request(), _ontology(), _mapping()
    constraint = _constraint(request, ontology, mapping, "Course")
    transport = CountingTransport([_response_ir("Course")])
    bridge, _ = _make_bridge(transport)
    out = bridge.generate_ir(request, ontology, mapping, constraint)
    assert out == _ir_for_ref("Course")
    assert len(transport.calls) == 1
    context, prompt = _prompt_context(transport)
    assert context["entities"] == ["Course"]
    assert context["available_fields"] == ["Course.credits", "Course.name"]
    assert sorted(f["name"] for f in context["fields"]) == [
        "Course.credits",
        "Course.name",
    ]
    assert all(n.startswith("Course.") for n in context["available_fields"])
    assert "Instructor.name" not in context["available_fields"]
    for secret in (PHYSICAL_TABLE, PHYSICAL_COLUMN, OTHER_TABLE, OTHER_COLUMN):
        assert secret not in prompt


def test_entity_without_mapped_fields_rejected_before_transport() -> None:
    ontology = Ontology(
        entities={"Course", "Lonely"},
        fields={
            "Course.name": OntologyField(
                name="Course.name",
                data_type=DataType.STRING,
                allowed_operators={Operator.EQUALS},
            ),
        },
    )
    mapping = MappingProfile(
        database_id="synth-p707-db",
        version=1,
        status=MappingStatus.PUBLISHED,
        fields={"Course.name": FieldMapping(table="t", column="c")},
    )
    request = _request()
    constraint = ClarificationQueryConstraint.model_validate(
        {
            "constraint_version": CLARIFICATION_QUERY_CONSTRAINT_VERSION,
            "request_id_sha256": request_id_fingerprint(request.request_id),
            "database_id_sha256": database_id_fingerprint(request.database_id),
            "ontology_fingerprint": ontology_fingerprint(ontology),
            "mapping_sha256": mapping_fingerprint(mapping),
            "selected_semantic_ref": "Lonely",
        }
    )
    transport = CountingTransport([_response_ir("Course")])
    bridge, _ = _make_bridge(transport)
    with pytest.raises(OllamaConstraintProviderError) as exc_info:
        bridge.generate_ir(request, ontology, mapping, constraint)
    _assert_fixed_error(exc_info.value)
    assert transport.calls == []


def test_restricted_view_is_deterministic_and_sorted() -> None:
    request, ontology, mapping = _request(), _ontology(), _mapping()
    constraint = _constraint(request, ontology, mapping, "Course")
    first = CountingTransport([_response_ir("Course")])
    second = CountingTransport([_response_ir("Course")])
    OllamaConstraintProvider(_make_model(first)).generate_ir(
        request, ontology, mapping, constraint
    )
    OllamaConstraintProvider(_make_model(second)).generate_ir(
        request, ontology, mapping, constraint
    )
    assert first.calls[0][1]["prompt"] == second.calls[0][1]["prompt"]


# --- strict fingerprints / mismatch ---


def test_request_mapping_mismatch_and_stale_rejected() -> None:
    request, ontology, mapping = _request(), _ontology(), _mapping()
    constraint = _constraint(request, ontology, mapping, "Course.name")
    for bad_mapping in (
        mapping.model_copy(update={"database_id": "other-db"}),
        mapping.model_copy(update={"status": MappingStatus.DRAFT}),
        mapping.model_copy(update={"status": MappingStatus.STALE}),
        mapping.model_copy(update={"version": 2}),
    ):
        transport = CountingTransport([_response_ir("Course.name")])
        bridge, _ = _make_bridge(transport)
        with pytest.raises(OllamaConstraintProviderError) as exc_info:
            bridge.generate_ir(request, ontology, bad_mapping, constraint)
        _assert_fixed_error(exc_info.value)
        assert transport.calls == []


def test_fingerprint_and_version_mismatch_rejected() -> None:
    request, ontology, mapping = _request(), _ontology(), _mapping()
    good = _constraint(request, ontology, mapping, "Course.name").model_dump(
        mode="json", warnings=False
    )
    for key in (
        "request_id_sha256",
        "database_id_sha256",
        "ontology_fingerprint",
        "mapping_sha256",
    ):
        mutated = dict(good)
        mutated[key] = "0" * 64 if good[key] != "0" * 64 else "1" * 64
        transport = CountingTransport([_response_ir("Course.name")])
        bridge, _ = _make_bridge(transport)
        with pytest.raises(OllamaConstraintProviderError) as exc_info:
            bridge.generate_ir(request, ontology, mapping, mutated)
        _assert_fixed_error(exc_info.value)
        assert transport.calls == []
    mutated_version = dict(good)
    mutated_version["constraint_version"] = "clarification-query-constraint-v0"
    transport = CountingTransport([_response_ir("Course.name")])
    bridge, _ = _make_bridge(transport)
    with pytest.raises(OllamaConstraintProviderError):
        bridge.generate_ir(request, ontology, mapping, mutated_version)
    assert transport.calls == []


def test_unknown_and_unmapped_ref_rejected() -> None:
    request, ontology, mapping = _request(), _ontology(), _mapping()
    for ref in ("Course.unknown", "Unknown.name", "Unknown", "Course.name.extra"):
        constraint = ClarificationQueryConstraint.model_validate(
            {
                "constraint_version": CLARIFICATION_QUERY_CONSTRAINT_VERSION,
                "request_id_sha256": request_id_fingerprint(request.request_id),
                "database_id_sha256": database_id_fingerprint(request.database_id),
                "ontology_fingerprint": ontology_fingerprint(ontology),
                "mapping_sha256": mapping_fingerprint(mapping),
                "selected_semantic_ref": "Course.name"
                if "." in ref and ref.count(".") > 1
                else "Course.name",
            }
        )
        # Forge the ref after computing valid fingerprints via dict path.
        forged = constraint.model_dump(mode="json", warnings=False)
        forged["selected_semantic_ref"] = ref
        transport = CountingTransport([_response_ir("Course.name")])
        bridge, _ = _make_bridge(transport)
        with pytest.raises(OllamaConstraintProviderError):
            bridge.generate_ir(request, ontology, mapping, forged)
        assert transport.calls == []
    # In ontology but not in mapping.
    thin_mapping = MappingProfile(
        database_id="synth-p707-db",
        version=1,
        status=MappingStatus.PUBLISHED,
        fields={"Course.name": FieldMapping(table="t", column="c")},
    )
    thin_constraint = _constraint(request, ontology, thin_mapping, "Course.name")
    forged_thin = thin_constraint.model_dump(mode="json", warnings=False)
    forged_thin["selected_semantic_ref"] = "Instructor.name"
    transport = CountingTransport([_response_ir("Course.name")])
    bridge, _ = _make_bridge(transport)
    with pytest.raises(OllamaConstraintProviderError):
        bridge.generate_ir(request, ontology, thin_mapping, forged_thin)
    assert transport.calls == []


def test_forged_constructed_extra_coercion_wrongtype_rejected() -> None:
    request, ontology, mapping = _request(), _ontology(), _mapping()
    constraint = _constraint(request, ontology, mapping, "Course.name")
    good_request = request.model_dump(mode="json", warnings=False)
    good_mapping = mapping.model_dump(mode="json", warnings=False)
    good_constraint = constraint.model_dump(mode="json", warnings=False)
    forged_request = request.model_copy(update={"question": "   "})
    forged_mapping = mapping.model_copy(update={"status": MappingStatus.DRAFT})
    forged_constraint = ClarificationQueryConstraint.model_construct(
        constraint_version=CLARIFICATION_QUERY_CONSTRAINT_VERSION,
        request_id_sha256="0" * 64,
        database_id_sha256="0" * 64,
        ontology_fingerprint="0" * 64,
        mapping_sha256="0" * 64,
        selected_semantic_ref="Course.name",
    )
    constructed_request = HarnessRequest.model_construct(
        request_id="", database_id="synth-p707-db", question="   "
    )
    extra_request = dict(good_request)
    extra_request["injected"] = "x"
    coerced_mapping = dict(good_mapping)
    coerced_mapping["version"] = "1"
    coerced_constraint = dict(good_constraint)
    coerced_constraint["mapping_sha256"] = 123
    nested_extra_ontology = ontology.model_dump(mode="json", warnings=False)
    nested_extra_ontology["fields"]["Course.name"]["injected"] = "x"
    nested_extra_mapping = mapping.model_dump(mode="json", warnings=False)
    nested_extra_mapping["fields"]["Course.name"]["injected"] = "x"
    cases = [
        {"request": forged_request},
        {"mapping": forged_mapping},
        {"constraint": forged_constraint},
        {"request": constructed_request},
        {"request": extra_request},
        {"mapping": coerced_mapping},
        {"constraint": coerced_constraint},
        {"ontology": nested_extra_ontology},
        {"mapping": nested_extra_mapping},
        {"request": ["not", "a", "request"]},
        {"ontology": "not-an-ontology"},
        {"mapping": 12345},
        {"constraint": None},
    ]
    for kwargs in cases:
        transport = CountingTransport([_response_ir("Course.name")])
        bridge, _ = _make_bridge(transport)
        call = {
            "request": request,
            "ontology": ontology,
            "mapping": mapping,
            "constraint": constraint,
        }
        call.update(kwargs)
        with pytest.raises(OllamaConstraintProviderError) as exc_info:
            bridge.generate_ir(
                call["request"], call["ontology"], call["mapping"], call["constraint"]
            )
        _assert_fixed_error(exc_info.value)
        assert transport.calls == []


def test_bridge_rejects_non_ollama_model() -> None:
    from backend.adaptive.api.app_factory import _FixedConstraintProvider

    for bad in (object(), {"generate_ir": lambda *a: {}}, _FixedConstraintProvider()):
        with pytest.raises(OllamaConstraintProviderError) as exc_info:
            OllamaConstraintProvider(bad)
        _assert_fixed_error(exc_info.value)


# --- defensive copy / fixed error / repr ---


class _MutatingOllamaModel(OllamaQueryModel):
    def __init__(self, transport: Any) -> None:
        super().__init__(
            "http://localhost:11434/", "synthetic-p707-tag", 5.0, transport=transport
        )
        self.seen: list[tuple[Any, Any, Any]] = []

    def generate_ir(self, request: Any, ontology: Any, mapping: Any) -> Any:
        self.seen.append((request, ontology, mapping))
        try:
            object.__setattr__(request, "question", "mutated")
        except Exception:
            pass
        try:
            request.question = "mutated"  # type: ignore[union-attr]
        except Exception:
            pass
        try:
            ontology.entities.add("Mutated")  # type: ignore[union-attr]
        except Exception:
            pass
        return super().generate_ir(request, ontology, mapping)


def test_model_mutation_does_not_reach_caller() -> None:
    request, ontology, mapping = _request(), _ontology(), _mapping()
    constraint = _constraint(request, ontology, mapping, "Course.name")
    before = (
        request.model_dump(mode="json", warnings=False),
        ontology.model_dump(mode="json", warnings=False),
        mapping.model_dump(mode="json", warnings=False),
        constraint.model_dump(mode="json", warnings=False),
    )
    transport = CountingTransport([_response_ir("Course.name")])
    spy = _MutatingOllamaModel(transport)
    bridge = OllamaConstraintProvider(spy)
    out = bridge.generate_ir(request, ontology, mapping, constraint)
    assert out == _ir_for_ref("Course.name")
    assert len(transport.calls) == 1
    assert (
        request.model_dump(mode="json", warnings=False),
        ontology.model_dump(mode="json", warnings=False),
        mapping.model_dump(mode="json", warnings=False),
        constraint.model_dump(mode="json", warnings=False),
    ) == before
    assert spy.seen[0][0] is not request
    assert spy.seen[0][1] is not ontology
    assert spy.seen[0][2] is not mapping


def test_fixed_error_repr_do_not_leak(
    capsys: Any, caplog: Any
) -> None:
    request = _request(
        question=f"{QUESTION_BASE} 강의 {SECRET_MARKER} {FAKE_PATH} {SQL_MARKER}"
    )
    ontology, mapping = _ontology(), _mapping()
    constraint = _constraint(request, ontology, mapping, "Course.name")
    transport = CountingTransport([_response_ir("Course.name")])
    bridge, _ = _make_bridge(transport)
    assert repr(bridge) == (
        "OllamaConstraintProvider(version='ollama-constraint-provider-v1')"
    )
    assert str(bridge) == repr(bridge)
    assert OLLAMA_CONSTRAINT_PROVIDER_VERSION == "ollama-constraint-provider-v1"
    blob = repr(bridge) + str(bridge)
    for secret in (
        SECRET_MARKER,
        QUESTION_BASE,
        FAKE_PATH,
        PHYSICAL_TABLE,
        PHYSICAL_COLUMN,
        SQL_MARKER,
        "localhost",
        "11434",
        "synthetic-p707-tag",
    ):
        assert secret not in blob
    bad_mapping = mapping.model_copy(update={"status": MappingStatus.DRAFT})
    with pytest.raises(OllamaConstraintProviderError) as exc_info:
        bridge.generate_ir(request, ontology, bad_mapping, constraint)
    _assert_fixed_error(exc_info.value)
    error_blob = str(exc_info.value) + repr(exc_info.value)
    out, err = capsys.readouterr()
    logs = "\n".join(r.getMessage() for r in caplog.records)
    for secret in (SECRET_MARKER, QUESTION_BASE, FAKE_PATH, PHYSICAL_TABLE, SQL_MARKER):
        assert secret not in error_blob
        assert secret not in out
        assert secret not in err
        assert secret not in logs
    assert transport.calls == []


def test_transport_error_context_is_suppressed() -> None:
    request, ontology, mapping = _request(), _ontology(), _mapping()
    constraint = _constraint(request, ontology, mapping, "Course.name")
    transport = CountingTransport([TimeoutError(SECRET_MARKER)])
    bridge, _ = _make_bridge(transport)
    with pytest.raises(OllamaConstraintProviderError) as exc_info:
        bridge.generate_ir(request, ontology, mapping, constraint)
    _assert_fixed_error(exc_info.value)
    assert SECRET_MARKER not in str(exc_info.value)
    assert len(transport.calls) == 1


# --- transport matrix ---


def _run_bridge_with_outcome(outcome: Any) -> tuple[Any, CountingTransport, Any]:
    request, ontology, mapping = _request(), _ontology(), _mapping()
    constraint = _constraint(request, ontology, mapping, "Course.name")
    transport = CountingTransport([outcome])
    bridge, _ = _make_bridge(transport)
    try:
        result = bridge.generate_ir(request, ontology, mapping, constraint)
        return result, transport, None
    except OllamaConstraintProviderError as exc:
        return None, transport, exc


def test_transport_success_returns_untouched_dict() -> None:
    result, transport, error = _run_bridge_with_outcome(_response_ir("Course.name"))
    assert error is None
    assert result == _ir_for_ref("Course.name")
    assert len(transport.calls) == 1


def test_transport_non_json_returns_untouched_string() -> None:
    result, transport, error = _run_bridge_with_outcome({"response": SQL_MARKER})
    assert error is None
    assert result == SQL_MARKER
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "outcome",
    [
        {"response": ""},
        {"response": "   \n "},
        TimeoutError(SECRET_MARKER),
        socket.timeout(SECRET_MARKER),
        RuntimeError(SECRET_MARKER),
        CancelledError(SECRET_MARKER),
        {"error": SECRET_MARKER, "response": "null"},
        {"response": 5},
        [],
        {"response": '{"a":1,"a":2}'},
        {"response": "NaN"},
    ],
)
def test_transport_failure_budget_and_redaction(outcome: Any) -> None:
    result, transport, error = _run_bridge_with_outcome(outcome)
    assert result is None
    assert isinstance(error, OllamaConstraintProviderError)
    _assert_fixed_error(error)
    assert SECRET_MARKER not in str(error)
    assert len(transport.calls) == 1


def test_no_retry_or_substitution_on_transport_error() -> None:
    request, ontology, mapping = _request(), _ontology(), _mapping()
    constraint = _constraint(request, ontology, mapping, "Course.name")
    transport = CountingTransport([RuntimeError("boom"), _response_ir("Course.name")])
    bridge, _ = _make_bridge(transport)
    with pytest.raises(OllamaConstraintProviderError):
        bridge.generate_ir(request, ontology, mapping, constraint)
    assert len(transport.calls) == 1


# --- prompt secrecy + dialect independence ---


def test_prompt_contains_only_canonical_semantics() -> None:
    request = _request(question=f"{QUESTION_BASE} 강의 보여줘")
    ontology, mapping = _ontology(), _mapping()
    constraint = _constraint(request, ontology, mapping, "Course.name")
    transport = CountingTransport([_response_ir("Course.name")])
    bridge, _ = _make_bridge(transport)
    bridge.generate_ir(request, ontology, mapping, constraint)
    context, prompt = _prompt_context(transport)
    assert "Course.name" in prompt
    assert request.question in prompt
    for secret in (
        PHYSICAL_TABLE,
        PHYSICAL_COLUMN,
        OTHER_TABLE,
        OTHER_COLUMN,
        DSN_MARKER,
        "svc-user",
        "s3cret-pw",
        "db-host-p707",
        "5432",
        SQL_MARKER,
        SECRET_MARKER,
        mapping_fingerprint(mapping),
        request_id_fingerprint(request.request_id),
        database_id_fingerprint(request.database_id),
    ):
        assert secret not in prompt
    assert "sqlite" not in prompt.lower()
    assert "postgres" not in prompt.lower()
    assert context["entities"] == ["Course"]
    assert context["available_fields"] == ["Course.name"]


def test_sqlite_and_postgres_ids_share_semantic_prompt_shape() -> None:
    ontology = _ontology()
    sqlite_mapping = _mapping(database_id="synth-sqlite-p707")
    pg_mapping = _mapping(
        database_id="synth-postgres-p707",
        table="pg_courses_p707",
        column="pg_course_name_p707",
    )
    question = f"{QUESTION_BASE} 강의 보여줘"
    sqlite_request = _request(database_id="synth-sqlite-p707", question=question)
    pg_request = _request(database_id="synth-postgres-p707", question=question)
    sqlite_constraint = _constraint(
        sqlite_request, ontology, sqlite_mapping, "Course.name"
    )
    pg_constraint = _constraint(pg_request, ontology, pg_mapping, "Course.name")
    sqlite_transport = CountingTransport([_response_ir("Course.name")])
    pg_transport = CountingTransport([_response_ir("Course.name")])
    OllamaConstraintProvider(_make_model(sqlite_transport)).generate_ir(
        sqlite_request, ontology, sqlite_mapping, sqlite_constraint
    )
    OllamaConstraintProvider(_make_model(pg_transport)).generate_ir(
        pg_request, ontology, pg_mapping, pg_constraint
    )
    sqlite_context, sqlite_prompt = _prompt_context(sqlite_transport)
    pg_context, pg_prompt = _prompt_context(pg_transport)
    assert sqlite_context == pg_context
    assert sqlite_prompt == pg_prompt
    for secret in ("pg_courses_p707", "pg_course_name_p707", PHYSICAL_TABLE):
        assert secret not in sqlite_prompt
        assert secret not in pg_prompt


# --- factory ---


class SmartTransport:
    """Prompt-aware synthetic transport returning the requested field."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], float]] = []

    def __call__(self, url: str, payload: dict[str, Any], timeout: float) -> Any:
        self.calls.append((url, copy.deepcopy(payload), timeout))
        prompt = payload.get("prompt", "")
        assert isinstance(prompt, str)
        context_raw = prompt.split("Context: ", 1)[1].split("\nQuestion:", 1)[0]
        context = json.loads(context_raw)
        available = context.get("available_fields", [])
        ref = available[0] if available else "Course.name"
        return {"response": json.dumps(_ir_for_ref(ref), ensure_ascii=False)}


def _smart_transport() -> SmartTransport:
    return SmartTransport()


def test_default_factory_uses_fixed_provider_with_zero_network(
    monkeypatch: Any,
) -> None:
    import backend.adaptive.catalog.postgres as catalog_module
    import backend.adaptive.harness.postgres as harness_module

    def _explode(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("PostgreSQL dependency must not be constructed")

    monkeypatch.setattr(catalog_module, "PostgresAdapter", _explode)
    monkeypatch.setattr(harness_module, "PostgresReadOnlyExecutor", _explode)
    app = create_synthetic_clarification_prototype()
    provider = app.state.adaptive_app_provider
    assert type(provider).__name__ == "_FixedConstraintProvider"
    assert not isinstance(provider, OllamaConstraintProvider)


def test_global_app_does_not_inject_ollama() -> None:
    from backend.adaptive.api import app_factory as prototype_module

    provider = prototype_module.app.state.adaptive_app_provider
    assert type(provider).__name__ == "_FixedConstraintProvider"


def test_factory_rejects_non_ollama_model() -> None:
    from backend.adaptive.api.app_factory import _FixedConstraintProvider

    for bad in (object(), {"model": "x"}, _FixedConstraintProvider()):
        with pytest.raises(RuntimeError, match="^synthetic prototype unavailable$"):
            create_synthetic_clarification_prototype(ollama_model=bad)  # type: ignore[arg-type]


def _start(client: TestClient, database_id: str, question: str, request_id: str = "req-p707") -> Any:
    return client.post(
        START_PATH,
        json={"request_id": request_id, "database_id": database_id, "question": question},
    )


def test_injected_factory_startup_start_submit_cancel_budget() -> None:
    transport = _smart_transport()
    model = _make_model(transport)
    app = create_synthetic_clarification_prototype(ollama_model=model)
    assert isinstance(app.state.adaptive_app_provider, OllamaConstraintProvider)
    assert transport.calls == []
    client = TestClient(app, raise_server_exceptions=False)
    question = f"{QUESTION_BASE} 강의 목록 보여줘"
    started = _start(client, SYNTHETIC_PROTOTYPE_DATABASE_ID, question)
    assert started.status_code == 200
    assert started.json()["status"] == "PENDING"
    assert transport.calls == []
    data = started.json()
    target = [o for o in data["options"] if o["semantic_ref"] == "Course.name"][0]
    submitted = client.post(
        SUBMIT_PATH,
        json={"session_token": data["session_token"], "selection": [target["option_id"]]},
    )
    assert submitted.status_code == 200
    out = submitted.json()
    assert out["status"] == "EXECUTED"
    assert out["execution"]["status"] == "SUCCEEDED"
    assert out["execution"]["columns"] == ["Course.name"]
    assert len(transport.calls) == 1
    # Cancel consumes a fresh session without a model call.
    started2 = _start(
        client, SYNTHETIC_PROTOTYPE_DATABASE_ID, question, request_id="req-p707-cancel"
    ).json()
    cancelled = client.post(
        SUBMIT_PATH,
        json={"session_token": started2["session_token"], "selection": [], "cancelled": True},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert len(transport.calls) == 1
    # Reuse and invalid selection do not call the model.
    reused = client.post(
        SUBMIT_PATH,
        json={"session_token": data["session_token"], "selection": [target["option_id"]]},
    )
    assert reused.status_code == 409
    assert len(transport.calls) == 1
    started3 = _start(
        client, SYNTHETIC_PROTOTYPE_DATABASE_ID, question, request_id="req-p707-bad"
    ).json()
    bad = client.post(
        SUBMIT_PATH,
        json={"session_token": started3["session_token"], "selection": ["0" * 64]},
    )
    assert bad.status_code == 400
    assert len(transport.calls) == 1


def test_injected_factory_transport_failures_map_to_fixed_http() -> None:
    for outcome, status, code in [
        ({"response": "not-json-but-string"}, 422, "CONSTRAINT_INVALID_QUERY_IR"),
        ({"response": ""}, 502, "CONSTRAINT_PROVIDER_ERROR"),
        (TimeoutError("boom"), 502, "CONSTRAINT_PROVIDER_ERROR"),
        (RuntimeError("boom"), 502, "CONSTRAINT_PROVIDER_ERROR"),
        (
            {"response": json.dumps({"entity": "Instructor", "select": ["Instructor.name"], "filters": [], "limit": 5})},
            422,
            "CONSTRAINT_VIOLATION",
        ),
    ]:
        transport = CountingTransport([outcome])
        app = create_synthetic_clarification_prototype(ollama_model=_make_model(transport))
        client = TestClient(app, raise_server_exceptions=False)
        question = f"{QUESTION_BASE} 강의 목록 보여줘"
        data = _start(client, SYNTHETIC_PROTOTYPE_DATABASE_ID, question).json()
        target = [o for o in data["options"] if o["semantic_ref"] == "Course.name"][0]
        response = client.post(
            SUBMIT_PATH,
            json={"session_token": data["session_token"], "selection": [target["option_id"]]},
        )
        assert response.status_code == status
        assert response.json() == {"error": {"code": code}}
        assert len(transport.calls) == 1
        blob = response.text
        for secret in (QUESTION_BASE, SECRET_MARKER, "SELECT", PHYSICAL_TABLE):
            assert secret not in blob


def test_injected_factory_responses_do_not_leak(
    capsys: Any, caplog: Any
) -> None:
    transport = _smart_transport()
    app = create_synthetic_clarification_prototype(ollama_model=_make_model(transport))
    client = TestClient(app, raise_server_exceptions=False)
    question = f"{QUESTION_BASE} 강의 {SECRET_MARKER} {FAKE_PATH} 보여줘"
    started = _start(client, SYNTHETIC_PROTOTYPE_DATABASE_ID, question)
    assert started.status_code == 200
    data = started.json()
    target = data["options"][0]
    submitted = client.post(
        SUBMIT_PATH,
        json={"session_token": data["session_token"], "selection": [target["option_id"]]},
    )
    assert submitted.status_code == 200
    blob = started.text + submitted.text
    assert "SELECT" not in blob
    assert SECRET_MARKER not in blob
    assert FAKE_PATH not in blob
    assert QUESTION_BASE not in blob
    assert data["session_token"] not in submitted.text
    state_blob = repr(app.state.adaptive_app_provider) + str(
        app.state.adaptive_app_provider
    )
    for secret in (SECRET_MARKER, QUESTION_BASE, "localhost", "synthetic-p707-tag"):
        assert secret not in state_blob
    out, err = capsys.readouterr()
    logs = "\n".join(r.getMessage() for r in caplog.records)
    for secret in (SECRET_MARKER, QUESTION_BASE, FAKE_PATH):
        assert secret not in out
        assert secret not in err
        assert secret not in logs


def test_generated_ir_uses_existing_execution_path() -> None:
    transport = _smart_transport()
    app = create_synthetic_clarification_prototype(ollama_model=_make_model(transport))
    client = TestClient(app, raise_server_exceptions=False)
    question = f"{QUESTION_BASE} 강의 목록 보여줘"
    data = _start(client, SYNTHETIC_PROTOTYPE_DATABASE_ID, question).json()
    target = [o for o in data["options"] if o["semantic_ref"] == "Instructor.name"][0]
    submitted = client.post(
        SUBMIT_PATH,
        json={"session_token": data["session_token"], "selection": [target["option_id"]]},
    )
    assert submitted.status_code == 200
    out = submitted.json()
    assert out["execution"]["status"] == "SUCCEEDED"
    assert out["execution"]["columns"] == ["Instructor.name"]
    assert len(out["execution"]["rows"]) > 0
    assert all(set(row) == {"Instructor.name"} for row in out["execution"]["rows"])
    assert out["version"] == CLARIFICATION_HTTP_VERSION


# --- postgres sharing (synthetic) ---

PG_DATABASE_ID = "synth-p707-pg"
PG_DSN = "postgresql://svc-user:s3cret-pw@127.0.0.1:5432/schema_adaptive_synthetic"
PG_FINGERPRINT = "ab" * 32

PG_FIELD_MAP = {
    "Course.code": ("courses", "course_code"),
    "Course.name": ("courses", "course_name"),
    "Course.credits": ("courses", "credits"),
    "Course.category": ("courses", "category"),
    "Department.code": ("departments", "department_code"),
    "Department.name": ("departments", "department_name"),
    "Instructor.name": ("instructors", "instructor_name"),
    "CourseOffering.year": ("course_offerings", "academic_year"),
    "CourseOffering.semester": ("course_offerings", "semester"),
    "CourseOffering.section": ("course_offerings", "section"),
    "CourseOffering.capacity": ("course_offerings", "capacity"),
    "CourseOffering.enrolled": ("course_offerings", "enrolled"),
    "Schedule.day": ("schedules", "day_of_week"),
    "Schedule.start_time": ("schedules", "start_time"),
    "Schedule.end_time": ("schedules", "end_time"),
}

PG_SEMANTIC_ROW: dict[str, Any] = {
    "Course.code": "CSE101",
    "Course.name": "자료구조",
    "Course.credits": Decimal("3.0"),
    "Course.category": "전공",
    "Department.code": "CSE",
    "Department.name": "컴퓨터공학과",
    "Instructor.name": "홍길동",
    "CourseOffering.year": 2026,
    "CourseOffering.semester": "1학기",
    "CourseOffering.section": "01",
    "CourseOffering.capacity": 50,
    "CourseOffering.enrolled": 30,
    "Schedule.day": "월",
    "Schedule.start_time": "09:00",
    "Schedule.end_time": "10:30",
}


def _pg_column(name: str) -> ColumnCatalog:
    return ColumnCatalog(
        name=name,
        declared_type="text",
        affinity=SQLiteAffinity.TEXT,
        nullable=False,
        sample_values=[],
    )


def _pg_catalog() -> DatabaseCatalog:
    def _fk(column: str, table: str, ref: str, key: int) -> ForeignKeyCatalog:
        return ForeignKeyCatalog(
            id=key,
            sequence=0,
            column=column,
            referenced_table=table,
            referenced_column=ref,
            on_update="NO ACTION",
            on_delete="NO ACTION",
            match="SIMPLE",
        )

    return DatabaseCatalog(
        database_id=PG_DATABASE_ID,
        dialect="postgres",
        schema_fingerprint=PG_FINGERPRINT,
        tables=[
            TableCatalog(
                name="course_offerings",
                columns=[_pg_column(n) for n in ("offering_id", "course_code", "instructor_id", "academic_year", "semester", "section", "capacity", "enrolled")],
                primary_key=["offering_id"],
                foreign_keys=[
                    _fk("course_code", "courses", "course_code", 0),
                    _fk("instructor_id", "instructors", "instructor_id", 1),
                ],
            ),
            TableCatalog(
                name="courses",
                columns=[_pg_column(n) for n in ("course_code", "course_name", "credits", "category", "department_code")],
                primary_key=["course_code"],
                foreign_keys=[_fk("department_code", "departments", "department_code", 0)],
            ),
            TableCatalog(
                name="departments",
                columns=[_pg_column("department_code"), _pg_column("department_name")],
                primary_key=["department_code"],
                foreign_keys=[],
            ),
            TableCatalog(
                name="instructors",
                columns=[_pg_column("instructor_id"), _pg_column("instructor_name")],
                primary_key=["instructor_id"],
                foreign_keys=[],
            ),
            TableCatalog(
                name="schedules",
                columns=[_pg_column(n) for n in ("schedule_id", "offering_id", "day_of_week", "start_time", "end_time")],
                primary_key=["schedule_id"],
                foreign_keys=[_fk("offering_id", "course_offerings", "offering_id", 0)],
            ),
        ],
    )


def _pg_profile() -> Any:
    from backend.adaptive.api.postgres_runtime import PostgresRuntimeProfile

    published = PublishedMappingProfile(
        database_id=PG_DATABASE_ID,
        version=1,
        schema_fingerprint=PG_FINGERPRINT,
        ontology_version=1,
        published_by="synthetic-test",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        fields={
            semantic: PublishedFieldMapping(table=t, column=c, reviewer="synthetic-test")
            for semantic, (t, c) in PG_FIELD_MAP.items()
        },
    )
    return PostgresRuntimeProfile(
        database_id=PG_DATABASE_ID,
        label="Synthetic PostgreSQL",
        connection_config=PostgresConnectionConfig(PG_DSN, schema="private_schema"),
        published_mapping=published,
    )


class _PgAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def inspect(self, database_id: str, *, include_samples: bool = False) -> DatabaseCatalog:
        assert database_id == PG_DATABASE_ID
        assert include_samples is False
        self.calls += 1
        return _pg_catalog().model_copy(deep=True)


class _PgExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, database_id: str, compiled: Any) -> tuple[list[str], list[dict[str, Any]]]:
        assert database_id == PG_DATABASE_ID
        self.calls += 1
        columns = list(compiled.expected_columns)
        return columns, [{c: PG_SEMANTIC_ROW[c] for c in columns}]


def test_sqlite_and_postgres_share_bridge_without_mixing() -> None:
    transport = _smart_transport()
    adapter, executor = _PgAdapter(), _PgExecutor()
    app = create_synthetic_clarification_prototype(
        postgres_profiles=[_pg_profile()],
        postgres_adapters={PG_DATABASE_ID: adapter},
        postgres_executors={PG_DATABASE_ID: executor},
        ollama_model=_make_model(transport),
    )
    client = TestClient(app, raise_server_exceptions=False)
    sqlite_question = f"{QUESTION_BASE} 강의 목록 보여줘"
    sqlite_data = _start(client, SYNTHETIC_PROTOTYPE_DATABASE_ID, sqlite_question).json()
    sqlite_target = [o for o in sqlite_data["options"] if o["semantic_ref"] == "Course.name"][0]
    sqlite_out = client.post(
        SUBMIT_PATH,
        json={"session_token": sqlite_data["session_token"], "selection": [sqlite_target["option_id"]]},
    )
    assert sqlite_out.status_code == 200
    assert sqlite_out.json()["execution"]["status"] == "SUCCEEDED"
    assert sqlite_out.json()["execution"]["columns"] == ["Course.name"]
    pg_question = f"{QUESTION_BASE} 강의 목록 보여줘"
    pg_data = _start(client, PG_DATABASE_ID, pg_question, request_id="req-p707-pg").json()
    pg_target = [o for o in pg_data["options"] if o["semantic_ref"] == "Course.name"][0]
    pg_out = client.post(
        SUBMIT_PATH,
        json={"session_token": pg_data["session_token"], "selection": [pg_target["option_id"]]},
    )
    assert pg_out.status_code == 200
    assert pg_out.json()["execution"]["status"] == "SUCCEEDED"
    assert pg_out.json()["execution"]["columns"] == ["Course.name"]
    assert len(transport.calls) == 2
    first_prompt = transport.calls[0][1]["prompt"]
    second_prompt = transport.calls[1][1]["prompt"]
    first_context = json.loads(first_prompt.split("Context: ", 1)[1].split("\nQuestion:", 1)[0])
    second_context = json.loads(second_prompt.split("Context: ", 1)[1].split("\nQuestion:", 1)[0])
    assert first_context == second_context
    blob = first_prompt + second_prompt + pg_out.text
    for secret in ("s3cret-pw", "127.0.0.1", "private_schema", "course_code", "pg_courses"):
        assert secret not in blob
