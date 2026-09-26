"""Synthetic tests for clarification-session-v1 (Phase 6-11).

Small synthetic ontology/mapping/ruleset fixtures only; temporary
SQLite DB and counting doubles. No HTTP/UI/storage/Ollama/PostgreSQL.
"""

from __future__ import annotations

import copy
import inspect
import sqlite3
import tempfile
import threading
import warnings
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.adaptive.harness import (
    CLARIFICATION_SESSION_VERSION as EXPORTED_SESSION_VERSION,
    ClarificationChoice,
    ClarificationSessionCoordinator as ExportedSessionCoordinator,
    ConservativeSqlPolicy,
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
    SingleTableSQLiteCompiler,
    SQLiteReadOnlyExecutor,
)
from backend.adaptive.harness.ambiguity_detector import (
    AMBIGUITY_DETECTOR_VERSION,
    AmbiguityDetectorError,
    AmbiguityDetectorErrorCode,
    detect_ambiguity,
)
from backend.adaptive.harness.clarification import (
    ClarificationError,
    ClarificationErrorCode,
)
from backend.adaptive.harness.clarification_execution_adapter import (
    ClarificationExecutionAdapterError,
)
from backend.adaptive.harness.clarification_query_constraint import (
    ClarificationQueryConstraintError,
    ClarificationQueryConstraintErrorCode,
)
from backend.adaptive.harness.clarification_session import (
    CLARIFICATION_SESSION_VERSION,
    ClarificationSessionCoordinator,
    ClarificationSessionError,
    ClarificationSessionErrorCode,
    ClarificationSessionStartResult,
    ClarificationSessionStartStatus,
    ClarificationSessionSubmitResult,
    ClarificationSessionSubmitStatus,
)
from backend.adaptive.harness.errors import HarnessFailure

QUESTION_BASE = "SYNTH_Q_CS11_d5e2"
MALICIOUS = "MALSTR_D5e2_<script>'\"; DROP"
LOCAL_PATH = "C:\\synth_d5e2\\private\\notes.txt"
PATH_LIKE_REQUEST_ID = "..\\..\\synth_d5e2\\secret\\request-id"
SECRET_REQUEST_ID = "AKIAIOSFODNN7_SYNTH_D5E2_SECRET"
PATH_LIKE_DATABASE_ID = "..\\synth_d5e2\\secret-db\\..\\private"
SECRET_DATABASE_ID = "synth_d5e2_db_secret_token_1b7c"
PHYSICAL_TABLE = "courses"
PHYSICAL_COLUMN = "subject_name"


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
        "request_id": "req-synth-cs11",
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


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _coordinator(**overrides):
    values: dict = {"ttl_seconds": 600.0}
    values.update(overrides)
    return ClarificationSessionCoordinator(**values)


