"""Synthetic tests for clarification-execution-adapter-v1 (Phase 6-10).

Fully synthetic fixtures only: tiny ontology/mapping/ruleset, temporary
SQLite DB, counting compiler/policy/executor doubles. No UI, PostgreSQL,
Ollama, CNU material, repair loops, queues, or snapshots.
"""

from __future__ import annotations

import copy
import inspect
import sqlite3
import tempfile
import warnings
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.adaptive.harness import (
    ClarificationChoice,
    CompiledQuery,
    ConservativeSqlPolicy,
    DataType,
    FieldMapping,
    HarnessErrorCode,
    HarnessRequest,
    HarnessResult,
    HarnessStage,
    HarnessStatus,
    MappingProfile,
    MappingStatus,
    Ontology,
    OntologyField,
    Operator,
    SingleTableSQLiteCompiler,
    SQLiteReadOnlyExecutor,
)
from backend.adaptive.harness.ambiguity_detector import (
    AMBIGUITY_DETECTOR_VERSION,
    AmbiguityDetectorError,
    detect_ambiguity,
    prepare_detected_clarification,
)
from backend.adaptive.harness.clarification import (
    ClarificationError,
)
from backend.adaptive.harness.clarification_execution_adapter import (
    CLARIFICATION_EXECUTION_ADAPTER_VERSION,
    ClarificationExecutionAdapterError,
    ClarificationExecutionAdapterErrorCode,
    ClarificationExecutionResult,
    _OneShotQueryModel,
    run_clarified_execution,
)
from backend.adaptive.harness.clarification_query_constraint import (
    ClarificationQueryConstraintError,
)
from backend.adaptive.harness.errors import HarnessFailure

QUESTION_BASE = "SYNTH_Q_CEA10_c41f"
MALICIOUS = "MALSTR_C41f_<script>'\"; DROP"
LOCAL_PATH = "C:\\synth_c41f\\private\\notes.txt"
PATH_LIKE_REQUEST_ID = "..\\..\\synth_c41f\\secret\\request-id"
SECRET_REQUEST_ID = "AKIAIOSFODNN7_SYNTH_C41F_SECRET"
PATH_LIKE_DATABASE_ID = "..\\synth_c41f\\secret-db\\..\\private"
SECRET_DATABASE_ID = "synth_c41f_db_secret_token_9e77"


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


def _mapping(database_id: str = "synth-db") -> MappingProfile:
    return MappingProfile(
        database_id=database_id,
        version=1,
        status=MappingStatus.PUBLISHED,
        fields={
            "Course.name": FieldMapping(table="courses", column="subject_name"),
            "Course.credits": FieldMapping(table="courses", column="credit_hours"),
            "Instructor.name": FieldMapping(table="courses", column="subject_name"),
        },
    )


def _request(**overrides) -> HarnessRequest:
    values = {
        "request_id": "req-synth-cea10",
        "database_id": "synth-db",
        "question": f"{QUESTION_BASE} 성적표 보여줘",
    }
    values.update(overrides)
    return HarnessRequest(**values)


def _entity_rules() -> dict:
    return {
        "version": AMBIGUITY_DETECTOR_VERSION,
        "rules": [
            {
                "rule_id": "rule-scope",
                "triggers": ["성적표"],
                "options": [
                    {"option_key": "opt-course", "semantic_ref": "Course"},
                    {"option_key": "opt-instructor", "semantic_ref": "Instructor"},
                ],
            }
        ],
    }


def _field_rules() -> dict:
    return {
        "version": AMBIGUITY_DETECTOR_VERSION,
        "rules": [
            {
                "rule_id": "rule-field",
                "triggers": ["성적표"],
                "options": [
                    {"option_key": "opt-name", "semantic_ref": "Course.name"},
                    {"option_key": "opt-credits", "semantic_ref": "Course.credits"},
                ],
            }
        ],
    }


def _pending_and_choice(request, ontology, mapping, rules, want_ref: str):
    signal = detect_ambiguity(request, ontology, mapping, rules)
    assert signal is not None
    wanted = [o for o in signal.options if o.semantic_ref == want_ref]
    assert len(wanted) == 1
    pending = prepare_detected_clarification(request, ontology, mapping, rules)
    assert pending is not None
    return pending, ClarificationChoice(selection=[wanted[0].option_id])


