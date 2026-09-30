"""Phase 8-07 (reduced): value dictionaries edited for PostgreSQL registrations."""

from __future__ import annotations

import inspect
import json
from typing import Any

import pytest

from backend.adaptive.registration import postgres_dictionaries as dictionaries
from tests.adaptive import test_postgres_registration as base
from tests.adaptive.test_postgres_registration import DB_ID, _register, _review, _review_all, client_repo  # noqa: F401

DICTIONARIES = "/api/adaptive/v1/postgres/dictionaries"


@pytest.fixture(autouse=True)
def stored_values(monkeypatch: pytest.MonkeyPatch):
    values = {
        "credit_hours": ["3/3/0", "2/2/0"],
        "day_of_week": ["월", "화"],
        "category": ["전공(핵심)", "교양", "일반선택"],
        "dept_name": ["공과대학 컴퓨터융합학부", "대학 학과공통"],
        "subject_name": ["전기회로", "자료구조"],
        "professor": ["홍길동"],
    }
    monkeypatch.setattr(base, "VALUES", values)
    return values


def _ready(client: Any) -> None:
    assert _register(client).status_code == 200
    _review_all(client)


def _set(client: Any, field: str, stored: str, aliases: list[str], label: str | None = None) -> Any:
    return client.post(DICTIONARIES, json={
        "action": "SET", "database_id": DB_ID, "field": field, "stored": stored,
        "label": label or stored, "aliases": aliases, "reviewer": "admin",
    })


# --- rules and checks -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("전공(핵심)", ["전핵"]),
        ("일반선택", ["일선"]),
        ("교양(소양필수)", ["교소필"]),
        ("교직", []),  # one unit
        ("공과대학 컴퓨터융합학부", []),  # words of other lengths: left to the model or the administrator
        ("ABCD", ["AC"]),
    ],
)
def test_rule_candidates(stored: str, expected: list[str]) -> None:
    assert dictionaries.rule_candidates(stored) == expected


def test_alias_checks_block_collisions_and_warn_on_other_text() -> None:
    values = {"Course.category": ["전공(핵심)", "교양"], "Department.name": ["공과대학 전자공학과"]}
    kwargs = {"values_by_field": values, "entries": {}, "other_texts": ["전기회로"]}
    assert dictionaries.check_alias("교양", "전공(핵심)", field="Course.category", **kwargs)[0] is not None
    assert dictionaries.check_alias("전자공학과", "교양", field="Course.category", **kwargs)[0] is not None  # a piece
    assert dictionaries.check_alias("전핵", "전공(핵심)", field="Course.category", **kwargs) == (None, None)
    blocked, warning = dictionaries.check_alias("전기", "전공(핵심)", field="Course.category", **kwargs)
    assert blocked is None and "전기회로" in warning
    assert dictionaries.check_alias("x", "교양", field="Course.category", **kwargs)[0] is not None


def test_ai_candidates_are_schema_limited_and_skip_failures() -> None:
    calls: list[dict[str, Any]] = []

    def model(prompt: str, schema: dict[str, Any]) -> Any:
        calls.append(schema)
        if len(calls) == 2:
            raise RuntimeError("down")
        return {key: ["컴융", "  ", key] for key in schema["required"]}

    values = [f"값{i}" for i in range(dictionaries.AI_BATCH + 1)]
    out = dictionaries.ai_candidates(model, "학과명", values)
    assert len(calls) == 2 and set(calls[0]["required"]) == set(values[: dictionaries.AI_BATCH])
    assert out == {value: ["컴융"] for value in values[: dictionaries.AI_BATCH]}  # the failed batch adds nothing


def test_the_module_holds_no_database_values() -> None:
    source = inspect.getsource(dictionaries)
    for text in ("컴융", "학과공통", "전핵", "교소"):
        assert text not in source


# --- API -------------------------------------------------------------------------------------


def test_editable_fields_and_values(client_repo) -> None:  # noqa: F811
    client, _repo, _app, _executor = client_repo
    _ready(client)
    body = client.get(DICTIONARIES, params={"database_id": DB_ID}).json()
    fields = {item["field"]: item for item in body["fields"]}
    assert {"Course.category", "Department.name", "Schedule.day"} <= set(fields)
    assert "Course.code" not in fields and "Course.credits" not in fields
    assert fields["Course.category"]["values"] == sorted(["전공(핵심)", "교양", "일반선택"])
    assert "s3cret" not in json.dumps(body)


