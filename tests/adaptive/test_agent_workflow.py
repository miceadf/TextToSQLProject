"""Synthetic tests for the Phase 6-06 agent workflow.

Every test uses injected models, repair providers, and in-memory
queues only; no network, Ollama, PostgreSQL, CNU data, UI, files, or
evaluation scores. Each workflow test runs a single request
(denominator 1) unless the name states otherwise.

Note on explicit returns: `AgentWorkflowResult.final_result` keeps the
existing `HarnessResult` SQL/rows contract as an explicit caller
return value. Non-exposure assertions below therefore cover the new
summary, critique, queue entry, workflow errors, stdout, and logs --
not the intentionally preserved `final_result` itself.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import sqlite3
import tempfile
from pathlib import Path

import pytest

from backend.adaptive.harness import (
    AGENT_WORKFLOW_CONFLICT,
    AGENT_WORKFLOW_INTERNAL_ERROR,
    AGENT_WORKFLOW_INVALID_INPUT,
    AGENT_WORKFLOW_VERSION,
    AgentWorkflowCoordinator,
    AgentWorkflowError,
    AgentWorkflowResult,
    BoundedRepairCoordinator,
    CritiqueDisposition,
    DataType,
    DetailedRepairOutcome,
    FieldMapping,
    HarnessErrorCode,
    HarnessRequest,
    HarnessStage,
    HarnessStatus,
    InMemoryReviewQueue,
    MappingProfile,
    MappingStatus,
    Ontology,
    OntologyField,
    Operator,
    RepairSummary,
    ReviewQueueError,
    ReviewQueueStatus,
    SQLiteReadOnlyExecutor,
)
from backend.adaptive.harness.agent_workflow import AgentWorkflowErrorCode
from backend.adaptive.harness.compiler import SingleTableSQLiteCompiler
from backend.adaptive.harness.policy import ConservativeSqlPolicy
from backend.adaptive.harness.repair import REPAIR_PROVIDER_ERROR


QUESTION = "합성 테스트 질문"
RUN_ID = "synth-run-6-06-a"


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

BAD_UNKNOWN_FIELD = {
    "entity": "Course", "select": ["Course.unknown"], "filters": [], "limit": 50,
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

    def propose_repair(self, request, ontology, mapping, *, failure_code, failure_stage):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.candidate


@pytest.fixture()
def setup():
    temporary = tempfile.TemporaryDirectory()
    path = Path(temporary.name) / "courses.sqlite3"
    _database(path)
    executor = CountingExecutor({"test-db": path})
    workflow = AgentWorkflowCoordinator(
        executor,
        compiler=SingleTableSQLiteCompiler(),
        sql_policy=ConservativeSqlPolicy(),
    )
    request = HarnessRequest(
        request_id="agent-workflow-test-1", database_id="test-db", question=QUESTION,
    )
    yield workflow, executor, request, _ontology(), _mapping(), path
    temporary.cleanup()


def test_initial_success_preserves_result_without_queue(setup) -> None:
    workflow, executor, request, ontology, mapping, _ = setup
    model = CountingModel(dict(GOOD_IR))
    provider = RecordingRepairProvider(dict(GOOD_IR))
    queue = InMemoryReviewQueue()
    result = workflow.run(
        request, ontology, mapping, model=model,
        repair_provider=provider, model_run_id=RUN_ID, queue=queue,
    )
    assert isinstance(result, AgentWorkflowResult)
    assert result.workflow_version == AGENT_WORKFLOW_VERSION
    assert result.repair_summary.succeeded is True
    assert (result.repair_summary.repair_attempted, result.repair_summary.repair_attempts) == (False, 0)
    assert result.critique_result.disposition is CritiqueDisposition.EXECUTED_UNVERIFIED
    assert result.critique_result.recovered_after_repair is False
    assert result.queue_status is ReviewQueueStatus.NOT_ELIGIBLE
    assert result.queue_entry is None
    assert len(queue) == 0
    assert model.calls == 1 and provider.calls == 0 and executor.calls == 1
    assert result.final_result is not None
    assert result.final_result.status is HarnessStatus.SUCCEEDED
    assert [row["Course.name"] for row in result.final_result.rows] == [
        "데이터베이스", "운영체제", "글쓰기",
    ]
    assert result.final_result.columns == ["Course.name"]


def test_empty_success_preserves_zero_rows_without_queue(setup) -> None:
    workflow, executor, request, ontology, mapping, _ = setup
    empty = dict(GOOD_IR, filters=[
        {"field": "Course.credits", "operator": "equals", "value": 999},
    ])
    queue = InMemoryReviewQueue()
    result = workflow.run(
        request, ontology, mapping, model=CountingModel(empty),
        repair_provider=RecordingRepairProvider(dict(GOOD_IR)),
        model_run_id=RUN_ID, queue=queue,
    )
    assert result.repair_summary.succeeded is True
    assert result.critique_result.disposition is CritiqueDisposition.EXECUTED_UNVERIFIED
    assert result.queue_status is ReviewQueueStatus.NOT_ELIGIBLE
    assert len(queue) == 0
    assert result.final_result is not None
    assert result.final_result.rows == []
    assert result.final_result.columns == ["Course.name"]
    assert executor.calls == 1


def test_retryable_failure_recovers_with_final_result(setup) -> None:
    workflow, executor, request, ontology, mapping, _ = setup
    model = CountingModel({})
    provider = RecordingRepairProvider(dict(GOOD_IR))
    queue = InMemoryReviewQueue()
    before = executor.calls
    result = workflow.run(
        request, ontology, mapping, model=model,
        repair_provider=provider, model_run_id=RUN_ID, queue=queue,
    )
    assert result.repair_summary.initial_code == HarnessErrorCode.MODEL_INVALID_OUTPUT.value
    assert result.repair_summary.succeeded is True
    assert result.repair_summary.final_code is None
    assert result.critique_result.disposition is CritiqueDisposition.EXECUTED_UNVERIFIED
    assert result.critique_result.repair_attempted is True
    assert result.critique_result.recovered_after_repair is True
    assert result.queue_status is ReviewQueueStatus.NOT_ELIGIBLE
    assert len(queue) == 0
    assert model.calls == 1 and provider.calls == 1
    assert executor.calls == before + 1
    assert result.final_result is not None
    assert result.final_result.status is HarnessStatus.SUCCEEDED
    assert len(result.final_result.rows) == 3


def test_failed_repair_registers_one_queue_entry(setup) -> None:
    workflow, _, request, ontology, mapping, _ = setup
    queue = InMemoryReviewQueue()
    result = workflow.run(
        request, ontology, mapping,
        model=CountingModel(dict(BAD_UNKNOWN_FIELD)),
        repair_provider=RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD)),
        model_run_id=RUN_ID, queue=queue,
    )
    assert result.repair_summary.succeeded is False
    assert result.repair_summary.final_code == HarnessErrorCode.UNKNOWN_FIELD.value
    assert result.repair_summary.final_stage is HarnessStage.IR_VALIDATED
    assert result.critique_result.disposition is CritiqueDisposition.MODEL_REVIEW
    assert result.queue_status is ReviewQueueStatus.ENQUEUED
    assert result.queue_entry is not None
    assert result.queue_entry.final_code == HarnessErrorCode.UNKNOWN_FIELD.value
    assert len(queue) == 1
    assert result.final_result is not None
    assert result.final_result.status is HarnessStatus.FAILED
    assert result.final_result.error is not None
    assert result.final_result.error.code is HarnessErrorCode.UNKNOWN_FIELD


def test_same_workflow_reregistration_is_already_present(setup) -> None:
    workflow, _, request, ontology, mapping, _ = setup
    queue = InMemoryReviewQueue()
    first = workflow.run(
        request, ontology, mapping,
        model=CountingModel(dict(BAD_UNKNOWN_FIELD)),
        repair_provider=RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD)),
        model_run_id=RUN_ID, queue=queue,
    )
    second = workflow.run(
        request, ontology, mapping,
        model=CountingModel(dict(BAD_UNKNOWN_FIELD)),
        repair_provider=RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD)),
        model_run_id=RUN_ID, queue=queue,
    )
    assert first.queue_status is ReviewQueueStatus.ENQUEUED
    assert second.queue_status is ReviewQueueStatus.ALREADY_PRESENT
    assert first.queue_entry == second.queue_entry
    assert len(queue) == 1


def test_same_key_different_result_is_conflict_preserving_entry(setup) -> None:
    workflow, _, request, ontology, mapping, _ = setup
    queue = InMemoryReviewQueue()
    first = workflow.run(
        request, ontology, mapping,
        model=CountingModel(dict(BAD_UNKNOWN_FIELD)),
        repair_provider=RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD)),
        model_run_id=RUN_ID, queue=queue,
    )
    assert first.queue_entry is not None
    stored_before = queue.list_entries()[0]
    bad_entity = {
        "entity": "Nope", "select": ["Course.name"], "filters": [], "limit": 50,
    }
    with pytest.raises(AgentWorkflowError) as exc_info:
        workflow.run(
            request, ontology, mapping,
            model=CountingModel(dict(bad_entity)),
            repair_provider=RecordingRepairProvider(dict(bad_entity)),
            model_run_id=RUN_ID, queue=queue,
        )
    assert exc_info.value.code == AGENT_WORKFLOW_CONFLICT
    assert str(exc_info.value) == AGENT_WORKFLOW_CONFLICT
    assert len(queue) == 1
    assert queue.list_entries()[0] == stored_before


def test_conflicting_second_workflow_preserves_first_entry(setup) -> None:
    workflow, _, request, ontology, mapping, _ = setup
    queue = InMemoryReviewQueue()
    first = workflow.run(
        request, ontology, mapping,
        model=CountingModel(dict(BAD_UNKNOWN_FIELD)),
        repair_provider=RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD)),
        model_run_id=RUN_ID, queue=queue,
    )
    assert first.queue_entry is not None
    # Same run/request IDs via dict request form, different final code.
    bad_entity = {
        "entity": "Nope", "select": ["Course.name"], "filters": [], "limit": 50,
    }
    dict_request = {
        "request_id": request.request_id,
        "database_id": "test-db",
        "question": QUESTION,
    }
    with pytest.raises(AgentWorkflowError) as exc_info:
        workflow.run(
            dict_request, ontology, mapping,
            model=CountingModel(dict(bad_entity)),
            repair_provider=RecordingRepairProvider(dict(bad_entity)),
            model_run_id=RUN_ID, queue=queue,
        )
    assert exc_info.value.code == AGENT_WORKFLOW_CONFLICT
    assert str(exc_info.value) == AGENT_WORKFLOW_CONFLICT
    assert len(queue) == 1
    assert queue.list_entries()[0] == first.queue_entry


@pytest.mark.parametrize("request_data", [
    {"database_id": "test-db"},
    {"database_id": "test-db", "question": "  "},
])
def test_terminal_invalid_request_is_not_queued(setup, request_data) -> None:
    workflow, executor, _, ontology, mapping, _ = setup
    model = CountingModel(dict(GOOD_IR))
    provider = RecordingRepairProvider(dict(GOOD_IR))
    queue = InMemoryReviewQueue()
    result = workflow.run(
        request_data, ontology, mapping, model=model,
        repair_provider=provider, model_run_id=RUN_ID, queue=queue,
    )
    assert result.repair_summary.final_code == HarnessErrorCode.INVALID_REQUEST.value
    assert result.critique_result.disposition is CritiqueDisposition.REQUEST_INVALID
    assert result.queue_status is ReviewQueueStatus.NOT_ELIGIBLE
    assert result.queue_entry is None
    assert len(queue) == 0
    assert model.calls == 0 and provider.calls == 0 and executor.calls == 0
    assert result.final_result is not None
    assert result.final_result.status is HarnessStatus.FAILED


def test_terminal_context_failure_registers_only_operational(setup) -> None:
    workflow, _, request, _, _, _ = setup
    mapping = _mapping(status=MappingStatus.DRAFT)
    model = CountingModel(dict(GOOD_IR))
    provider = RecordingRepairProvider(dict(GOOD_IR))
    queue = InMemoryReviewQueue()
    result = workflow.run(
        request, _ontology(), mapping, model=model,
        repair_provider=provider, model_run_id=RUN_ID, queue=queue,
    )
    assert result.repair_summary.final_code == HarnessErrorCode.MAPPING_NOT_PUBLISHED.value
    assert result.critique_result.disposition is CritiqueDisposition.CONTEXT_REVIEW
    assert result.queue_status is ReviewQueueStatus.ENQUEUED
    assert provider.calls == 0
    assert len(queue) == 1


def test_repair_provider_error_has_no_result_but_registers(setup) -> None:
    workflow, _, request, ontology, mapping, _ = setup
    queue = InMemoryReviewQueue()
    result = workflow.run(
        request, ontology, mapping,
        model=CountingModel({}),
        repair_provider=RecordingRepairProvider(error=RuntimeError("비밀-원문")),
        model_run_id=RUN_ID, queue=queue,
    )
    assert result.repair_summary.final_code == REPAIR_PROVIDER_ERROR
    assert result.final_result is None
    assert result.critique_result.disposition is CritiqueDisposition.MODEL_REVIEW
    assert result.critique_result.final_code == REPAIR_PROVIDER_ERROR
    assert result.queue_status is ReviewQueueStatus.ENQUEUED
    assert result.queue_entry is not None
    assert result.queue_entry.final_stage is None
    assert len(queue) == 1


def test_repair_cancellation_propagates_without_queue_change(setup) -> None:
    workflow, _, request, ontology, mapping, _ = setup
    queue = InMemoryReviewQueue()
    provider = RecordingRepairProvider(error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        workflow.run(
            request, ontology, mapping,
            model=CountingModel({}), repair_provider=provider,
            model_run_id=RUN_ID, queue=queue,
        )
    assert len(queue) == 0


@pytest.mark.parametrize("bad_run_id", ["", "   ", "x" * 201, None, 123])
def test_invalid_model_run_id_runs_nothing(setup, bad_run_id) -> None:
    workflow, executor, request, ontology, mapping, _ = setup
    model = CountingModel(dict(GOOD_IR))
    provider = RecordingRepairProvider(dict(GOOD_IR))
    queue = InMemoryReviewQueue()
    with pytest.raises(AgentWorkflowError) as exc_info:
        workflow.run(
            request, ontology, mapping, model=model,
            repair_provider=provider, model_run_id=bad_run_id, queue=queue,
        )
    assert exc_info.value.code == AGENT_WORKFLOW_INVALID_INPUT
    assert model.calls == 0 and provider.calls == 0 and executor.calls == 0
    assert len(queue) == 0


def test_queue_incompatible_valid_request_id_runs_nothing(setup) -> None:
    workflow, executor, _, ontology, mapping, _ = setup
    bad_request = HarnessRequest(
        request_id="   ", database_id="test-db", question=QUESTION,
    )
    model = CountingModel(dict(GOOD_IR))
    provider = RecordingRepairProvider(dict(GOOD_IR))
    queue = InMemoryReviewQueue()
    with pytest.raises(AgentWorkflowError) as exc_info:
        workflow.run(
            bad_request, ontology, mapping, model=model,
            repair_provider=provider, model_run_id=RUN_ID, queue=queue,
        )
    assert exc_info.value.code == AGENT_WORKFLOW_INVALID_INPUT
    assert model.calls == 0 and provider.calls == 0 and executor.calls == 0
    assert len(queue) == 0


@pytest.mark.parametrize("bad_queue", [None, "queue", object(), {"entries": []}])
def test_invalid_queue_or_types_run_nothing(setup, bad_queue) -> None:
    workflow, executor, request, ontology, mapping, _ = setup
    model = CountingModel(dict(GOOD_IR))
    provider = RecordingRepairProvider(dict(GOOD_IR))
    with pytest.raises(AgentWorkflowError) as exc_info:
        workflow.run(
            request, ontology, mapping, model=model,
            repair_provider=provider, model_run_id=RUN_ID, queue=bad_queue,
        )
    assert exc_info.value.code == AGENT_WORKFLOW_INVALID_INPUT
    assert model.calls == 0 and provider.calls == 0 and executor.calls == 0


def test_caller_cannot_inject_critique_or_disposition() -> None:
    signature = inspect.signature(AgentWorkflowCoordinator.run)
    names = set(signature.parameters)
    assert "critique_result" not in names
    assert "critique" not in names
    assert "disposition" not in names


def test_mutating_provider_leaves_caller_and_context_intact(setup) -> None:
    workflow, executor, request, ontology, mapping, _ = setup
    before_request = request.model_dump(mode="json")
    before_ontology = ontology.model_dump(mode="json")
    before_mapping = mapping.model_dump(mode="json")

    class MutatingProvider:
        def __init__(self) -> None:
            self.calls = 0

        def propose_repair(self, incoming, _ontology, _mapping, *, failure_code, failure_stage):
            self.calls += 1
            incoming.question = "바뀐 질문"
            incoming.database_id = "other-db"
            _ontology.entities.clear()
            _ontology.fields.clear()
            _mapping.fields.clear()
            return dict(GOOD_IR)

    provider = MutatingProvider()
    queue = InMemoryReviewQueue()
    result = workflow.run(
        request, ontology, mapping, model=CountingModel({}),
        repair_provider=provider, model_run_id=RUN_ID, queue=queue,
    )
    assert result.repair_summary.succeeded is True
    assert provider.calls == 1
    assert request.model_dump(mode="json") == before_request
    assert ontology.model_dump(mode="json") == before_ontology
    assert mapping.model_dump(mode="json") == before_mapping
    assert result.final_result is not None
    assert len(result.final_result.rows) == 3


def test_workflow_and_queue_results_are_defensive_copies(setup) -> None:
    workflow, _, request, ontology, mapping, _ = setup
    queue = InMemoryReviewQueue()
    result = workflow.run(
        request, ontology, mapping,
        model=CountingModel(dict(BAD_UNKNOWN_FIELD)),
        repair_provider=RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD)),
        model_run_id=RUN_ID, queue=queue,
    )
    assert result.queue_entry is not None
    listed = queue.list_entries()
    assert len(listed) == 1
    assert result.queue_entry == listed[0]
    assert result.queue_entry is not listed[0]
    with pytest.raises(Exception):
        result.queue_entry.final_code = "MUTATED"  # type: ignore[misc]
    with pytest.raises(Exception):
        result.repair_summary.final_code = "MUTATED"  # type: ignore[misc]
    again = queue.list_entries()
    assert again[0] == listed[0]
    assert again[0] is not listed[0]


def test_no_duplicate_calls_per_execution_and_next_request_recovers(setup) -> None:
    workflow, executor, request, ontology, mapping, _ = setup
    queue = InMemoryReviewQueue()
    model = CountingModel(dict(BAD_UNKNOWN_FIELD))
    provider = RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD))
    first = workflow.run(
        request, ontology, mapping, model=model,
        repair_provider=provider, model_run_id=RUN_ID, queue=queue,
    )
    assert first.repair_summary.succeeded is False
    assert (model.calls, provider.calls) == (1, 1)
    executor_calls = executor.calls
    second_request = HarnessRequest(
        request_id="agent-workflow-test-2", database_id="test-db", question=QUESTION,
    )
    second = workflow.run(
        second_request, ontology, mapping,
        model=CountingModel(dict(GOOD_IR)),
        repair_provider=RecordingRepairProvider(dict(GOOD_IR)),
        model_run_id="synth-run-6-06-b", queue=queue,
    )
    assert second.repair_summary.succeeded is True
    assert second.queue_status is ReviewQueueStatus.NOT_ELIGIBLE
    assert executor.calls == executor_calls + 1
    assert len(queue) == 1


def test_detailed_repair_outcome_preserves_final_result(setup) -> None:
    _, executor, request, ontology, mapping, _ = setup
    coordinator = BoundedRepairCoordinator(
        executor,
        compiler=SingleTableSQLiteCompiler(),
        sql_policy=ConservativeSqlPolicy(),
    )
    detailed = coordinator.run_detailed(
        request, ontology, mapping,
        model=CountingModel(dict(GOOD_IR)),
        repair_provider=RecordingRepairProvider(dict(GOOD_IR)),
    )
    assert isinstance(detailed, DetailedRepairOutcome)
    assert detailed.summary.succeeded is True
    assert detailed.final_result is not None
    assert detailed.final_result.status is HarnessStatus.SUCCEEDED
    assert len(detailed.final_result.rows) == 3
    legacy = coordinator.run(
        request, ontology, mapping,
        model=CountingModel(dict(GOOD_IR)),
        repair_provider=RecordingRepairProvider(dict(GOOD_IR)),
    )
    assert isinstance(legacy, RepairSummary)
    assert legacy == detailed.summary


def test_detailed_provider_error_carries_no_result(setup) -> None:
    _, executor, request, ontology, mapping, _ = setup
    coordinator = BoundedRepairCoordinator(
        executor,
        compiler=SingleTableSQLiteCompiler(),
        sql_policy=ConservativeSqlPolicy(),
    )
    detailed = coordinator.run_detailed(
        request, ontology, mapping,
        model=CountingModel({}),
        repair_provider=RecordingRepairProvider(error=RuntimeError("x")),
    )
    assert detailed.summary.final_code == REPAIR_PROVIDER_ERROR
    assert detailed.final_result is None
    with pytest.raises(Exception):
        DetailedRepairOutcome(
            summary=detailed.summary,
            final_result=detailed.summary,  # type: ignore[arg-type]
        )


def test_new_layer_has_no_io_or_provider_access() -> None:
    import ast
    import inspect as py_inspect

    import backend.adaptive.harness.agent_workflow as module

    tree = ast.parse(py_inspect.getsource(module))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0].lower())
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0].lower())
    for token in ("sqlite3", "socket", "urllib", "requests", "ollama", "tempfile", "pathlib"):
        assert token not in imported


def test_no_snapshot_auto_save_during_workflow(setup, monkeypatch) -> None:
    import backend.adaptive.harness.review_queue_storage as storage

    called = {"count": 0}

    def _forbid(*args, **kwargs):
        called["count"] += 1
        raise AssertionError("snapshot write must not run")

    monkeypatch.setattr(storage, "write_review_queue_snapshot", _forbid)
    workflow, _, request, ontology, mapping, _ = setup
    queue = InMemoryReviewQueue()
    workflow.run(
        request, ontology, mapping,
        model=CountingModel(dict(BAD_UNKNOWN_FIELD)),
        repair_provider=RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD)),
        model_run_id=RUN_ID, queue=queue,
    )
    assert called["count"] == 0


def test_sensitive_values_absent_from_new_contracts_logs(setup, caplog, capsys) -> None:
    workflow, _, request, ontology, mapping, _ = setup
    secret_question = QUESTION
    queue = InMemoryReviewQueue()
    with caplog.at_level(logging.DEBUG):
        result = workflow.run(
            request, ontology, mapping,
            model=CountingModel(dict(BAD_UNKNOWN_FIELD)),
            repair_provider=RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD)),
            model_run_id=RUN_ID, queue=queue,
        )
        print(result.repair_summary)
        print(result.critique_result)
        print(result.queue_entry)
        print(result.queue_status)
    assert result.queue_entry is not None
    exposed = (
        str(result.repair_summary)
        + str(result.critique_result)
        + str(result.queue_entry)
        + result.queue_status.value
        + caplog.text
        + capsys.readouterr().out
    )
    assert secret_question not in exposed
    assert "SELECT" not in exposed
    assert "courses" not in exposed.lower()
    assert "subject_name" not in exposed
    assert RUN_ID not in exposed
    assert request.request_id not in exposed
    try:
        workflow.run(
            request, ontology, mapping,
            model=CountingModel(dict(GOOD_IR)),
            repair_provider=RecordingRepairProvider(dict(GOOD_IR)),
            model_run_id="", queue=queue,
        )
    except AgentWorkflowError as exc:
        assert secret_question not in str(exc)
        assert "SELECT" not in str(exc)
    # Explicit caller return still preserves the executed SQL/rows.
    success = workflow.run(
        HarnessRequest(
            request_id="agent-workflow-test-3", database_id="test-db",
            question=QUESTION,
        ),
        ontology, mapping,
        model=CountingModel(dict(GOOD_IR)),
        repair_provider=RecordingRepairProvider(dict(GOOD_IR)),
        model_run_id="synth-run-6-06-c", queue=InMemoryReviewQueue(),
    )
    assert success.final_result is not None
    assert success.final_result.sql is not None


# --- Independent-review gap regressions: forged/mismatched contracts ---


def test_detailed_outcome_rejects_model_copy_forged_summary(setup) -> None:
    import warnings

    from pydantic import ValidationError

    _, executor, request, ontology, mapping, _ = setup
    coordinator = BoundedRepairCoordinator(
        executor,
        compiler=SingleTableSQLiteCompiler(),
        sql_policy=ConservativeSqlPolicy(),
    )
    detailed = coordinator.run_detailed(
        request, ontology, mapping,
        model=CountingModel(dict(BAD_UNKNOWN_FIELD)),
        repair_provider=RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD)),
    )
    assert detailed.final_result is not None
    forged_summary = detailed.summary.model_copy(
        update={"final_code": HarnessErrorCode.UNKNOWN_ENTITY.value}
    )
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        with pytest.raises(ValidationError):
            DetailedRepairOutcome.model_validate({
                "summary": forged_summary.model_dump(mode="json", warnings=False),
                "final_result": detailed.final_result.model_dump(
                    mode="json", warnings=False
                ),
            })
    assert not emitted


def test_detailed_outcome_rejects_constructed_nested_summary(setup) -> None:
    from pydantic import ValidationError

    _, executor, request, ontology, mapping, _ = setup
    coordinator = BoundedRepairCoordinator(
        executor,
        compiler=SingleTableSQLiteCompiler(),
        sql_policy=ConservativeSqlPolicy(),
    )
    detailed = coordinator.run_detailed(
        request, ontology, mapping,
        model=CountingModel(dict(GOOD_IR)),
        repair_provider=RecordingRepairProvider(dict(GOOD_IR)),
    )
    assert detailed.final_result is not None
    forged = RepairSummary.model_construct(
        initial_status=HarnessStatus.SUCCEEDED,
        initial_stage=HarnessStage.RESULT_VALIDATED,
        initial_code=None,
        repair_attempted=False,
        repair_attempts=0,
        final_status=HarnessStatus.SUCCEEDED,
        final_stage=HarnessStage.RESULT_VALIDATED,
        final_code=HarnessErrorCode.UNKNOWN_FIELD.value,
        succeeded=True,
    )
    with pytest.raises(ValidationError):
        DetailedRepairOutcome.model_validate({
            "summary": forged.model_dump(mode="json", warnings=False),
            "final_result": detailed.final_result.model_dump(
                mode="json", warnings=False
            ),
        })


@pytest.mark.parametrize("field", ["final_code", "final_stage", "final_status"])
def test_detailed_outcome_rejects_mismatched_finals(setup, field) -> None:
    from pydantic import ValidationError

    _, executor, request, ontology, mapping, _ = setup
    coordinator = BoundedRepairCoordinator(
        executor,
        compiler=SingleTableSQLiteCompiler(),
        sql_policy=ConservativeSqlPolicy(),
    )
    detailed = coordinator.run_detailed(
        request, ontology, mapping,
        model=CountingModel(dict(BAD_UNKNOWN_FIELD)),
        repair_provider=RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD)),
    )
    assert detailed.final_result is not None
    assert detailed.summary.final_code == HarnessErrorCode.UNKNOWN_FIELD.value
    dump = detailed.summary.model_dump(mode="json", warnings=False)
    if field == "final_code":
        dump["final_code"] = HarnessErrorCode.UNKNOWN_ENTITY.value
    elif field == "final_stage":
        dump["final_stage"] = HarnessStage.EXECUTED.value
    else:
        dump["final_status"] = HarnessStatus.SUCCEEDED.value
    with pytest.raises(ValidationError):
        DetailedRepairOutcome.model_validate({
            "summary": dump,
            "final_result": detailed.final_result.model_dump(
                mode="json", warnings=False
            ),
        })


def test_detailed_outcome_rejects_provider_error_iff_violation(setup) -> None:
    from pydantic import ValidationError

    _, executor, request, ontology, mapping, _ = setup
    coordinator = BoundedRepairCoordinator(
        executor,
        compiler=SingleTableSQLiteCompiler(),
        sql_policy=ConservativeSqlPolicy(),
    )
    errored = coordinator.run_detailed(
        request, ontology, mapping,
        model=CountingModel({}),
        repair_provider=RecordingRepairProvider(error=RuntimeError("x")),
    )
    assert errored.final_result is None
    success = coordinator.run_detailed(
        HarnessRequest(
            request_id="agent-workflow-gap-1", database_id="test-db",
            question=QUESTION,
        ),
        ontology, mapping,
        model=CountingModel(dict(GOOD_IR)),
        repair_provider=RecordingRepairProvider(dict(GOOD_IR)),
    )
    assert success.final_result is not None
    # Provider-error summary must not carry a result.
    with pytest.raises(ValidationError):
        DetailedRepairOutcome.model_validate({
            "summary": errored.summary.model_dump(mode="json", warnings=False),
            "final_result": success.final_result.model_dump(
                mode="json", warnings=False
            ),
        })
    # Non-provider-error summary must carry a result.
    with pytest.raises(ValidationError):
        DetailedRepairOutcome.model_validate({
            "summary": success.summary.model_dump(mode="json", warnings=False),
            "final_result": None,
        })


def test_workflow_result_rejects_forged_critique(setup) -> None:
    import warnings

    from pydantic import ValidationError

    workflow, _, request, ontology, mapping, _ = setup
    queue = InMemoryReviewQueue()
    result = workflow.run(
        request, ontology, mapping,
        model=CountingModel(dict(BAD_UNKNOWN_FIELD)),
        repair_provider=RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD)),
        model_run_id=RUN_ID, queue=queue,
    )
    assert result.queue_entry is not None
    forged_critique = result.critique_result.model_copy(
        update={"disposition": CritiqueDisposition.SAFETY_BLOCKED}
    )
    dump = result.model_dump(mode="json", warnings=False)
    dump["critique_result"] = forged_critique.model_dump(mode="json", warnings=False)
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        with pytest.raises(ValidationError):
            AgentWorkflowResult.model_validate(dump)
    assert not emitted


def test_workflow_result_rejects_ineligible_with_entry(setup) -> None:
    from pydantic import ValidationError

    workflow, _, request, ontology, mapping, _ = setup
    success = workflow.run(
        request, ontology, mapping,
        model=CountingModel(dict(GOOD_IR)),
        repair_provider=RecordingRepairProvider(dict(GOOD_IR)),
        model_run_id=RUN_ID, queue=InMemoryReviewQueue(),
    )
    assert success.queue_status is ReviewQueueStatus.NOT_ELIGIBLE
    failure = workflow.run(
        HarnessRequest(
            request_id="agent-workflow-gap-2", database_id="test-db",
            question=QUESTION,
        ),
        ontology, mapping,
        model=CountingModel(dict(BAD_UNKNOWN_FIELD)),
        repair_provider=RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD)),
        model_run_id="synth-run-6-06-gap", queue=InMemoryReviewQueue(),
    )
    assert failure.queue_entry is not None
    dump = success.model_dump(mode="json", warnings=False)
    dump["queue_entry"] = failure.queue_entry.model_dump(mode="json", warnings=False)
    with pytest.raises(ValidationError):
        AgentWorkflowResult.model_validate(dump)

    invalid = workflow.run(
        {"request_id": "agent-workflow-invalid", "database_id": "test-db",
         "question": "   "},
        ontology, mapping,
        model=CountingModel(dict(GOOD_IR)),
        repair_provider=RecordingRepairProvider(dict(GOOD_IR)),
        model_run_id="synth-run-6-06-invalid", queue=InMemoryReviewQueue(),
    )
    assert invalid.critique_result.disposition is CritiqueDisposition.REQUEST_INVALID
    forged_entry = failure.queue_entry.model_dump(mode="json", warnings=False)
    forged_entry.update({
        "disposition": CritiqueDisposition.REQUEST_INVALID.value,
        "final_code": HarnessErrorCode.INVALID_REQUEST.value,
        "final_stage": HarnessStage.REQUEST_VALIDATED.value,
        "repair_attempted": False,
        "recovered_after_repair": False,
    })
    dump = invalid.model_dump(mode="json", warnings=False)
    dump["queue_status"] = ReviewQueueStatus.ENQUEUED.value
    dump["queue_entry"] = forged_entry
    with pytest.raises(ValidationError):
        AgentWorkflowResult.model_validate(dump)


def test_workflow_result_rejects_eligible_entry_mismatch(setup) -> None:
    from pydantic import ValidationError

    workflow, _, request, ontology, mapping, _ = setup
    result = workflow.run(
        request, ontology, mapping,
        model=CountingModel(dict(BAD_UNKNOWN_FIELD)),
        repair_provider=RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD)),
        model_run_id=RUN_ID, queue=InMemoryReviewQueue(),
    )
    assert result.queue_entry is not None
    for update in (
        {"final_code": HarnessErrorCode.UNKNOWN_ENTITY.value},
        {"repair_attempted": False},
        {"recovered_after_repair": True},
    ):
        forged_entry = result.queue_entry.model_copy(update=update)
        dump = result.model_dump(mode="json", warnings=False)
        dump["queue_entry"] = forged_entry.model_dump(mode="json", warnings=False)
        with pytest.raises(ValidationError):
            AgentWorkflowResult.model_validate(dump)


def test_inconsistent_queue_result_maps_to_fixed_internal_error(setup) -> None:
    workflow, _, request, ontology, mapping, _ = setup

    class InconsistentQueue(InMemoryReviewQueue):
        def enqueue(self, *args, **kwargs):
            registration = super().enqueue(*args, **kwargs)
            assert registration.entry is not None
            forged = registration.entry.model_copy(
                update={"disposition": CritiqueDisposition.SAFETY_BLOCKED}
            )
            return registration.model_copy(update={"entry": forged})

    with pytest.raises(AgentWorkflowError) as caught:
        workflow.run(
            request, ontology, mapping,
            model=CountingModel(dict(BAD_UNKNOWN_FIELD)),
            repair_provider=RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD)),
            model_run_id=RUN_ID,
            queue=InconsistentQueue(),
        )
    assert caught.value.code == AGENT_WORKFLOW_INTERNAL_ERROR
    assert str(caught.value) == AGENT_WORKFLOW_INTERNAL_ERROR


def test_workflow_result_rejects_swapped_final_result(setup) -> None:
    from pydantic import ValidationError

    workflow, _, request, ontology, mapping, _ = setup
    first = workflow.run(
        request, ontology, mapping,
        model=CountingModel(dict(BAD_UNKNOWN_FIELD)),
        repair_provider=RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD)),
        model_run_id=RUN_ID, queue=InMemoryReviewQueue(),
    )
    assert first.final_result is not None
    second = workflow.run(
        HarnessRequest(
            request_id="agent-workflow-gap-3", database_id="test-db",
            question=QUESTION,
        ),
        ontology, mapping,
        model=CountingModel(dict(GOOD_IR)),
        repair_provider=RecordingRepairProvider(dict(GOOD_IR)),
        model_run_id="synth-run-6-06-gap", queue=InMemoryReviewQueue(),
    )
    assert second.final_result is not None
    dump = first.model_dump(mode="json", warnings=False)
    dump["final_result"] = second.final_result.model_dump(mode="json", warnings=False)
    with pytest.raises(ValidationError):
        AgentWorkflowResult.model_validate(dump)


def test_workflow_result_rejects_constructed_nested_summary(setup) -> None:
    from pydantic import ValidationError

    workflow, _, request, ontology, mapping, _ = setup
    result = workflow.run(
        request, ontology, mapping,
        model=CountingModel(dict(GOOD_IR)),
        repair_provider=RecordingRepairProvider(dict(GOOD_IR)),
        model_run_id=RUN_ID, queue=InMemoryReviewQueue(),
    )
    forged = RepairSummary.model_construct(
        initial_status=HarnessStatus.SUCCEEDED,
        initial_stage=HarnessStage.RESULT_VALIDATED,
        initial_code=None,
        repair_attempted=False,
        repair_attempts=0,
        final_status=HarnessStatus.SUCCEEDED,
        final_stage=HarnessStage.RESULT_VALIDATED,
        final_code=None,
        succeeded=True,
    )
    dump = result.model_dump(mode="json", warnings=False)
    dump["repair_summary"] = forged.model_dump(mode="json", warnings=False)
    # Constructed summary happens to agree here only if critique also
    # matches; mutate the critique instead to force a mismatch via a
    # constructed nested instance path.
    dump["critique_result"] = result.critique_result.model_dump(
        mode="json", warnings=False
    )
    dump["critique_result"]["disposition"] = CritiqueDisposition.MODEL_REVIEW.value
    with pytest.raises(ValidationError):
        AgentWorkflowResult.model_validate(dump)


def test_constructed_request_instance_follows_invalid_request(setup) -> None:
    workflow, executor, _, ontology, mapping, _ = setup
    forged = HarnessRequest.model_construct(
        request_id="agent-workflow-gap-4", database_id="test-db", question="  ",
    )
    model = CountingModel(dict(GOOD_IR))
    provider = RecordingRepairProvider(dict(GOOD_IR))
    queue = InMemoryReviewQueue()
    result = workflow.run(
        forged, ontology, mapping, model=model,
        repair_provider=provider, model_run_id=RUN_ID, queue=queue,
    )
    assert result.repair_summary.final_code == HarnessErrorCode.INVALID_REQUEST.value
    assert result.critique_result.disposition is CritiqueDisposition.REQUEST_INVALID
    assert result.queue_status is ReviewQueueStatus.NOT_ELIGIBLE
    assert result.queue_entry is None
    assert len(queue) == 0
    assert model.calls == 0 and provider.calls == 0 and executor.calls == 0
    assert result.final_result is not None
    assert result.final_result.status is HarnessStatus.FAILED


def test_model_copy_mutated_request_instance_follows_invalid_request(setup) -> None:
    workflow, executor, request, ontology, mapping, _ = setup
    mutated = request.model_copy(update={"question": "  "})
    model = CountingModel(dict(GOOD_IR))
    provider = RecordingRepairProvider(dict(GOOD_IR))
    queue = InMemoryReviewQueue()
    result = workflow.run(
        mutated, ontology, mapping, model=model,
        repair_provider=provider, model_run_id=RUN_ID, queue=queue,
    )
    assert result.repair_summary.final_code == HarnessErrorCode.INVALID_REQUEST.value
    assert result.critique_result.disposition is CritiqueDisposition.REQUEST_INVALID
    assert result.queue_status is ReviewQueueStatus.NOT_ELIGIBLE
    assert result.queue_entry is None
    assert len(queue) == 0
    assert model.calls == 0 and provider.calls == 0 and executor.calls == 0
