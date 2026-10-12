"""Phase 9-05: raw-text time columns at publish, and missing fields the model only selected."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.adaptive import test_legacy_view_partial_mapping as lv
from tests.adaptive import test_postgres_registration as base
from tests.adaptive.test_unknown_conditions import ask
from tests.adaptive.test_chat_query_endpoint import ir

# --- 1. publish: a time field left as raw text must hold "HH:MM" -----------------------------


def publish_with(tmp_path, monkeypatch, start: list[str], end: list[str]) -> tuple[Any, Any]:
    columns = {**lv.VIEW_COLUMNS, "start_time": "character varying", "end_time": "character varying"}
    monkeypatch.setattr(base, "make_catalog", lambda: lv.make_catalog(columns=columns))
    monkeypatch.setattr(base, "VALUES", {**base.VALUES, "start_time": start, "end_time": end})
    fixture = base.client_repo.__wrapped__(tmp_path)
    client, repo, _app, _executor = next(fixture)
    assert base._register(client).status_code == 200
    base._review_all(client)
    response = base._review(client, {"action": "PUBLISH", "database_id": base.DB_ID, "published_by": "admin"})
    return response, repo


def transforms(repo: Any) -> dict[str, str]:
    profile = repo / ".schema-adaptive" / "postgres-profiles" / base.DB_ID / "profile.json"
    return json.loads(profile.read_text(encoding="utf-8"))["field_transforms"]


def test_a_time_range_text_column_blocks_publish(tmp_path, monkeypatch) -> None:
    """9-04 rehearsal: "09:00-10:15" compared as text gave wrong rows without a warning."""
    response, _repo = publish_with(tmp_path, monkeypatch, ["09:00-10:15", "13:30-14:45"], ["09:00-10:15"])
    assert response.status_code == 422
    assert response.json()["error"]["fields"] == ["Schedule.end_time", "Schedule.start_time"]


@pytest.mark.parametrize(
    "values",
    [["9:00"], ["09:00:00"], ["오전 9시"], ["09:00", "미정"]],  # unpadded, seconds, words, one stray value
)
def test_any_value_text_cannot_order_blocks_publish(tmp_path, monkeypatch, values: list[str]) -> None:
    response, _repo = publish_with(tmp_path, monkeypatch, values, ["10:00"])
    assert response.status_code == 422 and response.json()["error"]["fields"] == ["Schedule.start_time"]


def test_plain_hhmm_text_publishes_as_is(tmp_path, monkeypatch) -> None:
    response, repo = publish_with(tmp_path, monkeypatch, ["09:00", "13:30"], ["10:15", "14:45"])
    assert response.status_code == 200, response.text[:300]
    found = transforms(repo)
    assert "Schedule.start_time" not in found and "Schedule.end_time" not in found


def test_four_digit_text_still_gets_its_transform(tmp_path, monkeypatch) -> None:
    response, repo = publish_with(tmp_path, monkeypatch, ["0900", "1330"], ["1015", "1445"])
    assert response.status_code == 200, response.text[:300]
    assert transforms(repo)["Schedule.start_time"] == "HHMM_TEXT_TO_HHMM"


# --- 2. chat: a missing field the model only selected ----------------------------------------


CREDITS = {"field": "Course.credits", "operator": "equals", "value": 3}


def test_an_unasked_missing_select_field_is_dropped_not_declined() -> None:
    """9-04 rehearsal: the model selected only 분반 and a DB without sections declined everything."""
    body, executor, _model = ask(ir(["CourseOffering.semester"], [CREDITS]), "3학점 과목 보여줘")
    assert body["status"] == "SUCCEEDED", body
    assert executor.queries
    # ADR-101: model-generated retired fields are unknown in the v3 runtime.
    body, executor, _model = ask(ir(["Course.name", "CourseOffering.year"], [CREDITS]), "3학점 과목")
    assert body["status"] == "NOT_UNDERSTOOD" and body["failure_code"] == "UNKNOWN_FIELD"
    assert executor.queries == []


@pytest.mark.parametrize(
    ("output", "question"),
    [
        (ir(["CourseOffering.semester"], [CREDITS]), "3학점 과목의 학기 알려줘"),  # the question names it
        (ir(["Course.name"], [{"field": "CourseOffering.semester", "operator": "equals", "value": "2"}]), "2학기 과목"),
        (ir(["Course.name", "Department.code"]), "학과 코드 알려줘"),
    ],
)
def test_a_named_or_filtered_missing_field_is_still_declined(output: Any, question: str) -> None:
    body, executor, _model = ask(output, question)
    assert body["status"] == "UNSUPPORTED" and executor.queries == []


def test_an_unknown_field_is_still_not_understood() -> None:
    body, _executor, _model = ask(ir(["Course.nickname"], [CREDITS]), "3학점 과목")
    assert body["status"] == "NOT_UNDERSTOOD"


# --- Luna 9-05 review ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "values",
    [
        ["09:00", ""],  # the value read leaves out empty text, which sorts before every time
        [],  # nothing readable: fail closed
    ],
)
def test_empty_or_unreadable_time_text_blocks_publish(tmp_path, monkeypatch, values: list[str]) -> None:
    response, _repo = publish_with(tmp_path, monkeypatch, values, ["10:00"])
    assert response.status_code == 422 and response.json()["error"]["fields"] == ["Schedule.start_time"]


@pytest.mark.parametrize("question", ["지난해 개설된 3학점 과목", "2025년 3학점 과목 보여줘", "올해 3학점 과목"])
def test_a_year_asked_without_its_label_is_still_declined(question: str) -> None:
    body, executor, _model = ask(ir(["CourseOffering.year"], [CREDITS]), question)
    assert body["status"] == "UNSUPPORTED" and executor.queries == []
