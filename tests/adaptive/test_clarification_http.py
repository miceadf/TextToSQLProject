"""Synthetic tests for clarification-http-v1 (Phase 6-12).

Small synthetic ontology/mapping/ruleset fixtures only; temporary
SQLite DB and counting doubles. No legacy mount, UI, Ollama,
PostgreSQL, or CNU material.
"""

from __future__ import annotations

import copy
import inspect
import json
import sqlite3
import tempfile
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.adaptive.api.clarification_http import (
    CLARIFICATION_HTTP_VERSION,
    START_PATH,
    SUBMIT_PATH,
    ClarificationHttpConfigError,
    ClarificationHttpErrorCode,
    HttpExecution,
    StartRequestBody,
    StartResponse,
    SubmitRequestBody,
    SubmitResponse,
    _ERROR_STATUS,
    create_clarification_http_app,
)
from backend.adaptive.api import (
    CLARIFICATION_HTTP_VERSION as EXPORTED_HTTP_VERSION,
    create_clarification_http_app as exported_app_factory,
)
from backend.adaptive.harness import (
    ClarificationChoice,
    ClarificationSessionCoordinator,
    ConservativeSqlPolicy,
    DataType,
    FieldMapping,
    HarnessErrorCode,
    HarnessRequest,
    HarnessStage,
    MappingProfile,
    MappingStatus,
    Ontology,
    OntologyField,
    Operator,
    SingleTableSQLiteCompiler,
    SQLiteReadOnlyExecutor,
)
from backend.adaptive.harness.ambiguity_detector import AMBIGUITY_DETECTOR_VERSION
from backend.adaptive.harness.errors import HarnessFailure

QUESTION_BASE = "SYNTH_Q_CH12_a91f"
MALICIOUS = "MALSTR_A91f_<script>'\"; DROP"
LOCAL_PATH = "C:\\synth_a91f\\private\\notes.txt"
SECRET_TOKEN = "AKIAIOSFODNN7_SYNTH_A91F_SECRET"


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


def _mapping(database_id: str = "synth-db", status: MappingStatus = MappingStatus.PUBLISHED) -> MappingProfile:
    return MappingProfile(
        database_id=database_id,
        version=1,
        status=status,
        fields={
            "Course.name": FieldMapping(table="courses", column="subject_name"),
            "Course.credits": FieldMapping(table="courses", column="credit_hours"),
            "Instructor.name": FieldMapping(table="courses", column="subject_name"),
        },
    )


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


def _conflict_rules() -> dict:
    return {
        "version": AMBIGUITY_DETECTOR_VERSION,
        "rules": [
            {
                "rule_id": "rule-first",
                "triggers": ["성적표"],
                "options": [
                    {"option_key": "opt-course", "semantic_ref": "Course"},
                    {"option_key": "opt-instructor", "semantic_ref": "Instructor"},
                ],
            },
            {
                "rule_id": "rule-second",
                "triggers": ["보여줘"],
                "options": [
                    {"option_key": "opt-a", "semantic_ref": "Course"},
                    {"option_key": "opt-b", "semantic_ref": "Course.name"},
                ],
            },
        ],
    }


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


class CountingExecutor:
    def __init__(self, inner) -> None:
        self.calls = 0
        self.inner = inner

    def execute(self, database_id, query):
        self.calls += 1
        return self.inner.execute(database_id, query)


class ServerLoader:
    def __init__(self, ontology, mapping, rules) -> None:
        self.calls = 0
        self._triple = (ontology, mapping, rules)

    def load(self, database_id: str):
        self.calls += 1
        return copy.deepcopy(self._triple[0]), copy.deepcopy(self._triple[1]), copy.deepcopy(self._triple[2])


