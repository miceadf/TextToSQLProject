"""Phase 8-21 B: time fields stored as period numbers, read through a period table."""

from __future__ import annotations

from typing import Any

import pytest

from backend.adaptive.api import legacy_view_profile as lvp
from backend.adaptive.api.chat_query import answer_chat_query
from backend.adaptive.api.postgres_runtime import PostgresRuntimeError, PostgresRuntimeProfile
from backend.adaptive.catalog.postgres import PostgresConnectionConfig
from backend.adaptive.harness.errors import HarnessFailure
from backend.adaptive.mapping.values import ValueDictionary, ValueEntry, contains_label_values, time_label_values
from backend.adaptive.ontology.contracts import Operator
from backend.adaptive.registration import postgres_dictionaries as dictionaries_store
from tests.adaptive.test_legacy_view_partial_mapping import (
    DATABASE_ID,
    SECRET_DSN,
    VIEW_COLUMNS,
    FakeExecutor,
    make_catalog,
    make_context,
    make_published,
    query_ir,
)

# Synthetic 30-minute periods: 1 → 09:00, 2 → 09:30, ... 6 → 11:30.
PERIODS = ValueDictionary(
    entries=tuple(
        ValueEntry(stored=str(n), label=f"{9 + (n - 1) // 2:02d}:{30 * ((n - 1) % 2):02d}") for n in range(1, 7)
    ),
    reviewer="synthetic-test",
    reason="period table (synthetic)",
)
TIMES = {"Schedule.start_time": PERIODS}


def period_context(
    *, executor: Any = None, dictionaries: dict[str, Any] | None = None, start_type: str = "integer"
) -> Any:
    transforms = {name: kind for name, kind in lvp.FIELD_TRANSFORMS.items() if name != "Schedule.start_time"}
    profile = PostgresRuntimeProfile(
        database_id=DATABASE_ID,
        label=lvp.LABEL,
        connection_config=PostgresConnectionConfig(SECRET_DSN, include_views=True),
        published_mapping=make_published(),
        field_transforms=transforms,
        unsupported_fields=lvp.UNSUPPORTED_FIELDS,
        value_dictionaries=TIMES if dictionaries is None else dictionaries,
    )
    catalog = make_catalog(columns={**VIEW_COLUMNS, "start_time": start_type})
    return make_context(profile, executor=executor, catalog=catalog)


def start(operator: str, value: Any) -> Any:
    return query_ir(["Course.name"], [{"field": "Schedule.start_time", "operator": operator, "value": value}])


# --- the period table -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("operator", "value", "periods"),
    [
        (Operator.LESS_THAN, "10:00", ("1", "2")),
        (Operator.GREATER_THAN_OR_EQUAL, "10:30", ("4", "5", "6")),
        (Operator.EQUALS, "9:30", ("2",)),
        (Operator.NOT_EQUALS, "09:00", ("2", "3", "4", "5", "6")),
        (Operator.GREATER_THAN, "22:00", ()),
    ],
)
def test_a_time_comparison_selects_periods(operator: Operator, value: str, periods: tuple[str, ...]) -> None:
    assert PERIODS.time_labels()
    assert time_label_values("Schedule.start_time", operator, value, value_dictionaries=TIMES) == periods


def test_only_period_tables_compare_times() -> None:
    names = ValueDictionary(entries=(ValueEntry(stored="1", label="월요일"),), reviewer="t", reason="t")
    assert not names.time_labels()
    assert time_label_values("Schedule.day", Operator.EQUALS, "09:00", value_dictionaries={"Schedule.day": names}) is None
    assert time_label_values("Schedule.start_time", Operator.CONTAINS, "09", value_dictionaries=TIMES) is None
    assert contains_label_values("Schedule.start_time", Operator.CONTAINS, "09", value_dictionaries=TIMES) == ()
    with pytest.raises(ValueError):
        time_label_values("Schedule.start_time", Operator.LESS_THAN, "오후", value_dictionaries=TIMES)


def test_period_numbers_show_as_times() -> None:
    assert PERIODS.label_for(3) == "10:00" and PERIODS.label_for("3") == "10:00"
    assert PERIODS.label_for(99) == 99 and PERIODS.label_for(True) is True


