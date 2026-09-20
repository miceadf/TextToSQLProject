"""Synthetic tests for the deterministic critique disposition.

Every test uses injected models and repair providers only; no network,
no real models, no evaluation scores. Each coordinator case runs a single
request (denominator 1) unless the name states otherwise.
"""

from __future__ import annotations

import sqlite3
import tempfile
import warnings
from pathlib import Path

import pytest

from backend.adaptive.harness import (
    CRITIQUE_DISPOSITION_VERSION,
    CRITIQUE_INVALID_SUMMARY,
    BoundedRepairCoordinator,
    CritiqueDisposition,
    CritiqueError,
    CritiqueResult,
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
    critique_repair_summary,
)
from backend.adaptive.harness.compiler import SingleTableSQLiteCompiler
from backend.adaptive.harness.policy import ConservativeSqlPolicy
from backend.adaptive.harness.repair import REPAIR_PROVIDER_ERROR


QUESTION = "합성 테스트 질문"

EXPECTED_DISPOSITIONS: dict[str, CritiqueDisposition] = {
    HarnessErrorCode.INVALID_REQUEST.value: CritiqueDisposition.REQUEST_INVALID,
    HarnessErrorCode.MODEL_INVALID_OUTPUT.value: CritiqueDisposition.MODEL_REVIEW,
    HarnessErrorCode.UNKNOWN_ENTITY.value: CritiqueDisposition.MODEL_REVIEW,
    HarnessErrorCode.UNKNOWN_FIELD.value: CritiqueDisposition.MODEL_REVIEW,
    HarnessErrorCode.INVALID_OPERATOR.value: CritiqueDisposition.MODEL_REVIEW,
    HarnessErrorCode.INVALID_VALUE_TYPE.value: CritiqueDisposition.MODEL_REVIEW,
    REPAIR_PROVIDER_ERROR: CritiqueDisposition.MODEL_REVIEW,
    HarnessErrorCode.DATABASE_NOT_FOUND.value: CritiqueDisposition.CONTEXT_REVIEW,
    HarnessErrorCode.MAPPING_NOT_PUBLISHED.value: CritiqueDisposition.CONTEXT_REVIEW,
    HarnessErrorCode.MAPPING_MISSING.value: CritiqueDisposition.CONTEXT_REVIEW,
    HarnessErrorCode.MULTI_TABLE_NOT_SUPPORTED.value: CritiqueDisposition.CONTEXT_REVIEW,
    HarnessErrorCode.AMBIGUOUS_JOIN_PATH.value: CritiqueDisposition.CONTEXT_REVIEW,
    HarnessErrorCode.INVALID_JOIN_PLAN.value: CritiqueDisposition.CONTEXT_REVIEW,
    HarnessErrorCode.SELF_JOIN_NOT_SUPPORTED.value: CritiqueDisposition.CONTEXT_REVIEW,
    HarnessErrorCode.UNSAFE_SQL.value: CritiqueDisposition.SAFETY_BLOCKED,
    HarnessErrorCode.SQL_PARSE_ERROR.value: CritiqueDisposition.SAFETY_BLOCKED,
    HarnessErrorCode.MULTIPLE_STATEMENTS.value: CritiqueDisposition.SAFETY_BLOCKED,
    HarnessErrorCode.UNSAFE_SQL_NODE.value: CritiqueDisposition.SAFETY_BLOCKED,
    HarnessErrorCode.UNAUTHORIZED_TABLE.value: CritiqueDisposition.SAFETY_BLOCKED,
    HarnessErrorCode.UNAUTHORIZED_COLUMN.value: CritiqueDisposition.SAFETY_BLOCKED,
    HarnessErrorCode.INVALID_JOIN_PREDICATE.value: CritiqueDisposition.SAFETY_BLOCKED,
    HarnessErrorCode.INVALID_PROJECTION.value: CritiqueDisposition.SAFETY_BLOCKED,
    HarnessErrorCode.INVALID_PLACEHOLDER.value: CritiqueDisposition.SAFETY_BLOCKED,
    HarnessErrorCode.INVALID_LIMIT.value: CritiqueDisposition.SAFETY_BLOCKED,
    HarnessErrorCode.EXECUTION_TIMEOUT.value: CritiqueDisposition.EXECUTION_FAILURE,
    HarnessErrorCode.EXECUTION_ERROR.value: CritiqueDisposition.EXECUTION_FAILURE,
    HarnessErrorCode.RESULT_TOO_LARGE.value: CritiqueDisposition.EXECUTION_FAILURE,
    HarnessErrorCode.RESULT_SCHEMA_MISMATCH.value: CritiqueDisposition.EXECUTION_FAILURE,
    HarnessErrorCode.SQL_COMPILE_ERROR.value: CritiqueDisposition.INTERNAL_REVIEW,
}