def _make_app(rules, sqlite_path=None, provider_payload=None, executor_inner=None, coordinator=None):
    coord = coordinator or ClarificationSessionCoordinator(ttl_seconds=600.0)
    loader = ServerLoader(_ontology(), _mapping(), rules)
    provider = CountingProvider(
        provider_payload if provider_payload is not None else {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 10}
    )
    if executor_inner is None:
        inner = SQLiteReadOnlyExecutor({"synth-db": sqlite_path}) if sqlite_path is not None else object()
        if sqlite_path is None:
            class _Noop:
                def execute(self, database_id, query):
                    raise HarnessFailure(HarnessErrorCode.EXECUTION_ERROR, "synthetic", stage=HarnessStage.EXECUTED)
            inner = _Noop()
    else:
        inner = executor_inner
    executor = CountingExecutor(inner)
    app = create_clarification_http_app(coord, loader, provider, executor)
    return app, coord, loader, provider, executor


def _start(client: TestClient, **overrides):
    body = {"request_id": "req-synth-ch12", "database_id": "synth-db", "question": f"{QUESTION_BASE} 성적표 보여줘"}
    body.update(overrides)
    return client.post(START_PATH, json=body)


def test_routes_exact_and_no_legacy_import():
    assert EXPORTED_HTTP_VERSION == CLARIFICATION_HTTP_VERSION
    assert exported_app_factory is create_clarification_http_app
    app, *_ = _make_app(_field_rules())
    routes = sorted([(r.path, tuple(sorted(r.methods))) for r in app.routes if hasattr(r, "methods") and r.methods])
    assert routes == [(START_PATH, ("POST",)), (SUBMIT_PATH, ("POST",))]
    assert "backend.main" not in __import__("sys").modules
    source = Path("backend/adaptive/api/clarification_http.py").read_text(encoding="utf-8")
    assert "from backend.main" not in source
    assert "from backend import main" not in source
    assert "import backend.main" not in source


def test_no_match_start_not_required():
    app, coord, loader, provider, executor = _make_app(_field_rules())
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(START_PATH, json={"request_id": "r1", "database_id": "synth-db", "question": f"{QUESTION_BASE} plain timetable"})
    assert response.status_code == 200
    payload = response.json()
    assert payload == {"version": CLARIFICATION_HTTP_VERSION, "status": "NOT_REQUIRED"}
    assert loader.calls == 1
    assert provider.calls == 0
    assert executor.calls == 0


def test_entity_field_and_zero_row_submit(sqlite_db):
    for rules, want in ((_entity_rules(), "Course"), (_field_rules(), "Course.name")):
        app, _, _, provider, executor = _make_app(rules, sqlite_path=sqlite_db)
        client = TestClient(app, raise_server_exceptions=False)
        started = _start(client)
        assert started.status_code == 200
        data = started.json()
        assert data["status"] == "PENDING"
        assert set(data.keys()) == {"version", "status", "session_token", "options"}
        assert isinstance(data["session_token"], str)
        assert 2 <= len(data["options"]) <= 16
        for opt in data["options"]:
            assert set(opt.keys()) == {"option_id", "semantic_ref"}
        blob = json.dumps(data)
        for forbidden in ("fingerprint", "sha256", "subject_name", "courses", "rule-scope", "성적표", QUESTION_BASE):
            assert forbidden not in blob
        target = [o for o in data["options"] if o["semantic_ref"] == want]
        assert len(target) == 1
        submitted = client.post(SUBMIT_PATH, json={"session_token": data["session_token"], "selection": [target[0]["option_id"]], "cancelled": False})
        assert submitted.status_code == 200
        out = submitted.json()
        assert out["status"] == "EXECUTED"
        assert out["execution"]["status"] == "SUCCEEDED"
        assert "columns" in out["execution"] and "rows" in out["execution"]
        out_blob = json.dumps(out)
        for forbidden in ("sql", "query_ir", "trace", "fingerprint", "request_id", QUESTION_BASE):
            assert forbidden not in out_blob.lower()
        assert provider.calls == 1
        assert executor.calls == 1
    # zero-row success stays success
    payload = {"entity": "Course", "select": ["Course.name"], "filters": [{"field": "Course.name", "operator": "equals", "value": "NO_SUCH_VALUE_XYZ"}], "limit": 10}
    app, *_ = _make_app(_field_rules(), sqlite_path=sqlite_db, provider_payload=payload)
    client = TestClient(app, raise_server_exceptions=False)
    started = _start(client)
    data = started.json()
    target = [o for o in data["options"] if o["semantic_ref"] == "Course.name"][0]
    submitted = client.post(SUBMIT_PATH, json={"session_token": data["session_token"], "selection": [target["option_id"]], "cancelled": False})
    assert submitted.status_code == 200
    out = submitted.json()
    assert out["execution"]["status"] == "SUCCEEDED"
    assert out["execution"]["rows"] == []


