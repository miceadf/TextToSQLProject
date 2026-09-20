"""Synthetic tests for the in-memory operational-failure review queue.

Every test uses injected models and repair providers only; no network,
no real models, no evaluation scores, no disk/DB access. Each coordinator
case runs a single request (denominator 1) unless the name states
otherwise.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import tempfile
from pathlib import Path

import pytest

from backend.adaptive.harness import (
    CRITIQUE_INVALID_SUMMARY,
    BoundedRepairCoordinator,
    CritiqueDisposition,
    CritiqueError,
    DataType,
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
)
from backend.adaptive.harness.compiler import SingleTableSQLiteCompiler
from backend.adaptive.harness.policy import ConservativeSqlPolicy
from backend.adaptive.harness.repair import REPAIR_PROVIDER_ERROR
from backend.adaptive.harness.review_queue import (
    REVIEW_QUEUE_CONFLICT,
    REVIEW_QUEUE_INVALID_INPUT,
    REVIEW_QUEUE_VERSION,
    InMemoryReviewQueue,
    ReviewQueueError,
    ReviewQueueErrorCode,
    ReviewQueueStatus,
    model_run_id_fingerprint,
    review_request_id_fingerprint,
)


QUESTION = "합성 테스트 질문"
RUN_ID = "synth-run-6-04-a"
OTHER_RUN_ID = "synth-run-6-04-b"


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
    coordinator = BoundedRepairCoordinator(
        executor,
        compiler=SingleTableSQLiteCompiler(),
        sql_policy=ConservativeSqlPolicy(),
    )
    request = HarnessRequest(
        request_id="review-queue-test-1", database_id="test-db", question=QUESTION,
    )
    yield coordinator, executor, request, _ontology(), _mapping()
    temporary.cleanup()


def _failed_summary(code: str, stage: HarnessStage | None) -> RepairSummary:
    return RepairSummary(
        initial_status=HarnessStatus.FAILED,
        initial_stage=HarnessStage.IR_VALIDATED,
        initial_code=HarnessErrorCode.UNKNOWN_FIELD.value,
        repair_attempted=True,
        repair_attempts=1,
        final_status=HarnessStatus.FAILED,
        final_stage=stage,
        final_code=code,
        succeeded=False,
    )


def test_coordinator_success_is_not_eligible(setup) -> None:
    coordinator, _, request, ontology, mapping = setup
    summary = coordinator.run(
        request, ontology, mapping,
        model=CountingModel(GOOD_IR),
        repair_provider=RecordingRepairProvider(GOOD_IR),
    )
    assert summary.succeeded is True
    queue = InMemoryReviewQueue()
    registration = queue.enqueue(summary, model_run_id=RUN_ID, request_id=request.request_id)
    assert registration.status is ReviewQueueStatus.NOT_ELIGIBLE
    assert registration.entry is None
    assert len(queue) == 0
    assert queue.list_entries() == ()


def test_coordinator_input_failure_is_not_eligible(setup) -> None:
    coordinator, _, _, ontology, mapping = setup
    provider = RecordingRepairProvider(GOOD_IR)
    summary = coordinator.run(
        {"database_id": "test-db"},
        ontology, mapping,
        model=CountingModel(GOOD_IR), repair_provider=provider,
    )
    assert summary.final_code == HarnessErrorCode.INVALID_REQUEST.value
    queue = InMemoryReviewQueue()
    registration = queue.enqueue(
        summary, model_run_id=RUN_ID, request_id="synth-request-invalid-1"
    )
    assert registration.status is ReviewQueueStatus.NOT_ELIGIBLE
    assert registration.entry is None
    assert len(queue) == 0


def test_coordinator_model_failure_enqueues_fixed_metadata_only(setup) -> None:
    coordinator, _, request, ontology, mapping = setup
    bad = {"entity": "Course", "select": ["Course.unknown"], "filters": [], "limit": 50}
    summary = coordinator.run(
        request, ontology, mapping,
        model=CountingModel(bad),
        repair_provider=RecordingRepairProvider(dict(bad)),
    )
    assert summary.succeeded is False
    queue = InMemoryReviewQueue()
    registration = queue.enqueue(summary, model_run_id=RUN_ID, request_id=request.request_id)
    assert registration.status is ReviewQueueStatus.ENQUEUED
    assert registration.entry is not None
    entry = registration.entry
    assert entry.queue_version == REVIEW_QUEUE_VERSION
    assert entry.disposition is CritiqueDisposition.MODEL_REVIEW
    assert entry.final_code == HarnessErrorCode.UNKNOWN_FIELD.value
    assert entry.final_stage is HarnessStage.IR_VALIDATED
    assert entry.repair_attempted is True
    assert entry.recovered_after_repair is False
    assert entry.model_run_id_sha256 == model_run_id_fingerprint(RUN_ID)
    assert entry.request_id_sha256 == review_request_id_fingerprint(request.request_id)
    assert len(queue) == 1
    # Fixed metadata only: no raw content in the stored entry.
    dumped = entry.model_dump(mode="json")
    assert QUESTION not in str(dumped)
    assert "courses" not in str(dumped).lower()
    assert "SELECT" not in str(dumped)
    assert RUN_ID not in str(dumped)
    assert request.request_id not in str(dumped)


def test_coordinator_repair_provider_failure_enqueues_model_review(setup) -> None:
    coordinator, _, request, ontology, mapping = setup
    summary = coordinator.run(
        request, ontology, mapping,
        model=CountingModel({}),
        repair_provider=RecordingRepairProvider(error=RuntimeError("비밀-원문")),
    )
    assert summary.final_code == REPAIR_PROVIDER_ERROR
    queue = InMemoryReviewQueue()
    registration = queue.enqueue(summary, model_run_id=RUN_ID, request_id=request.request_id)
    assert registration.status is ReviewQueueStatus.ENQUEUED
    assert registration.entry is not None
    assert registration.entry.disposition is CritiqueDisposition.MODEL_REVIEW
    assert registration.entry.final_code == REPAIR_PROVIDER_ERROR
    assert registration.entry.final_stage is None
    assert len(queue) == 1


@pytest.mark.parametrize(("code", "stage", "disposition"), [
    (HarnessErrorCode.MAPPING_MISSING.value, HarnessStage.IR_VALIDATED,
     CritiqueDisposition.CONTEXT_REVIEW),
    (HarnessErrorCode.UNSAFE_SQL.value, HarnessStage.SQL_VALIDATED,
     CritiqueDisposition.SAFETY_BLOCKED),
    (HarnessErrorCode.EXECUTION_TIMEOUT.value, HarnessStage.EXECUTED,
     CritiqueDisposition.EXECUTION_FAILURE),
    (HarnessErrorCode.SQL_COMPILE_ERROR.value, HarnessStage.SQL_COMPILED,
     CritiqueDisposition.INTERNAL_REVIEW),
])
def test_direct_context_policy_execution_internal_failures_enqueue(code, stage, disposition) -> None:
    queue = InMemoryReviewQueue()
    registration = queue.enqueue(
        _failed_summary(code, stage),
        model_run_id=RUN_ID, request_id="synth-request-direct-1",
    )
    assert registration.status is ReviewQueueStatus.ENQUEUED
    assert registration.entry is not None
    assert registration.entry.disposition is disposition
    assert registration.entry.final_code == code
    assert registration.entry.final_stage is stage
    assert len(queue) == 1


def test_same_entry_reregistration_is_already_present(setup) -> None:
    coordinator, _, request, ontology, mapping = setup
    bad = {"entity": "Course", "select": ["Course.unknown"], "filters": [], "limit": 50}
    summary = coordinator.run(
        request, ontology, mapping,
        model=CountingModel(bad),
        repair_provider=RecordingRepairProvider(dict(bad)),
    )
    queue = InMemoryReviewQueue()
    first = queue.enqueue(summary, model_run_id=RUN_ID, request_id=request.request_id)
    second = queue.enqueue(summary, model_run_id=RUN_ID, request_id=request.request_id)
    assert first.status is ReviewQueueStatus.ENQUEUED
    assert second.status is ReviewQueueStatus.ALREADY_PRESENT
    assert first.entry is not None and second.entry is not None
    assert first.entry == second.entry
    assert len(queue) == 1
    assert len(queue.list_entries()) == 1


def test_same_key_different_result_is_fixed_conflict(setup) -> None:
    coordinator, _, request, ontology, mapping = setup
    bad = {"entity": "Course", "select": ["Course.unknown"], "filters": [], "limit": 50}
    summary = coordinator.run(
        request, ontology, mapping,
        model=CountingModel(bad),
        repair_provider=RecordingRepairProvider(dict(bad)),
    )
    queue = InMemoryReviewQueue()
    first = queue.enqueue(summary, model_run_id=RUN_ID, request_id=request.request_id)
    assert first.status is ReviewQueueStatus.ENQUEUED
    other = _failed_summary(HarnessErrorCode.UNSAFE_SQL.value, HarnessStage.SQL_VALIDATED)
    with pytest.raises(ReviewQueueError) as exc_info:
        queue.enqueue(other, model_run_id=RUN_ID, request_id=request.request_id)
    assert str(exc_info.value) == REVIEW_QUEUE_CONFLICT
    assert exc_info.value.code == REVIEW_QUEUE_CONFLICT
    # Existing entry is unchanged and still listed alone.
    assert len(queue) == 1
    stored = queue.list_entries()[0]
    assert first.entry is not None
    assert stored == first.entry
    assert stored.final_code == HarnessErrorCode.UNKNOWN_FIELD.value


def test_different_keys_are_separate_entries_in_order() -> None:
    queue = InMemoryReviewQueue()
    first = queue.enqueue(
        _failed_summary(HarnessErrorCode.UNKNOWN_FIELD.value, HarnessStage.IR_VALIDATED),
        model_run_id=RUN_ID, request_id="synth-request-order-1",
    )
    second = queue.enqueue(
        _failed_summary(HarnessErrorCode.UNSAFE_SQL.value, HarnessStage.SQL_VALIDATED),
        model_run_id=RUN_ID, request_id="synth-request-order-2",
    )
    third = queue.enqueue(
        _failed_summary(HarnessErrorCode.UNKNOWN_FIELD.value, HarnessStage.IR_VALIDATED),
        model_run_id=OTHER_RUN_ID, request_id="synth-request-order-1",
    )
    assert [r.status for r in (first, second, third)] == [
        ReviewQueueStatus.ENQUEUED] * 3
    entries = queue.list_entries()
    assert len(entries) == 3
    assert entries[0].request_id_sha256 == review_request_id_fingerprint("synth-request-order-1")
    assert entries[1].request_id_sha256 == review_request_id_fingerprint("synth-request-order-2")
    assert entries[2].model_run_id_sha256 == model_run_id_fingerprint(OTHER_RUN_ID)


def test_forged_repair_count_and_code_are_blocked_before_enqueue() -> None:
    import warnings

    queue = InMemoryReviewQueue()
    valid = _failed_summary(HarnessErrorCode.UNKNOWN_FIELD.value, HarnessStage.IR_VALIDATED)
    for forged in (
        valid.model_copy(update={"repair_attempts": 2}),
        valid.model_copy(update={"final_code": ["SYNTHETICSECRET123"]}),
    ):
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter("always")
            with pytest.raises(CritiqueError) as exc_info:
                queue.enqueue(
                    forged, model_run_id=RUN_ID, request_id="synth-request-forged-1"
                )
        assert str(exc_info.value) == CRITIQUE_INVALID_SUMMARY
        assert not emitted
    assert len(queue) == 0


@pytest.mark.parametrize("bad_id", ["", "   ", "x" * 201, None, 123, ["synth-run"]])
def test_input_id_errors_are_blocked_before_classification(bad_id) -> None:
    queue = InMemoryReviewQueue()
    summary = _failed_summary(HarnessErrorCode.UNKNOWN_FIELD.value, HarnessStage.IR_VALIDATED)
    with pytest.raises(ReviewQueueError) as exc_info:
        queue.enqueue(summary, model_run_id=bad_id, request_id="synth-request-id-1")
    assert str(exc_info.value) == REVIEW_QUEUE_INVALID_INPUT
    assert exc_info.value.code == REVIEW_QUEUE_INVALID_INPUT
    with pytest.raises(ReviewQueueError) as exc_info:
        queue.enqueue(summary, model_run_id=RUN_ID, request_id=bad_id)
    assert str(exc_info.value) == REVIEW_QUEUE_INVALID_INPUT
    assert len(queue) == 0


def test_critique_failure_leaves_queue_unchanged() -> None:
    queue = InMemoryReviewQueue()
    first = queue.enqueue(
        _failed_summary(HarnessErrorCode.UNKNOWN_FIELD.value, HarnessStage.IR_VALIDATED),
        model_run_id=RUN_ID, request_id="synth-request-stable-1",
    )
    assert first.status is ReviewQueueStatus.ENQUEUED
    with pytest.raises(CritiqueError):
        queue.enqueue(
            _failed_summary("SOME_NEW_CODE", HarnessStage.EXECUTED),
            model_run_id=RUN_ID, request_id="synth-request-stable-2",
        )
    assert len(queue) == 1
    assert queue.list_entries()[0] == first.entry


def test_returned_and_listed_entries_cannot_mutate_queue(setup) -> None:
    coordinator, _, request, ontology, mapping = setup
    bad = {"entity": "Course", "select": ["Course.unknown"], "filters": [], "limit": 50}
    summary = coordinator.run(
        request, ontology, mapping,
        model=CountingModel(bad),
        repair_provider=RecordingRepairProvider(dict(bad)),
    )
    queue = InMemoryReviewQueue()
    registration = queue.enqueue(summary, model_run_id=RUN_ID, request_id=request.request_id)
    assert registration.entry is not None
    listed = queue.list_entries()
    assert len(listed) == 1
    # Frozen entries reject field assignment; copies must not share identity.
    with pytest.raises(Exception):
        listed[0].final_code = "MUTATED"  # type: ignore[misc]
    assert registration.entry is not listed[0]
    again = queue.list_entries()
    assert again[0] == listed[0]
    assert again[0] is not listed[0]
    assert len(queue) == 1


def test_path_and_secret_like_ids_leave_no_raw_trace(setup, capsys, caplog) -> None:
    import io
    from contextlib import redirect_stdout

    coordinator, _, request, ontology, mapping = setup
    bad = {"entity": "Course", "select": ["Course.unknown"], "filters": [], "limit": 50}
    summary = coordinator.run(
        request, ontology, mapping,
        model=CountingModel(bad),
        repair_provider=RecordingRepairProvider(dict(bad)),
    )
    path_like_run = "..\\synth_q7f3a\\secret\\run-id"
    secret_like_request = "AKIAIOSFODNN7_SYNTH_Q7F3A_SECRET"
    queue = InMemoryReviewQueue()
    buffer = io.StringIO()
    with caplog.at_level(logging.DEBUG):
        with redirect_stdout(buffer):
            registration = queue.enqueue(
                summary, model_run_id=path_like_run, request_id=secret_like_request
            )
            print(registration)
            print(queue.list_entries())
            found = queue.get(registration.entry.idempotency_key) if registration.entry else None
            print(found)
    assert registration.status is ReviewQueueStatus.ENQUEUED
    assert registration.entry is not None
    assert registration.entry.model_run_id_sha256 == model_run_id_fingerprint(path_like_run)
    assert registration.entry.request_id_sha256 == review_request_id_fingerprint(
        secret_like_request
    )
    captured = buffer.getvalue() + str(registration) + str(queue.list_entries()) + caplog.text
    assert path_like_run not in captured
    assert secret_like_request not in captured
    assert QUESTION not in captured
    assert "SELECT" not in captured
    assert "courses" not in captured.lower()
    # Fixed conflict errors also carry no raw IDs.
    other = _failed_summary(HarnessErrorCode.UNSAFE_SQL.value, HarnessStage.SQL_VALIDATED)
    try:
        queue.enqueue(other, model_run_id=path_like_run, request_id=secret_like_request)
        raise AssertionError("conflict expected")
    except ReviewQueueError as exc:
        assert path_like_run not in str(exc)
        assert secret_like_request not in str(exc)
    # Invalid-ID errors carry no raw content either.
    try:
        queue.enqueue(summary, model_run_id="", request_id=secret_like_request)
        raise AssertionError("invalid input expected")
    except ReviewQueueError as exc:
        assert secret_like_request not in str(exc)


def test_hash_rule_is_canonical_utf8_and_documented() -> None:
    run_id = "합성-실행-아이디"
    request_id = "합성-요청-아이디"
    expected_run = hashlib.sha256(
        ("review-queue-v1:model-run-id:" + run_id).encode("utf-8")
    ).hexdigest()
    expected_request = hashlib.sha256(
        ("review-queue-v1:request-id:" + request_id).encode("utf-8")
    ).hexdigest()
    assert model_run_id_fingerprint(run_id) == expected_run
    assert review_request_id_fingerprint(request_id) == expected_request
    queue = InMemoryReviewQueue()
    registration = queue.enqueue(
        _failed_summary(HarnessErrorCode.UNKNOWN_FIELD.value, HarnessStage.IR_VALIDATED),
        model_run_id=run_id, request_id=request_id,
    )
    assert registration.entry is not None
    assert registration.entry.model_run_id_sha256 == expected_run
    assert registration.entry.request_id_sha256 == expected_request


def test_queue_makes_no_model_provider_or_executor_calls(setup) -> None:
    coordinator, executor, request, ontology, mapping = setup
    model = CountingModel(GOOD_IR)
    provider = RecordingRepairProvider(GOOD_IR)
    summary = coordinator.run(
        request, ontology, mapping, model=model, repair_provider=provider,
    )
    bad = {"entity": "Course", "select": ["Course.unknown"], "filters": [], "limit": 50}
    second_model = CountingModel(bad)
    second_provider = RecordingRepairProvider(dict(bad))
    other = coordinator.run(
        HarnessRequest(
            request_id="review-queue-test-2", database_id="test-db", question=QUESTION,
        ),
        ontology, mapping,
        model=second_model, repair_provider=second_provider,
    )
    # Queue operations below must not touch models, providers, or executors.
    model_calls = (model.calls, second_model.calls)
    provider_calls = (provider.calls, second_provider.calls)
    executor_calls = executor.calls
    queue = InMemoryReviewQueue()
    queue.enqueue(summary, model_run_id=RUN_ID, request_id=request.request_id)
    queue.enqueue(other, model_run_id=RUN_ID, request_id="review-queue-test-2")
    queue.list_entries()
    assert (model.calls, second_model.calls) == model_calls
    assert (provider.calls, second_provider.calls) == provider_calls
    assert executor.calls == executor_calls


def test_queue_module_has_no_io_or_provider_access() -> None:
    import ast
    import inspect

    import backend.adaptive.harness.review_queue as module

    tree = ast.parse(inspect.getsource(module))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0].lower())
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0].lower())
    for token in ("sqlite3", "socket", "urllib", "requests", "ollama"):
        assert token not in imported
    # The queue holds no executor/provider/model/DB handles.
    queue = InMemoryReviewQueue()
    assert not any(
        hasattr(queue, name)
        for name in ("executor", "provider", "model", "connection", "client")
    )