def test_set_suggest_remove_and_conflicts(client_repo) -> None:  # noqa: F811
    client, repo, _app, _executor = client_repo
    _ready(client)
    ok = _set(client, "Department.name", "공과대학 컴퓨터융합학부", ["컴융", "컴융부"])
    assert ok.status_code == 200 and ok.json()["warnings"] == []
    entry = next(f for f in ok.json()["fields"] if f["field"] == "Department.name")["entries"][0]
    assert entry == {"stored": "공과대학 컴퓨터융합학부", "label": "공과대학 컴퓨터융합학부", "aliases": ["컴융", "컴융부"]}
    warned = _set(client, "Course.category", "전공(핵심)", ["전기"])
    assert warned.status_code == 200 and warned.json()["warnings"][0]["alias"] == "전기"
    # another stored value, an existing alias, or a value that is not stored
    assert _set(client, "Course.category", "교양", ["일반선택"]).status_code == 409
    assert _set(client, "Course.category", "교양", ["컴융"]).status_code == 409
    assert _set(client, "Course.category", "없는값", ["없는약어"]).status_code == 400
    assert _set(client, "Course.code", "x", []).status_code == 400  # identity field

    suggested = client.post(DICTIONARIES, json={
        "action": "SUGGEST", "database_id": DB_ID, "field": "Course.category", "ai": False,
    }).json()
    by_stored = {(c["stored"], c["alias"]): c for c in suggested["candidates"]}
    assert by_stored[("전공(핵심)", "전핵")]["source"] == "RULE" and by_stored[("전공(핵심)", "전핵")]["blocked"] is None
    assert ("일반선택", "일선") in by_stored and suggested["ai_used"] is False

    removed = client.post(DICTIONARIES, json={
        "action": "REMOVE", "database_id": DB_ID, "field": "Course.category", "stored": "전공(핵심)", "reviewer": "admin",
    })
    assert removed.status_code == 200
    assert not next(f for f in removed.json()["fields"] if f["field"] == "Course.category")["entries"]
    stored_file = repo / ".schema-adaptive" / "postgres-registrations" / DB_ID / "dictionaries.json"
    assert "컴융" in stored_file.read_text(encoding="utf-8")


def test_a_too_short_alias_is_not_reported_as_a_collision(client_repo) -> None:  # noqa: F811
    client, _repo, _app, _executor = client_repo
    _ready(client)
    short = _set(client, "Schedule.day", "월", ["x"])
    assert short.status_code == 400
    assert short.json() == {"error": {"code": "POSTGRES_REGISTRATION_ALIAS_LENGTH"}}
    assert _set(client, "Course.category", "교양", ["일반선택"]).json() == {
        "error": {"code": "POSTGRES_REGISTRATION_ALIAS_CONFLICT"}
    }


def test_publish_applies_dictionaries_instead_of_transforms(client_repo) -> None:  # noqa: F811
    client, repo, app, _executor = client_repo
    _ready(client)
    assert _set(client, "Department.name", "공과대학 컴퓨터융합학부", ["컴융"]).status_code == 200
    assert _set(client, "Schedule.day", "월", ["월욜"], label="월").status_code == 200
    published = _review(client, {"action": "PUBLISH", "database_id": DB_ID, "published_by": "admin"})
    assert published.status_code == 200, published.text[:300]
    profile = json.loads((repo / ".schema-adaptive" / "postgres-profiles" / DB_ID / "profile.json").read_text(encoding="utf-8"))
    assert set(profile["value_dictionaries"]) == {"Department.name", "Schedule.day"}
    assert "Schedule.day" not in profile["field_transforms"]  # a dictionary field gets no transform
    assert profile["field_transforms"]["Course.credits"] == "SLASH_FIRST_NUMERIC"
    context = app.state.synthetic_postgres_contexts[DB_ID]
    assert context.value_dictionaries()["Department.name"].resolve("컴융") == "공과대학 컴퓨터융합학부"


def test_publish_stops_when_a_stored_value_disappeared(client_repo, stored_values) -> None:  # noqa: F811
    client, repo, _app, _executor = client_repo
    _ready(client)
    assert _set(client, "Course.category", "교양", ["교"]).status_code == 400  # too short is blocked (8-17: ALIAS_LENGTH)
    assert _set(client, "Course.category", "교양", ["교양과목"]).status_code == 200
    stored_values["category"] = ["전공(핵심)", "일반선택"]
    blocked = _review(client, {"action": "PUBLISH", "database_id": DB_ID, "published_by": "admin"})
    assert blocked.status_code == 422 and blocked.json()["error"]["fields"] == ["Course.category"]
    assert not (repo / ".schema-adaptive" / "postgres-profiles" / DB_ID).exists()