def test_cancel_then_reuse_conflict(sqlite_db):
    app, *_ = _make_app(_field_rules(), sqlite_path=sqlite_db)
    client = TestClient(app, raise_server_exceptions=False)
    started = _start(client).json()
    cancelled = client.post(SUBMIT_PATH, json={"session_token": started["session_token"], "selection": [], "cancelled": True})
    assert cancelled.status_code == 200
    assert cancelled.json() == {"version": CLARIFICATION_HTTP_VERSION, "status": "CANCELLED"}
    reuse = client.post(SUBMIT_PATH, json={"session_token": started["session_token"], "selection": [], "cancelled": True})
    assert reuse.status_code == 409
    assert reuse.json() == {"error": {"code": "CLARIFICATION_SESSION_ALREADY_CONSUMED"}}


def test_expired_unknown_and_concurrent(sqlite_db):
    class Clock:
        def __init__(self) -> None:
            self.now = 1000.0
        def __call__(self) -> float:
            return self.now
    clock = Clock()
    coord = ClarificationSessionCoordinator(ttl_seconds=60.0, clock=clock)
    app, _, _, _, _ = _make_app(_field_rules(), sqlite_path=sqlite_db, coordinator=coord)
    client = TestClient(app, raise_server_exceptions=False)
    started = _start(client).json()
    clock.now += 61.0
    expired = client.post(SUBMIT_PATH, json={"session_token": started["session_token"], "selection": [], "cancelled": True})
    assert expired.status_code == 410
    assert expired.json() == {"error": {"code": "CLARIFICATION_SESSION_EXPIRED"}}
    unknown = client.post(SUBMIT_PATH, json={"session_token": "0" * 64, "selection": [], "cancelled": True})
    assert unknown.status_code == 404
    assert unknown.json() == {"error": {"code": "CLARIFICATION_SESSION_NOT_FOUND"}}
    # concurrent: one 200, one 409
    app2, *_ = _make_app(_field_rules(), sqlite_path=sqlite_db)
    client2 = TestClient(app2, raise_server_exceptions=False)
    started2 = _start(client2).json()
    option = [o for o in started2["options"] if o["semantic_ref"] == "Course.name"][0]
    outcomes: list = []
    barrier = threading.Barrier(2)
    def attempt() -> None:
        barrier.wait()
        r = client2.post(SUBMIT_PATH, json={"session_token": started2["session_token"], "selection": [option["option_id"]], "cancelled": False})
        outcomes.append((r.status_code, r.json()))
    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    codes = sorted(o[0] for o in outcomes)
    assert codes == [200, 409]


