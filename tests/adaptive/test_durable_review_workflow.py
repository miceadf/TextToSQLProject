"""Synthetic tests for the Phase 6-07 durable review lifecycle.

Every test uses injected models, repair providers, read-only SQLite, and
private snapshots under a temporary git repository only; no network,
Ollama, PostgreSQL, CNU data, UI, approvals, or evaluation scores. Each
lifecycle test runs a single request (denominator 1) unless the name
states otherwise.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import pytest
from pydantic import BaseModel

from backend.adaptive.harness import (
    AGENT_WORKFLOW_VERSION,
    AgentWorkflowCoordinator,
    AgentWorkflowError,
    AgentWorkflowErrorCode,
    AgentWorkflowResult,
    CritiqueDisposition,
    DataType,
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
    ReviewQueueStatus,
    SQLiteReadOnlyExecutor,
)
from backend.adaptive.harness.compiler import SingleTableSQLiteCompiler
from backend.adaptive.harness.durable_review import (
    DURABLE_REVIEW_CONFLICT,
    DURABLE_REVIEW_INTERNAL_ERROR,
    DURABLE_REVIEW_INVALID_INPUT,
    DURABLE_REVIEW_STORAGE_ERROR,
    DURABLE_REVIEW_WORKFLOW_VERSION,
    DurableReviewPersistence,
    DurableReviewWorkflowCoordinator,
    DurableReviewWorkflowError,
    DurableReviewWorkflowResult,
    restore_review_queue,
)
from backend.adaptive.harness.policy import ConservativeSqlPolicy
from backend.adaptive.harness.review_queue_storage import (
    ReviewQueueSnapshot,
    load_review_queue_snapshot,
    snapshot_payload_sha256,
    write_review_queue_snapshot,
)

QUESTION = "합성 테스트 질문"
RUN_ID = "synth-run-6-07-a"
OTHER_RUN_ID = "synth-run-6-07-b"
SECRET_LIKE_RUN = "AKIAIOSFODNN7_SYNTH_Q7F3A_SECRET_RUN"


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


def _mapping() -> MappingProfile:
    return MappingProfile(
        database_id="test-db",
        version=1,
        status=MappingStatus.PUBLISHED,
        fields={
            "Course.name": FieldMapping(table="courses", column="subject_name"),
            "Course.credits": FieldMapping(table="courses", column="credit_hours"),
            "Course.category": FieldMapping(table="courses", column="category"),
        },
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

BAD_UNKNOWN_ENTITY = {
    "entity": "Nope", "select": ["Course.name"], "filters": [], "limit": 50,
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
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(root)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (root / ".gitignore").write_text(".schema-adaptive/\n", encoding="utf-8")
    return root


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
    durable = DurableReviewWorkflowCoordinator(workflow)
    request = HarnessRequest(
        request_id="durable-test-1", database_id="test-db", question=QUESTION,
    )
    yield durable, executor, request, _ontology(), _mapping()
    temporary.cleanup()


def _snapshot_path(repository: Path, name: str) -> Path:
    return repository / ".schema-adaptive" / "review-queue" / name


def _run_empty(
    durable: DurableReviewWorkflowCoordinator,
    executor: CountingExecutor,
    request: HarnessRequest,
    ontology: Ontology,
    mapping: MappingProfile,
    repository: Path,
    name: str,
    *,
    model_response: object,
    provider_response: object,
    run_id: str = RUN_ID,
):
    model = CountingModel(model_response)
    provider = RecordingRepairProvider(provider_response)
    result = durable.run(
        request,
        ontology,
        mapping,
        model=model,
        repair_provider=provider,
        model_run_id=run_id,
        current_snapshot=None,
        next_snapshot=_snapshot_path(repository, name),
        repository_root=repository,
    )
    return result, model, provider


# ---- restore boundary ----


def test_restore_preserves_order_identity_and_keys(repository: Path) -> None:
    queue = InMemoryReviewQueue()
    first = queue.enqueue(
        _failed_summary(HarnessErrorCode.UNKNOWN_FIELD.value, HarnessStage.IR_VALIDATED),
        model_run_id=RUN_ID,
        request_id="synth-request-6-07-1",
    )
    second = queue.enqueue(
        _failed_summary(HarnessErrorCode.UNSAFE_SQL.value, HarnessStage.SQL_VALIDATED),
        model_run_id=OTHER_RUN_ID,
        request_id="synth-request-6-07-2",
    )
    assert first.status is ReviewQueueStatus.ENQUEUED
    assert second.status is ReviewQueueStatus.ENQUEUED
    target = _snapshot_path(repository, "restore-source.json")
    write_review_queue_snapshot(target, queue, repository_root=repository)
    loaded = load_review_queue_snapshot(target, repository_root=repository)
    restored = restore_review_queue(loaded)
    restored_entries = restored.list_entries()
    source_entries = queue.list_entries()
    assert len(restored_entries) == 2
    assert [entry.idempotency_key for entry in restored_entries] == [
        entry.idempotency_key for entry in source_entries
    ]
    assert [(e.model_run_id_sha256, e.request_id_sha256) for e in restored_entries] == [
        (e.model_run_id_sha256, e.request_id_sha256) for e in source_entries
    ]
    # Defensive copies: distinct objects with equal content.
    assert restored_entries[0] is not source_entries[0]
    assert restored_entries[0] == source_entries[0]


def test_restore_rejects_forged_instance() -> None:
    forged = ReviewQueueSnapshot.model_construct(
        schema_version="review-queue-snapshot-v1",
        storage_tool_version="review-queue-storage-v1",
        queue_version="review-queue-v1",
        entry_count=1,
        entries=[
            {
                "queue_version": "review-queue-v1",
                "model_run_id_sha256": "0" * 64,
                "request_id_sha256": "1" * 64,
                "disposition": "EXECUTED_UNVERIFIED",
                "final_code": "INVALID_REQUEST",
                "final_stage": "REQUEST_VALIDATED",
                "repair_attempted": False,
                "recovered_after_repair": False,
                "idempotency_key": "forged",
            }
        ],
        payload_sha256="2" * 64,
    )
    with pytest.raises(DurableReviewWorkflowError) as exc_info:
        restore_review_queue(forged)
    assert exc_info.value.code == DURABLE_REVIEW_INVALID_INPUT
    assert str(exc_info.value) == DURABLE_REVIEW_INVALID_INPUT


def test_restore_rejects_duplicate_identity_and_bad_version(repository: Path) -> None:
    queue = InMemoryReviewQueue()
    queue.enqueue(
        _failed_summary(HarnessErrorCode.UNKNOWN_FIELD.value, HarnessStage.IR_VALIDATED),
        model_run_id=RUN_ID,
        request_id="synth-request-6-07-1",
    )
    target = _snapshot_path(repository, "restore-dupe-source.json")
    write_review_queue_snapshot(target, queue, repository_root=repository)
    document = json.loads(target.read_bytes().decode("utf-8"))
    entries = document["entries"]
    entries.append(dict(entries[0]))
    payload = {key: document[key] for key in document if key != "payload_sha256"}
    payload["entries"] = entries
    payload["entry_count"] = len(entries)
    document = dict(payload)
    document["payload_sha256"] = snapshot_payload_sha256(payload)
    with pytest.raises(DurableReviewWorkflowError) as exc_info:
        restore_review_queue(document)
    assert exc_info.value.code == DURABLE_REVIEW_INVALID_INPUT

    bad_version = json.loads(target.read_bytes().decode("utf-8"))
    bad_version["schema_version"] = "review-queue-snapshot-v9"
    with pytest.raises(DurableReviewWorkflowError):
        restore_review_queue(bad_version)


def test_restore_rejects_hash_and_count_mismatch(repository: Path) -> None:
    queue = InMemoryReviewQueue()
    queue.enqueue(
        _failed_summary(HarnessErrorCode.UNKNOWN_FIELD.value, HarnessStage.IR_VALIDATED),
        model_run_id=RUN_ID,
        request_id="synth-request-6-07-1",
    )
    target = _snapshot_path(repository, "restore-tamper-source.json")
    write_review_queue_snapshot(target, queue, repository_root=repository)
    tampered = json.loads(target.read_bytes().decode("utf-8"))
    tampered["payload_sha256"] = "0" * 64
    with pytest.raises(DurableReviewWorkflowError) as exc_info:
        restore_review_queue(tampered)
    assert exc_info.value.code == DURABLE_REVIEW_INVALID_INPUT

    miscounted = json.loads(target.read_bytes().decode("utf-8"))
    miscounted["entry_count"] = 99
    with pytest.raises(DurableReviewWorkflowError):
        restore_review_queue(miscounted)


def _failed_summary(code: str, stage: HarnessStage | None):
    from backend.adaptive.harness import RepairSummary

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


# ---- durable lifecycle ----


def test_empty_start_eligible_failure_writes_one_entry(setup, repository: Path) -> None:
    durable, executor, request, ontology, mapping = setup
    result, model, provider = _run_empty(
        durable,
        executor,
        request,
        ontology,
        mapping,
        repository,
        "next-1.json",
        model_response=dict(BAD_UNKNOWN_FIELD),
        provider_response=dict(BAD_UNKNOWN_FIELD),
    )
    assert isinstance(result, DurableReviewWorkflowResult)
    assert result.workflow_version == DURABLE_REVIEW_WORKFLOW_VERSION
    assert result.workflow_result.workflow_version == AGENT_WORKFLOW_VERSION
    assert result.workflow_result.queue_status is ReviewQueueStatus.ENQUEUED
    assert result.persistence is DurableReviewPersistence.WRITTEN
    assert result.entry_count == 1
    assert result.snapshot_hash is not None and len(result.snapshot_hash) == 64
    assert model.calls == 1 and provider.calls == 1 and executor.calls == 0
    # Call budget preserved: one workflow execution, no retry loop.
    assert result.workflow_result.repair_summary.repair_attempts == 1
    stored = load_review_queue_snapshot(
        _snapshot_path(repository, "next-1.json"), repository_root=repository
    )
    assert stored.entry_count == 1
    assert stored.payload_sha256 == result.snapshot_hash
    assert stored.entries[0] == result.workflow_result.queue_entry


def test_existing_snapshot_new_failure_appends_preserving_source(
    setup, repository: Path
) -> None:
    durable, executor, request, ontology, mapping = setup
    first, _, _ = _run_empty(
        durable,
        executor,
        request,
        ontology,
        mapping,
        repository,
        "chain-1.json",
        model_response=dict(BAD_UNKNOWN_FIELD),
        provider_response=dict(BAD_UNKNOWN_FIELD),
    )
    assert first.persistence is DurableReviewPersistence.WRITTEN
    source_path = _snapshot_path(repository, "chain-1.json")
    source_bytes = source_path.read_bytes()
    second_request = HarnessRequest(
        request_id="durable-test-2", database_id="test-db", question=QUESTION,
    )
    model = CountingModel(dict(BAD_UNKNOWN_FIELD))
    provider = RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD))
    second = durable.run(
        second_request,
        ontology,
        mapping,
        model=model,
        repair_provider=provider,
        model_run_id=OTHER_RUN_ID,
        current_snapshot=source_path,
        next_snapshot=_snapshot_path(repository, "chain-2.json"),
        repository_root=repository,
    )
    assert second.workflow_result.queue_status is ReviewQueueStatus.ENQUEUED
    assert second.persistence is DurableReviewPersistence.WRITTEN
    assert second.entry_count == 2
    assert source_path.read_bytes() == source_bytes
    stored = load_review_queue_snapshot(
        _snapshot_path(repository, "chain-2.json"), repository_root=repository
    )
    assert [entry.request_id_sha256 for entry in stored.entries][0] == (
        load_review_queue_snapshot(source_path, repository_root=repository)
        .entries[0]
        .request_id_sha256
    )
    assert stored.entries[1] == second.workflow_result.queue_entry
    assert stored.entries[0] != stored.entries[1]


def test_same_source_same_result_is_already_present_unchanged(
    setup, repository: Path
) -> None:
    durable, executor, request, ontology, mapping = setup
    first, _, _ = _run_empty(
        durable,
        executor,
        request,
        ontology,
        mapping,
        repository,
        "idem-1.json",
        model_response=dict(BAD_UNKNOWN_FIELD),
        provider_response=dict(BAD_UNKNOWN_FIELD),
    )
    assert first.persistence is DurableReviewPersistence.WRITTEN
    model = CountingModel(dict(BAD_UNKNOWN_FIELD))
    provider = RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD))
    second = durable.run(
        request,
        ontology,
        mapping,
        model=model,
        repair_provider=provider,
        model_run_id=RUN_ID,
        current_snapshot=_snapshot_path(repository, "idem-1.json"),
        next_snapshot=_snapshot_path(repository, "idem-2.json"),
        repository_root=repository,
    )
    assert second.workflow_result.queue_status is ReviewQueueStatus.ALREADY_PRESENT
    assert second.persistence is DurableReviewPersistence.UNCHANGED
    assert second.snapshot_hash is None
    assert second.workflow_result.queue_entry == first.workflow_result.queue_entry
    assert not _snapshot_path(repository, "idem-2.json").exists()


def test_success_empty_success_and_request_invalid_write_nothing(
    setup, repository: Path
) -> None:
    durable, executor, request, ontology, mapping = setup
    good, _, _ = _run_empty(
        durable,
        executor,
        request,
        ontology,
        mapping,
        repository,
        "success-next.json",
        model_response=dict(GOOD_IR),
        provider_response=dict(GOOD_IR),
    )
    assert good.workflow_result.queue_status is ReviewQueueStatus.NOT_ELIGIBLE
    assert good.persistence is DurableReviewPersistence.UNCHANGED
    assert good.snapshot_hash is None
    assert not _snapshot_path(repository, "success-next.json").exists()

    empty_ir = dict(
        GOOD_IR,
        filters=[{"field": "Course.credits", "operator": "equals", "value": 999}],
    )
    empty_request = HarnessRequest(
        request_id="durable-test-empty", database_id="test-db", question=QUESTION,
    )
    empty_result = durable.run(
        empty_request,
        ontology,
        mapping,
        model=CountingModel(empty_ir),
        repair_provider=RecordingRepairProvider(dict(GOOD_IR)),
        model_run_id=RUN_ID,
        current_snapshot=None,
        next_snapshot=_snapshot_path(repository, "empty-next.json"),
        repository_root=repository,
    )
    assert empty_result.workflow_result.queue_status is ReviewQueueStatus.NOT_ELIGIBLE
    assert empty_result.persistence is DurableReviewPersistence.UNCHANGED
    assert empty_result.workflow_result.final_result is not None
    assert empty_result.workflow_result.final_result.rows == []
    assert not _snapshot_path(repository, "empty-next.json").exists()

    invalid_result = durable.run(
        {"request_id": "durable-invalid-1", "database_id": "test-db", "question": ""},
        ontology,
        mapping,
        model=CountingModel(dict(GOOD_IR)),
        repair_provider=RecordingRepairProvider(dict(GOOD_IR)),
        model_run_id=RUN_ID,
        current_snapshot=None,
        next_snapshot=_snapshot_path(repository, "invalid-next.json"),
        repository_root=repository,
    )
    assert (
        invalid_result.workflow_result.critique_result.disposition
        is CritiqueDisposition.REQUEST_INVALID
    )
    assert invalid_result.workflow_result.queue_status is ReviewQueueStatus.NOT_ELIGIBLE
    assert invalid_result.persistence is DurableReviewPersistence.UNCHANGED
    assert not _snapshot_path(repository, "invalid-next.json").exists()


def test_same_identity_different_result_is_conflict(setup, repository: Path) -> None:
    durable, executor, request, ontology, mapping = setup
    first, _, _ = _run_empty(
        durable,
        executor,
        request,
        ontology,
        mapping,
        repository,
        "conflict-1.json",
        model_response=dict(BAD_UNKNOWN_FIELD),
        provider_response=dict(BAD_UNKNOWN_FIELD),
    )
    assert first.persistence is DurableReviewPersistence.WRITTEN
    source_path = _snapshot_path(repository, "conflict-1.json")
    source_bytes = source_path.read_bytes()
    with pytest.raises(DurableReviewWorkflowError) as exc_info:
        durable.run(
            request,
            ontology,
            mapping,
            model=CountingModel(dict(BAD_UNKNOWN_ENTITY)),
            repair_provider=RecordingRepairProvider(dict(BAD_UNKNOWN_ENTITY)),
            model_run_id=RUN_ID,
            current_snapshot=source_path,
            next_snapshot=_snapshot_path(repository, "conflict-2.json"),
            repository_root=repository,
        )
    assert exc_info.value.code == DURABLE_REVIEW_CONFLICT
    assert str(exc_info.value) == DURABLE_REVIEW_CONFLICT
    assert source_path.read_bytes() == source_bytes
    assert not _snapshot_path(repository, "conflict-2.json").exists()


def test_forged_snapshot_blocks_before_any_call(setup, repository: Path) -> None:
    durable, executor, request, ontology, mapping = setup
    forged_path = _snapshot_path(repository, "forged.json")
    forged_path.parent.mkdir(parents=True, exist_ok=True)
    forged_path.write_text('{"schema_version": "review-queue-snapshot-v1"}\n', encoding="utf-8")
    model = CountingModel(dict(BAD_UNKNOWN_FIELD))
    provider = RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD))
    before = executor.calls
    with pytest.raises(DurableReviewWorkflowError) as exc_info:
        durable.run(
            request,
            ontology,
            mapping,
            model=model,
            repair_provider=provider,
            model_run_id=SECRET_LIKE_RUN,
            current_snapshot=forged_path,
            next_snapshot=_snapshot_path(repository, "forged-next.json"),
            repository_root=repository,
        )
    assert exc_info.value.code == DURABLE_REVIEW_INVALID_INPUT
    assert SECRET_LIKE_RUN not in str(exc_info.value)
    assert model.calls == 0 and provider.calls == 0 and executor.calls == before
    assert not _snapshot_path(repository, "forged-next.json").exists()


def test_source_equals_target_blocked_before_any_call(setup, repository: Path) -> None:
    durable, executor, request, ontology, mapping = setup
    target = _snapshot_path(repository, "same.json")
    model = CountingModel(dict(BAD_UNKNOWN_FIELD))
    provider = RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD))
    before = executor.calls
    with pytest.raises(DurableReviewWorkflowError) as exc_info:
        durable.run(
            request,
            ontology,
            mapping,
            model=model,
            repair_provider=provider,
            model_run_id=RUN_ID,
            current_snapshot=target,
            next_snapshot=target,
            repository_root=repository,
        )
    assert exc_info.value.code == DURABLE_REVIEW_INVALID_INPUT
    assert model.calls == 0 and provider.calls == 0 and executor.calls == before


def test_existing_target_blocked_before_any_call(setup, repository: Path) -> None:
    durable, executor, request, ontology, mapping = setup
    target = _snapshot_path(repository, "taken.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}\n", encoding="utf-8")
    model = CountingModel(dict(BAD_UNKNOWN_FIELD))
    provider = RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD))
    before = executor.calls
    with pytest.raises(DurableReviewWorkflowError) as exc_info:
        durable.run(
            request,
            ontology,
            mapping,
            model=model,
            repair_provider=provider,
            model_run_id=RUN_ID,
            current_snapshot=None,
            next_snapshot=target,
            repository_root=repository,
        )
    assert exc_info.value.code == DURABLE_REVIEW_INVALID_INPUT
    assert model.calls == 0 and provider.calls == 0 and executor.calls == before
    assert target.read_text(encoding="utf-8") == "{}\n"


def test_outside_and_non_ignored_paths_blocked(setup, repository: Path, tmp_path: Path) -> None:
    durable, executor, request, ontology, mapping = setup
    outside = tmp_path / "outside.json"
    with pytest.raises(DurableReviewWorkflowError) as exc_info:
        durable.run(
            request,
            ontology,
            mapping,
            model=CountingModel(dict(BAD_UNKNOWN_FIELD)),
            repair_provider=RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD)),
            model_run_id=RUN_ID,
            current_snapshot=None,
            next_snapshot=outside,
            repository_root=repository,
        )
    assert exc_info.value.code == DURABLE_REVIEW_INVALID_INPUT

    sibling = repository / "tracked.json"
    with pytest.raises(DurableReviewWorkflowError):
        durable.run(
            request,
            ontology,
            mapping,
            model=CountingModel(dict(BAD_UNKNOWN_FIELD)),
            repair_provider=RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD)),
            model_run_id=RUN_ID,
            current_snapshot=None,
            next_snapshot=sibling,
            repository_root=repository,
        )


def test_tracked_target_blocked_before_any_call(setup, repository: Path) -> None:
    durable, executor, request, ontology, mapping = setup
    target = _snapshot_path(repository, "tracked.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repository), "add", "-f", str(target)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    model = CountingModel(dict(BAD_UNKNOWN_FIELD))
    provider = RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD))
    before = executor.calls
    with pytest.raises(DurableReviewWorkflowError) as exc_info:
        durable.run(
            request,
            ontology,
            mapping,
            model=model,
            repair_provider=provider,
            model_run_id=RUN_ID,
            current_snapshot=None,
            next_snapshot=target,
            repository_root=repository,
        )
    assert exc_info.value.code == DURABLE_REVIEW_INVALID_INPUT
    assert model.calls == 0 and provider.calls == 0 and executor.calls == before


def test_publish_failure_fails_closed_preserving_bytes(
    setup, repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import backend.adaptive.harness.durable_review as durable_module

    durable, executor, request, ontology, mapping = setup
    first, _, _ = _run_empty(
        durable,
        executor,
        request,
        ontology,
        mapping,
        repository,
        "race-1.json",
        model_response=dict(BAD_UNKNOWN_FIELD),
        provider_response=dict(BAD_UNKNOWN_FIELD),
    )
    assert first.persistence is DurableReviewPersistence.WRITTEN
    source_path = _snapshot_path(repository, "race-1.json")
    source_bytes = source_path.read_bytes()
    failing_request = HarnessRequest(
        request_id="durable-test-race", database_id="test-db", question=QUESTION,
    )

    def _boom_link(source: str, target: str) -> None:
        raise OSError("synthetic-link-failure")

    monkeypatch.setattr(os, "link", _boom_link)
    with pytest.raises(DurableReviewWorkflowError) as exc_info:
        durable.run(
            failing_request,
            ontology,
            mapping,
            model=CountingModel(dict(BAD_UNKNOWN_FIELD)),
            repair_provider=RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD)),
            model_run_id=SECRET_LIKE_RUN,
            current_snapshot=source_path,
            next_snapshot=_snapshot_path(repository, "race-2.json"),
            repository_root=repository,
        )
    assert exc_info.value.code == DURABLE_REVIEW_STORAGE_ERROR
    assert str(exc_info.value) == DURABLE_REVIEW_STORAGE_ERROR
    assert SECRET_LIKE_RUN not in str(exc_info.value)
    assert "synthetic-link-failure" not in str(exc_info.value)
    assert source_path.read_bytes() == source_bytes
    assert not _snapshot_path(repository, "race-2.json").exists()
    assert durable_module.DURABLE_REVIEW_WORKFLOW_VERSION == (
        "durable-review-workflow-v1"
    )


def test_publish_collision_fails_closed_with_storage_code(
    setup, repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    durable, executor, request, ontology, mapping = setup
    original_link = os.link

    def _colliding_link(source: str, target: str) -> None:
        raise FileExistsError("review queue snapshot already exists")

    monkeypatch.setattr(os, "link", _colliding_link)
    try:
        with pytest.raises(DurableReviewWorkflowError) as exc_info:
            durable.run(
                request,
                ontology,
                mapping,
                model=CountingModel(dict(BAD_UNKNOWN_FIELD)),
                repair_provider=RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD)),
                model_run_id=RUN_ID,
                current_snapshot=None,
                next_snapshot=_snapshot_path(repository, "collide.json"),
                repository_root=repository,
            )
    finally:
        monkeypatch.setattr(os, "link", original_link)
    assert exc_info.value.code == DURABLE_REVIEW_STORAGE_ERROR
    assert not _snapshot_path(repository, "collide.json").exists()


def test_temp_cleanup_failure_rolls_back_new_target(
    setup, repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    durable, _, request, ontology, mapping = setup
    original_unlink = Path.unlink

    def _fail_temp_cleanup(path: Path, *args, **kwargs) -> None:
        if path.name.startswith(".snapshot-"):
            raise OSError("synthetic-cleanup-failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _fail_temp_cleanup)
    target = _snapshot_path(repository, "cleanup.json")
    with pytest.raises(DurableReviewWorkflowError) as exc_info:
        durable.run(
            request,
            ontology,
            mapping,
            model=CountingModel(dict(BAD_UNKNOWN_FIELD)),
            repair_provider=RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD)),
            model_run_id=RUN_ID,
            current_snapshot=None,
            next_snapshot=target,
            repository_root=repository,
        )
    assert exc_info.value.code == DURABLE_REVIEW_STORAGE_ERROR
    assert not target.exists()


def test_internal_workflow_error_is_not_mislabeled_as_input(
    setup, repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    durable, _, request, ontology, mapping = setup

    def _internal(*args, **kwargs):
        raise AgentWorkflowError(AgentWorkflowErrorCode.INTERNAL_ERROR)

    monkeypatch.setattr(durable._workflow, "run", _internal)
    with pytest.raises(DurableReviewWorkflowError) as exc_info:
        durable.run(
            request,
            ontology,
            mapping,
            model=CountingModel(dict(GOOD_IR)),
            repair_provider=RecordingRepairProvider(dict(GOOD_IR)),
            model_run_id=RUN_ID,
            current_snapshot=None,
            next_snapshot=_snapshot_path(repository, "internal.json"),
            repository_root=repository,
        )
    assert exc_info.value.code == DURABLE_REVIEW_INTERNAL_ERROR
    assert str(exc_info.value) == DURABLE_REVIEW_INTERNAL_ERROR
    assert not _snapshot_path(repository, "internal.json").exists()


def test_successful_publish_does_not_reopen_target(
    setup, repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import backend.adaptive.harness.durable_review as durable_module

    durable, _, request, ontology, mapping = setup

    def _unexpected_reload(*args, **kwargs):
        raise AssertionError("published target must not be reopened")

    monkeypatch.setattr(
        durable_module, "load_review_queue_snapshot", _unexpected_reload
    )
    target = _snapshot_path(repository, "no-reopen.json")
    result = durable.run(
        request,
        ontology,
        mapping,
        model=CountingModel(dict(BAD_UNKNOWN_FIELD)),
        repair_provider=RecordingRepairProvider(dict(BAD_UNKNOWN_FIELD)),
        model_run_id=RUN_ID,
        current_snapshot=None,
        next_snapshot=target,
        repository_root=repository,
    )
    assert result.persistence is DurableReviewPersistence.WRITTEN
    assert target.is_file()
    assert load_review_queue_snapshot(
        target, repository_root=repository
    ).payload_sha256 == result.snapshot_hash


def test_result_rejects_forged_nested_workflow_result(setup, repository: Path) -> None:
    durable, executor, request, ontology, mapping = setup
    result, _, _ = _run_empty(
        durable,
        executor,
        request,
        ontology,
        mapping,
        repository,
        "nest-1.json",
        model_response=dict(BAD_UNKNOWN_FIELD),
        provider_response=dict(BAD_UNKNOWN_FIELD),
    )
    forged_inner = result.workflow_result.model_copy(
        update={"queue_status": ReviewQueueStatus.NOT_ELIGIBLE}
    )
    with pytest.raises(Exception):
        DurableReviewWorkflowResult.model_validate(
            {
                "workflow_version": DURABLE_REVIEW_WORKFLOW_VERSION,
                "workflow_result": forged_inner.model_dump(mode="json"),
                "persistence": "UNCHANGED",
                "snapshot_hash": None,
                "entry_count": 1,
            }
        )
    constructed = DurableReviewWorkflowResult.model_construct(
        workflow_version=DURABLE_REVIEW_WORKFLOW_VERSION,
        workflow_result=result.workflow_result,
        persistence=DurableReviewPersistence.UNCHANGED,
        snapshot_hash=None,
        entry_count=1,
    )
    with pytest.raises(Exception):
        DurableReviewWorkflowResult.model_validate(
            constructed.model_dump(mode="json", warnings=False)
            if isinstance(constructed, BaseModel)
            else constructed
        )


def test_returned_result_is_defensively_copied(setup, repository: Path) -> None:
    durable, executor, request, ontology, mapping = setup
    result, _, _ = _run_empty(
        durable,
        executor,
        request,
        ontology,
        mapping,
        repository,
        "copy-1.json",
        model_response=dict(BAD_UNKNOWN_FIELD),
        provider_response=dict(BAD_UNKNOWN_FIELD),
    )
    assert isinstance(result, DurableReviewWorkflowResult)
    with pytest.raises(Exception):
        result.persistence = DurableReviewPersistence.UNCHANGED  # type: ignore[misc]
    reloaded = DurableReviewWorkflowResult.model_validate(
        result.model_dump(mode="json")
    )
    assert reloaded == result
    assert reloaded.workflow_result is not result.workflow_result


def test_no_caller_queue_injection_api(setup) -> None:
    durable, _, _, _, _ = setup
    import inspect

    signature = inspect.signature(durable.run)
    assert "queue" not in signature.parameters
    assert "critique" not in "".join(signature.parameters)
    assert "disposition" not in "".join(signature.parameters)
    assert "entry" not in "".join(signature.parameters).replace("entry_count", "")
    init_signature = inspect.signature(DurableReviewWorkflowCoordinator.__init__)
    assert "queue" not in init_signature.parameters