# One allowed stage per code for direct-summary construction. Multi-stage
# codes are expanded in a separate test below.
REPRESENTATIVE_STAGES: dict[str, HarnessStage | None] = {
    HarnessErrorCode.INVALID_REQUEST.value: HarnessStage.REQUEST_VALIDATED,
    HarnessErrorCode.MODEL_INVALID_OUTPUT.value: HarnessStage.IR_GENERATED,
    HarnessErrorCode.UNKNOWN_ENTITY.value: HarnessStage.IR_VALIDATED,
    HarnessErrorCode.UNKNOWN_FIELD.value: HarnessStage.IR_VALIDATED,
    HarnessErrorCode.INVALID_OPERATOR.value: HarnessStage.IR_VALIDATED,
    HarnessErrorCode.INVALID_VALUE_TYPE.value: HarnessStage.IR_VALIDATED,
    REPAIR_PROVIDER_ERROR: None,
    HarnessErrorCode.DATABASE_NOT_FOUND.value: HarnessStage.EXECUTED,
    HarnessErrorCode.MAPPING_NOT_PUBLISHED.value: HarnessStage.CONTEXT_READY,
    HarnessErrorCode.MAPPING_MISSING.value: HarnessStage.IR_VALIDATED,
    HarnessErrorCode.MULTI_TABLE_NOT_SUPPORTED.value: HarnessStage.SQL_COMPILED,
    HarnessErrorCode.AMBIGUOUS_JOIN_PATH.value: HarnessStage.SQL_COMPILED,
    HarnessErrorCode.INVALID_JOIN_PLAN.value: HarnessStage.SQL_COMPILED,
    HarnessErrorCode.SELF_JOIN_NOT_SUPPORTED.value: HarnessStage.SQL_COMPILED,
    HarnessErrorCode.UNSAFE_SQL.value: HarnessStage.SQL_VALIDATED,
    HarnessErrorCode.SQL_PARSE_ERROR.value: HarnessStage.SQL_VALIDATED,
    HarnessErrorCode.MULTIPLE_STATEMENTS.value: HarnessStage.SQL_VALIDATED,
    HarnessErrorCode.UNSAFE_SQL_NODE.value: HarnessStage.SQL_VALIDATED,
    HarnessErrorCode.UNAUTHORIZED_TABLE.value: HarnessStage.SQL_VALIDATED,
    HarnessErrorCode.UNAUTHORIZED_COLUMN.value: HarnessStage.SQL_VALIDATED,
    HarnessErrorCode.INVALID_JOIN_PREDICATE.value: HarnessStage.SQL_VALIDATED,
    HarnessErrorCode.INVALID_PROJECTION.value: HarnessStage.SQL_VALIDATED,
    HarnessErrorCode.INVALID_PLACEHOLDER.value: HarnessStage.SQL_VALIDATED,
    HarnessErrorCode.INVALID_LIMIT.value: HarnessStage.SQL_VALIDATED,
    HarnessErrorCode.EXECUTION_TIMEOUT.value: HarnessStage.EXECUTED,
    HarnessErrorCode.EXECUTION_ERROR.value: HarnessStage.EXECUTED,
    HarnessErrorCode.RESULT_TOO_LARGE.value: HarnessStage.EXECUTED,
    HarnessErrorCode.RESULT_SCHEMA_MISMATCH.value: HarnessStage.RESULT_VALIDATED,
    HarnessErrorCode.SQL_COMPILE_ERROR.value: HarnessStage.SQL_COMPILED,
}


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
        request_id="critique-test-1", database_id="test-db", question=QUESTION,
    )
    yield coordinator, executor, request, _ontology(), _mapping()
    temporary.cleanup()


