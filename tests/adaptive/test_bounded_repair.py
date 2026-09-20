"""Synthetic tests for the bounded single-retry repair coordinator.

Every test uses injected models and repair providers only; no network,
no real models, no evaluation scores. Each coordinator test runs a
single request (denominator 1) unless the name states otherwise.
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from pathlib import Path

import pytest

from backend.adaptive.harness import (
    BoundedRepairCoordinator,
    DataType,
    ExecutionHarness,
    FakeModel,
    FieldMapping,
    HarnessErrorCode,
    HarnessRequest,
    HarnessStage,
    HarnessStatus,
    MappingProfile,
    MappingStatus,
    Ontology,
    OntologyField,
    Operator,
    RepairSummary,
    SQLiteReadOnlyExecutor,
    is_repairable,
)
from backend.adaptive.harness.compiler import SingleTableSQLiteCompiler
from backend.adaptive.harness.policy import ConservativeSqlPolicy


QUESTION = "합성 테스트 질문"


def _database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE courses (subject_name TEXT, credit_hours INTEGER, category TEXT)"
        )
        connection.executemany(
            "INSERT INTO courses VALUES (?, ?, ?)",
            [
                ("데이터베이스", 3, "전공"),
                ("운영체제", 3, "전공"),
                ("글쓰기", 2, "교양"),
            ],
        )
        connection.commit()
    finally:
        connection.close()


def _ontology() -> Ontology:
    return Ontology(
        entities={"Course"},
        fields={
            "Course.name": OntologyField(
                name="Course.name",
                data_type=DataType.STRING,
                allowed_operators={Operator.EQUALS, Operator.CONTAINS},
            ),
            "Course.credits": OntologyField(
                name="Course.credits",
                data_type=DataType.NUMBER,
                allowed_operators={Operator.EQUALS, Operator.GREATER_THAN},
            ),
            "Course.category": OntologyField(
                name="Course.category",
                data_type=DataType.STRING,
                allowed_operators={Operator.EQUALS},
            ),
        },
    )


def _mapping(**overrides) -> MappingProfile:
    fields = overrides.pop("fields", None)
    if fields is None:
        fields = {
            "Course.name": FieldMapping(table="courses", column="subject_name"),
            "Course.credits": FieldMapping(table="courses", column="credit_hours"),
            "Course.category": FieldMapping(table="courses", column="category"),
        }
    return MappingProfile(
        database_id="test-db", version=1,
        status=overrides.pop("status", MappingStatus.PUBLISHED),
        fields=fields, **overrides,
    )


GOOD_IR = {
    "entity": "Course",
    "select": ["Course.name"],
    "filters": [],
    "limit": 50,
}


class CountingExecutor(SQLiteReadOnlyExecutor):
    def __init__(self, databases: dict) -> None:
        super().__init__(databases)
        self.calls = 0

    def execute(self, database_id, compiled):
        self.calls += 1
        return super().execute(database_id, compiled)


class CountingModel:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls = 0

    def generate_ir(self, request, ontology, mapping):
        self.calls += 1
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class RecordingRepairProvider:
    def __init__(self, candidate: object = None, error: BaseException | None = None) -> None:
        self.candidate = candidate
        self.error = error
        self.calls = 0
        self.seen: list[dict] = []

    def propose_repair(self, request, ontology, mapping, *, failure_code, failure_stage):
        self.calls += 1
        self.seen.append({
            "request": request,
            "ontology": ontology,
            "mapping": mapping,
            "failure_code": failure_code,
            "failure_stage": failure_stage,
        })
        if self.error is not None:
            raise self.error
        return self.candidate


@pytest.fixture()
def setup():
    temporary = tempfile.TemporaryDirectory()
    path = Path(temporary.name) / "courses.sqlite3"
    _database(path)
    executor = CountingExecutor({"test-db": path})
    coordinator = BoundedRepairCoordinator(
        executor,
        compiler=SingleTableSQLiteCompiler(),
        sql_policy=ConservativeSqlPolicy(),
    )
    request = HarnessRequest(
        request_id="repair-test-1", database_id="test-db", question=QUESTION,
    )
    yield coordinator, executor, request, _ontology(), _mapping(), path
    temporary.cleanup()


def test_initial_success_attempts_no_repair(setup) -> None:
    coordinator, executor, request, ontology, mapping, _ = setup
    model = CountingModel(GOOD_IR)
    provider = RecordingRepairProvider(GOOD_IR)
    summary = coordinator.run(request, ontology, mapping, model=model, repair_provider=provider)
    assert summary.succeeded is True
    assert (summary.repair_attempted, summary.repair_attempts) == (False, 0)
    assert model.calls == 1 and provider.calls == 0 and executor.calls == 1
    assert summary.initial_code is None and summary.final_code is None


def test_empty_result_is_success_without_repair(setup) -> None:
    coordinator, executor, request, ontology, mapping, _ = setup
    empty = dict(GOOD_IR, filters=[
        {"field": "Course.credits", "operator": "equals", "value": 999},
    ])
    summary = coordinator.run(
        request, ontology, mapping,
        model=CountingModel(empty), repair_provider=RecordingRepairProvider(GOOD_IR),
    )
    assert summary.succeeded is True
    assert (summary.repair_attempted, summary.repair_attempts) == (False, 0)
    assert executor.calls == 1


def test_structural_error_repairs_exactly_once(setup) -> None:
    coordinator, executor, request, ontology, mapping, _ = setup
    model = CountingModel({})
    provider = RecordingRepairProvider(GOOD_IR)
    before = executor.calls
    summary = coordinator.run(request, ontology, mapping, model=model, repair_provider=provider)
    assert summary.initial_status is HarnessStatus.FAILED
    assert summary.initial_code == HarnessErrorCode.MODEL_INVALID_OUTPUT.value
    assert summary.initial_stage is HarnessStage.IR_GENERATED
    assert summary.succeeded is True
    assert (summary.repair_attempted, summary.repair_attempts) == (True, 1)
    assert model.calls == 1 and provider.calls == 1
    # First attempt never reached SQL execution; repair ran the full path once.
    assert executor.calls == before + 1


@pytest.mark.parametrize(("bad_ir", "code"), [
    (
        {"entity": "Nope", "select": ["Course.name"], "filters": [], "limit": 50},
        HarnessErrorCode.UNKNOWN_ENTITY,
    ),
    (
        {"entity": "Course", "select": ["Course.unknown"], "filters": [], "limit": 50},
        HarnessErrorCode.UNKNOWN_FIELD,
    ),
    (
        {"entity": "Course", "select": ["Course.name"], "filters": [
            {"field": "Course.credits", "operator": "contains", "value": "x"}],
         "limit": 50},
        HarnessErrorCode.INVALID_OPERATOR,
    ),
    (
        {"entity": "Course", "select": ["Course.name"], "filters": [
            {"field": "Course.credits", "operator": "equals", "value": "three"}],
         "limit": 50},
        HarnessErrorCode.INVALID_VALUE_TYPE,
    ),
])
def test_each_repairable_semantic_error_succeeds_after_one_repair(setup, bad_ir, code) -> None:
    coordinator, _, request, ontology, mapping, _ = setup
    provider = RecordingRepairProvider(GOOD_IR)
    summary = coordinator.run(
        request, ontology, mapping,
        model=CountingModel(bad_ir), repair_provider=provider,
    )
    assert summary.initial_code == code.value
    assert summary.initial_stage is HarnessStage.IR_VALIDATED
    assert summary.succeeded is True
    assert summary.final_code is None
    assert provider.calls == 1


def test_bad_repair_candidate_stops_after_two_attempts(setup) -> None:
    coordinator, _, request, ontology, mapping, _ = setup
    provider = RecordingRepairProvider({"entity": "Course", "select": ["Course.unknown"],
                                        "filters": [], "limit": 50})
    summary = coordinator.run(
        request, ontology, mapping,
        model=CountingModel({}), repair_provider=provider,
    )
    assert summary.succeeded is False
    assert (summary.repair_attempted, summary.repair_attempts) == (True, 1)
    assert provider.calls == 1
    assert summary.final_code == HarnessErrorCode.UNKNOWN_FIELD.value


def test_repair_provider_error_ends_safely_without_retry(setup) -> None:
    coordinator, _, request, ontology, mapping, _ = setup
    provider = RecordingRepairProvider(error=RuntimeError("비밀-원문"))
    summary = coordinator.run(
        request, ontology, mapping,
        model=CountingModel({}), repair_provider=provider,
    )
    assert summary.succeeded is False
    assert (summary.repair_attempted, summary.repair_attempts) == (True, 1)
    assert summary.final_code == "REPAIR_PROVIDER_ERROR"
    assert "비밀-원문" not in str(summary)


def test_repair_cancellation_propagates(setup) -> None:
    coordinator, _, request, ontology, mapping, _ = setup
    provider = RecordingRepairProvider(error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        coordinator.run(
            request, ontology, mapping,
            model=CountingModel({}), repair_provider=provider,
        )
    assert provider.calls == 1


def test_initial_model_error_propagates_without_repair(setup) -> None:
    coordinator, _, request, ontology, mapping, _ = setup
    provider = RecordingRepairProvider(GOOD_IR)
    with pytest.raises(RuntimeError, match="초기-원문"):
        coordinator.run(
            request, ontology, mapping,
            model=CountingModel(RuntimeError("초기-원문")),
            repair_provider=provider,
        )
    assert provider.calls == 0


@pytest.mark.parametrize("request_data", [
    {"database_id": "test-db"},
    {"database_id": "test-db", "question": "  "},
])
def test_invalid_request_is_terminal(setup, request_data) -> None:
    coordinator, _, _, ontology, mapping, _ = setup
    provider = RecordingRepairProvider(GOOD_IR)
    summary = coordinator.run(
        request_data, ontology, mapping,
        model=CountingModel(GOOD_IR), repair_provider=provider,
    )
    assert summary.succeeded is False
    assert summary.initial_code == HarnessErrorCode.INVALID_REQUEST.value
    assert provider.calls == 0


def test_unpublished_mapping_is_terminal(setup) -> None:
    coordinator, _, request, ontology, _, _ = setup
    mapping = _mapping(status=MappingStatus.DRAFT)
    provider = RecordingRepairProvider(GOOD_IR)
    summary = coordinator.run(
        request, ontology, mapping,
        model=CountingModel(GOOD_IR), repair_provider=provider,
    )
    assert summary.initial_code == HarnessErrorCode.MAPPING_NOT_PUBLISHED.value
    assert provider.calls == 0


def test_unmapped_field_is_terminal_at_validated_stage(setup) -> None:
    coordinator, _, request, ontology, _, _ = setup
    mapping = _mapping(fields={
        "Course.credits": FieldMapping(table="courses", column="credit_hours"),
        "Course.category": FieldMapping(table="courses", column="category"),
    })
    provider = RecordingRepairProvider(GOOD_IR)
    summary = coordinator.run(
        request, ontology, mapping,
        model=CountingModel(GOOD_IR), repair_provider=provider,
    )
    # Same stage as repairable codes, but the code itself is not repairable.
    assert summary.initial_code == HarnessErrorCode.MAPPING_MISSING.value
    assert summary.initial_stage is HarnessStage.IR_VALIDATED
    assert provider.calls == 0


def test_repairability_requires_code_and_stage_together() -> None:
    assert is_repairable(HarnessErrorCode.UNKNOWN_FIELD, HarnessStage.IR_VALIDATED)
    assert not is_repairable(HarnessErrorCode.UNKNOWN_FIELD, HarnessStage.EXECUTED)
    assert not is_repairable(HarnessErrorCode.MODEL_INVALID_OUTPUT, HarnessStage.IR_VALIDATED)
    assert not is_repairable(HarnessErrorCode.UNKNOWN_FIELD, None)
    assert not is_repairable(HarnessErrorCode.MAPPING_MISSING, HarnessStage.IR_VALIDATED)


def test_original_request_and_context_are_not_mutated(setup) -> None:
    coordinator, _, request, ontology, mapping, _ = setup
    snapshot = request.model_dump(mode="json")

    class MutatingProvider:
        def __init__(self) -> None:
            self.calls = 0

        def propose_repair(self, incoming, _ontology, _mapping, *, failure_code, failure_stage):
            self.calls += 1
            incoming.question = "바뀐 질문"
            incoming.database_id = "other-db"
            return GOOD_IR

    provider = MutatingProvider()
    summary = coordinator.run(
        request, ontology, mapping,
        model=CountingModel({}), repair_provider=provider,
    )
    assert summary.succeeded is True
    assert provider.calls == 1
    assert request.model_dump(mode="json") == snapshot


def test_mutating_initial_model_with_valid_ir_still_uses_original_context(setup) -> None:
    coordinator, executor, request, ontology, mapping, _ = setup
    request_before = request.model_dump(mode="json")
    ontology_before = ontology.model_dump(mode="json")
    mapping_before = mapping.model_dump(mode="json")

    class MutatingValidModel:
        def __init__(self) -> None:
            self.calls = 0

        def generate_ir(self, incoming_request, incoming_ontology, incoming_mapping):
            self.calls += 1
            incoming_request.question = "바뀐 질문"
            incoming_request.database_id = "other-db"
            incoming_ontology.entities.clear()
            incoming_ontology.fields.clear()
            incoming_mapping.fields.clear()
            return dict(GOOD_IR)

    model = MutatingValidModel()
    provider = RecordingRepairProvider(GOOD_IR)
    summary = coordinator.run(
        request, ontology, mapping,
        model=model, repair_provider=provider,
    )
    # The harness must validate/compile/execute against the original context,
    # not the model's mutated copies: success with no repair call.
    assert summary.succeeded is True
    assert summary.initial_code is None
    assert (summary.repair_attempted, summary.repair_attempts) == (False, 0)
    assert model.calls == 1 and provider.calls == 0 and executor.calls == 1
    assert request.model_dump(mode="json") == request_before
    assert ontology.model_dump(mode="json") == ontology_before
    assert mapping.model_dump(mode="json") == mapping_before


def test_mutating_initial_model_cannot_change_repair_context(setup) -> None:
    coordinator, executor, request, ontology, mapping, _ = setup
    request_before = request.model_dump(mode="json")
    ontology_before = ontology.model_dump(mode="json")
    mapping_before = mapping.model_dump(mode="json")

    class MutatingInitialModel:
        def __init__(self) -> None:
            self.calls = 0

        def generate_ir(self, incoming_request, incoming_ontology, incoming_mapping):
            self.calls += 1
            incoming_request.question = "바뀐 질문"
            incoming_request.database_id = "other-db"
            incoming_ontology.entities.clear()
            incoming_ontology.fields.clear()
            incoming_mapping.fields.clear()
            return {}

    provider = RecordingRepairProvider(GOOD_IR)
    summary = coordinator.run(
        request, ontology, mapping,
        model=MutatingInitialModel(), repair_provider=provider,
    )
    assert summary.initial_code == HarnessErrorCode.MODEL_INVALID_OUTPUT.value
    assert summary.succeeded is True
    assert summary.final_code is None
    assert provider.calls == 1
    assert executor.calls == 1
    # Caller originals are unchanged.
    assert request.model_dump(mode="json") == request_before
    assert ontology.model_dump(mode="json") == ontology_before
    assert mapping.model_dump(mode="json") == mapping_before
    # Repair provider and second attempt saw the pristine context.
    assert provider.seen[0]["request"].model_dump(mode="json") == request_before
    assert provider.seen[0]["ontology"].model_dump(mode="json") == ontology_before
    assert provider.seen[0]["mapping"].model_dump(mode="json") == mapping_before


def test_mutating_repair_provider_cannot_change_second_attempt(setup) -> None:
    coordinator, executor, request, ontology, mapping, _ = setup
    request_before = request.model_dump(mode="json")
    ontology_before = ontology.model_dump(mode="json")
    mapping_before = mapping.model_dump(mode="json")

    class ClearingRepairProvider:
        def __init__(self) -> None:
            self.calls = 0

        def propose_repair(self, incoming_request, incoming_ontology, incoming_mapping,
                           *, failure_code, failure_stage):
            self.calls += 1
            incoming_request.question = "바뀐 질문"
            incoming_request.database_id = "other-db"
            incoming_ontology.entities.clear()
            incoming_ontology.fields.clear()
            incoming_mapping.fields.clear()
            return dict(GOOD_IR)

    provider = ClearingRepairProvider()
    summary = coordinator.run(
        request, ontology, mapping,
        model=CountingModel({}), repair_provider=provider,
    )
    # Without snapshots the cleared mapping would make the second attempt
    # fail with MAPPING_MISSING; with snapshots the repair still succeeds.
    assert summary.succeeded is True
    assert summary.final_code is None
    assert provider.calls == 1
    assert executor.calls == 1
    assert request.model_dump(mode="json") == request_before
    assert ontology.model_dump(mode="json") == ontology_before
    assert mapping.model_dump(mode="json") == mapping_before
    assert len(mapping.fields) == 3


def test_provider_receives_only_request_and_fixed_code(setup) -> None:
    coordinator, _, request, ontology, mapping, _ = setup
    provider = RecordingRepairProvider(GOOD_IR)
    summary = coordinator.run(
        request, ontology, mapping,
        model=CountingModel({}), repair_provider=provider,
    )
    assert summary.succeeded is True
    assert len(provider.seen) == 1
    seen = provider.seen[0]
    assert set(seen) == {"request", "ontology", "mapping", "failure_code", "failure_stage"}
    assert seen["failure_code"] is HarnessErrorCode.MODEL_INVALID_OUTPUT
    assert seen["failure_stage"] is HarnessStage.IR_GENERATED
    assert seen["request"] is not request
    assert seen["request"].model_dump(mode="json") == request.model_dump(mode="json")


def test_summary_and_errors_carry_no_original_text(setup, caplog) -> None:
    import logging

    coordinator, _, request, ontology, mapping, _ = setup
    provider = RecordingRepairProvider(error=RuntimeError("비밀-원문"))
    with caplog.at_level(logging.DEBUG):
        summary = coordinator.run(
            request, ontology, mapping,
            model=CountingModel({}), repair_provider=provider,
        )
    assert isinstance(summary, RepairSummary)
    assert "합성 테스트 질문" not in str(summary)
    assert "비밀-원문" not in str(summary)
    assert "courses" not in str(summary).lower()
    assert caplog.text == ""


def test_invalid_ir_never_executes_and_db_is_unchanged(setup) -> None:
    coordinator, executor, request, ontology, mapping, path = setup
    before = path.read_bytes()
    bad = {"entity": "Course", "select": ["Course.unknown"], "filters": [], "limit": 50}
    summary = coordinator.run(
        request, ontology, mapping,
        model=CountingModel(bad),
        repair_provider=RecordingRepairProvider(dict(bad)),
    )
    assert summary.succeeded is False
    assert executor.calls == 0
    assert path.read_bytes() == before


def test_repair_candidate_uses_full_harness_path(setup) -> None:
    coordinator, executor, request, ontology, mapping, path = setup
    before = path.read_bytes()
    summary = coordinator.run(
        request, ontology, mapping,
        model=CountingModel({}),
        repair_provider=RecordingRepairProvider(GOOD_IR),
    )
    assert summary.succeeded is True
    # The repair ran through the same executor exactly once.
    assert executor.calls == 1
    assert path.read_bytes() == before


def test_existing_harness_contract_is_unchanged(setup) -> None:
    _, executor, request, ontology, mapping, _ = setup
    harness = ExecutionHarness(
        model=FakeModel({QUESTION: GOOD_IR}),
        compiler=SingleTableSQLiteCompiler(),
        sql_policy=ConservativeSqlPolicy(),
        executor=executor,
    )
    result = harness.run(request, ontology, mapping)
    assert result.status is HarnessStatus.SUCCEEDED
    assert [row["Course.name"] for row in result.rows] == ["데이터베이스", "운영체제", "글쓰기"]