def test_malformed_and_validation_errors_are_400_fixed():
    app, *_ = _make_app(_field_rules())
    client = TestClient(app, raise_server_exceptions=False)
    bad_bodies = [
        ("not-json{", "application/json"),
        ('{"request_id": 123}', "application/json"),
        ('{"database_id": "synth-db"}', "application/json"),
        ('{"request_id": "r", "database_id": "synth-db", "question": "q", "ontology": {}}', "application/json"),
        (json.dumps({"request_id": "", "database_id": "synth-db", "question": "q"}), "application/json"),
        (json.dumps({"request_id": "r", "database_id": "synth-db", "question": "x" * 2001}), "application/json"),
        (json.dumps({"request_id": "r", "database_id": "synth-db", "question": "   "}), "application/json"),
    ]
    secrets = [QUESTION_BASE, MALICIOUS, LOCAL_PATH, SECRET_TOKEN]
    for raw, ctype in bad_bodies:
        response = client.post(START_PATH, content=raw.encode(), headers={"Content-Type": ctype})
        assert response.status_code == 400
        assert response.json() == {"error": {"code": "CLARIFICATION_HTTP_INVALID_REQUEST"}}
    # malicious content must not be reflected
    response = client.post(START_PATH, json={"request_id": SECRET_TOKEN, "database_id": "..\\secret", "question": f"{QUESTION_BASE} 성적표 {MALICIOUS} {LOCAL_PATH}"})
    blob = response.text
    assert response.status_code in (200, 400, 409, 503)
    if response.status_code == 400:
        for secret in secrets:
            assert secret not in blob
    # submit validation
    for payload in [
        {"selection": [], "cancelled": True},
        {"session_token": "tok", "selection": [], "cancelled": True, "extra": 1},
        {"session_token": 123, "selection": [], "cancelled": True},
        {"session_token": "tok", "selection": "not-a-list", "cancelled": False},
    ]:
        response = client.post(SUBMIT_PATH, json=payload)
        assert response.status_code == 400
        assert response.json() == {"error": {"code": "CLARIFICATION_HTTP_INVALID_REQUEST"}}