def test_coordinator_initial_success_is_executed_unverified(setup) -> None:
    coordinator, executor, request, ontology, mapping = setup
    before = executor.calls
    summary = coordinator.run(
        request, ontology, mapping,
        model=CountingModel(GOOD_IR),
        repair_provider=RecordingRepairProvider(GOOD_IR),
    )
    assert summary.succeeded is True
    result = critique_repair_summary(summary)
    assert result.disposition is CritiqueDisposition.EXECUTED_UNVERIFIED
    assert result.repair_attempted is False
    assert result.recovered_after_repair is False
    assert result.final_code is None
    assert result.final_stage is HarnessStage.RESULT_VALIDATED
    assert result.critique_version == CRITIQUE_DISPOSITION_VERSION
    assert executor.calls == before + 1


def test_coordinator_repair_success_marks_recovery(setup) -> None:
    coordinator, executor, request, ontology, mapping = setup
    before = executor.calls
    summary = coordinator.run(
        request, ontology, mapping,
        model=CountingModel({}),
        repair_provider=RecordingRepairProvider(GOOD_IR),
    )
    assert summary.succeeded is True
    result = critique_repair_summary(summary)
    assert result.disposition is CritiqueDisposition.EXECUTED_UNVERIFIED
    assert result.repair_attempted is True
    assert result.recovered_after_repair is True
    assert result.final_code is None
    assert executor.calls == before + 1


def test_coordinator_initial_failure_without_repair(setup) -> None:
    coordinator, executor, request, ontology, mapping = setup
    provider = RecordingRepairProvider(GOOD_IR)
    summary = coordinator.run(
        {"database_id": "test-db"},
        ontology, mapping,
        model=CountingModel(GOOD_IR), repair_provider=provider,
    )
    assert summary.succeeded is False
    assert provider.calls == 0
    result = critique_repair_summary(summary)
    assert result.disposition is CritiqueDisposition.REQUEST_INVALID
    assert result.final_code == HarnessErrorCode.INVALID_REQUEST.value
    assert result.final_stage is HarnessStage.REQUEST_VALIDATED
    # INVALID_REQUEST is an input-contract bucket, never ambiguity.
    assert result.disposition is not CritiqueDisposition.MODEL_REVIEW
    assert result.repair_attempted is False
    assert result.recovered_after_repair is False
    assert executor.calls == 0


def test_coordinator_repair_failure_classifies_final_code(setup) -> None:
    coordinator, _, request, ontology, mapping = setup
    bad = {"entity": "Course", "select": ["Course.unknown"], "filters": [], "limit": 50}
    summary = coordinator.run(
        request, ontology, mapping,
        model=CountingModel({}),
        repair_provider=RecordingRepairProvider(dict(bad)),
    )
    assert summary.succeeded is False
    assert summary.final_code == HarnessErrorCode.UNKNOWN_FIELD.value
    result = critique_repair_summary(summary)
    assert result.disposition is CritiqueDisposition.MODEL_REVIEW
    assert result.repair_attempted is True
    assert result.recovered_after_repair is False


def test_coordinator_repair_provider_error_is_model_review(setup) -> None:
    coordinator, _, request, ontology, mapping = setup
    summary = coordinator.run(
        request, ontology, mapping,
        model=CountingModel({}),
        repair_provider=RecordingRepairProvider(error=RuntimeError("비밀-원문")),
    )
    assert summary.final_code == REPAIR_PROVIDER_ERROR
    assert summary.final_stage is None
    result = critique_repair_summary(summary)
    assert result.disposition is CritiqueDisposition.MODEL_REVIEW
    assert result.final_stage is None
    assert result.repair_attempted is True
    assert result.recovered_after_repair is False


def test_empty_result_success_is_executed_unverified(setup) -> None:
    coordinator, _, request, ontology, mapping = setup
    empty = dict(GOOD_IR, filters=[
        {"field": "Course.credits", "operator": "equals", "value": 999},
    ])
    summary = coordinator.run(
        request, ontology, mapping,
        model=CountingModel(empty),
        repair_provider=RecordingRepairProvider(GOOD_IR),
    )
    assert summary.succeeded is True
    result = critique_repair_summary(summary)
    assert result.disposition is CritiqueDisposition.EXECUTED_UNVERIFIED
    assert result.repair_attempted is False
    assert result.recovered_after_repair is False


