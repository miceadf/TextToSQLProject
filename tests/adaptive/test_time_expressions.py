"""Phase 8-25: closed time-expression rules, the model's hint and the repair."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.adaptive.api import chat_query
from backend.adaptive.api import legacy_view_profile as lvp
from backend.adaptive.api.chat_query import CHAT_QUERY_PATH, register_chat_query_routes
from backend.adaptive.api.time_hints import (
    END_TIME,
    START_TIME,
    TimeCondition,
    apply_time_conditions,
    find_time_conditions,
)
from backend.adaptive.harness import ollama as harness_ollama
from backend.adaptive.harness.contracts import FieldMapping, MappingProfile, MappingStatus
from tests.adaptive.test_chat_query_endpoint import RecordingExecutor, ir
from tests.adaptive.test_legacy_view_partial_mapping import DATABASE_ID, make_context, make_profile

GTE, LT, LTE, EQ = "greater_than_or_equal", "less_than", "less_than_or_equal", "equals"


def found(question: str) -> list[tuple[str, str, str]]:
    return [(item.field, item.operator, item.value) for item in find_time_conditions(question)]


# --- the rules --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("금요일 오전 수업 보여줘", [(START_TIME, LT, "12:00")]),
        ("오후 수업", [(START_TIME, GTE, "12:00")]),
        ("오전에 끝나는 수업", [(END_TIME, LT, "12:00")]),
        ("오후 3시 이후에 시작하는 수업 보여줘", [(START_TIME, GTE, "15:00")]),
        ("15시 이후 수업", [(START_TIME, GTE, "15:00")]),
        ("3시 반 이후", [(START_TIME, GTE, "15:30")]),
        ("9시 30분부터 하는 수업", [(START_TIME, GTE, "09:30")]),
        ("10:30 이후 시작", [(START_TIME, GTE, "10:30")]),
        ("09:00에 시작하는 수업", [(START_TIME, EQ, "09:00")]),
        ("오후 4시 전에 끝나는 수업", [(END_TIME, LT, "16:00")]),
        ("6시까지 하는 수업", [(END_TIME, LTE, "18:00")]),
        ("9시에 시작하는 수업", [(START_TIME, EQ, "09:00")]),
        ("월요일 오전 9시 수업", [(START_TIME, EQ, "09:00")]),
        ("시작 시간이 3시 이후인 수업", [(START_TIME, GTE, "15:00")]),
        ("끝나는 시간이 5시 이전", [(END_TIME, LT, "17:00")]),
        ("9시부터 12시까지", [(START_TIME, GTE, "09:00"), (END_TIME, LTE, "12:00")]),
        ("9시에 시작해서 12시 전에 끝나는 수업", [(START_TIME, EQ, "09:00"), (END_TIME, LT, "12:00")]),
        ("저녁 7시 이후 수업", [(START_TIME, GTE, "19:00")]),
        ("8시 수업", [(START_TIME, EQ, "08:00")]),
        ("오후 12시 이후 시작", [(START_TIME, GTE, "12:00")]),
        ("오전에 시작해서 오후 3시 전에 끝나는 수업", [(START_TIME, LT, "12:00"), (END_TIME, LT, "15:00")]),
    ],
)
def test_time_words_are_read_by_closed_rules(question: str, expected: list[tuple[str, str, str]]) -> None:
    assert found(question) == expected


@pytest.mark.parametrize(
    "question",
    [
        "3시간 수업",  # a duration
        "2학기 3학점 과목",
        "컴융 3학년 전공",
        "3시 전 수업",  # start or end: left to the model
        "3시 넘어서 시작",  # an unknown direction
        "3시~5시 수업",  # a range the rules do not read
        "15:00-16:30 수업",
        "월요일 3시 전공 수업",  # "전공" is not "전"
        "오전 12시 수업",
        "25시 이후",
        "3시에 하는 수업",
        "오전 수업 중 9시 이후 시작하고 5시 전에 끝나는 것",  # three conditions
        # Luna 8-25 review: negated or contradictory mentions, midnight
        "오전 수업 말고 오후 수업만 보여줘",
        "오후 3시 이후 아닌 수업",
        "오전 수업 오후 수업",
        "9시 수업 10시 수업",
        "밤 12시 이후 시작하는 수업",
        "저녁 12시 수업",
    ],
)
def test_uncertain_time_words_give_no_condition(question: str) -> None:
    assert found(question) == []


def test_identical_conditions_count_once() -> None:
    assert found("오전 오전 수업") == [(START_TIME, LT, "12:00")]


# --- the repair -------------------------------------------------------------------------------


MORNING = TimeCondition("오전", START_TIME, LT, "12:00")
AFTER_THREE = TimeCondition("3시 이후", START_TIME, GTE, "15:00")
BEFORE_NOON_END = TimeCondition("12시 전", END_TIME, LT, "12:00")
DAY = {"field": "Schedule.day", "operator": "equals", "value": "금"}


def test_a_dropped_time_condition_is_added() -> None:
    output = ir(["Course.name"], [DAY])
    assert apply_time_conditions(output, [MORNING])["filters"] == [DAY, MORNING.filter()]
    assert output["filters"] == [DAY]  # the input is not changed in place


def test_a_wrong_field_or_strict_operator_is_aligned() -> None:
    for wrong in (
        {"field": END_TIME, "operator": "greater_than", "value": "15:00"},
        {"field": START_TIME, "operator": "greater_than", "value": "1500"},
        {"field": START_TIME, "operator": "greater_than_or_equal", "value": "03:00"},
    ):
        output = apply_time_conditions(ir(["Course.name"], [DAY, wrong]), [AFTER_THREE])
        assert output["filters"] == [DAY, AFTER_THREE.filter()]


def test_two_conditions_pair_by_value_then_order() -> None:
    model = [
        {"field": START_TIME, "operator": "less_than", "value": "12:00"},
        {"field": START_TIME, "operator": "equals", "value": "09:00"},
    ]
    nine = TimeCondition("9시", START_TIME, EQ, "09:00")
    output = apply_time_conditions(ir(["Course.name"], model), [nine, BEFORE_NOON_END])
    assert output["filters"] == [BEFORE_NOON_END.filter(), nine.filter()]


@pytest.mark.parametrize(
    "output",
    [
        ir(["Course.name"], [{"field": START_TIME, "operator": "less_than", "value": "12:00"}] * 2),
        "not a dict",
        ir(["Course.name"], "not a list"),  # type: ignore[arg-type]
    ],
)
def test_unexpected_shapes_are_left_alone(output: Any) -> None:
    assert apply_time_conditions(output, [MORNING]) == output


def test_an_output_without_filters_gets_the_condition() -> None:
    output = {"entity": "Course", "select": ["Course.name"], "limit": 50}  # filters default to []
    assert apply_time_conditions(output, [MORNING])["filters"] == [MORNING.filter()]


def test_no_condition_leaves_the_output_alone() -> None:
    output = ir(["Course.name"], [])
    assert apply_time_conditions(output, []) is output


# --- the prompt -------------------------------------------------------------------------------


def _mapping() -> MappingProfile:
    return MappingProfile(
        database_id="db", version=1, status=MappingStatus.PUBLISHED,
        fields={name: FieldMapping(table="t", column=name.split(".")[1]) for name in ("Course.name", START_TIME)},
    )


def test_a_time_hint_carries_its_operator_and_plain_hints_keep_the_old_line() -> None:
    ontology, _labels = chat_query._runtime_ontology()
    base = harness_ollama._prompt("질문", ontology, _mapping(), "query-ir-v1")
    head, _, question = base.partition("\nQuestion: ")
    plain = {"text": "a", "field": "Course.name", "value": "b"}
    assert harness_ollama._prompt("질문", ontology, _mapping(), "query-ir-v1", [plain]) == (
        head + "\n" + harness_ollama.VALUE_HINTS_LABEL + json.dumps([plain], ensure_ascii=False)
        + "\nQuestion: " + question
    )
    timed = [MORNING.payload(), plain]
    assert harness_ollama._prompt("질문", ontology, _mapping(), "query-ir-v1", timed) == (
        head + "\n" + harness_ollama.CONDITION_HINTS_LABEL + json.dumps(timed, ensure_ascii=False)
        + "\nQuestion: " + question
    )


@pytest.mark.parametrize(
    "hint",
    [
        {**MORNING.payload(), "operator": "contains"},
        {**MORNING.payload(), "operator": "drop table"},
        {**MORNING.payload(), "extra": "x"},
        {**MORNING.payload(), "field": END_TIME},  # not an available field
        {**MORNING.payload(), "operator": ""},
        # Luna 8-25 review: an operator the hinted field does not allow
        {"text": "a", "field": "Course.name", "operator": "less_than", "value": "b"},
    ],
)
def test_invalid_time_hints_are_refused(hint: dict[str, str]) -> None:
    ontology, _labels = chat_query._runtime_ontology()
    with pytest.raises(ValueError):
        harness_ollama._prompt("질문", ontology, _mapping(), "query-ir-v1", [hint])


# --- the chat path ----------------------------------------------------------------------------


class Model:
    def __init__(self, output: Any) -> None:
        self.output = output
        self.calls: list[dict[str, Any]] = []

    def generate_ir(self, request: Any, ontology: Any, mapping: Any, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.output


def ask(output: Any, question: str, *, profile: Any = None) -> tuple[dict[str, Any], RecordingExecutor, Model]:
    executor = RecordingExecutor()
    model = Model(output)
    app = FastAPI()
    register_chat_query_routes(app, pg_contexts={DATABASE_ID: make_context(profile, executor=executor)}, model=model)
    with TestClient(app, raise_server_exceptions=False) as client:
        body = client.post(CHAT_QUERY_PATH, json={"database_id": DATABASE_ID, "query": question}).json()
    return body, executor, model


def understood(body: dict[str, Any]) -> list[tuple[str, str, Any]]:
    return [(item["field"], item["operator"], item["value"]) for item in body["understood"]["filters"]]


def test_the_model_gets_the_time_hint_and_a_dropped_morning_is_added() -> None:
    body, executor, model = ask(ir(["Course.name"], [{**DAY}]), "금요일 오전 수업 보여줘")
    assert model.calls == [{"value_hints": [MORNING.payload() | {"text": "오전"}]}]
    assert body["status"] == "SUCCEEDED"
    assert understood(body) == [("Schedule.day", "equals", "금"), (START_TIME, LT, "12:00")]
    [compiled] = executor.queries
    assert "12:00" in compiled.parameters


def test_an_end_time_reading_of_a_start_question_is_aligned() -> None:
    wrong = ir(["Course.name"], [{"field": END_TIME, "operator": "greater_than", "value": "15:00"}])
    body, _executor, _model = ask(wrong, "오후 3시 이후에 시작하는 수업 보여줘")
    assert understood(body) == [(START_TIME, GTE, "15:00")]


def test_questions_without_time_words_call_the_model_as_before() -> None:
    body, _executor, model = ask(ir(["Course.name"], [{"field": "Course.credits", "operator": "equals", "value": 3}]), "3학점 과목")
    assert model.calls == [{}] and body["status"] == "SUCCEEDED"


def _without_start_time() -> Any:
    fields = {semantic: column for semantic, (column, _) in lvp.VIEW_FIELDS.items() if semantic != START_TIME}
    transforms = {name: value for name, value in lvp.FIELD_TRANSFORMS.items() if name != START_TIME}
    return make_profile(fields=fields, transforms=transforms, unsupported=(*lvp.UNSUPPORTED_FIELDS, START_TIME))


@pytest.mark.parametrize(
    "output",
    [
        ir(["Course.name"], [DAY]),  # the model dropped the time
        {"select": "nonsense"},  # an invalid output
    ],
)
def test_a_time_the_database_lacks_is_unsupported_whatever_the_model_says(output: Any) -> None:
    body, executor, model = ask(output, "금요일 오전 수업 보여줘", profile=_without_start_time())
    assert body["status"] == "UNSUPPORTED"
    assert body["unsupported"] == [{"field": START_TIME, "label": "시작 시간"}]
    assert executor.queries == [] and model.calls == [{}]  # no hint for a field the database lacks


def test_a_lacking_time_and_an_outside_concept_come_together() -> None:
    body, _executor, _model = ask(ir(["Course.name"], [DAY]), "금요일 오전 강의실 수업", profile=_without_start_time())
    assert body["unsupported"] == [
        {"field": START_TIME, "label": "시작 시간"},
        {"concept": "classroom", "label": "강의실"},
    ]


def test_a_positional_only_hint_parameter_gets_no_hint() -> None:
    """Luna 8-25 review: such a model cannot take ``value_hints=`` by keyword."""

    class PositionalModel:
        def __init__(self) -> None:
            self.calls = 0

        def generate_ir(self, request: Any, ontology: Any, mapping: Any, value_hints: Any = None, /) -> Any:
            self.calls += 1
            return ir(["Course.name"], [DAY])

    model = PositionalModel()
    app = FastAPI()
    register_chat_query_routes(app, pg_contexts={DATABASE_ID: make_context(executor=RecordingExecutor())}, model=model)
    with TestClient(app, raise_server_exceptions=False) as client:
        body = client.post(CHAT_QUERY_PATH, json={"database_id": DATABASE_ID, "query": "금요일 오전 수업"}).json()
    assert body["status"] == "SUCCEEDED" and model.calls == 1
    assert understood(body) == [("Schedule.day", "equals", "금"), (START_TIME, LT, "12:00")]  # the repair still applies


def test_failing_time_rules_leave_the_chat_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_question: str) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(chat_query, "find_time_conditions", broken)
    body, _executor, model = ask(ir(["Course.name"], [DAY]), "금요일 오전 수업 보여줘")
    assert body["status"] == "SUCCEEDED" and model.calls == [{}]
    assert understood(body) == [("Schedule.day", "equals", "금")]