def test_loader_failures_are_503_without_downstream(sqlite_db):
    class RaisingLoader:
        calls = 0
        def load(self, database_id: str):
            self.calls += 1
            raise RuntimeError("synthetic loader boom")
    coord = ClarificationSessionCoordinator(ttl_seconds=600.0)
    provider = CountingProvider({"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5})
    executor = CountingExecutor(SQLiteReadOnlyExecutor({"synth-db": sqlite_db}))
    for loader in (
        RaisingLoader(),
        ServerLoader(_ontology(), _mapping(database_id="other-db"), _field_rules()),
        ServerLoader(_ontology(), _mapping(status=MappingStatus.DRAFT), _field_rules()),
        ServerLoader(_ontology(), {"database_id": "synth-db"}, _field_rules()),
    ):
        counting_coord = CountingCoordinator(coord) if not isinstance(loader, RaisingLoader) else coord
        app = create_clarification_http_app(counting_coord, loader, provider, executor)
        client = TestClient(app, raise_server_exceptions=False)
        before_provider, before_exec = provider.calls, executor.calls
        response = client.post(START_PATH, json={"request_id": "r", "database_id": "synth-db", "question": f"{QUESTION_BASE} 성적표"})
        assert response.status_code == 503
        assert response.json() == {"error": {"code": "CLARIFICATION_HTTP_CONTEXT_UNAVAILABLE"}}
        assert provider.calls == before_provider
        assert executor.calls == before_exec
        if isinstance(counting_coord, CountingCoordinator):
            assert counting_coord.starts == 0


class CountingCoordinator:
    def __init__(self, inner) -> None:
        self._inner = inner
        self.starts = 0
    def start(self, *args, **kwargs):
        self.starts += 1
        return self._inner.start(*args, **kwargs)
    def submit(self, *args, **kwargs):
        return self._inner.submit(*args, **kwargs)


def test_error_table_mapping(sqlite_db):
    app, *_ = _make_app(_conflict_rules(), sqlite_path=sqlite_db)
    client = TestClient(app, raise_server_exceptions=False)
    conflict = _start(client)
    assert conflict.status_code == 409
    assert conflict.json() == {"error": {"code": "AMBIGUITY_CONFLICT"}}
    # invalid choice -> 400
    app2, *_ = _make_app(_field_rules(), sqlite_path=sqlite_db)
    client2 = TestClient(app2, raise_server_exceptions=False)
    started = _start(client2).json()
    bad = client2.post(SUBMIT_PATH, json={"session_token": started["session_token"], "selection": ["0" * 64], "cancelled": False})
    assert bad.status_code == 400
    assert bad.json() == {"error": {"code": "CLARIFICATION_INVALID_CHOICE"}}
    # constraint violation -> 422
    violating = {"entity": "Instructor", "select": ["Instructor.name"], "filters": [], "limit": 5}
    app3, *_ = _make_app(_field_rules(), sqlite_path=sqlite_db, provider_payload=violating)
    client3 = TestClient(app3, raise_server_exceptions=False)
    started3 = _start(client3).json()
    target = [o for o in started3["options"] if o["semantic_ref"] == "Course.name"][0]
    violated = client3.post(SUBMIT_PATH, json={"session_token": started3["session_token"], "selection": [target["option_id"]], "cancelled": False})
    assert violated.status_code == 422
    assert violated.json() == {"error": {"code": "CONSTRAINT_VIOLATION"}}
    # provider error -> 502
    class BoomProvider:
        def generate_ir(self, *args, **kwargs):
            raise RuntimeError("synthetic boom")
    coord = ClarificationSessionCoordinator(ttl_seconds=600.0)
    loader = ServerLoader(_ontology(), _mapping(), _field_rules())
    executor = CountingExecutor(SQLiteReadOnlyExecutor({"synth-db": sqlite_db}))
    app4 = create_clarification_http_app(coord, loader, BoomProvider(), executor)
    client4 = TestClient(app4, raise_server_exceptions=False)
    started4 = _start(client4).json()
    target4 = [o for o in started4["options"] if o["semantic_ref"] == "Course.name"][0]
    failed = client4.post(SUBMIT_PATH, json={"session_token": started4["session_token"], "selection": [target4["option_id"]], "cancelled": False})
    assert failed.status_code == 502
    assert failed.json() == {"error": {"code": "CONSTRAINT_PROVIDER_ERROR"}}
    # explicit table entries
    assert _ERROR_STATUS["CLARIFICATION_HTTP_INVALID_REQUEST"] == 400
    assert _ERROR_STATUS["CLARIFICATION_SESSION_NOT_FOUND"] == 404
    assert _ERROR_STATUS["CLARIFICATION_SESSION_EXPIRED"] == 410
    assert _ERROR_STATUS["CLARIFICATION_SESSION_ALREADY_CONSUMED"] == 409
    assert _ERROR_STATUS["AMBIGUITY_CONFLICT"] == 409
    assert _ERROR_STATUS["AMBIGUITY_CONTEXT_MISMATCH"] == 409
    assert _ERROR_STATUS["CONSTRAINT_VIOLATION"] == 422
    assert _ERROR_STATUS["CONSTRAINT_PROVIDER_ERROR"] == 502
    assert _ERROR_STATUS["CLARIFICATION_HTTP_CONTEXT_UNAVAILABLE"] == 503
    assert _ERROR_STATUS["EXECUTION_ADAPTER_INTERNAL_ERROR"] == 500


def test_domain_failures_stay_200_redacted(sqlite_db):
    class FailCompiler:
        def compile(self, query_ir, mapping):
            raise HarnessFailure(HarnessErrorCode.SQL_COMPILE_ERROR, "synthetic", stage=HarnessStage.SQL_COMPILED)
    coord = ClarificationSessionCoordinator(ttl_seconds=600.0)
    loader = ServerLoader(_ontology(), _mapping(), _field_rules())
    provider = CountingProvider({"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5})
    executor = CountingExecutor(SQLiteReadOnlyExecutor({"synth-db": sqlite_db}))
    app = create_clarification_http_app(coord, loader, provider, executor, compiler=FailCompiler())
    client = TestClient(app, raise_server_exceptions=False)
    started = _start(client).json()
    target = [o for o in started["options"] if o["semantic_ref"] == "Course.name"][0]
    response = client.post(SUBMIT_PATH, json={"session_token": started["session_token"], "selection": [target["option_id"]], "cancelled": False})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "EXECUTED"
    assert payload["execution"]["status"] == "FAILED"
    assert payload["execution"]["error"]["code"] == HarnessErrorCode.SQL_COMPILE_ERROR.value
    assert payload["execution"]["error"]["stage"] == HarnessStage.SQL_COMPILED.value
    assert "rows" not in payload["execution"]
    blob = json.dumps(payload)
    assert "synthetic" not in blob
    assert "SELECT" not in blob
    # result-schema mismatch via wrong columns
    class WrongColumns:
        def execute(self, database_id, query):
            return (["wrong"], [])
    coord2 = ClarificationSessionCoordinator(ttl_seconds=600.0)
    app2 = create_clarification_http_app(coord2, ServerLoader(_ontology(), _mapping(), _field_rules()), CountingProvider({"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5}), CountingExecutor(WrongColumns()))
    client2 = TestClient(app2, raise_server_exceptions=False)
    started2 = _start(client2).json()
    target2 = [o for o in started2["options"] if o["semantic_ref"] == "Course.name"][0]
    response2 = client2.post(SUBMIT_PATH, json={"session_token": started2["session_token"], "selection": [target2["option_id"]], "cancelled": False})
    assert response2.status_code == 200
    assert response2.json()["execution"]["error"]["code"] == HarnessErrorCode.RESULT_SCHEMA_MISMATCH.value


def test_unexpected_errors_are_500_without_leak(sqlite_db, capsys):
    class BoomCoordinator:
        def start(self, *args, **kwargs):
            raise RuntimeError("synthetic secret boom")
        def submit(self, *args, **kwargs):
            raise RuntimeError("synthetic secret boom")
    loader = ServerLoader(_ontology(), _mapping(), _field_rules())
    provider = CountingProvider({})
    executor = CountingExecutor(object())
    app = create_clarification_http_app(BoomCoordinator(), loader, provider, executor)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(START_PATH, json={"request_id": "r", "database_id": "synth-db", "question": f"{QUESTION_BASE} 성적표"})
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "CLARIFICATION_HTTP_INTERNAL_ERROR"}}
    out, err = capsys.readouterr()
    assert "synthetic secret boom" not in out
    assert "synthetic secret boom" not in err
    assert "synthetic secret boom" not in response.text


def test_factory_validation_singleton_and_forbidden_fields(sqlite_db):
    coord = ClarificationSessionCoordinator(ttl_seconds=600.0)
    loader = ServerLoader(_ontology(), _mapping(), _field_rules())
    provider = CountingProvider({})
    executor = CountingExecutor(SQLiteReadOnlyExecutor({"synth-db": sqlite_db}))
    for bad in [
        (object(), loader, provider, executor, {}),
        (coord, object(), provider, executor, {}),
        (coord, loader, object(), executor, {}),
        (coord, loader, provider, object(), {}),
        (coord, loader, provider, executor, {"compiler": object()}),
        (coord, loader, provider, executor, {"sql_policy": object()}),
    ]:
        with pytest.raises(ClarificationHttpConfigError) as exc_info:
            create_clarification_http_app(bad[0], bad[1], bad[2], bad[3], **bad[4])
        assert exc_info.value.code == ClarificationHttpErrorCode.INVALID_CONFIGURATION.value
    # singleton reuse: same coordinator serves start then submit
    app = create_clarification_http_app(coord, loader, provider, executor)
    client = TestClient(app, raise_server_exceptions=False)
    started = client.post(START_PATH, json={"request_id": "r", "database_id": "synth-db", "question": f"{QUESTION_BASE} plain"}).json()
    assert started["status"] == "NOT_REQUIRED"
    for model in (StartRequestBody, SubmitRequestBody):
        fields = set(model.model_fields.keys())
        for forbidden in ("ontology", "mapping", "rules", "pending", "constraint", "query_ir", "model", "executor", "result", "semantic_ref"):
            assert forbidden not in fields
    params = set(inspect.signature(create_clarification_http_app).parameters)
    for forbidden in ("pending", "signal", "semantic_ref", "query_ir", "constraint", "query_model", "harness_result", "prebuilt_sql", "generation_result", "result", "record"):
        assert forbidden not in params


def test_response_forgery_and_non_json_rows_rejected():
    with pytest.raises(ValidationError):
        StartResponse.model_validate({"version": CLARIFICATION_HTTP_VERSION, "status": "NOT_REQUIRED", "session_token": "tok", "options": []})
    with pytest.raises(ValidationError):
        SubmitResponse.model_validate({"version": CLARIFICATION_HTTP_VERSION, "status": "SUCCEEDED" if False else "CANCELLED", "execution": {"status": "SUCCEEDED", "columns": ["a"], "rows": [{"a": 1}]}})
    with pytest.raises(ValidationError):
        SubmitResponse.model_validate({"version": CLARIFICATION_HTTP_VERSION, "status": "EXECUTED", "execution": {"status": "SUCCEEDED", "columns": ["a"], "rows": [{"a": 1}], "error": {"code": "X"}}})
    with pytest.raises(ValidationError):
        SubmitResponse.model_validate({"version": CLARIFICATION_HTTP_VERSION, "status": "EXECUTED", "execution": {"status": "FAILED", "columns": ["a"], "rows": [], "error": {"code": "X"}}})
    with pytest.raises(ValidationError):
        StartResponse.model_validate({"version": CLARIFICATION_HTTP_VERSION, "status": "PENDING", "session_token": "t", "options": [], "sql": "SELECT 1"})
    with pytest.raises(ValidationError):
        HttpExecution.model_validate({"status": "SUCCEEDED", "columns": ["a"], "rows": [{"a": float("nan")}]})
    with pytest.raises(ValidationError):
        HttpExecution.model_validate({"status": "SUCCEEDED", "columns": ["a"], "rows": [{"a": {1, 2}}]})
    with pytest.raises(ValidationError):
        HttpExecution.model_validate({"status": "SUCCEEDED", "columns": ["a"], "rows": [{"other": 1}]})
    with pytest.raises(ValidationError):
        StartResponse.model_validate(
            {
                "version": CLARIFICATION_HTTP_VERSION,
                "status": "PENDING",
                "session_token": "x" * 43,
                "options": [
                    {"option_id": "0" * 64, "semantic_ref": "Course"},
                    {"option_id": "0" * 64, "semantic_ref": "Course.name"},
                ],
            }
        )
    valid_option = {
        "option_id": "0" * 64,
        "semantic_ref": "Course",
    }
    other_option = {
        "option_id": "1" * 64,
        "semantic_ref": "Instructor",
    }
    valid_start = StartResponse.model_validate(
        {
            "version": CLARIFICATION_HTTP_VERSION,
            "status": "PENDING",
            "session_token": "x" * 43,
            "options": [valid_option, other_option],
        }
    )
    forged_option = valid_start.options[0].model_copy(
        update={"semantic_ref": "invalid ref"}
    )
    with pytest.raises(ValidationError):
        StartResponse.model_validate(
            {
                **valid_start.model_dump(mode="json", warnings=False),
                "options": [forged_option, valid_start.options[1]],
            }
        )


def test_no_stdout_or_logs_on_errors(sqlite_db, caplog, capsys):
    app, *_ = _make_app(_field_rules(), sqlite_path=sqlite_db)
    client = TestClient(app, raise_server_exceptions=False)
    client.post(START_PATH, content=b"{{{", headers={"Content-Type": "application/json"})
    client.post(SUBMIT_PATH, json={"session_token": SECRET_TOKEN, "selection": [], "cancelled": True})
    out, err = capsys.readouterr()
    logs = "\n".join(r.getMessage() for r in caplog.records)
    for secret in (SECRET_TOKEN, QUESTION_BASE, MALICIOUS):
        assert secret not in out
        assert secret not in err
        assert secret not in logs