@pytest.fixture()
def sqlite_db():
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "courses.sqlite3"
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE courses (subject_name TEXT, credit_hours INTEGER)"
        )
        connection.executemany(
            "INSERT INTO courses VALUES (?, ?)",
            [("데이터베이스", 3), ("운영체제", 3)],
        )
        connection.commit()
    finally:
        connection.close()
    yield path
    tmp.cleanup()


class CountingProvider:
    def __init__(self, payload) -> None:
        self.calls = 0
        self.payload = payload

    def generate_ir(self, request, ontology, mapping, constraint):
        self.calls += 1
        if callable(self.payload):
            return self.payload(request, ontology, mapping, constraint)
        return copy.deepcopy(self.payload)


class CountingCompiler:
    def __init__(self) -> None:
        self.calls = 0
        self.inner = SingleTableSQLiteCompiler()

    def compile(self, query_ir, mapping):
        self.calls += 1
        return self.inner.compile(query_ir, mapping)


class CountingPolicy:
    def __init__(self) -> None:
        self.calls = 0
        self.inner = ConservativeSqlPolicy()

    def validate(self, query, **kwargs):
        self.calls += 1
        return self.inner.validate(query, **kwargs)


class CountingExecutor:
    def __init__(self, inner) -> None:
        self.calls = 0
        self.inner = inner

    def execute(self, database_id, query):
        self.calls += 1
        return self.inner.execute(database_id, query)


def _run_all(request, ontology, mapping, rules, pending, choice, provider, executor,
             compiler=None, sql_policy=None):
    kwargs: dict = {}
    if compiler is not None:
        kwargs["compiler"] = compiler
    if sql_policy is not None:
        kwargs["sql_policy"] = sql_policy
    return run_clarified_execution(
        request, ontology, mapping, rules, pending, choice, provider, executor,
        **kwargs,
    )


def test_entity_and_field_success_single_calls(sqlite_db):
    for want, payload in (
        ("Course", {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 10}),
        ("Course.name", {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 10}),
    ):
        rules = _entity_rules() if want == "Course" else _field_rules()
        request, ontology, mapping = _request(), _ontology(), _mapping()
        pending, choice = _pending_and_choice(request, ontology, mapping, rules, want)
        provider = CountingProvider(payload)
        compiler, policy = CountingCompiler(), CountingPolicy()
        executor = CountingExecutor(SQLiteReadOnlyExecutor({"synth-db": sqlite_db}))
        result = _run_all(request, ontology, mapping, rules, pending, choice,
                          provider, executor, compiler, policy)
        assert isinstance(result, ClarificationExecutionResult)
        assert result.adapter_version == CLARIFICATION_EXECUTION_ADAPTER_VERSION
        assert provider.calls == 1
        assert compiler.calls == 1
        assert policy.calls == 1
        assert executor.calls == 1
        assert result.harness_result.status is HarnessStatus.SUCCEEDED
        assert result.harness_result.query_ir == result.generation.query_ir
        assert len(result.harness_result.rows) == 2


def test_zero_row_success(sqlite_db):
    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _field_rules()
    pending, choice = _pending_and_choice(request, ontology, mapping, rules, "Course.credits")
    provider = CountingProvider(
        {"entity": "Course", "select": ["Course.name"],
         "filters": [{"field": "Course.credits", "operator": "equals", "value": 99}],
         "limit": 10}
    )
    executor = SQLiteReadOnlyExecutor({"synth-db": sqlite_db})
    result = _run_all(request, ontology, mapping, rules, pending, choice, provider, executor)
    assert result.harness_result.status is HarnessStatus.SUCCEEDED
    assert result.harness_result.rows == []
    assert result.harness_result.query_ir == result.generation.query_ir