@pytest.mark.parametrize(
    "body",
    [
        {"action": "SET", "database_id": DB_ID, "field": "Course.category", "stored": "교양", "label": "교양", "aliases": "교", "reviewer": "a"},
        {"action": "SET", "database_id": DB_ID, "field": "Course.category", "stored": "교양", "label": "교양", "aliases": [], "reviewer": "a", "x": 1},
        {"action": "DROP", "database_id": DB_ID, "field": "Course.category"},
        {"action": "SUGGEST", "database_id": DB_ID, "field": "Course.category", "ai": "yes"},
        {"action": "SUGGEST", "database_id": "not-registered", "field": "Course.category", "ai": False},
    ],
)
def test_invalid_dictionary_requests(client_repo, body: dict[str, Any]) -> None:  # noqa: F811
    client, _repo, _app, _executor = client_repo
    _ready(client)
    assert client.post(DICTIONARIES, json=body).status_code in (400, 404)


# --- review fixes -------------------------------------------------------------------------


def test_big_columns_are_checked_with_exact_queries(client_repo, stored_values, monkeypatch) -> None:  # noqa: F811
    client, repo, _app, _executor = client_repo
    stored_values["dept_name"] = [f"대학{i:03d} 학과" for i in range(dictionaries.MAX_LISTED_VALUES + 1)]
    _ready(client)
    fields = {f["field"]: f for f in client.get(DICTIONARIES, params={"database_id": DB_ID}).json()["fields"]}
    assert fields["Department.name"]["values"] is None  # too many to list
    assert _set(client, "Department.name", "없는 학과", ["없과"]).status_code == 400  # not stored
    assert _set(client, "Course.category", "교양", ["대학001 학과"]).status_code == 409  # a stored value elsewhere
    assert _set(client, "Department.name", "대학001 학과", ["대일"]).status_code == 200
    stored_values["dept_name"] = stored_values["dept_name"][2:]  # the stored value disappears
    blocked = _review(client, {"action": "PUBLISH", "database_id": DB_ID, "published_by": "admin"})
    assert blocked.status_code == 422 and blocked.json()["error"]["fields"] == ["Department.name"]


def test_entries_of_fields_that_left_the_editable_set_can_be_removed(client_repo) -> None:  # noqa: F811
    client, _repo, _app, _executor = client_repo
    _ready(client)
    assert _set(client, "Course.category", "교양", ["교양과목"]).status_code == 200
    _review(client, {"action": "IGNORE", "database_id": DB_ID, "semantic_field": "Course.category", "reviewer": "admin", "reason": "없음"})
    fields = {f["field"]: f for f in client.get(DICTIONARIES, params={"database_id": DB_ID}).json()["fields"]}
    assert fields["Course.category"]["values"] is None and fields["Course.category"]["entries"]
    assert _review(client, {"action": "PUBLISH", "database_id": DB_ID, "published_by": "admin"}).status_code == 422
    removed = client.post(DICTIONARIES, json={
        "action": "REMOVE", "database_id": DB_ID, "field": "Course.category", "stored": "교양", "reviewer": "admin",
    })
    assert removed.status_code == 200
    assert _review(client, {"action": "PUBLISH", "database_id": DB_ID, "published_by": "admin"}).status_code == 200


@pytest.mark.parametrize("reviewer", ["", " ", "r" * 65])
def test_reviewer_names_are_validated(client_repo, reviewer: str) -> None:  # noqa: F811
    client, _repo, _app, _executor = client_repo
    _ready(client)
    response = client.post(DICTIONARIES, json={
        "action": "SET", "database_id": DB_ID, "field": "Course.category", "stored": "교양",
        "label": "교양", "aliases": [], "reviewer": reviewer,
    })
    assert response.status_code == 400


def test_before_review_nothing_is_editable(client_repo) -> None:  # noqa: F811
    client, _repo, _app, _executor = client_repo
    assert _register(client).status_code == 200
    body = client.get(DICTIONARIES, params={"database_id": DB_ID})
    assert body.status_code == 200 and body.json()["fields"] == []