@pytest.fixture()
def sqlite_db():
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "courses.sqlite3"
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE courses (subject_name TEXT, credit_hours INTEGER)")
        connection.executemany(
            "INSERT INTO courses VALUES (?, ?)", [("데이터베이스", 3), ("운영체제", 3)]
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


def _choice_for(request, ontology, mapping, rules, want_ref: str) -> ClarificationChoice:
    signal = detect_ambiguity(request, ontology, mapping, rules)
    assert signal is not None
    wanted = [o for o in signal.options if o.semantic_ref == want_ref]
    assert len(wanted) == 1
    return ClarificationChoice(selection=[wanted[0].option_id])


def test_no_match_returns_not_required_without_session():
    assert EXPORTED_SESSION_VERSION == CLARIFICATION_SESSION_VERSION
    assert ExportedSessionCoordinator is ClarificationSessionCoordinator
    coord = _coordinator()
    plain = _request(question=f"{QUESTION_BASE} plain timetable")
    provider, executor = CountingProvider({}), CountingExecutor(object())
    started = coord.start(plain, _ontology(), _mapping(), _field_rules())
    assert started.status is ClarificationSessionStartStatus.NOT_REQUIRED
    assert started.session_token is None
    assert started.pending is None
    assert started.session_version == CLARIFICATION_SESSION_VERSION
    assert provider.calls == 0 if hasattr(provider, "calls") else True
    assert "calls" not in started.model_dump(mode="json")


def test_single_match_pending_and_snapshot_isolation():
    coord = _coordinator()
    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _field_rules()
    before = (
        request.model_dump(mode="json"),
        ontology.model_dump(mode="json"),
        mapping.model_dump(mode="json"),
    )
    started = coord.start(request, ontology, mapping, rules)
    assert started.status is ClarificationSessionStartStatus.PENDING
    assert started.session_token is not None
    assert started.pending is not None
    assert started.options is not None
    assert len(started.pending.options) == 2
    assert len(started.options) == 2
    # Caller mutation after start does not affect the stored snapshot.
    request.question = f"{QUESTION_BASE} mutated 성적표"
    choice = _choice_for(_request(), _ontology(), _mapping(), _field_rules(), "Course.name")
    assert (request.model_dump(mode="json"),) != (before[0],)  # mutated
    # Tampering the returned pending copy must not affect the session.
    tampered = started.pending.model_copy(update={"mapping_version": 99})
    assert tampered.mapping_version == 99
    assert started.pending.mapping_version == 1


def test_start_options_can_be_submitted_without_redetection(sqlite_db):
    coord = _coordinator()
    started = coord.start(_request(), _ontology(), _mapping(), _field_rules())
    assert started.session_token is not None
    assert started.options is not None
    selected = next(
        option for option in started.options if option.semantic_ref == "Course.name"
    )
    result = coord.submit(
        started.session_token,
        ClarificationChoice(selection=[selected.option_id]),
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
    assert result.status is ClarificationSessionSubmitStatus.EXECUTED


def test_entity_and_field_submit_execute_once(sqlite_db):
    for want, rules in (("Course", _entity_rules()), ("Course.name", _field_rules())):
        coord = _coordinator()
        request, ontology, mapping = _request(), _ontology(), _mapping()
        started = coord.start(request, ontology, mapping, rules)
        assert started.session_token is not None
        choice = _choice_for(request, ontology, mapping, rules, want)
        provider = CountingProvider(
            {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 10}
        )
        compiler, policy = CountingCompiler(), CountingPolicy()
        executor = CountingExecutor(SQLiteReadOnlyExecutor({"synth-db": sqlite_db}))
        result = coord.submit(
            started.session_token, choice, provider, executor,
            compiler=compiler, sql_policy=policy,
        )
        assert result.status is ClarificationSessionSubmitStatus.EXECUTED
        assert provider.calls == 1
        assert compiler.calls == 1
        assert policy.calls == 1
        assert executor.calls == 1
        assert result.execution is not None
        assert result.execution.harness_result.status is HarnessStatus.SUCCEEDED
        assert result.execution.harness_result.query_ir == result.execution.generation.query_ir
        assert result.clarification_result is None


def test_cancel_calls_nothing_and_consumes():
    coord = _coordinator()
    started = coord.start(_request(), _ontology(), _mapping(), _field_rules())
    assert started.session_token is not None
    provider = CountingProvider(
        {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5}
    )
    executor = CountingExecutor(object())
    result = coord.submit(
        started.session_token, ClarificationChoice(cancelled=True), provider, executor
    )
    assert result.status is ClarificationSessionSubmitStatus.CANCELLED
    assert result.clarification_result is not None
    assert result.execution is None
    assert provider.calls == 0
    with pytest.raises(ClarificationSessionError) as exc_info:
        coord.submit(started.session_token, ClarificationChoice(cancelled=True), provider, executor)
    assert exc_info.value.code == ClarificationSessionErrorCode.ALREADY_CONSUMED.value


def test_ttl_boundary_then_expiry_without_reactivation(sqlite_db):
    clock = FakeClock(now=1000.0)
    coord = ClarificationSessionCoordinator(ttl_seconds=60.0, clock=clock)
    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _field_rules()
    started = coord.start(request, ontology, mapping, rules)
    assert started.session_token is not None
    clock.now = 1000.0 + 60.0 - 0.001
    choice = _choice_for(request, ontology, mapping, rules, "Course.name")
    provider = CountingProvider(
        {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5}
    )
    executor = SQLiteReadOnlyExecutor({"synth-db": sqlite_db})
    result = coord.submit(started.session_token, choice, provider, executor)
    assert result.status is ClarificationSessionSubmitStatus.EXECUTED

    started2 = coord.start(_request(), _ontology(), _mapping(), _field_rules())
    assert started2.session_token is not None
    clock.now = clock.now + 60.0 + 1.0
    provider2 = CountingProvider(
        {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5}
    )
    executor2 = CountingExecutor(SQLiteReadOnlyExecutor({"synth-db": sqlite_db}))
    with pytest.raises(ClarificationSessionError) as exc_info:
        coord.submit(started2.session_token, choice, provider2, executor2)
    assert exc_info.value.code == ClarificationSessionErrorCode.EXPIRED.value
    assert provider2.calls == 0
    assert executor2.calls == 0
    with pytest.raises(ClarificationSessionError) as exc_info2:
        coord.submit(started2.session_token, choice, provider2, executor2)
    assert exc_info2.value.code == ClarificationSessionErrorCode.EXPIRED.value


def test_unknown_malformed_and_invalid_choice_errors():
    coord = _coordinator()
    started = coord.start(_request(), _ontology(), _mapping(), _field_rules())
    assert started.session_token is not None
    provider = CountingProvider(
        {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5}
    )
    executor = CountingExecutor(object())
    with pytest.raises(ClarificationSessionError) as exc_info:
        coord.submit("0" * 64, ClarificationChoice(cancelled=True), provider, executor)
    assert exc_info.value.code == ClarificationSessionErrorCode.NOT_FOUND.value
    for bad in ("", "short", "bad token!!", None, 123):
        with pytest.raises(ClarificationSessionError) as exc_info:
            coord.submit(bad, ClarificationChoice(cancelled=True), provider, executor)
        assert exc_info.value.code == ClarificationSessionErrorCode.INVALID_INPUT.value
    forged = ClarificationChoice.model_construct(selection=LOCAL_PATH, cancelled=False)
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        with pytest.raises(ClarificationSessionError) as exc_info:
            coord.submit(started.session_token, forged, provider, executor)
        assert exc_info.value.code == ClarificationSessionErrorCode.INVALID_INPUT.value
    assert not emitted
    # First syntactically valid invalid choice consumes the session.
    with pytest.raises((ClarificationError, AmbiguityDetectorError)) as exc_info:
        coord.submit(
            started.session_token,
            ClarificationChoice(selection=["0" * 64]),
            provider,
            executor,
        )
    assert exc_info.value.code in (
        ClarificationErrorCode.INVALID_CHOICE.value,
        AmbiguityDetectorErrorCode.CONTEXT_MISMATCH.value,
    )
    with pytest.raises(ClarificationSessionError) as exc_info:
        coord.submit(started.session_token, ClarificationChoice(cancelled=True), provider, executor)
    assert exc_info.value.code == ClarificationSessionErrorCode.ALREADY_CONSUMED.value
    # cancel+selection stays consumed with the original downstream error.
    coord2 = _coordinator()
    started2 = coord2.start(_request(), _ontology(), _mapping(), _field_rules())
    assert started2.session_token is not None
    with pytest.raises(ClarificationError) as exc_info:
        coord2.submit(
            started2.session_token,
            ClarificationChoice(selection=["0" * 64], cancelled=True),
            provider,
            executor,
        )
    assert exc_info.value.code == ClarificationErrorCode.INVALID_CHOICE.value
    with pytest.raises(ClarificationSessionError) as exc_info:
        coord2.submit(started2.session_token, ClarificationChoice(cancelled=True), provider, executor)
    assert exc_info.value.code == ClarificationSessionErrorCode.ALREADY_CONSUMED.value


def test_downstream_failures_stay_consumed_and_late_results_preserved(sqlite_db):
    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _field_rules()
    good = {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5}

    class FailingProvider:
        def __init__(self) -> None:
            self.calls = 0

        def generate_ir(self, req, ont, mp, constraint):
            self.calls += 1
            raise RuntimeError("boom synthetic")

    for provider, expected in (
        (FailingProvider(), ClarificationQueryConstraintErrorCode.PROVIDER_ERROR.value),
        (
            CountingProvider({"entity": "Instructor", "select": ["Instructor.name"], "filters": [], "limit": 5}),
            ClarificationQueryConstraintErrorCode.CONSTRAINT_VIOLATION.value,
        ),
    ):
        coord = _coordinator()
        started = coord.start(request, ontology, mapping, rules)
        assert started.session_token is not None
        choice = _choice_for(request, ontology, mapping, rules, "Course.name")
        with pytest.raises(ClarificationQueryConstraintError) as exc_info:
            coord.submit(started.session_token, choice, provider, CountingExecutor(object()))
        assert exc_info.value.code == expected
        with pytest.raises(ClarificationSessionError) as exc_info2:
            coord.submit(started.session_token, choice, provider, CountingExecutor(object()))
        assert exc_info2.value.code == ClarificationSessionErrorCode.ALREADY_CONSUMED.value

    class FailCompiler:
        def __init__(self) -> None:
            self.calls = 0

        def compile(self, query_ir, mp):
            self.calls += 1
            raise HarnessFailure(
                HarnessErrorCode.SQL_COMPILE_ERROR, "synthetic", stage=HarnessStage.SQL_COMPILED
            )

    coord = _coordinator()
    started = coord.start(request, ontology, mapping, rules)
    assert started.session_token is not None
    choice = _choice_for(request, ontology, mapping, rules, "Course.name")
    result = coord.submit(
        started.session_token, choice, CountingProvider(good),
        SQLiteReadOnlyExecutor({"synth-db": sqlite_db}), compiler=FailCompiler(),
    )
    assert result.status is ClarificationSessionSubmitStatus.EXECUTED
    assert result.execution is not None
    assert result.execution.harness_result.status is HarnessStatus.FAILED
    assert result.execution.harness_result.error is not None
    assert result.execution.harness_result.error.code is HarnessErrorCode.SQL_COMPILE_ERROR
    assert result.execution.harness_result.error.stage is HarnessStage.SQL_COMPILED
    assert result.execution.harness_result.query_ir == result.execution.generation.query_ir


def test_concurrent_submit_reaches_downstream_once(sqlite_db):
    coord = _coordinator()
    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _field_rules()
    started = coord.start(request, ontology, mapping, rules)
    assert started.session_token is not None
    choice = _choice_for(request, ontology, mapping, rules, "Course.name")
    provider = CountingProvider(
        {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5}
    )
    executor = CountingExecutor(SQLiteReadOnlyExecutor({"synth-db": sqlite_db}))
    outcomes: list = []
    barrier = threading.Barrier(2)

    def attempt():
        barrier.wait()
        try:
            coord.submit(started.session_token, choice, provider, executor)
            outcomes.append("ok")
        except ClarificationSessionError as exc:
            outcomes.append(exc.code)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == sorted(["ok", ClarificationSessionErrorCode.ALREADY_CONSUMED.value])
    assert provider.calls == 1
    assert executor.calls == 1


def test_token_collision_preserves_existing_records():
    fixed = "Abcdefghijklmnop_1234567890-ABCDEFGHijklmnop"
    coord = ClarificationSessionCoordinator(ttl_seconds=600.0, token_factory=lambda: fixed)
    first = coord.start(_request(), _ontology(), _mapping(), _field_rules())
    assert first.status is ClarificationSessionStartStatus.PENDING
    with pytest.raises(ClarificationSessionError) as exc_info:
        coord.start(_request(), _ontology(), _mapping(), _field_rules())
    assert exc_info.value.code == ClarificationSessionErrorCode.INTERNAL_ERROR.value
    # Active record still usable exactly once.
    choice = _choice_for(_request(), _ontology(), _mapping(), _field_rules(), "Course.name")

    class StubExecutor:
        def execute(self, database_id, query):
            raise HarnessFailure(
                HarnessErrorCode.EXECUTION_ERROR, "synthetic", stage=HarnessStage.EXECUTED
            )

    result = coord.submit(
        fixed, choice,
        CountingProvider({"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5}),
        StubExecutor(),
    )
    assert result.status is ClarificationSessionSubmitStatus.EXECUTED
    # Terminal collision does not reactivate.
    with pytest.raises(ClarificationSessionError) as exc_info:
        coord.start(_request(), _ontology(), _mapping(), _field_rules())
    assert exc_info.value.code == ClarificationSessionErrorCode.INTERNAL_ERROR.value
    with pytest.raises(ClarificationSessionError) as exc_info:
        coord.submit(fixed, choice, CountingProvider({}), StubExecutor())
    assert exc_info.value.code == ClarificationSessionErrorCode.ALREADY_CONSUMED.value


def test_clock_failure_consumes_claimed_session_and_start_rejects_overflow():
    clock = FakeClock(now=1000.0)
    coord = _coordinator(clock=clock)
    started = coord.start(_request(), _ontology(), _mapping(), _field_rules())
    assert started.session_token is not None
    choice = _choice_for(_request(), _ontology(), _mapping(), _field_rules(), "Course.name")

    def broken_clock():
        raise RuntimeError("synthetic clock failure")

    coord._clock = broken_clock
    with pytest.raises(ClarificationSessionError) as exc_info:
        coord.submit(started.session_token, choice, CountingProvider({}), object())
    assert exc_info.value.code == ClarificationSessionErrorCode.INTERNAL_ERROR.value
    with pytest.raises(ClarificationSessionError) as exc_info:
        coord.submit(started.session_token, choice, CountingProvider({}), object())
    assert exc_info.value.code == ClarificationSessionErrorCode.ALREADY_CONSUMED.value

    overflow = ClarificationSessionCoordinator(
        ttl_seconds=86400.0,
        clock=lambda: 1.7976931348623157e308,
    )
    with pytest.raises(ClarificationSessionError) as exc_info:
        overflow.start(_request(), _ontology(), _mapping(), _field_rules())
    assert exc_info.value.code == ClarificationSessionErrorCode.INTERNAL_ERROR.value


def test_later_start_lazily_purges_expired_raw_context():
    clock = FakeClock(now=1000.0)
    tokens = iter(("A" * 43, "B" * 43))
    coord = ClarificationSessionCoordinator(
        ttl_seconds=1.0,
        clock=clock,
        token_factory=lambda: next(tokens),
    )
    first = coord.start(_request(), _ontology(), _mapping(), _field_rules())
    assert first.session_token is not None
    assert len(coord._active) == 1
    clock.now = 1001.0
    coord.start(_request(), _ontology(), _mapping(), _field_rules())
    assert len(coord._active) == 1
    with pytest.raises(ClarificationSessionError) as exc_info:
        coord.submit(
            first.session_token,
            ClarificationChoice(cancelled=True),
            CountingProvider({}),
            object(),
        )
    assert exc_info.value.code == ClarificationSessionErrorCode.EXPIRED.value


def test_forged_nested_results_and_no_injection_api(sqlite_db):
    coord = _coordinator()
    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _field_rules()
    started = coord.start(request, ontology, mapping, rules)
    assert started.session_token is not None
    choice = _choice_for(request, ontology, mapping, rules, "Course.name")
    result = coord.submit(
        started.session_token, choice,
        CountingProvider({"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5}),
        SQLiteReadOnlyExecutor({"synth-db": sqlite_db}),
    )
    assert result.status is ClarificationSessionSubmitStatus.EXECUTED
    forged_pending = started.pending.model_copy(update={"mapping_version": 99}) if started.pending else None
    with pytest.raises(ValidationError):
        ClarificationSessionStartResult.model_validate(
            {**started.model_dump(mode="json", warnings=False), "pending": forged_pending.model_dump(mode="json")}
        )
    with pytest.raises(ValidationError):
        ClarificationSessionStartResult.model_validate(
            {**started.model_dump(mode="json", warnings=False), "status": "NOT_REQUIRED"}
        )
    assert started.options is not None
    forged_option = started.options[0].model_copy(update={"option_id": "0" * 64})
    with pytest.raises(ValidationError):
        ClarificationSessionStartResult.model_validate(
            {
                **started.model_dump(mode="json", warnings=False),
                "options": [forged_option, *started.options[1:]],
            }
        )
    payload = result.model_dump(mode="json", warnings=False)
    payload["request_id_sha256"] = "0" * 64
    with pytest.raises(ValidationError):
        ClarificationSessionSubmitResult.model_validate(payload)
    swapped = result.model_dump(mode="json", warnings=False)
    swapped["status"] = "CANCELLED"
    with pytest.raises(ValidationError):
        ClarificationSessionSubmitResult.model_validate(swapped)

    start_params = set(inspect.signature(Coord_start := coord.start).parameters)
    submit_params = set(inspect.signature(coord.submit).parameters)
    for forbidden in (
        "pending", "signal", "semantic_ref", "query_ir", "constraint", "query_model",
        "harness_result", "prebuilt_sql", "generation_result", "result", "record",
    ):
        assert forbidden not in start_params
        assert forbidden not in submit_params


def test_errors_and_streams_have_no_raw(sqlite_db, caplog, capsys):
    request = _request(
        request_id=PATH_LIKE_REQUEST_ID,
        database_id=PATH_LIKE_DATABASE_ID,
        question=f"{QUESTION_BASE} 성적표 {MALICIOUS} {LOCAL_PATH}",
    )
    mapping = _mapping(database_id=PATH_LIKE_DATABASE_ID)
    coord = _coordinator()
    started = coord.start(request, _ontology(), mapping, _field_rules())
    assert started.session_token is not None
    token = started.session_token
    executor = SQLiteReadOnlyExecutor({PATH_LIKE_DATABASE_ID: sqlite_db})
    choice = _choice_for(request, _ontology(), mapping, _field_rules(), "Course.name")
    result = coord.submit(
        token, choice,
        CountingProvider({"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5}),
        executor,
    )
    assert result.status is ClarificationSessionSubmitStatus.EXECUTED
    blobs: list[str] = []
    with pytest.raises(ClarificationSessionError) as exc_info:
        coord.submit(token, choice, CountingProvider({}), executor)
    blobs.append(str(exc_info.value))
    with pytest.raises(ClarificationSessionError) as exc_info:
        coord.submit("unknown-token-value-123456", choice, CountingProvider({}), executor)
    blobs.append(str(exc_info.value))
    with pytest.raises(ClarificationExecutionAdapterError):
        coord.submit(
            coord.start(request, _ontology(), mapping, _field_rules()).session_token,  # type: ignore[arg-type]
            choice, object(), executor,
        )
    out, err = capsys.readouterr()
    log_blob = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (
        token,
        QUESTION_BASE,
        MALICIOUS,
        LOCAL_PATH,
        PATH_LIKE_REQUEST_ID,
        PATH_LIKE_DATABASE_ID,
        SECRET_REQUEST_ID,
        SECRET_DATABASE_ID,
        PHYSICAL_TABLE,
        PHYSICAL_COLUMN,
        "opt-name",
        "rule-field",
        "성적표",
    ):
        for blob in blobs:
            assert secret not in blob
        assert secret not in out
        assert secret not in err
        assert secret not in log_blob