def test_early_failures_have_zero_downstream_calls(sqlite_db):
    ontology, mapping = _ontology(), _mapping()
    executor_inner = SQLiteReadOnlyExecutor({"synth-db": sqlite_db})

    def fresh_counters():
        return CountingCompiler(), CountingPolicy(), CountingExecutor(executor_inner)

    # cancel
    request, rules = _request(), _field_rules()
    pending = prepare_detected_clarification(request, ontology, mapping, rules)
    compiler, policy, executor = fresh_counters()
    provider = CountingProvider({"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5})
    with pytest.raises((ClarificationQueryConstraintError, ClarificationExecutionAdapterError,
                         ClarificationError, AmbiguityDetectorError)):
        _run_all(request, ontology, mapping, rules, pending,
                 ClarificationChoice(cancelled=True), provider, executor, compiler, policy)
    assert provider.calls == 0 and compiler.calls == 0 and policy.calls == 0 and executor.calls == 0

    # no match
    plain = _request(question=f"{QUESTION_BASE} plain timetable")
    compiler, policy, executor = fresh_counters()
    provider = CountingProvider({"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5})
    with pytest.raises((AmbiguityDetectorError, ClarificationError,
                         ClarificationQueryConstraintError, ClarificationExecutionAdapterError)):
        _run_all(plain, ontology, mapping, rules, None,  # type: ignore[arg-type]
                 ClarificationChoice(selection=["0" * 64]), provider, executor, compiler, policy)
    assert provider.calls == 0 and compiler.calls == 0 and executor.calls == 0

    # drift variants
    request, rules = _request(), _field_rules()
    good_pending, good_choice = _pending_and_choice(request, ontology, mapping, rules, "Course.name")
    drifted_rules = {"version": AMBIGUITY_DETECTOR_VERSION, "rules": [
        {"rule_id": "rule-field", "triggers": ["출석부"],
         "options": [{"option_key": "opt-name", "semantic_ref": "Course.name"},
                     {"option_key": "opt-credits", "semantic_ref": "Course.credits"}]}]}
    for kwargs in (
        {"request_data": _request(question=f"{QUESTION_BASE} plain timetable")},
        {"rules_data": drifted_rules},
        {"choice": ClarificationChoice(selection=["0" * 64])},
        {"mapping": _mapping("other-db")},
        {"provider": object()},
        {"executor": object()},
    ):
        compiler, policy, executor_c = fresh_counters()
        provider = CountingProvider({"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5})
        call = {"request_data": request, "ontology": ontology, "mapping": mapping,
                "rules_data": rules, "pending": good_pending, "choice": good_choice,
                "provider": provider, "executor": executor_c,
                "compiler": compiler, "sql_policy": policy}
        call.update(kwargs)
        with pytest.raises((AmbiguityDetectorError, ClarificationError,
                             ClarificationQueryConstraintError, ClarificationExecutionAdapterError)):
            run_clarified_execution(**call)
        assert provider.calls == 0 and compiler.calls == 0 and policy.calls == 0 and executor_c.calls == 0


def test_constraint_provider_invalid_ir_block_compiler(sqlite_db):
    executor_inner = SQLiteReadOnlyExecutor({"synth-db": sqlite_db})
    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _field_rules()
    pending, choice = _pending_and_choice(request, ontology, mapping, rules, "Course.name")

    violating = CountingProvider({"entity": "Instructor", "select": ["Instructor.name"], "filters": [], "limit": 5})
    compiler, policy, executor = CountingCompiler(), CountingPolicy(), CountingExecutor(executor_inner)
    with pytest.raises(ClarificationQueryConstraintError):
        _run_all(request, ontology, mapping, rules, pending, choice, violating, executor, compiler, policy)
    assert compiler.calls == 0 and policy.calls == 0 and executor.calls == 0

    bad_struct = CountingProvider({"entity": "Course", "select": [], "filters": []})
    compiler, policy, executor = CountingCompiler(), CountingPolicy(), CountingExecutor(executor_inner)
    with pytest.raises(ClarificationQueryConstraintError):
        _run_all(request, ontology, mapping, rules, pending, choice, bad_struct, executor, compiler, policy)
    assert compiler.calls == 0 and executor.calls == 0

    class Failing:
        def __init__(self) -> None:
            self.calls = 0

        def generate_ir(self, request, ontology, mapping, constraint):
            self.calls += 1
            raise RuntimeError("boom synthetic failure")

    failing = Failing()
    compiler, policy, executor = CountingCompiler(), CountingPolicy(), CountingExecutor(executor_inner)
    with pytest.raises(ClarificationQueryConstraintError):
        _run_all(request, ontology, mapping, rules, pending, choice, failing, executor, compiler, policy)
    assert failing.calls == 1 and compiler.calls == 0 and executor.calls == 0


def test_late_failures_preserve_code_stage_and_ir(sqlite_db):
    from backend.adaptive.harness.contracts import HarnessErrorCode as Code
    from backend.adaptive.harness.contracts import HarnessStage as Stage

    class FailCompiler:
        def __init__(self) -> None:
            self.calls = 0

        def compile(self, query_ir, mapping):
            self.calls += 1
            raise HarnessFailure(Code.SQL_COMPILE_ERROR, "synthetic compile failure", stage=Stage.SQL_COMPILED)

    class FailPolicy:
        def __init__(self) -> None:
            self.calls = 0

        def validate(self, query, **kwargs):
            self.calls += 1
            raise HarnessFailure(Code.UNSAFE_SQL, "synthetic policy failure", stage=Stage.SQL_VALIDATED)

    class FailExecutor:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, database_id, query):
            self.calls += 1
            raise HarnessFailure(Code.EXECUTION_ERROR, "synthetic execution failure", stage=Stage.EXECUTED)

    class WrongColumnsExecutor:
        def __init__(self, inner) -> None:
            self.calls = 0
            self.inner = inner

        def execute(self, database_id, query):
            self.calls += 1
            columns, _ = self.inner.execute(database_id, query)
            return (["wrong-column"], [])

    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _field_rules()
    payload = {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5}
    real_executor = SQLiteReadOnlyExecutor({"synth-db": sqlite_db})

    cases = [
        (FailCompiler(), ConservativeSqlPolicy(), real_executor, Code.SQL_COMPILE_ERROR, Stage.SQL_COMPILED),
        (SingleTableSQLiteCompiler(), FailPolicy(), real_executor, Code.UNSAFE_SQL, Stage.SQL_VALIDATED),
        (SingleTableSQLiteCompiler(), ConservativeSqlPolicy(), FailExecutor(), Code.EXECUTION_ERROR, Stage.EXECUTED),
        (SingleTableSQLiteCompiler(), ConservativeSqlPolicy(), WrongColumnsExecutor(real_executor),
         Code.RESULT_SCHEMA_MISMATCH, Stage.RESULT_VALIDATED),
    ]
    for compiler, policy, executor, code, stage in cases:
        pending, choice = _pending_and_choice(request, ontology, mapping, rules, "Course.name")
        provider = CountingProvider(payload)
        result = run_clarified_execution(
            copy.deepcopy(request), copy.deepcopy(ontology), copy.deepcopy(mapping),
            copy.deepcopy(rules), copy.deepcopy(pending), copy.deepcopy(choice),
            provider, executor, compiler=compiler, sql_policy=policy,
        )
        assert result.harness_result.status is HarnessStatus.FAILED
        assert result.harness_result.error is not None
        assert result.harness_result.error.code is code
        assert result.harness_result.error.stage is stage
        assert result.harness_result.query_ir == result.generation.query_ir


def test_malicious_compiler_blocked_before_executor(sqlite_db):
    class MaliciousCompiler:
        def __init__(self) -> None:
            self.calls = 0

        def compile(self, query_ir, mapping):
            self.calls += 1
            return CompiledQuery(
                sql='DROP TABLE courses; SELECT "subject_name" FROM "courses" LIMIT ?',
                parameters=[5],
                expected_columns=["Course.name"],
            )

    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _field_rules()
    pending, choice = _pending_and_choice(request, ontology, mapping, rules, "Course.name")
    provider = CountingProvider({"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5})
    executor = CountingExecutor(SQLiteReadOnlyExecutor({"synth-db": sqlite_db}))
    result = _run_all(request, ontology, mapping, rules, pending, choice,
                      provider, executor, MaliciousCompiler(), ConservativeSqlPolicy())
    assert result.harness_result.status is HarnessStatus.FAILED
    assert result.harness_result.error is not None
    assert result.harness_result.error.code is HarnessErrorCode.UNSAFE_SQL
    assert executor.calls == 0


def test_mutation_isolation(sqlite_db):
    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _field_rules()
    pending, choice = _pending_and_choice(request, ontology, mapping, rules, "Course.name")
    before = (request.model_dump(mode="json"), ontology.model_dump(mode="json"), mapping.model_dump(mode="json"))

    def mutating(request_c, ontology_c, mapping_c, constraint_c):
        object.__setattr__(request_c, "question", f"{QUESTION_BASE} mutated")
        object.__setattr__(mapping_c, "version", 999)
        return {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5}

    provider = CountingProvider(mutating)
    inner = SQLiteReadOnlyExecutor({"synth-db": sqlite_db})

    class MutatingExecutor:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, database_id, query):
            self.calls += 1
            columns, rows = inner.execute(database_id, query)
            query.parameters.append(999)
            query.sql = query.sql + " -- mutated"
            return columns, rows

    executor = MutatingExecutor()
    generation_before = {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5}
    result = _run_all(request, ontology, mapping, rules, pending, choice, provider, executor)
    assert (request.model_dump(mode="json"), ontology.model_dump(mode="json"), mapping.model_dump(mode="json")) == before
    assert result.generation.query_ir.model_dump(mode="json") == generation_before
    assert result.harness_result.query_ir == result.generation.query_ir
    assert result.harness_result.status is HarnessStatus.SUCCEEDED


def test_one_shot_second_call_and_drift_are_fixed_errors():
    request, ontology, mapping = _request(), _ontology(), _mapping()
    query_ir = {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5}
    from backend.adaptive.harness.contracts import QueryIR as QIR
    model = _OneShotQueryModel(request, ontology, mapping, QIR.model_validate(query_ir))
    first = model.generate_ir(request, ontology, mapping)
    assert first == QIR.model_validate(query_ir)
    with pytest.raises(HarnessFailure):
        model.generate_ir(request, ontology, mapping)
    fresh = _OneShotQueryModel(request, ontology, mapping, QIR.model_validate(query_ir))
    with pytest.raises(HarnessFailure):
        fresh.generate_ir(_request(question=f"{QUESTION_BASE} other 성적표"), ontology, mapping)


def test_forged_nested_result_and_public_injection_impossible(sqlite_db, monkeypatch):
    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _field_rules()
    pending, choice = _pending_and_choice(request, ontology, mapping, rules, "Course.name")
    provider = CountingProvider({"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5})
    executor = SQLiteReadOnlyExecutor({"synth-db": sqlite_db})

    params = set(inspect.signature(run_clarified_execution).parameters)
    for forbidden in ("query_ir", "queryir", "generation_result", "constrained_result",
                      "constraint", "model", "query_model", "harness_result", "prebuilt_sql", "result"):
        assert forbidden not in params

    import backend.adaptive.harness.clarification_execution_adapter as adapter

    real_harness = adapter.ExecutionHarness

    class ForgedResultHarness(real_harness):
        def run(self, request_data, ontology, mapping):
            result = super().run(request_data, ontology, mapping)
            forged = result.model_copy(update={"query_ir": None})
            return HarnessResult.model_validate(forged.model_dump(mode="json", warnings=False))

    monkeypatch.setattr(adapter, "ExecutionHarness", ForgedResultHarness)
    with pytest.raises(ClarificationExecutionAdapterError) as exc_info:
        _run_all(request, ontology, mapping, rules, pending, choice,
                 CountingProvider({"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5}),
                 executor)
    assert exc_info.value.code == ClarificationExecutionAdapterErrorCode.INTERNAL_ERROR.value

    class EarlyFailureHarness(real_harness):
        def run(self, request_data, ontology, mapping):
            result = super().run(request_data, ontology, mapping)
            assert result.status is HarnessStatus.SUCCEEDED
            from backend.adaptive.harness.contracts import HarnessError
            failed = result.model_copy(update={
                "status": HarnessStatus.FAILED,
                "error": HarnessError(code=HarnessErrorCode.MODEL_INVALID_OUTPUT,
                                      stage=HarnessStage.IR_GENERATED, message="synthetic early failure"),
            })
            return HarnessResult.model_validate(failed.model_dump(mode="json", warnings=False))

    monkeypatch.setattr(adapter, "ExecutionHarness", EarlyFailureHarness)
    with pytest.raises(ClarificationExecutionAdapterError) as exc_info:
        _run_all(request, ontology, mapping, rules, pending, choice, provider, executor)
    assert exc_info.value.code == ClarificationExecutionAdapterErrorCode.INTERNAL_ERROR.value


def test_result_contract_rejects_direct_nested_forgery(sqlite_db):
    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _field_rules()
    pending, choice = _pending_and_choice(request, ontology, mapping, rules, "Course.name")
    result = _run_all(
        request,
        ontology,
        mapping,
        rules,
        pending,
        choice,
        CountingProvider(
            {
                "entity": "Course",
                "select": ["Course.name"],
                "filters": [],
                "limit": 5,
            }
        ),
        SQLiteReadOnlyExecutor({"synth-db": sqlite_db}),
    )

    wrong_ir = result.harness_result.model_copy(
        update={
            "query_ir": result.generation.query_ir.model_copy(
                update={"limit": result.generation.query_ir.limit + 1}
            )
        }
    )
    wrong_request = result.harness_result.model_copy(update={"request_id": "other-request"})
    early_failure = result.harness_result.model_copy(
        update={
            "status": HarnessStatus.FAILED,
            "error": {
                "code": HarnessErrorCode.MODEL_INVALID_OUTPUT,
                "stage": HarnessStage.IR_GENERATED,
                "message": "synthetic",
            },
        }
    )
    success_with_error = result.harness_result.model_copy(
        update={
            "error": {
                "code": HarnessErrorCode.EXECUTION_ERROR,
                "stage": HarnessStage.EXECUTED,
                "message": "synthetic",
            }
        }
    )

    for forged in (wrong_ir, wrong_request, early_failure, success_with_error):
        payload = result.model_dump(mode="json", warnings=False)
        payload["harness_result"] = forged.model_dump(mode="json", warnings=False)
        with pytest.raises(ValidationError):
            ClarificationExecutionResult.model_validate(payload)

def test_adapter_errors_and_streams_have_no_raw(sqlite_db, caplog, capsys):
    request = _request(request_id=PATH_LIKE_REQUEST_ID, database_id=PATH_LIKE_DATABASE_ID,
                       question=f"{QUESTION_BASE} 성적표 {MALICIOUS} {LOCAL_PATH}")
    mapping = _mapping(database_id=PATH_LIKE_DATABASE_ID)
    ontology, rules = _ontology(), _field_rules()
    pending, choice = _pending_and_choice(request, ontology, mapping, rules, "Course.name")
    executor = SQLiteReadOnlyExecutor({PATH_LIKE_DATABASE_ID: sqlite_db})
    provider = CountingProvider({"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5})
    result = _run_all(request, ontology, mapping, rules, pending, choice, provider, executor)
    # HarnessResult boundary explicitly keeps raw SQL/rows per the existing contract.
    assert result.harness_result.sql is not None and "SELECT" in result.harness_result.sql
    assert len(result.harness_result.rows) == 2

    bad = CountingProvider({"entity": "Instructor", "select": ["Instructor.name"], "filters": [], "limit": 5})
    with pytest.raises(ClarificationQueryConstraintError) as exc_info:
        _run_all(request, ontology, mapping, rules, pending, choice, bad, executor)
    error_blob = str(exc_info.value)
    with pytest.raises(ClarificationExecutionAdapterError) as exc_info2:
        run_clarified_execution(request, ontology, mapping, rules, pending, choice,
                                object(), executor)
    error_blob += str(exc_info2.value)
    out, err = capsys.readouterr()
    log_blob = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (PATH_LIKE_REQUEST_ID, PATH_LIKE_DATABASE_ID, SECRET_REQUEST_ID,
                   SECRET_DATABASE_ID, MALICIOUS, LOCAL_PATH, QUESTION_BASE,
                   "courses", "subject_name", "opt-name", "rule-field", "성적표"):
        assert secret not in error_blob
        assert secret not in out
        assert secret not in err
        assert secret not in log_blob


def test_forged_inputs_rejected_without_warnings(sqlite_db):
    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _field_rules()
    pending, choice = _pending_and_choice(request, ontology, mapping, rules, "Course.name")
    good = {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5}
    executor = SQLiteReadOnlyExecutor({"synth-db": sqlite_db})
    forged_request = request.model_copy(update={"question": "   "})
    forged_mapping = mapping.model_copy(update={"status": "DRAFT"})
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        for kwargs in ({"request_data": forged_request}, {"mapping": forged_mapping},
                       {"provider": object()}, {"executor": object()}):
            call = {"request_data": request, "ontology": ontology, "mapping": mapping,
                    "rules_data": rules, "pending": pending, "choice": choice,
                    "provider": CountingProvider(good), "executor": executor}
            call.update(kwargs)
            with pytest.raises((AmbiguityDetectorError, ClarificationError,
                                 ClarificationQueryConstraintError, ClarificationExecutionAdapterError)):
                run_clarified_execution(**call)
    assert not emitted
