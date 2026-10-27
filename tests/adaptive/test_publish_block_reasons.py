"""Phase 9-07: every blocked field comes back at once, with its label and reason."""

from __future__ import annotations

from typing import Any

import pytest

from tests.adaptive import test_legacy_view_partial_mapping as lv
from tests.adaptive import test_postgres_registration as base

PUBLISH = {"action": "PUBLISH", "database_id": base.DB_ID, "published_by": "admin"}


_ALIVE: list[Any] = []  # the client fixture closes its client when its generator is collected


def registered(tmp_path, monkeypatch, types: dict[str, str], values: dict[str, list[str]], *, skip: str = "") -> Any:
    return registered_repo(tmp_path, monkeypatch, types, values, skip=skip)[0]


def registered_repo(
    tmp_path, monkeypatch, types: dict[str, str], values: dict[str, list[str]], *, skip: str = ""
) -> tuple[Any, Any]:
    monkeypatch.setattr(base, "make_catalog", lambda: lv.make_catalog(columns={**lv.VIEW_COLUMNS, **types}))
    monkeypatch.setattr(base, "VALUES", {**base.VALUES, **values})
    fixture = base.client_repo.__wrapped__(tmp_path)
    _ALIVE.append(fixture)
    client, repo, _app, _executor = next(fixture)
    response = base._register(client)
    assert response.status_code == 200, response.text[:200]
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
    return client, repo


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


def test_text_on_numeric_column_is_blocked_but_text_or_number_compatible_mappings_pass(tmp_path, monkeypatch) -> None:
    numeric_root = tmp_path / "numeric-day"
    numeric_root.mkdir()
    client = registered(numeric_root, monkeypatch, {"day_of_week": "smallint"}, {"day_of_week": ["1"]})
    remap(client, "Schedule.day", "day_of_week")
    response = base._review(client, PUBLISH)
    reasons = {item["field"]: item["reason"] for item in response.json()["error"]["reasons"]}
    assert reasons["Schedule.day"] == "TEXT_ON_NUMBER"

    text_root = tmp_path / "text-day"
    text_root.mkdir()
    text_client = registered(text_root, monkeypatch, {"day_of_week": "varchar"}, {"day_of_week": ["Monday"]})
    remap(text_client, "Schedule.day", "day_of_week")
    assert base._review(text_client, PUBLISH).status_code == 200

    capacity_root = tmp_path / "numeric-capacity"
    capacity_root.mkdir()
    number_client = registered(capacity_root, monkeypatch, {"capacity": "smallint"}, {"capacity": ["40"]})
    remap(number_client, "CourseOffering.capacity", "capacity")
    assert base._review(number_client, PUBLISH).status_code == 200

    # Luna 9-30: a numeric section (or course code) column stays legitimate (Luna 8-23)
    section_root = tmp_path / "numeric-section"
    section_root.mkdir()
    section_client = registered(section_root, monkeypatch, {"section": "integer"}, {"section": ["1"]})
    assert base._review(section_client, PUBLISH).status_code == 200


def test_publish_review_maps_join_ambiguity_and_keeps_generic_failures(tmp_path, monkeypatch) -> None:
    from backend.adaptive.registration import postgres_registrations as pg

    _client, repo = registered_repo(tmp_path, monkeypatch, {}, {})  # publishable apart from validate

    class Ambiguous(Exception):
        fields = ("Instructor.name",)

    kwargs = dict(
        repository_root=repo,
        database_id=base.DB_ID,
        published_by="admin",
        source=pg.PostgresSource("campus", "Campus server", base.SECRET_DSN),
        # the fixture's values, as the HTTP path reads them (credits "3/3/0" gets its transform)
        read_values=lambda _table, column: [v for v in base.VALUES.get(column, []) if isinstance(v, str)],
    )
    context, blocked = pg.publish_review(**kwargs, validate=lambda _profile: (_ for _ in ()).throw(Ambiguous()))
    assert context is None
    assert blocked == [pg.BlockedField("Instructor.name", "JOIN_AMBIGUOUS")]
    assert not (repo / ".schema-adaptive" / "postgres-profiles" / base.DB_ID).exists()

    with pytest.raises(pg.PostgresRegistrationError) as caught:
        pg.publish_review(**kwargs, validate=lambda _profile: (_ for _ in ()).throw(Exception()))
    assert caught.value.code == "PUBLISH_BLOCKED"
    assert not (repo / ".schema-adaptive" / "postgres-profiles" / base.DB_ID).exists()


def test_http_publish_exposes_join_ambiguity_reason(tmp_path, monkeypatch) -> None:
    from backend.adaptive.registration import postgres_registrations as pg

    from backend.adaptive.api import postgres_runtime as runtime

    client = registered(tmp_path, monkeypatch, {}, {})

    # Luna 9-30: fail inside the runtime check, so the real publish_review turns it into the blocker
    def ambiguous(*_args, **_kwargs):
        raise runtime.JoinPathError(("Instructor.name",))

    monkeypatch.setattr(runtime, "build_validated_contexts", ambiguous)
    response = base._review(client, PUBLISH)
    assert response.status_code == 422
    from backend.adaptive.ontology import load_default_ontology

    label = load_default_ontology().fields["Instructor.name"].label
    assert response.json()["error"]["fields"] == ["Instructor.name"]
    assert response.json()["error"]["reasons"] == [
        {"field": "Instructor.name", "label": label, "reason": "JOIN_AMBIGUOUS"}
    ]


def test_the_screen_knows_every_reason_code() -> None:
    import re
    from pathlib import Path

    from backend.adaptive.registration.postgres_registrations import BLOCK_REASONS

    source = Path("src/adaptive/mappingReview.ts").read_text(encoding="utf-8")
    block = source[source.index("PUBLISH_BLOCK_REASONS"):source.index("];", source.index("PUBLISH_BLOCK_REASONS"))]
    assert set(re.findall(r"'([A-Z_]+)'", block)) == set(BLOCK_REASONS)
    for reason in BLOCK_REASONS:
        assert f"case '{reason}':" in source, reason  # each one has its own sentence
