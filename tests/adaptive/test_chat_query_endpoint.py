"""Phase 7-13 QueryIR chat query endpoint over the legacy view profile."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.adaptive.api.chat_query import (
    CHAT_QUERY_PATH,
    CHAT_QUERY_VERSION,
    register_chat_query_routes,
)
from backend.adaptive.api.app_factory import (
    SYNTHETIC_PROTOTYPE_DATABASE_ID,
    create_synthetic_clarification_prototype,
)
from backend.adaptive.harness.contracts import CompiledQuery, HarnessErrorCode
from backend.adaptive.harness.errors import HarnessFailure
from backend.adaptive.harness.recorded import RecordedFailureCode
from backend.adaptive.harness.recording_orchestrator import RecordingProviderError
from tests.adaptive.test_legacy_view_partial_mapping import (
    DATABASE_ID,
    SECRET_DSN,
    VIEW,
    FakeAdapter,
    make_catalog,
    make_context,
    make_profile,
)

CARD_COLUMNS = [
    "course_code",
    "course_name",
    "credits",
    "category",
    "target_grade",
    "department_name",
    "instructor_name",
    "section",
    "capacity",
    "enrolled",
    "day",
    "start_time",
    "end_time",
]

ROW = {
    "Course.code": "0001-0001",
    "Course.name": "자료구조",
    "Course.credits": Decimal("3"),
    "Course.category": "전공(핵심)",
    "Course.target_grade": 2,
    "Department.name": "컴퓨터공학과",
    "Instructor.name": "홍길동",
    "CourseOffering.section": "01",
    "CourseOffering.capacity": 40,
    "CourseOffering.enrolled": 12,
    "Schedule.day": "월",
    "Schedule.start_time": "13:00",
    "Schedule.end_time": "14:15",
}

QUESTION = "월요일 오후에 하는 3학점 전공 과목 알려줘"


class RecordingExecutor:
    def __init__(self, rows: list[dict[str, Any]] | None = None, error: Exception | None = None) -> None:
        self.rows = [ROW] if rows is None else rows
        self.error = error
        self.queries: list[CompiledQuery] = []

    def execute(self, database_id: str, compiled: CompiledQuery):
        assert database_id == DATABASE_ID
        self.queries.append(compiled)
        if self.error is not None:
            raise self.error
        columns = list(compiled.expected_columns)
        return columns, [{column: row[column] for column in columns} for row in self.rows]


class FakeModel:
    def __init__(self, output: Any = None, error: Exception | None = None) -> None:
        self.output = output
        self.error = error
        self.calls: list[Any] = []

    def generate_ir(self, request: Any, ontology: Any, mapping: Any) -> Any:
        self.calls.append((request, ontology, mapping))
        if self.error is not None:
            raise self.error
        return self.output


def ir(select: list[str], filters: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
    return {"entity": "Course", "select": select, "filters": filters or [], "limit": 50, **extra}


GOOD_IR = ir(
    ["Course.name"],
    [
        {"field": "Course.credits", "operator": "equals", "value": 3},
        {"field": "Schedule.day", "operator": "equals", "value": "월"},
        {"field": "Schedule.start_time", "operator": "greater_than_or_equal", "value": "12:00"},
    ],
)


def make_client(model: Any, *, executor: Any = None, catalog=None) -> tuple[TestClient, Any]:
    executor = executor or RecordingExecutor()
    context = make_context(executor=executor, catalog=catalog)
    app = FastAPI()
    register_chat_query_routes(app, pg_contexts={DATABASE_ID: context}, model=model)
    return TestClient(app, raise_server_exceptions=False), executor


def post(client: TestClient, question: str = QUESTION, database_id: str = DATABASE_ID):
    return client.post(CHAT_QUERY_PATH, json={"database_id": database_id, "query": question})


def assert_no_leak(text: str) -> None:
    for secret in ("s3cret", VIEW, "credit_hours", "subject_name", "SELECT", "SPLIT_PART"):
        assert secret not in text


def test_success_returns_card_rows_and_understood_filters() -> None:
    model = FakeModel(GOOD_IR)
    client, executor = make_client(model)
    response = post(client)
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == CHAT_QUERY_VERSION
    assert body["status"] == "SUCCEEDED"
    assert body["columns"] == CARD_COLUMNS
    assert body["row_count"] == 1 and body["limit"] == 200
    row = body["rows"][0]
    assert row["course_name"] == "자료구조" and row["credits"] == 3 and row["start_time"] == "13:00"
    assert body["understood"]["filters"][0] == {
        "field": "Course.credits",
        "label": "학점",
        "operator": "equals",
        "value": 3,
    }
    assert len(model.calls) == 1
    request, _ontology, mapping = model.calls[0]
    assert request.question == QUESTION and mapping.database_id == DATABASE_ID
    # The projection and the row cap are server rules; the filters are the model's.
    [compiled] = executor.queries
    assert compiled.expected_columns[0] == "Course.code"
    assert compiled.parameters == [3, "월", "12:00", 200]
    assert_no_leak(response.text)


def test_time_values_are_normalized_before_binding_and_display() -> None:
    model = FakeModel(ir(["Course.name"], [
        {"field": "Schedule.start_time", "operator": "greater_than_or_equal", "value": "14:00:00"},
    ]))
    client, executor = make_client(model)
    body = post(client, "오후 2시 이후 수업").json()
    assert body["understood"]["filters"][0]["value"] == "14:00"
    assert executor.queries[0].parameters == ["14:00", 200]


def test_word_like_identifiers_only_match_whole_words() -> None:
    # "section" is a physical column; a value merely containing the letters
    # inside a longer word is not a reflection.
    model = FakeModel(ir(["Course.name"], [{"field": "Course.name", "operator": "contains", "value": "Intersectional"}]))
    client, executor = make_client(model)
    assert post(client).json()["status"] == "SUCCEEDED"
    assert len(executor.queries) == 1


def test_server_policies_fix_the_limit_and_relax_name_like_equals() -> None:
    model = FakeModel(ir(
        ["Course.name"],
        [
            {"field": "Department.name", "operator": "equals", "value": "수학과"},
            {"field": "Instructor.name", "operator": "equals", "value": "홍길동"},
            {"field": "Course.category", "operator": "equals", "value": "전공"},
            {"field": "Course.code", "operator": "equals", "value": "0001-0001"},
            {"field": "CourseOffering.section", "operator": "equals", "value": "01"},
            {"field": "Course.credits", "operator": "equals", "value": 3},
        ],
        limit=5,
    ))
    client, executor = make_client(model)
    body = post(client, "수학과 홍길동 전공 과목").json()
    assert body["limit"] == 200
    assert [item["operator"] for item in body["understood"]["filters"]] == [
        "contains", "contains", "contains", "equals", "equals", "equals",
    ]
    [compiled] = executor.queries
    assert compiled.parameters == ["%수학과%", "%홍길동%", "%전공%", "0001-0001", "01", 3, 200]
    assert compiled.sql.count(" LIKE %s") == 3


@pytest.mark.parametrize(
    ("raw", "bound"),
    [("월요일", "월"), ("월", "월"), ("Mon", "월"), ("friday", "금"), (" 토요일 ", "토")],
)
def test_weekday_values_are_normalized(raw: str, bound: str) -> None:
    model = FakeModel(ir(["Course.name"], [{"field": "Schedule.day", "operator": "equals", "value": raw}]))
    client, executor = make_client(model)
    body = post(client, "요일 수업").json()
    assert body["status"] == "SUCCEEDED"
    assert body["understood"]["filters"][0]["value"] == bound
    assert executor.queries[0].parameters == [bound, 200]


def test_weekday_contains_is_allowed_and_unknown_days_are_not_understood() -> None:
    model = FakeModel(ir(["Course.name"], [{"field": "Schedule.day", "operator": "contains", "value": "화요일"}]))
    client, executor = make_client(model)
    assert post(client, "화요일 수업").json()["status"] == "SUCCEEDED"
    assert executor.queries[0].parameters == ["%화%", 200]
    model = FakeModel(ir(["Course.name"], [{"field": "Schedule.day", "operator": "equals", "value": "주말"}]))
    client, executor = make_client(model)
    body = post(client, "주말 수업").json()
    assert body["status"] == "NOT_UNDERSTOOD" and body["failure_code"] == "INVALID_VALUE_TYPE"
    assert executor.queries == []


def test_unsupported_field_answers_without_execution() -> None:
    model = FakeModel(ir(["Course.name"], [{"field": "CourseOffering.year", "operator": "equals", "value": 2024}]))
    client, executor = make_client(model)
    body = post(client, "2024년에 열린 강의").json()
    assert body["status"] == "UNSUPPORTED"
    assert body["unsupported"] == [{"field": "CourseOffering.year", "label": "개설 연도"}]
    assert executor.queries == []
    select_only = FakeModel(ir(["Course.name", "Department.code"]))
    client, executor = make_client(select_only)
    assert post(client, "학과 코드 알려줘").json()["unsupported"][0]["label"] == "학과 코드"
    assert executor.queries == []


@pytest.mark.parametrize(
    ("output", "failure_code"),
    [
        ("SELECT * FROM v_course_info", "MODEL_INVALID_OUTPUT"),
        ({"entity": "Course"}, "MODEL_INVALID_OUTPUT"),
        (ir(["Course.name"], limit=0), "MODEL_INVALID_OUTPUT"),
        (ir(["Course.room"]), "UNKNOWN_FIELD"),
        (ir(["Course.name"], [{"field": "Course.nickname", "operator": "equals", "value": "x"}]), "UNKNOWN_FIELD"),
        # the whole question copied into an equals filter
        (ir(["Course.name"], [{"field": "Course.name", "operator": "equals", "value": QUESTION}]), "REFLECTED_INPUT"),
        # physical identifiers reflected into a value
        (ir(["Course.name"], [{"field": "Course.name", "operator": "equals", "value": "credit_hours"}]), "REFLECTED_INPUT"),
        (ir(["Course.name"], [{"field": "Course.name", "operator": "equals", "value": DATABASE_ID}]), "REFLECTED_INPUT"),
        # identifiers or the question embedded in a longer value
        (ir(["Course.name"], [{"field": "Course.name", "operator": "equals", "value": "credit_hours=3"}]), "REFLECTED_INPUT"),
        (ir(["Course.name"], [{"field": "Course.name", "operator": "contains", "value": f"SELECT * FROM {VIEW}"}]), "REFLECTED_INPUT"),
        (ir(["Course.name"], [{"field": "Course.name", "operator": "contains", "value": "Subject_Name"}]), "REFLECTED_INPUT"),
        (ir(["Course.name"], [{"field": "Course.name", "operator": "equals", "value": QUESTION + " 부탁"}]), "REFLECTED_INPUT"),
        # operator the ontology does not allow for the field
        (ir(["Course.name"], [{"field": "Course.credits", "operator": "contains", "value": "3"}]), "INVALID_OPERATOR"),
        (ir(["Course.name"], [{"field": "Course.credits", "operator": "equals", "value": "three"}]), "INVALID_VALUE_TYPE"),
        (ir(["Course.name"], entity="Student"), "UNKNOWN_ENTITY"),
    ],
)
def test_not_understood_outputs_never_execute(output: Any, failure_code: str) -> None:
    model = FakeModel(output)
    client, executor = make_client(model)
    response = post(client)
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "version": CHAT_QUERY_VERSION,
        "database_id": DATABASE_ID,
        "status": "NOT_UNDERSTOOD",
        "failure_code": failure_code,
    }
    assert executor.queries == []
    assert len(model.calls) == 1


@pytest.mark.parametrize("value", ["14:00:30", "오후"])
def test_time_values_minute_text_cannot_represent_never_execute(value: str) -> None:
    """Phase 8-25: asked with a time word, the closed time rules replace such a value instead."""
    output = ir(["Course.name"], [{"field": "Schedule.start_time", "operator": "greater_than_or_equal", "value": value}])
    client, executor = make_client(FakeModel(output))
    body = post(client, "3학점 전공 과목 알려줘").json()
    assert body["status"] == "NOT_UNDERSTOOD" and body["failure_code"] == "INVALID_VALUE_TYPE"
    assert executor.queries == []
    repaired = post(client, QUESTION).json()  # "오후" in the question: start >= 12:00
    assert repaired["status"] == "SUCCEEDED"
    assert repaired["understood"]["filters"][0]["value"] == "12:00"


@pytest.mark.parametrize(
    ("error", "failure_code"),
    [
        (RecordingProviderError(RecordedFailureCode.TIMEOUT), "TIMEOUT"),
        (RecordingProviderError(RecordedFailureCode.EMPTY_RESPONSE), "EMPTY_RESPONSE"),
        (RuntimeError("boom " + SECRET_DSN), "PROVIDER_ERROR"),
    ],
)
def test_model_failures_are_fixed_503(error: Exception, failure_code: str) -> None:
    client, executor = make_client(FakeModel(error=error))
    response = post(client)
    assert response.status_code == 503
    assert response.json() == {"error": {"code": "CHAT_MODEL_UNAVAILABLE", "failure_code": failure_code}}
    assert executor.queries == []
    assert_no_leak(response.text)


def test_missing_model_is_503_without_calls() -> None:
    client, executor = make_client(None)
    response = post(client)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CHAT_MODEL_UNAVAILABLE"
    assert executor.queries == []


def test_execution_failure_is_fixed_500_without_detail() -> None:
    executor = RecordingExecutor(error=RuntimeError("driver said " + SECRET_DSN + " SELECT"))
    client, _ = make_client(FakeModel(GOOD_IR), executor=executor)
    response = post(client)
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "CHAT_EXECUTION_FAILED", "failure_code": "EXECUTION_ERROR"}
    }
    assert_no_leak(response.text)


def test_unexpected_result_value_type_fails_closed() -> None:
    executor = RecordingExecutor(rows=[{**ROW, "CourseOffering.capacity": object()}])
    client, _ = make_client(FakeModel(GOOD_IR), executor=executor)
    response = post(client)
    assert response.status_code == 500
    assert response.json()["error"]["failure_code"] == "RESULT_SHAPE_MISMATCH"


def test_overlong_text_value_fails_closed() -> None:
    executor = RecordingExecutor(rows=[{**ROW, "Course.name": "가" * 1001}])
    client, _ = make_client(FakeModel(GOOD_IR), executor=executor)
    response = post(client)
    assert response.status_code == 500
    assert response.json()["error"]["failure_code"] == "RESULT_SHAPE_MISMATCH"


def test_unknown_and_drifted_databases_are_404() -> None:
    model = FakeModel(GOOD_IR)
    client, _ = make_client(model)
    assert post(client, database_id="other-db").status_code == 404
    assert post(client, database_id=SYNTHETIC_PROTOTYPE_DATABASE_ID).status_code == 404
    assert model.calls == []

    context = make_context(executor=RecordingExecutor())
    adapter = context.adapter()
    assert isinstance(adapter, FakeAdapter)
    adapter.catalog = make_catalog().model_copy(update={"schema_fingerprint": "cd" * 32})
    app = FastAPI()
    register_chat_query_routes(app, pg_contexts={DATABASE_ID: context}, model=model)
    response = post(TestClient(app, raise_server_exceptions=False))
    assert response.status_code == 404
    assert model.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {"database_id": DATABASE_ID},
        {"database_id": DATABASE_ID, "query": "강의", "extra": 1},
        {"database_id": DATABASE_ID, "query": "   "},
        {"database_id": DATABASE_ID, "query": "가" * 501},
        {"database_id": DATABASE_ID, "query": "강의\x00"},
        {"database_id": DATABASE_ID, "query": 3},
        {"database_id": "../etc", "query": "강의"},
    ],
)
def test_invalid_requests_are_400_without_model_calls(body: dict[str, Any]) -> None:
    model = FakeModel(GOOD_IR)
    client, _ = make_client(model)
    response = client.post(CHAT_QUERY_PATH, json=body)
    assert response.status_code == 400
    assert response.json() == {"error": {"code": "CHAT_INVALID_REQUEST"}}
    assert model.calls == []


def test_non_json_and_duplicate_key_bodies_are_400() -> None:
    model = FakeModel(GOOD_IR)
    client, _ = make_client(model)
    headers = {"content-type": "application/json"}
    assert client.post(CHAT_QUERY_PATH, content=b"not json", headers=headers).status_code == 400
    duplicate = ('{"database_id":"%s","query":"a","query":"b"}' % DATABASE_ID).encode()
    assert client.post(CHAT_QUERY_PATH, content=duplicate, headers=headers).status_code == 400
    assert client.post(CHAT_QUERY_PATH, content=b"x" * (70 * 1024), headers=headers).status_code == 400
    assert model.calls == []


def test_prototype_registers_route_and_answers_503_without_ollama() -> None:
    app = create_synthetic_clarification_prototype(
        postgres_profiles=[make_profile()],
        postgres_adapters={DATABASE_ID: FakeAdapter()},
        postgres_executors={DATABASE_ID: RecordingExecutor()},
    )
    client = TestClient(app, raise_server_exceptions=False)
    response = post(client)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CHAT_MODEL_UNAVAILABLE"
    # Synthetic SQLite databases keep the clarification flow, not this route.
    assert post(client, database_id=SYNTHETIC_PROTOTYPE_DATABASE_ID).status_code == 404


def test_compile_failure_is_execution_error_not_not_understood(monkeypatch: pytest.MonkeyPatch) -> None:
    model = FakeModel(GOOD_IR)
    executor = RecordingExecutor()
    context = make_context(executor=executor)
    compiler = context.compiler()

    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise HarnessFailure(HarnessErrorCode.INVALID_JOIN_PLAN, "detail " + VIEW)

    monkeypatch.setattr(compiler, "compile", fail)
    app = FastAPI()
    register_chat_query_routes(app, pg_contexts={DATABASE_ID: context}, model=model)
    response = post(TestClient(app, raise_server_exceptions=False))
    assert response.status_code == 500
    assert response.json()["error"]["failure_code"] == "INVALID_JOIN_PLAN"
    assert_no_leak(response.text)
    assert executor.queries == []
