"""Phase 9-07: every blocked field comes back at once, with its label and reason."""

from __future__ import annotations

from typing import Any

from tests.adaptive import test_legacy_view_partial_mapping as lv
from tests.adaptive import test_postgres_registration as base

PUBLISH = {"action": "PUBLISH", "database_id": base.DB_ID, "published_by": "admin"}


_ALIVE: list[Any] = []  # the client fixture closes its client when its generator is collected


def registered(tmp_path, monkeypatch, types: dict[str, str], values: dict[str, list[str]], *, skip: str = "") -> Any:
    monkeypatch.setattr(base, "make_catalog", lambda: lv.make_catalog(columns={**lv.VIEW_COLUMNS, **types}))
    monkeypatch.setattr(base, "VALUES", {**base.VALUES, **values})
    fixture = base.client_repo.__wrapped__(tmp_path)
    _ALIVE.append(fixture)
    client, _repo, _app, _executor = next(fixture)
    assert base._register(client).status_code == 200
    detail = base._review(client, {"action": "INIT", "database_id": base.DB_ID, "reviewer": "admin"}).json()
    for field in detail["review"]["fields"]:
        name = field["semantic_field"]
        if name == skip:
            continue  # left pending
        if field["candidates"]:
            action = {"action": "APPROVE", "candidate_index": 0}
        else:
            action = {"action": "IGNORE", "reason": "test"}
        base._review(client, {**action, "database_id": base.DB_ID, "semantic_field": name, "reviewer": "admin"})
    return client


def remap(client: Any, field: str, column: str) -> None:
    response = base._review(client, {
        "action": "REMAP", "database_id": base.DB_ID, "semantic_field": field, "table": lv.VIEW,
        "column": column, "reviewer": "admin", "reason": "test",
    })
    assert response.status_code == 200, response.text[:200]


def test_one_publish_names_every_blocked_field_and_why(tmp_path, monkeypatch) -> None:
    """9-04 rehearsal: same column, time range text and dashed credits took four publishes."""
    client = registered(
        tmp_path, monkeypatch,
        {"credit_hours": "character varying", "start_time": "character varying", "end_time": "character varying"},
        {"credit_hours": ["3-2-2"], "start_time": ["09:00-10:15"], "end_time": ["09:00-10:15"]},
    )
    remap(client, "Schedule.start_time", "end_time")  # both times on one column
    response = base._review(client, PUBLISH)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["fields"] == ["Course.credits", "Schedule.end_time", "Schedule.start_time"]
    assert error["reasons"] == [
        {"field": "Course.credits", "label": "학점", "reason": "NUMBER_FORMAT"},
        {"field": "Schedule.end_time", "label": "종료 시간", "reason": "TIME_FORMAT"},
        {"field": "Schedule.start_time", "label": "시작 시간", "reason": "COLUMN_SHARED",
         "other": "Schedule.end_time", "other_label": "종료 시간"},
    ]


def test_pending_fields_come_with_the_other_reasons(tmp_path, monkeypatch) -> None:
    client = registered(tmp_path, monkeypatch, {"capacity": "character varying"}, {"capacity": ["40"]}, skip="Course.name")
    response = base._review(client, PUBLISH)
    reasons = {item["field"]: item["reason"] for item in response.json()["error"]["reasons"]}
    assert reasons == {"Course.name": "PENDING", "CourseOffering.capacity": "NUMBER_FORMAT"}
    assert sorted(reasons) == response.json()["error"]["fields"]


def test_a_clean_publish_still_succeeds(tmp_path, monkeypatch) -> None:
    client = registered(tmp_path, monkeypatch, {}, {})
    assert base._review(client, PUBLISH).status_code == 200


def test_a_stale_dictionary_comes_with_the_other_blockers(tmp_path, monkeypatch) -> None:
    """Luna 9-07: a stale value dictionary used to return first and hide every other blocker."""
    from tests.adaptive.test_value_dictionary_editor import DICTIONARIES

    client = registered(tmp_path, monkeypatch, {"capacity": "character varying"}, {"capacity": ["40"]}, skip="Course.name")
    response = client.post(DICTIONARIES, json={
        "action": "SET", "database_id": base.DB_ID, "field": "Course.category", "stored": "교양",
        "label": "교양", "aliases": ["교양과목"], "reviewer": "admin",
    })
    assert response.status_code == 200, response.text[:200]
    monkeypatch.setattr(base, "VALUES", {**base.VALUES, "category": ["전공(핵심)"]})  # "교양" is gone now
    response = base._review(client, PUBLISH)
    reasons = {item["field"]: item["reason"] for item in response.json()["error"]["reasons"]}
    assert reasons == {
        "Course.category": "DICTIONARY_STALE", "Course.name": "PENDING", "CourseOffering.capacity": "NUMBER_FORMAT",
    }


def test_the_screen_knows_every_reason_code() -> None:
    import re
    from pathlib import Path

    from backend.adaptive.registration.postgres_registrations import BLOCK_REASONS

    source = Path("src/adaptive/mappingReview.ts").read_text(encoding="utf-8")
    block = source[source.index("PUBLISH_BLOCK_REASONS"):source.index("];", source.index("PUBLISH_BLOCK_REASONS"))]
    assert set(re.findall(r"'([A-Z_]+)'", block)) == set(BLOCK_REASONS)
    for reason in BLOCK_REASONS:
        assert f"case '{reason}':" in source, reason  # each one has its own sentence