def test_all_known_codes_have_pinned_disposition() -> None:
    assert set(EXPECTED_DISPOSITIONS) == set(REPRESENTATIVE_STAGES)
    assert set(EXPECTED_DISPOSITIONS) == {
        code.value for code in HarnessErrorCode
    } | {REPAIR_PROVIDER_ERROR}
    for code, stage in REPRESENTATIVE_STAGES.items():
        result = critique_repair_summary(_failed_summary(code, stage))
        assert result.disposition is EXPECTED_DISPOSITIONS[code]
        assert result.final_code == code
        assert result.final_stage is stage
        assert result.repair_attempted is True
        assert result.recovered_after_repair is False
        assert result.critique_version == CRITIQUE_DISPOSITION_VERSION


def test_multi_stage_codes_accept_each_allowed_stage() -> None:
    cases = [
        (HarnessErrorCode.MAPPING_MISSING.value, HarnessStage.CONTEXT_READY),
        (HarnessErrorCode.MAPPING_MISSING.value, HarnessStage.SQL_COMPILED),
        (HarnessErrorCode.AMBIGUOUS_JOIN_PATH.value, HarnessStage.SQL_VALIDATED),
        (HarnessErrorCode.INVALID_JOIN_PLAN.value, HarnessStage.SQL_VALIDATED),
    ]
    for code, stage in cases:
        result = critique_repair_summary(_failed_summary(code, stage))
        assert result.disposition is CritiqueDisposition.CONTEXT_REVIEW


def test_representative_policy_timeout_result_and_compile_codes() -> None:
    assert critique_repair_summary(
        _failed_summary(HarnessErrorCode.UNSAFE_SQL.value, HarnessStage.SQL_VALIDATED)
    ).disposition is CritiqueDisposition.SAFETY_BLOCKED
    assert critique_repair_summary(
        _failed_summary(HarnessErrorCode.EXECUTION_TIMEOUT.value, HarnessStage.EXECUTED)
    ).disposition is CritiqueDisposition.EXECUTION_FAILURE
    assert critique_repair_summary(
        _failed_summary(HarnessErrorCode.EXECUTION_ERROR.value, HarnessStage.EXECUTED)
    ).disposition is CritiqueDisposition.EXECUTION_FAILURE
    assert critique_repair_summary(
        _failed_summary(
            HarnessErrorCode.RESULT_SCHEMA_MISMATCH.value,
            HarnessStage.RESULT_VALIDATED,
        )
    ).disposition is CritiqueDisposition.EXECUTION_FAILURE
    assert critique_repair_summary(
        _failed_summary(HarnessErrorCode.SQL_COMPILE_ERROR.value, HarnessStage.SQL_COMPILED)
    ).disposition is CritiqueDisposition.INTERNAL_REVIEW
    # Safety blocks and system failures never masquerade as model failures.
    assert critique_repair_summary(
        _failed_summary(HarnessErrorCode.UNAUTHORIZED_TABLE.value, HarnessStage.SQL_VALIDATED)
    ).disposition is not CritiqueDisposition.MODEL_REVIEW


@pytest.mark.parametrize(("code", "wrong_stage"), [
    (HarnessErrorCode.UNKNOWN_FIELD.value, HarnessStage.EXECUTED),
    (HarnessErrorCode.MODEL_INVALID_OUTPUT.value, HarnessStage.IR_VALIDATED),
    (HarnessErrorCode.UNSAFE_SQL.value, HarnessStage.SQL_COMPILED),
    (HarnessErrorCode.EXECUTION_TIMEOUT.value, HarnessStage.RESULT_VALIDATED),
    (REPAIR_PROVIDER_ERROR, HarnessStage.IR_VALIDATED),
])
def test_forged_stage_is_rejected_with_fixed_code(code, wrong_stage) -> None:
    with pytest.raises(CritiqueError) as exc_info:
        critique_repair_summary(_failed_summary(code, wrong_stage))
    assert str(exc_info.value) == CRITIQUE_INVALID_SUMMARY
    assert exc_info.value.code == CRITIQUE_INVALID_SUMMARY