# --- compiler and policy ----------------------------------------------------------------------


def compile_and_validate(context: Any, ir: Any, *, sql: str | None = None, parameters: list[Any] | None = None) -> Any:
    mapping = context.harness_mapping()
    compiled = context.compiler().compile(ir, mapping)
    if sql is not None or parameters is not None:
        compiled = compiled.model_copy(
            update={"sql": sql or compiled.sql, "parameters": compiled.parameters if parameters is None else parameters}
        )
    context.policy().validate(compiled, query_ir=ir, mapping=mapping)
    return compiled


def test_compiler_and_policy_agree_on_the_period_list() -> None:
    context = period_context()
    compiled = compile_and_validate(context, start("less_than", "10:00"))
    assert compiled.sql.endswith('WHERE "t0"."start_time" IN (%s, %s) LIMIT %s')
    assert compiled.parameters == ["1", "2", 50]


@pytest.mark.parametrize(
    "tamper",
    [
        lambda sql, params: (sql, ["1", "3", 50]),  # another period
        lambda sql, params: (sql.replace("IN (%s, %s)", "IN (%s)"), ["1", 50]),  # one short
        lambda sql, params: (sql.replace("IN (%s, %s)", "IN (%s, '2')"), ["1", 50]),  # literal
        lambda sql, params: (sql.replace(" IN ", " NOT IN "), params),
        lambda sql, params: (sql.replace('"t0"."start_time" IN', '"t0"."end_time" IN'), params),
        lambda sql, params: (sql.replace("IN (%s, %s)", "< %s"), ["10:00", 50]),  # the raw comparison
    ],
)
def test_the_policy_refuses_any_other_shape(tamper: Any) -> None:
    context = period_context()
    ir = start("less_than", "10:00")
    compiled = context.compiler().compile(ir, context.harness_mapping())
    sql, parameters = tamper(compiled.sql, list(compiled.parameters))
    with pytest.raises(HarnessFailure):
        compile_and_validate(context, ir, sql=sql, parameters=parameters)


def test_no_matching_period_fails_closed_in_the_compiler() -> None:
    with pytest.raises(HarnessFailure):
        compile_and_validate(period_context(), start("greater_than", "22:00"))


# --- runtime and registration rules -----------------------------------------------------------


def test_a_time_on_a_period_column_needs_a_period_table() -> None:
    with pytest.raises(PostgresRuntimeError):
        period_context(dictionaries={})
    names = ValueDictionary(entries=(ValueEntry(stored="1", label="1교시"),), reviewer="t", reason="t")
    with pytest.raises(PostgresRuntimeError):
        period_context(dictionaries={"Schedule.start_time": names})
    with pytest.raises(PostgresRuntimeError):  # a period table belongs on an integer column
        period_context(start_type="character varying(5)")


def test_period_entries_are_checked_by_shape() -> None:
    assert dictionaries_store.is_period_field("Schedule.start_time")
    assert not dictionaries_store.is_period_field("Schedule.day")
    assert dictionaries_store.valid_period_entry("12", "14:30", [])
    for stored, label, aliases in (
        ("a", "09:00", []), ("1", "9:00", []), ("1", "25:00", []), ("1", "09:00", ["1교시"]),
        ("01", "09:00", []),  # Luna 8-21 B review: an integer column returns 1, so "01" would never show its time
    ):
        assert not dictionaries_store.valid_period_entry(stored, label, aliases)


# --- chat -------------------------------------------------------------------------------------


class _Model:
    def __init__(self, filters: list[dict[str, Any]]) -> None:
        self.filters = filters

    def generate_ir(self, *_args: Any) -> dict[str, Any]:
        return {"entity": "Course", "select": ["Course.name"], "filters": self.filters, "limit": 50}


def ask(filters: list[dict[str, Any]], executor: Any) -> dict[str, Any]:
    return answer_chat_query(
        database_id=DATABASE_ID,
        question="수업",
        pg_contexts={DATABASE_ID: period_context(executor=executor)},
        model=_Model(filters),
    )


def test_the_chat_answers_a_morning_question_through_periods() -> None:
    executor = FakeExecutor()
    answer = ask([{"field": "Schedule.start_time", "operator": "less_than", "value": "10:00"}], executor)
    assert answer["status"] == "SUCCEEDED" and executor.queries[0].parameters == ["1", "2", 200]


