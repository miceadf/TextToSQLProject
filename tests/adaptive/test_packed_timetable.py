"""Phase 8-21 C: a whole timetable packed into one text column."""

from __future__ import annotations

from typing import Any

import pytest

from backend.adaptive.api import legacy_view_profile as lvp
from backend.adaptive.api.chat_query import answer_chat_query
from backend.adaptive.api.postgres_runtime import PostgresRuntimeProfile
from backend.adaptive.catalog.postgres import PostgresConnectionConfig
from backend.adaptive.evaluation.ai_mapping import values_fit
from backend.adaptive.harness.errors import HarnessFailure
from backend.adaptive.harness.postgres_values import OVER_LIMIT, PostgresDistinctValueReader
from backend.adaptive.mapping.transforms import (
    FieldTransform,
    normalize_filter_value,
    transform_operators,
)
from backend.adaptive.ontology.contracts import DataType, Operator
from backend.adaptive.registration.postgres_registrations import packed_weekdays_fit, transform_fits
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

PACKED = ["월 09:00-10:15, 수 09:00-10:15", "금 13:00-15:45", "월1-2/수3", "화 9:00~10:30"]


@pytest.mark.parametrize(
    ("values", "fits"),
    [
        (PACKED, True),
        (["월", "화"], False),  # bare weekdays are KOREAN_WEEKDAY
        (["월", "수 10:00-11:00"], True),
        (["수학", "월 09:00"], False),  # a course name is never a timetable
        (["월요일 09:00"], False),
        (["월 09:00, 오후"], False),
        ([], False),
    ],
)
def test_packed_timetables_are_recognised_strictly(values: list[str], fits: bool) -> None:
    assert packed_weekdays_fit(values) is fits
    assert transform_fits(FieldTransform.PACKED_WEEKDAY, "character varying(100)", DataType.STRING, values) is fits
    assert values_fit(FieldTransform.PACKED_WEEKDAY, values) is fits


def test_the_transform_only_searches_by_containment() -> None:
    assert transform_operators(FieldTransform.PACKED_WEEKDAY) == frozenset({Operator.CONTAINS})
    assert normalize_filter_value(FieldTransform.PACKED_WEEKDAY, "월요일") == "월"
    assert not transform_fits(FieldTransform.PACKED_WEEKDAY, "integer", DataType.STRING, PACKED)
    with pytest.raises(ValueError):
        normalize_filter_value(FieldTransform.PACKED_WEEKDAY, "주말")


def packed_context(executor: Any = None) -> Any:
    transforms = {
        **{name: kind for name, kind in lvp.FIELD_TRANSFORMS.items() if not name.startswith("Schedule.")},
        "Schedule.day": FieldTransform.PACKED_WEEKDAY,
    }
    fields = {name: column for name, (column, _) in lvp.VIEW_FIELDS.items() if name not in ("Schedule.start_time", "Schedule.end_time")}
    profile = PostgresRuntimeProfile(
        database_id=DATABASE_ID,
        label=lvp.LABEL,
        connection_config=PostgresConnectionConfig(SECRET_DSN, include_views=True),
        published_mapping=make_published(fields),
        field_transforms=transforms,
        unsupported_fields=(*lvp.UNSUPPORTED_FIELDS, "Schedule.start_time", "Schedule.end_time"),
    )
    catalog = make_catalog(columns={**VIEW_COLUMNS, "day_of_week": "character varying(100)"})
    return make_context(profile, executor=executor, catalog=catalog)


class _Model:
    def __init__(self, filters: list[dict[str, Any]]) -> None:
        self.filters = filters

    def generate_ir(self, *_args: Any) -> dict[str, Any]:
        return {"entity": "Course", "select": ["Course.name"], "filters": self.filters, "limit": 50}


def test_the_chat_finds_a_weekday_inside_the_timetable() -> None:
    executor = FakeExecutor()
    answer = answer_chat_query(
        database_id=DATABASE_ID,
        question="월요일 수업",
        pg_contexts={DATABASE_ID: packed_context(executor)},
        model=_Model([{"field": "Schedule.day", "operator": "equals", "value": "월요일"}]),
    )
    assert answer["status"] == "SUCCEEDED"
    [query] = executor.queries
    assert '"t0"."day_of_week" LIKE %s' in query.sql and query.parameters == ["%월%", 200]
    assert answer["understood"]["filters"][0]["operator"] == "contains"


def test_equality_on_a_packed_timetable_is_refused_below_the_chat() -> None:
    context = packed_context()
    ir = query_ir(["Course.name"], [{"field": "Schedule.day", "operator": "equals", "value": "월"}])
    with pytest.raises(HarnessFailure):
        context.compiler().compile(ir, context.harness_mapping())


def test_a_time_question_is_unsupported_here() -> None:
    answer = answer_chat_query(
        database_id=DATABASE_ID,
        question="오전 수업",
        pg_contexts={DATABASE_ID: packed_context(FakeExecutor())},
        model=_Model([{"field": "Schedule.start_time", "operator": "less_than", "value": "12:00"}]),
    )
    assert answer["status"] == "UNSUPPORTED"


def test_format_checks_read_a_sample_of_a_large_column() -> None:
    class _Cursor:
        def execute(self, query: str, params: Any) -> None:
            self.rows = [(f"월 {n:02d}:00",) for n in range(params[0])]

        def fetchall(self) -> list[Any]:
            return self.rows

    class _Connection:
        def cursor(self) -> _Cursor:
            return _Cursor()

        def rollback(self) -> None:
            pass

        def close(self) -> None:
            pass

    reader = PostgresDistinctValueReader(
        {DATABASE_ID: PostgresConnectionConfig(SECRET_DSN)}, connector=lambda _config: _Connection()
    )
    assert reader.read(DATABASE_ID, "t", "c", 5) is OVER_LIMIT
    assert len(reader.read(DATABASE_ID, "t", "c", 5, sample=True)) == 5


def test_only_the_packed_weekday_may_be_chosen_from_a_sample() -> None:
    """Luna 8-21 C review: a sample must not decide a transform that casts values."""
    from backend.adaptive.registration.postgres_registrations import SampledValues

    credits = SampledValues(["3/3/0", "2/2/0"])
    assert not transform_fits(FieldTransform.SLASH_FIRST_NUMERIC, "character varying(20)", DataType.NUMBER, credits)
    assert transform_fits(FieldTransform.SLASH_FIRST_NUMERIC, "character varying(20)", DataType.NUMBER, list(credits))
    assert transform_fits(FieldTransform.PACKED_WEEKDAY, "character varying(100)", DataType.STRING, SampledValues(PACKED))