def test_unknown_code_is_rejected() -> None:
    with pytest.raises(CritiqueError) as exc_info:
        critique_repair_summary(_failed_summary("SOME_NEW_CODE", HarnessStage.EXECUTED))
    assert str(exc_info.value) == CRITIQUE_INVALID_SUMMARY


def test_no_repair_final_must_equal_initial() -> None:
    # Review regression: with zero repairs the coordinator returns the
    # initial outcome unchanged, so a diverging final code/stage is
    # fabricated and must fail closed with the fixed code.
    summary = RepairSummary(
        initial_status=HarnessStatus.FAILED,
        initial_stage=HarnessStage.REQUEST_VALIDATED,
        initial_code=HarnessErrorCode.INVALID_REQUEST.value,
        repair_attempted=False,
        repair_attempts=0,
        final_status=HarnessStatus.FAILED,
        final_stage=HarnessStage.SQL_VALIDATED,
        final_code=HarnessErrorCode.UNSAFE_SQL.value,
        succeeded=False,
    )
    with pytest.raises(CritiqueError) as exc_info:
        critique_repair_summary(summary)
    assert str(exc_info.value) == CRITIQUE_INVALID_SUMMARY
    assert exc_info.value.code == CRITIQUE_INVALID_SUMMARY


def test_provider_failure_requires_repair_attempt() -> None:
    summary = RepairSummary(
        initial_status=HarnessStatus.FAILED,
        initial_stage=None,
        initial_code=REPAIR_PROVIDER_ERROR,
        repair_attempted=False,
        repair_attempts=0,
        final_status=HarnessStatus.FAILED,
        final_stage=None,
        final_code=REPAIR_PROVIDER_ERROR,
        succeeded=False,
    )
    with pytest.raises(CritiqueError) as exc_info:
        critique_repair_summary(summary)
    assert str(exc_info.value) == CRITIQUE_INVALID_SUMMARY


def test_forged_repair_count_and_code_fail_closed_without_warnings() -> None:
    valid = _failed_summary(HarnessErrorCode.UNKNOWN_FIELD.value, HarnessStage.IR_VALIDATED)
    for forged in (
        valid.model_copy(update={"repair_attempts": 2}),
        valid.model_copy(update={"final_code": ["SYNTHETICSECRET123"]}),
    ):
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter("always")
            with pytest.raises(CritiqueError) as exc_info:
                critique_repair_summary(forged)
        assert str(exc_info.value) == CRITIQUE_INVALID_SUMMARY
        assert not emitted


@pytest.mark.parametrize(("initial_code", "initial_stage"), [
    (HarnessErrorCode.INVALID_REQUEST.value, HarnessStage.REQUEST_VALIDATED),
    (HarnessErrorCode.UNSAFE_SQL.value, HarnessStage.SQL_VALIDATED),
    (HarnessErrorCode.MAPPING_MISSING.value, HarnessStage.IR_VALIDATED),
    (HarnessErrorCode.EXECUTION_TIMEOUT.value, HarnessStage.EXECUTED),
])
def test_repair_requires_retryable_initial_pair(initial_code, initial_stage) -> None:
    # The coordinator only repairs Phase 6-01 retryable pairs, so a repair
    # claimed after input, policy, context, or execution failures is
    # fabricated and must fail closed with the fixed code.
    summary = RepairSummary(
        initial_status=HarnessStatus.FAILED,
        initial_stage=initial_stage,
        initial_code=initial_code,
        repair_attempted=True,
        repair_attempts=1,
        final_status=HarnessStatus.FAILED,
        final_stage=HarnessStage.IR_VALIDATED,
        final_code=HarnessErrorCode.UNKNOWN_FIELD.value,
        succeeded=False,
    )
    with pytest.raises(CritiqueError) as exc_info:
        critique_repair_summary(summary)
    assert str(exc_info.value) == CRITIQUE_INVALID_SUMMARY
    assert exc_info.value.code == CRITIQUE_INVALID_SUMMARY