def test_a_time_no_period_reaches_is_empty_without_sql() -> None:
    executor = FakeExecutor()
    answer = ask([{"field": "Schedule.start_time", "operator": "greater_than", "value": "22:00"}], executor)
    assert answer["status"] == "SUCCEEDED" and answer["row_count"] == 0 and executor.queries == []
    assert answer["unmatched"] == [{"field": "Schedule.start_time", "label": "시작 시간", "value": "22:00"}]


def test_a_value_that_is_not_a_time_is_not_understood() -> None:
    executor = FakeExecutor()
    answer = ask([{"field": "Schedule.start_time", "operator": "less_than", "value": "오후"}], executor)
    assert answer == {**answer, "status": "NOT_UNDERSTOOD", "failure_code": "INVALID_VALUE_TYPE"}
    assert executor.queries == []


# --- registration API -------------------------------------------------------------------------


def test_publish_needs_the_period_table_and_takes_unused_periods(tmp_path, monkeypatch) -> None:
    """The admin types the school's whole timetable, including periods no class uses yet."""
    import json

    from tests.adaptive import test_legacy_view_partial_mapping as lv
    from tests.adaptive import test_postgres_registration as base
    from tests.adaptive.test_value_dictionary_editor import DICTIONARIES

    columns = {**VIEW_COLUMNS, "start_time": "integer", "end_time": "integer"}
    monkeypatch.setattr(base, "make_catalog", lambda: lv.make_catalog(columns=columns))
    monkeypatch.setattr(base, "VALUES", {**base.VALUES, "start_time": ["1", "3"], "end_time": ["2", "4"]})
    fixture = base.client_repo.__wrapped__(tmp_path)
    client, repo, app, _executor = next(fixture)
    try:
        assert base._register(client).status_code == 200
        base._review_all(client)
        for name in ("Schedule.start_time", "Schedule.end_time"):
            remapped = base._review(client, {
                "action": "REMAP", "database_id": base.DB_ID, "semantic_field": name, "table": lv.VIEW,
                "column": name.split(".")[1], "reviewer": "admin", "reason": "교시 번호",
            })
            assert remapped.status_code == 200, remapped.text[:200]
        publish = {"action": "PUBLISH", "database_id": base.DB_ID, "published_by": "admin"}
        blocked = base._review(client, publish)
        assert blocked.status_code == 422
        assert blocked.json()["error"]["fields"] == ["Schedule.end_time", "Schedule.start_time"]

        def put(field: str, stored: str, label: str, aliases: list[str] | None = None) -> int:
            return client.post(DICTIONARIES, json={
                "action": "SET", "database_id": base.DB_ID, "field": field, "stored": stored,
                "label": label, "aliases": aliases or [], "reviewer": "admin",
            }).status_code

        listed = {item["field"]: item for item in client.get(DICTIONARIES, params={"database_id": base.DB_ID}).json()["fields"]}
        assert listed["Schedule.start_time"]["values"] is None  # the screen offers a free input
        assert put("Schedule.start_time", "9", "13:00") == 200  # no class starts in period 9 yet
        assert put("Schedule.start_time", "1", "1교시") == 400  # a period table holds times
        assert put("Schedule.start_time", "1", "09:00", ["1교시"]) == 400
        for n, time in (("1", "09:00"), ("3", "10:00")):
            assert put("Schedule.start_time", n, time) == 200
        for n, time in (("2", "10:00"), ("4", "11:00")):
            assert put("Schedule.end_time", n, time) == 200
        published = base._review(client, publish)
        assert published.status_code == 200, published.text[:300]
        profile = json.loads(
            (repo / ".schema-adaptive" / "postgres-profiles" / base.DB_ID / "profile.json").read_text(encoding="utf-8")
        )
        assert {"Schedule.start_time", "Schedule.end_time"} <= set(profile["value_dictionaries"])
        assert "Schedule.start_time" not in profile["field_transforms"]
        context = app.state.synthetic_postgres_contexts[base.DB_ID]
        assert context.value_dictionaries()["Schedule.start_time"].label_for(9) == "13:00"
    finally:
        fixture.close()
