"""Phase 9-06: a number field is published only on a numeric column or a validated transform."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.adaptive import test_legacy_view_partial_mapping as lv
from tests.adaptive import test_postgres_registration as base


def publish_with(tmp_path, monkeypatch, types: dict[str, str], values: dict[str, list[str]]) -> tuple[Any, Any]:
    monkeypatch.setattr(base, "make_catalog", lambda: lv.make_catalog(columns={**lv.VIEW_COLUMNS, **types}))
    monkeypatch.setattr(base, "VALUES", {**base.VALUES, **values})
    fixture = base.client_repo.__wrapped__(tmp_path)
    client, repo, _app, _executor = next(fixture)
    assert base._register(client).status_code == 200
    base._review_all(client)
    response = base._review(client, {"action": "PUBLISH", "database_id": base.DB_ID, "published_by": "admin"})
    return response, repo


def transforms(repo: Any) -> dict[str, str]:
    profile = repo / ".schema-adaptive" / "postgres-profiles" / base.DB_ID / "profile.json"
    return json.loads(profile.read_text(encoding="utf-8"))["field_transforms"]


@pytest.mark.parametrize(
    "values",
    [
        ["3-2-2", "2-2-0"],  # 9-04 rehearsal: HTTP 500 at question time
        ["3", "2"],  # read as numbers, but text = 3 still fails in SQL
        ["3/3/0", "삼학점"],  # one value breaks the slash transform
    ],
)
def test_credits_on_a_text_column_without_a_transform_block_publish(tmp_path, monkeypatch, values: list[str]) -> None:
    response, _repo = publish_with(tmp_path, monkeypatch, {"credit_hours": "character varying"}, {"credit_hours": values})
    assert response.status_code == 422
    assert response.json()["error"]["fields"] == ["Course.credits"]


def test_slash_credits_keep_their_transform(tmp_path, monkeypatch) -> None:
    response, repo = publish_with(tmp_path, monkeypatch, {"credit_hours": "character varying"}, {"credit_hours": ["3/3/0"]})
    assert response.status_code == 200, response.text[:300]
    assert transforms(repo)["Course.credits"] == "SLASH_FIRST_NUMERIC"


@pytest.mark.parametrize("declared", ["numeric", "numeric(3,1)", "integer", "double precision"])
def test_numeric_columns_publish_as_is(tmp_path, monkeypatch, declared: str) -> None:
    response, repo = publish_with(tmp_path, monkeypatch, {"credit_hours": declared}, {"credit_hours": ["3", "2"]})
    assert response.status_code == 200, response.text[:300]
    assert "Course.credits" not in transforms(repo)


def test_every_number_field_is_checked(tmp_path, monkeypatch) -> None:
    response, _repo = publish_with(tmp_path, monkeypatch, {"capacity": "character varying"}, {"capacity": ["40"]})
    assert response.status_code == 422
    assert response.json()["error"]["fields"] == ["CourseOffering.capacity"]


# --- Luna 9-06 review ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("types", "values", "field"),
    [
        ({"credit_hours": "character varying"}, {"credit_hours": ["3/3/0", ""]}, "Course.credits"),  # CAST('' AS NUMERIC)
        ({"start_time": "character varying", "end_time": "character varying"},
         {"start_time": ["0900", ""], "end_time": ["1015"]}, "Schedule.start_time"),
    ],
)
def test_empty_text_under_a_cast_transform_blocks_publish(tmp_path, monkeypatch, types, values, field) -> None:
    """The value read leaves out "", so the transform alone would be chosen and fail or misorder in SQL."""
    response, _repo = publish_with(tmp_path, monkeypatch, types, values)
    assert response.status_code == 422
    assert response.json()["error"]["fields"] == [field]


def test_a_number_field_gets_no_dictionary_exemption(tmp_path, monkeypatch) -> None:
    """A value dictionary never serves a number field (the runtime refuses it), so it cannot unblock one;
    the admin sees the field and the reason instead of a nameless refusal."""
    from backend.adaptive.registration import postgres_dictionaries

    original = postgres_dictionaries.build_dictionaries
    monkeypatch.setattr(
        postgres_dictionaries, "build_dictionaries",
        lambda *args, **kwargs: {**original(*args, **kwargs), "Course.credits": object()},
    )
    response, _repo = publish_with(tmp_path, monkeypatch, {"credit_hours": "character varying"}, {"credit_hours": ["3"]})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["fields"] == ["Course.credits"]
    assert error["reasons"] == [{"field": "Course.credits", "label": "학점", "reason": "NUMBER_FORMAT"}]