@pytest.mark.parametrize("summary", [
    RepairSummary(
        initial_status=HarnessStatus.SUCCEEDED,
        initial_stage=HarnessStage.RESULT_VALIDATED,
        initial_code=None,
        repair_attempted=False,
        repair_attempts=0,
        final_status=HarnessStatus.SUCCEEDED,
        final_stage=HarnessStage.RESULT_VALIDATED,
        final_code=HarnessErrorCode.UNKNOWN_FIELD.value,
        succeeded=True,
    ),
    RepairSummary(
        initial_status=HarnessStatus.SUCCEEDED,
        initial_stage=HarnessStage.RESULT_VALIDATED,
        initial_code=None,
        repair_attempted=True,
        repair_attempts=1,
        final_status=HarnessStatus.SUCCEEDED,
        final_stage=HarnessStage.RESULT_VALIDATED,
        final_code=None,
        succeeded=True,
    ),
    RepairSummary(
        initial_status=HarnessStatus.FAILED,
        initial_stage=HarnessStage.IR_VALIDATED,
        initial_code=HarnessErrorCode.UNKNOWN_FIELD.value,
        repair_attempted=False,
        repair_attempts=0,
        final_status=HarnessStatus.SUCCEEDED,
        final_stage=HarnessStage.RESULT_VALIDATED,
        final_code=None,
        succeeded=True,
    ),
    RepairSummary(
        initial_status=HarnessStatus.FAILED,
        initial_stage=HarnessStage.IR_VALIDATED,
        initial_code=HarnessErrorCode.UNKNOWN_FIELD.value,
        repair_attempted=True,
        repair_attempts=1,
        final_status=HarnessStatus.FAILED,
        final_stage=HarnessStage.IR_VALIDATED,
        final_code=HarnessErrorCode.UNKNOWN_FIELD.value,
        succeeded=True,
    ),
    RepairSummary(
        initial_status=HarnessStatus.FAILED,
        initial_stage=HarnessStage.IR_VALIDATED,
        initial_code=HarnessErrorCode.UNKNOWN_FIELD.value,
        repair_attempted=False,
        repair_attempts=1,
        final_status=HarnessStatus.FAILED,
        final_stage=HarnessStage.IR_VALIDATED,
        final_code=HarnessErrorCode.UNKNOWN_FIELD.value,
        succeeded=False,
    ),
    RepairSummary(
        initial_status=HarnessStatus.SUCCEEDED,
        initial_stage=HarnessStage.RESULT_VALIDATED,
        initial_code=None,
        repair_attempted=False,
        repair_attempts=0,
        final_status=HarnessStatus.FAILED,
        final_stage=HarnessStage.IR_VALIDATED,
        final_code=HarnessErrorCode.UNKNOWN_FIELD.value,
        succeeded=False,
    ),
])
def test_contradictory_summary_is_rejected(summary) -> None:
    with pytest.raises(CritiqueError) as exc_info:
        critique_repair_summary(summary)
    assert str(exc_info.value) == CRITIQUE_INVALID_SUMMARY


def test_critique_output_and_errors_carry_no_original_text(setup, caplog) -> None:
    import logging

    coordinator, _, request, ontology, mapping = setup
    with caplog.at_level(logging.DEBUG):
        summary = coordinator.run(
            request, ontology, mapping,
            model=CountingModel({}),
            repair_provider=RecordingRepairProvider(error=RuntimeError("비밀-원문")),
        )
        result = critique_repair_summary(summary)
    assert isinstance(result, CritiqueResult)
    assert "합성 테스트 질문" not in str(result)
    assert "비밀-원문" not in str(result)
    assert "courses" not in str(result).lower()
    assert "SELECT" not in str(result)
    assert caplog.text == ""
    try:
        critique_repair_summary(_failed_summary("SOME_NEW_CODE", HarnessStage.EXECUTED))
    except CritiqueError as exc:
        assert "SOME_NEW_CODE" not in str(exc)
        assert "합성 테스트 질문" not in str(exc)


def test_critique_makes_no_harness_calls(setup) -> None:
    coordinator, executor, request, ontology, mapping = setup
    model = CountingModel(GOOD_IR)
    provider = RecordingRepairProvider(GOOD_IR)
    summary = coordinator.run(
        request, ontology, mapping, model=model, repair_provider=provider,
    )
    model_calls, provider_calls, executor_calls = model.calls, provider.calls, executor.calls
    critique_repair_summary(summary)
    critique_repair_summary(summary)
    assert (model.calls, provider.calls, executor.calls) == (
        model_calls, provider_calls, executor_calls,
    )
