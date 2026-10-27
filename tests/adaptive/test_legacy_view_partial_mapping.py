"""Phase 7-12 legacy view partial mapping and declared value transforms."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.adaptive.api import legacy_view_profile as lvp
from backend.adaptive.api.postgres_runtime import (
    DatabaseRoutingExecutor,
    PostgresRuntimeError,
    PostgresRuntimeProfile,
    build_validated_contexts,
)
from backend.adaptive.api.postgres_startup_profile import (
    PostgresStartupProfileError,
    load_postgres_startup_profiles,
)
from backend.adaptive.api.app_factory import create_synthetic_clarification_prototype
from backend.adaptive.api.database_registry import CATALOG_PATH, DATABASES_PATH
from backend.adaptive.catalog.contracts import (
    ColumnCatalog,
    DatabaseCatalog,
    SQLiteAffinity,
    TableCatalog,
)
from backend.adaptive.catalog.postgres import (
    PG_TABLES_QUERY,
    PG_TABLES_WITH_VIEWS_QUERY,
    PostgresAdapter,
    PostgresConnectionConfig,
)
from backend.adaptive.harness.contracts import (
    CompiledQuery,
    HarnessErrorCode,
    QueryIR,
)
from backend.adaptive.harness.errors import HarnessFailure
from backend.adaptive.mapping.contracts import PublishedFieldMapping, PublishedMappingProfile
from backend.adaptive.mapping.transforms import (
    FieldTransform,
    column_type_supports,
    normalize_filter_value,
    normalize_transformed_value,
    render_postgres_transform,
    transform_supports_data_type,
)
from backend.adaptive.ontology.contracts import DataType

DATABASE_ID = lvp.DATABASE_ID
SECRET_DSN = "postgresql://svc-user:s3cret-pw-712@127.0.0.1:5432/legacy712"
FINGERPRINT = "ab" * 32
VIEW = lvp.VIEW

# view column -> declared PostgreSQL type (subset of the legacy view shape)
VIEW_COLUMNS = {
    "course_year": "integer",
    "subject_code": "character varying(20)",
    "section": "character varying(5)",
    "subject_name": "character varying(200)",
    "category": "character varying(50)",
    "credit_hours": "character varying(20)",
    "target_year": "integer",
    "professor": "character varying(100)",
    "capacity": "integer",
    "enrolled": "integer",
    "dept_name": "character varying(100)",
    "day_of_week": "character varying(5)",
    "start_time": "time without time zone",
    "end_time": "time without time zone",
}

CREDITS_SQL = "CAST(SPLIT_PART(\"t0\".\"credit_hours\", '/', 1) AS NUMERIC)"
START_SQL = "TO_CHAR(\"t0\".\"start_time\", 'HH24:MI')"


def make_catalog(*, columns: dict[str, str] | None = None) -> DatabaseCatalog:
    return DatabaseCatalog(
        database_id=DATABASE_ID,
        dialect="postgres",
        schema_fingerprint=FINGERPRINT,
        tables=[
            TableCatalog(
                name=VIEW,
                columns=[
                    ColumnCatalog(
                        name=name,
                        declared_type=declared,
                        affinity=SQLiteAffinity.TEXT,
                        nullable=True,
                        sample_values=[],
                    )
                    for name, declared in (columns or VIEW_COLUMNS).items()
                ],
                primary_key=[],
                foreign_keys=[],
            )
        ],
    )


def make_published(fields: dict[str, str] | None = None) -> PublishedMappingProfile:
    columns = fields or {semantic: column for semantic, (column, _) in lvp.VIEW_FIELDS.items()}
    return PublishedMappingProfile(
        database_id=DATABASE_ID,
        version=1,
        schema_fingerprint=FINGERPRINT,
        ontology_version=lvp.ONTOLOGY_VERSION,
        published_by="synthetic-test",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        fields={
            semantic: PublishedFieldMapping(table=VIEW, column=column, reviewer="synthetic-test")
            for semantic, column in columns.items()
        },
    )


def make_profile(
    *,
    fields: dict[str, str] | None = None,
    transforms: dict[str, FieldTransform] | None = None,
    unsupported: tuple[str, ...] | list[str] | None = None,
) -> PostgresRuntimeProfile:
    return PostgresRuntimeProfile(
        database_id=DATABASE_ID,
        label=lvp.LABEL,
        connection_config=PostgresConnectionConfig(SECRET_DSN, include_views=True),
        published_mapping=make_published(fields),
        field_transforms=dict(lvp.FIELD_TRANSFORMS) if transforms is None else transforms,
        unsupported_fields=lvp.UNSUPPORTED_FIELDS if unsupported is None else unsupported,
    )


class FakeAdapter:
    def __init__(self, catalog: DatabaseCatalog | None = None) -> None:
        self.catalog = catalog or make_catalog()
        self.calls = 0

    def inspect(self, database_id: str, *, include_samples: bool = False) -> DatabaseCatalog:
        assert database_id == DATABASE_ID and include_samples is False
        self.calls += 1
        return self.catalog.model_copy(deep=True)


class FakeExecutor:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows
        self.queries: list[CompiledQuery] = []

    def execute(self, database_id: str, compiled: CompiledQuery) -> tuple[list[str], list[dict[str, Any]]]:
        self.queries.append(compiled)
        columns = list(compiled.expected_columns)
        return columns, list(self.rows or [])


def make_context(profile: PostgresRuntimeProfile | None = None, *, executor: Any = None, catalog=None):
    return build_validated_contexts(
        [profile or make_profile()],
        adapters_by_id={DATABASE_ID: FakeAdapter(catalog)},
        executors_by_id={DATABASE_ID: executor or FakeExecutor()},
        expected_ontology_version=lvp.ONTOLOGY_VERSION,
    )[DATABASE_ID]


def query_ir(select: list[str], filters: list[dict[str, Any]] | None = None) -> QueryIR:
    return QueryIR.model_validate(
        {"entity": "Course", "select": select, "filters": filters or [], "limit": 50}
    )


def compile_and_validate(context: Any, ir: QueryIR) -> CompiledQuery:
    mapping = context.harness_mapping()
    compiled = context.compiler().compile(ir, mapping)
    context.policy().validate(compiled, query_ir=ir, mapping=mapping)
    return compiled


# --- transforms module -------------------------------------------------------


def test_transform_templates_types_and_normalization() -> None:
    assert render_postgres_transform(FieldTransform.SLASH_FIRST_NUMERIC, '"t0"."c"') == (
        "CAST(SPLIT_PART(\"t0\".\"c\", '/', 1) AS NUMERIC)"
    )
    assert render_postgres_transform(FieldTransform.TIME_TO_HHMM, '"t0"."c"') == (
        "TO_CHAR(\"t0\".\"c\", 'HH24:MI')"
    )
    assert transform_supports_data_type(FieldTransform.SLASH_FIRST_NUMERIC, DataType.NUMBER)
    assert not transform_supports_data_type(FieldTransform.SLASH_FIRST_NUMERIC, DataType.STRING)
    assert transform_supports_data_type(FieldTransform.TIME_TO_HHMM, DataType.TIME)
    assert column_type_supports(FieldTransform.SLASH_FIRST_NUMERIC, "character varying(20)")
    assert not column_type_supports(FieldTransform.SLASH_FIRST_NUMERIC, "integer")
    assert column_type_supports(FieldTransform.TIME_TO_HHMM, "time without time zone")
    assert not column_type_supports(FieldTransform.TIME_TO_HHMM, "timestamp without time zone")
    assert not column_type_supports(FieldTransform.TIME_TO_HHMM, "text")
    normalize = normalize_transformed_value
    assert normalize(FieldTransform.SLASH_FIRST_NUMERIC, Decimal("3")) == 3
    assert isinstance(normalize(FieldTransform.SLASH_FIRST_NUMERIC, Decimal("3.0")), int)
    assert normalize(FieldTransform.SLASH_FIRST_NUMERIC, Decimal("1.5")) == 1.5
    assert normalize(FieldTransform.SLASH_FIRST_NUMERIC, Decimal("NaN")) is None
    assert normalize(FieldTransform.SLASH_FIRST_NUMERIC, None) is None
    assert normalize(FieldTransform.TIME_TO_HHMM, "09:00") == "09:00"
    with pytest.raises(ValueError):
        FieldTransform("UPPER")


# --- compiler and AST policy -------------------------------------------------


def test_partial_profile_compiles_transforms_and_passes_policy() -> None:
    context = make_context()
    assert context.unsupported_fields() == (
        "CourseOffering.semester",
        "CourseOffering.year",
        "Department.code",
    )
    compiled = compile_and_validate(
        context,
        query_ir(
            ["Course.name", "Course.credits", "Schedule.start_time"],
            [
                {"field": "Course.credits", "operator": "equals", "value": 3},
                {"field": "Schedule.start_time", "operator": "greater_than_or_equal", "value": "12:00"},
            ],
        ),
    )
    assert compiled.sql == (
        f'SELECT "t0"."subject_name" AS "Course.name", {CREDITS_SQL} AS "Course.credits", '
        f'{START_SQL} AS "Schedule.start_time" FROM "{VIEW}" AS "t0" '
        f"WHERE {CREDITS_SQL} = %s AND {START_SQL} >= %s LIMIT %s"
    )
    assert compiled.parameters == [3, "12:00", 50]


@pytest.mark.parametrize(
    ("raw", "bound"),
    [("14:00:00", "14:00"), ("9:00", "09:00"), ("09:30", "09:30"), (" 13:05 ", "13:05")],
)
def test_time_filter_values_are_bound_in_hhmm(raw: str, bound: str) -> None:
    assert normalize_filter_value(FieldTransform.TIME_TO_HHMM, raw) == bound
    assert normalize_filter_value(FieldTransform.SLASH_FIRST_NUMERIC, raw) == raw
    compiled = compile_and_validate(
        make_context(),
        query_ir(
            ["Course.name"],
            [{"field": "Schedule.start_time", "operator": "greater_than_or_equal", "value": raw}],
        ),
    )
    assert compiled.parameters == [bound, 50]


@pytest.mark.parametrize("raw", ["14:00:30", "오후", "25:00", "12:60", "1400", ""])
def test_unrepresentable_time_values_are_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_filter_value(FieldTransform.TIME_TO_HHMM, raw)
    context = make_context()
    ir = query_ir(
        ["Course.name"],
        [{"field": "Schedule.start_time", "operator": "greater_than_or_equal", "value": raw}],
    )
    with pytest.raises(HarnessFailure) as caught:
        context.compiler().compile(ir, context.harness_mapping())
    assert caught.value.code is HarnessErrorCode.INVALID_VALUE_TYPE
    # The policy refuses the raw value too, so a bypassed compiler cannot bind it.
    sql = (
        f'SELECT "t0"."subject_name" AS "Course.name" FROM "{VIEW}" AS "t0" '
        f"WHERE {START_SQL} >= %s LIMIT %s"
    )
    with pytest.raises(HarnessFailure):
        context.policy().validate(
            CompiledQuery(sql=sql, parameters=[raw, 50], expected_columns=ir.select),
            query_ir=ir,
            mapping=context.harness_mapping(),
        )


@pytest.mark.parametrize(
    ("raw", "bound"),
    [("전공", "%전공%"), ("전공_핵심", "%전공\\_핵심%"), ("100%", "%100\\%%"), ("a\\b", "%a\\\\b%")],
)
def test_postgres_contains_escapes_like_wildcards(raw: str, bound: str) -> None:
    context = make_context()
    ir = query_ir(["Course.name"], [{"field": "Course.category", "operator": "contains", "value": raw}])
    compiled = compile_and_validate(context, ir)
    assert compiled.parameters == [bound, 50]
    # The policy refuses an unescaped pattern for the same filter.
    if bound != f"%{raw}%":
        with pytest.raises(HarnessFailure):
            context.policy().validate(
                compiled.model_copy(update={"parameters": [f"%{raw}%", 50]}),
                query_ir=ir,
                mapping=context.harness_mapping(),
            )


def test_transform_operators_per_transform() -> None:
    from backend.adaptive.mapping.transforms import TRANSFORM_OPERATORS, transform_operators
    from backend.adaptive.ontology.contracts import Operator

    assert transform_operators(FieldTransform.SLASH_FIRST_NUMERIC) == TRANSFORM_OPERATORS
    assert transform_operators(FieldTransform.TIME_TO_HHMM) == TRANSFORM_OPERATORS
    assert Operator.CONTAINS not in TRANSFORM_OPERATORS
    assert transform_operators(FieldTransform.KOREAN_WEEKDAY) == {
        Operator.EQUALS, Operator.NOT_EQUALS, Operator.CONTAINS,
    }
    assert render_postgres_transform(FieldTransform.KOREAN_WEEKDAY, '"t0"."c"') == '"t0"."c"'
    assert transform_supports_data_type(FieldTransform.KOREAN_WEEKDAY, DataType.STRING)
    assert column_type_supports(FieldTransform.KOREAN_WEEKDAY, "character varying(5)")
    assert not column_type_supports(FieldTransform.KOREAN_WEEKDAY, "integer")


@pytest.mark.parametrize("raw", ["주말", "", "월화", "1", "Mo"])
def test_unknown_weekdays_are_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_filter_value(FieldTransform.KOREAN_WEEKDAY, raw)
    context = make_context()
    ir = query_ir(["Course.name"], [{"field": "Schedule.day", "operator": "equals", "value": raw}])
    with pytest.raises(HarnessFailure) as caught:
        context.compiler().compile(ir, context.harness_mapping())
    assert caught.value.code is HarnessErrorCode.INVALID_VALUE_TYPE


def test_weekday_identity_transform_is_checked_exactly() -> None:
    context = make_context()
    ir = query_ir(["Schedule.day"], [{"field": "Schedule.day", "operator": "equals", "value": "월요일"}])
    compiled = compile_and_validate(context, ir)
    assert compiled.parameters == ["월", 50]
    assert '"t0"."day_of_week" AS "Schedule.day"' in compiled.sql
    for sql, params in [
        # another column in place of the weekday column
        (f'SELECT "t0"."subject_name" AS "Schedule.day" FROM "{VIEW}" AS "t0" '
         f'WHERE "t0"."day_of_week" = %s LIMIT %s', ["월", 50]),
        # a function around the weekday column
        (f'SELECT UPPER("t0"."day_of_week") AS "Schedule.day" FROM "{VIEW}" AS "t0" '
         f'WHERE "t0"."day_of_week" = %s LIMIT %s', ["월", 50]),
        # the raw model value instead of the normalized one
        (compiled.sql, ["월요일", 50]),
    ]:
        with pytest.raises(HarnessFailure):
            context.policy().validate(
                CompiledQuery(sql=sql, parameters=params, expected_columns=ir.select),
                query_ir=ir,
                mapping=context.harness_mapping(),
            )


def test_policy_rejects_unnormalized_time_parameter() -> None:
    context = make_context()
    ir = query_ir(
        ["Course.name"],
        [{"field": "Schedule.start_time", "operator": "greater_than_or_equal", "value": "14:00:00"}],
    )
    compiled = context.compiler().compile(ir, context.harness_mapping())
    tampered = compiled.model_copy(update={"parameters": ["14:00:00", 50]})
    with pytest.raises(HarnessFailure):
        context.policy().validate(tampered, query_ir=ir, mapping=context.harness_mapping())


def test_untransformed_query_sql_is_unchanged() -> None:
    compiled = compile_and_validate(
        make_context(),
        query_ir(["Course.name"], [{"field": "Course.category", "operator": "contains", "value": "전공"}]),
    )
    assert compiled.sql == (
        f'SELECT "t0"."subject_name" AS "Course.name" FROM "{VIEW}" AS "t0" '
        'WHERE "t0"."category" LIKE %s LIMIT %s'
    )


def test_contains_on_transformed_field_is_rejected_by_compiler() -> None:
    context = make_context()
    ir = query_ir(["Course.name"], [{"field": "Course.credits", "operator": "contains", "value": "3"}])
    with pytest.raises(HarnessFailure) as caught:
        context.compiler().compile(ir, context.harness_mapping())
    assert caught.value.code is HarnessErrorCode.INVALID_OPERATOR


def test_unsupported_field_fails_closed_in_compiler() -> None:
    # The harness rejects it earlier as MAPPING_MISSING (see the smoke test);
    # a direct compile still cannot plan an unmapped field.
    context = make_context()
    with pytest.raises(HarnessFailure) as caught:
        context.compiler().compile(query_ir(["CourseOffering.year"]), context.harness_mapping())
    assert caught.value.code is HarnessErrorCode.INVALID_JOIN_PLAN


@pytest.mark.parametrize(
    ("select_sql", "where_sql"),
    [
        # wrong split part index
        ("CAST(SPLIT_PART(\"t0\".\"credit_hours\", '/', 2) AS NUMERIC)", None),
        # transform applied to another column
        ("CAST(SPLIT_PART(\"t0\".\"subject_name\", '/', 1) AS NUMERIC)", None),
        # raw column instead of the declared transform
        ('"t0"."credit_hours"', None),
        # a different cast target
        ("CAST(SPLIT_PART(\"t0\".\"credit_hours\", '/', 1) AS TEXT)", None),
        # comment hidden inside the transform subtree
        ("CAST(SPLIT_PART(\"t0\".\"credit_hours\", '/', 1) /* x */ AS NUMERIC)", None),
        # WHERE compares the raw column
        (CREDITS_SQL, '"t0"."credit_hours" = %s'),
        # WHERE uses a pattern match on the transform
        (CREDITS_SQL, f"{CREDITS_SQL} LIKE %s"),
    ],
)
def test_policy_rejects_tampered_transforms(select_sql: str, where_sql: str | None) -> None:
    context = make_context()
    ir = query_ir(
        ["Course.name", "Course.credits"],
        [{"field": "Course.credits", "operator": "equals", "value": 3}],
    )
    sql = (
        f'SELECT "t0"."subject_name" AS "Course.name", {select_sql} AS "Course.credits" '
        f'FROM "{VIEW}" AS "t0" WHERE {where_sql or CREDITS_SQL + " = %s"} LIMIT %s'
    )
    tampered = CompiledQuery(sql=sql, parameters=[3, 50], expected_columns=ir.select)
    with pytest.raises(HarnessFailure):
        context.policy().validate(tampered, query_ir=ir, mapping=context.harness_mapping())


@pytest.mark.parametrize(
    "name_sql",
    [
        "UPPER(\"t0\".\"subject_name\")",
        "'x'",
        "CAST(SPLIT_PART(\"t0\".\"credit_hours\", '/', 1) AS NUMERIC)",
    ],
)
def test_policy_rejects_functions_and_literals_outside_declared_transforms(name_sql: str) -> None:
    context = make_context()
    ir = query_ir(["Course.name"])
    sql = f'SELECT {name_sql} AS "Course.name" FROM "{VIEW}" AS "t0" LIMIT %s'
    tampered = CompiledQuery(sql=sql, parameters=[50], expected_columns=ir.select)
    with pytest.raises(HarnessFailure):
        context.policy().validate(tampered, query_ir=ir, mapping=context.harness_mapping())


@pytest.mark.parametrize(
    "sql",
    [
        # extra projection after the transformed one
        f'SELECT "t0"."subject_name" AS "Course.name", {CREDITS_SQL} AS "Course.credits", '
        f'{CREDITS_SQL} AS "x" FROM "{VIEW}" AS "t0" WHERE {CREDITS_SQL} = %s LIMIT %s',
        # transformed projection missing
        f'SELECT "t0"."subject_name" AS "Course.name" FROM "{VIEW}" AS "t0" '
        f"WHERE {CREDITS_SQL} = %s LIMIT %s",
        # extra predicate with a literal comparison
        f'SELECT "t0"."subject_name" AS "Course.name", {CREDITS_SQL} AS "Course.credits" '
        f'FROM "{VIEW}" AS "t0" WHERE {CREDITS_SQL} = %s AND {CREDITS_SQL} > 0 LIMIT %s',
        # predicate missing
        f'SELECT "t0"."subject_name" AS "Course.name", {CREDITS_SQL} AS "Course.credits" '
        f'FROM "{VIEW}" AS "t0" LIMIT %s',
        # predicates swapped so the transform sits where a plain column is expected
        f'SELECT "t0"."subject_name" AS "Course.name", {CREDITS_SQL} AS "Course.credits" '
        f'FROM "{VIEW}" AS "t0" WHERE "t0"."category" LIKE %s AND {CREDITS_SQL} = %s LIMIT %s',
    ],
)
def test_policy_rejects_projection_and_predicate_count_or_order_changes(sql: str) -> None:
    context = make_context()
    ir = query_ir(
        ["Course.name", "Course.credits"],
        [
            {"field": "Course.credits", "operator": "equals", "value": 3},
            {"field": "Course.category", "operator": "contains", "value": "전공"},
        ][: 2 if "LIKE" in sql else 1],
    )
    parameters: list[Any] = [3, "%전공%", 50] if "LIKE" in sql else [3, 50]
    tampered = CompiledQuery(sql=sql, parameters=parameters, expected_columns=ir.select)
    with pytest.raises(HarnessFailure):
        context.policy().validate(tampered, query_ir=ir, mapping=context.harness_mapping())


def test_policy_without_transforms_rejects_transform_sql() -> None:
    full = make_context(make_profile(transforms={}))
    ir = query_ir(["Course.credits"])
    sql = f'SELECT {CREDITS_SQL} AS "Course.credits" FROM "{VIEW}" AS "t0" LIMIT %s'
    with pytest.raises(HarnessFailure):
        full.policy().validate(
            CompiledQuery(sql=sql, parameters=[50], expected_columns=ir.select),
            query_ir=ir,
            mapping=full.harness_mapping(),
        )


# --- runtime validation ------------------------------------------------------


def _fields_without(*semantic: str) -> dict[str, str]:
    return {
        field: column
        for field, (column, _) in lvp.VIEW_FIELDS.items()
        if field not in semantic
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        # a mapped field is missing and not declared unsupported
        {"fields": _fields_without("Course.code")},
        # partial mapping without an unsupported list
        {"unsupported": ()},
        # declared unsupported but still mapped
        {"unsupported": (*lvp.UNSUPPORTED_FIELDS, "Course.code")},
        # unknown field in the unsupported list
        {"unsupported": (*lvp.UNSUPPORTED_FIELDS, "Course.nickname")},
        # transform on an unsupported field
        {"transforms": {**lvp.FIELD_TRANSFORMS, "CourseOffering.year": FieldTransform.SLASH_FIRST_NUMERIC}},
        # transform incompatible with the ontology type
        {"transforms": {**lvp.FIELD_TRANSFORMS, "Course.name": FieldTransform.SLASH_FIRST_NUMERIC}},
        {"transforms": {**lvp.FIELD_TRANSFORMS, "Course.credits": FieldTransform.TIME_TO_HHMM}},
    ],
)
def test_runtime_rejects_invalid_partial_profiles(kwargs: dict[str, Any]) -> None:
    with pytest.raises(PostgresRuntimeError):
        make_context(make_profile(**kwargs))


def test_runtime_rejects_transform_on_incompatible_column_type() -> None:
    catalog = make_catalog(columns={**VIEW_COLUMNS, "credit_hours": "integer"})
    with pytest.raises(PostgresRuntimeError):
        make_context(catalog=catalog)


def test_profile_rejects_malformed_transform_and_unsupported_values() -> None:
    with pytest.raises(PostgresRuntimeError):
        make_profile(transforms={"Course.credits": "UPPER"})  # type: ignore[dict-item]
    with pytest.raises(PostgresRuntimeError):
        make_profile(unsupported=("Department.code", "Department.code"))
    with pytest.raises(PostgresRuntimeError):
        make_profile(unsupported="Department.code")  # type: ignore[arg-type]


def test_routing_executor_normalizes_only_transformed_values() -> None:
    executor = FakeExecutor(
        rows=[{"Course.credits": Decimal("3"), "CourseOffering.capacity": Decimal("40")},
              {"Course.credits": Decimal("1.5"), "CourseOffering.capacity": Decimal("7")}]
    )
    context = make_context(executor=executor)

    class _NoSqlite:
        def execute(self, *_args: Any) -> Any:
            raise AssertionError("sqlite must not be used")

    routing = DatabaseRoutingExecutor(_NoSqlite(), {DATABASE_ID: context})
    ir = query_ir(["Course.credits", "CourseOffering.capacity"])
    compiled = compile_and_validate(context, ir)
    _columns, rows = routing.execute(DATABASE_ID, compiled)
    assert rows == [
        {"Course.credits": 3, "CourseOffering.capacity": Decimal("40")},
        {"Course.credits": 1.5, "CourseOffering.capacity": Decimal("7")},
    ]
    assert isinstance(rows[0]["Course.credits"], int)


def test_partial_postgres_catalog_lists_the_present_fields() -> None:
    """Phase 9-34: with course code and name mapped, the list opens; missing fields are null."""
    executor = FakeExecutor()
    app = create_synthetic_clarification_prototype(
        postgres_profiles=[make_profile()],
        postgres_adapters={DATABASE_ID: FakeAdapter()},
        postgres_executors={DATABASE_ID: executor},
    )
    client = TestClient(app, raise_server_exceptions=False)
    listed = client.get(DATABASES_PATH).json()["databases"]
    assert {"database_id": DATABASE_ID, "label": lvp.LABEL, "schema": "postgres"} in listed
    response = client.get(CATALOG_PATH, params={"database_id": DATABASE_ID})
    assert response.status_code == 200
    selected = set(executor.queries[-1].expected_columns)
    assert {"Course.code", "Course.name"} <= selected
    assert not selected & set(lvp.UNSUPPORTED_FIELDS)  # "이 DB에 없음" fields are not queried
    assert "s3cret" not in response.text


# --- inspector ---------------------------------------------------------------


class _Cursor:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    def execute(self, sql: str, _params: Any) -> None:
        self.log.append(sql)

    def fetchall(self) -> list[Any]:
        return []

    def close(self) -> None:
        pass


class _Connection:
    server_version = 160000

    def __init__(self, log: list[str]) -> None:
        self.log = log

    def cursor(self) -> _Cursor:
        return _Cursor(self.log)

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.mark.parametrize(("include_views", "expected"), [(False, PG_TABLES_QUERY), (True, PG_TABLES_WITH_VIEWS_QUERY)])
def test_inspector_includes_views_only_when_opted_in(include_views: bool, expected: str) -> None:
    log: list[str] = []
    config = PostgresConnectionConfig(SECRET_DSN, include_views=include_views)
    PostgresAdapter({DATABASE_ID: config}, connector=lambda _cfg: _Connection(log)).inspect(DATABASE_ID)
    assert expected in log
    assert ("'v'" in expected) is include_views
    assert "'v'" not in PG_TABLES_QUERY


def test_connection_config_include_views_is_strict_and_copied() -> None:
    with pytest.raises(ValueError):
        PostgresConnectionConfig(SECRET_DSN, include_views="yes")  # type: ignore[arg-type]
    assert make_profile().connection_config.include_views is True
    assert "s3cret" not in repr(make_profile().connection_config)


# --- startup profile v2 ------------------------------------------------------


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo712"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (root / ".gitignore").write_text(".schema-adaptive/\n", encoding="utf-8")
    return root


def _write_documents(repository: Path, profile_patch: dict[str, Any] | None = None) -> None:
    profile_bytes, mapping_bytes = lvp.build_documents(
        FINGERPRINT, datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    profile = json.loads(profile_bytes)
    profile.update(profile_patch or {})
    directory = repository / lvp.PROFILE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "mapping.json").write_bytes(mapping_bytes)
    (directory / "profile.json").write_text(json.dumps(profile), encoding="utf-8")


def test_startup_loads_v2_profile(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_documents(repository)
    monkeypatch.setenv(lvp.DSN_ENV, SECRET_DSN)
    [profile] = load_postgres_startup_profiles(repository_root=repository, database_ids=[DATABASE_ID])
    assert profile.connection_config.include_views is True
    assert profile.field_transforms == lvp.FIELD_TRANSFORMS
    assert profile.unsupported_fields == tuple(sorted(lvp.UNSUPPORTED_FIELDS))
    assert set(profile.published_mapping.fields) == set(lvp.VIEW_FIELDS)
    context = make_context(profile)
    assert context.unsupported_fields() == tuple(sorted(lvp.UNSUPPORTED_FIELDS))


@pytest.mark.parametrize(
    "patch",
    [
        {"field_transforms": {"Course.credits": "UPPER"}},
        {"field_transforms": {"Course.credits": 1}},
        {"field_transforms": ["Course.credits"]},
        {"unsupported_fields": ["Department.code", "CourseOffering.year"]},  # unsorted
        {"unsupported_fields": ["Department.code", "Department.code"]},
        {"unsupported_fields": ["department.code"]},
        {"unsupported_fields": ["Course.credits"]},  # overlaps a transform
        {"include_views": "true"},
        {"extra": 1},
        {"version": "postgres-runtime-profile-v3"},
    ],
)
def test_startup_rejects_malformed_v2_profiles(
    repository: Path, monkeypatch: pytest.MonkeyPatch, patch: dict[str, Any]
) -> None:
    _write_documents(repository, patch)
    monkeypatch.setenv(lvp.DSN_ENV, SECRET_DSN)
    with pytest.raises(PostgresStartupProfileError) as caught:
        load_postgres_startup_profiles(repository_root=repository, database_ids=[DATABASE_ID])
    assert "s3cret" not in str(caught.value)


def test_startup_v1_profile_still_requires_full_mapping_and_no_views(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _profile_bytes, mapping_bytes = lvp.build_documents(
        FINGERPRINT, datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    import hashlib

    v1 = {
        "version": "postgres-runtime-profile-v1",
        "database_id": DATABASE_ID,
        "label": lvp.LABEL,
        "schema": "public",
        "dsn_env": lvp.DSN_ENV,
        "mapping_sha256": hashlib.sha256(mapping_bytes).hexdigest(),
    }
    directory = repository / lvp.PROFILE_DIR
    directory.mkdir(parents=True)
    (directory / "mapping.json").write_bytes(mapping_bytes)
    (directory / "profile.json").write_text(json.dumps(v1), encoding="utf-8")
    monkeypatch.setenv(lvp.DSN_ENV, SECRET_DSN)
    [profile] = load_postgres_startup_profiles(repository_root=repository, database_ids=[DATABASE_ID])
    assert profile.connection_config.include_views is False
    assert profile.field_transforms == {} and profile.unsupported_fields == ()
    # Without the v2 declarations the 12-field mapping is incomplete.
    with pytest.raises(PostgresRuntimeError):
        make_context(profile)


def test_startup_v1_keys_with_v2_fields_are_rejected(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_documents(repository, {"version": "postgres-runtime-profile-v1"})
    monkeypatch.setenv(lvp.DSN_ENV, SECRET_DSN)
    with pytest.raises(PostgresStartupProfileError):
        load_postgres_startup_profiles(repository_root=repository, database_ids=[DATABASE_ID])


# --- CLI ---------------------------------------------------------------------


def test_write_profile_is_idempotent_and_refuses_silent_replacement(repository: Path) -> None:
    catalog = make_catalog()
    assert lvp.write_profile(repository, catalog, replace=False) == "WRITTEN"
    assert lvp.write_profile(repository, catalog, replace=False) == "UNCHANGED"
    changed = catalog.model_copy(update={"schema_fingerprint": "cd" * 32})
    with pytest.raises(lvp.LegacyViewProfileError) as caught:
        lvp.write_profile(repository, changed, replace=False)
    assert caught.value.code == "PROFILE_EXISTS"
    assert lvp.write_profile(repository, changed, replace=True) == "WRITTEN"
    mapping = json.loads((repository / lvp.PROFILE_DIR / "mapping.json").read_text(encoding="utf-8"))
    assert mapping["schema_fingerprint"] == "cd" * 32


def test_write_profile_does_not_report_a_half_replaced_pair_unchanged(repository: Path) -> None:
    catalog = make_catalog()
    assert lvp.write_profile(repository, catalog, replace=False) == "WRITTEN"
    new_fingerprint = "cd" * 32
    _, mapping_bytes = lvp.build_documents(
        new_fingerprint, datetime(2026, 2, 1, tzinfo=timezone.utc)
    )
    # A crash after the mapping replacement leaves the old profile hash behind.
    (repository / lvp.PROFILE_DIR / "mapping.json").write_bytes(mapping_bytes)
    changed = catalog.model_copy(update={"schema_fingerprint": new_fingerprint})
    with pytest.raises(lvp.LegacyViewProfileError) as caught:
        lvp.write_profile(repository, changed, replace=False)
    assert caught.value.code == "PROFILE_EXISTS"
    assert lvp.write_profile(repository, changed, replace=True) == "WRITTEN"
    assert lvp.write_profile(repository, changed, replace=False) == "UNCHANGED"


def test_write_profile_requires_view_columns(repository: Path) -> None:
    missing = {name: kind for name, kind in VIEW_COLUMNS.items() if name != "credit_hours"}
    with pytest.raises(lvp.LegacyViewProfileError) as caught:
        lvp.write_profile(repository, make_catalog(columns=missing), replace=False)
    assert caught.value.code == "VIEW_COLUMN_MISMATCH"
    wrong_type = {**VIEW_COLUMNS, "start_time": "text"}
    with pytest.raises(lvp.LegacyViewProfileError):
        lvp.write_profile(repository, make_catalog(columns=wrong_type), replace=False)
    assert not (repository / lvp.PROFILE_DIR).exists()


def test_cli_requires_live_flag_and_env_without_connecting(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import backend.adaptive.catalog.postgres as catalog_module

    def explode(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("no connection expected")

    monkeypatch.setattr(catalog_module, "PostgresAdapter", explode)
    assert lvp.main(["--smoke"], environ={lvp.DSN_ENV: SECRET_DSN}) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "LIVE_FLAG_REQUIRED"
    assert lvp.main(["--write", "--live"], environ={}) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "ENV_MISSING"
    assert lvp.main(["--check"], environ={}) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["code"] == "ENV_MISSING"
    assert "s3cret" not in output
    # Parse errors never echo arguments (a DSN pasted by mistake stays private).
    assert lvp.main(["--check", SECRET_DSN], environ={}) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["code"] == "INVALID_ARGUMENTS"
    assert "s3cret" not in captured.out + captured.err
    assert lvp.main(["--check", "--smoke"], environ={}) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "INVALID_ARGUMENTS"


def test_smoke_runs_fixed_queries_through_runtime() -> None:
    rows = [{
        "Course.name": "자료구조", "CourseOffering.section": "01", "Instructor.name": "홍길동",
        "Course.credits": Decimal("3"), "Schedule.day": "월", "Schedule.start_time": "13:00",
        "Schedule.end_time": "14:15", "Course.category": "전공(핵심)",
    }]

    class RowExecutor(FakeExecutor):
        def execute(self, database_id: str, compiled: CompiledQuery):
            self.queries.append(compiled)
            columns = list(compiled.expected_columns)
            return columns, [{column: row[column] for column in columns} for row in rows]

    executor = RowExecutor()
    results = lvp.run_smoke(make_context(executor=executor))
    assert [item["status"] for item in results] == ["OK"] * len(lvp.SMOKE_QUERIES)
    assert results[-1]["failure_code"] == "UNKNOWN_FIELD"
    # the unsupported query never reaches the executor
    assert len(executor.queries) == len(lvp.SMOKE_QUERIES) - 1
    text = json.dumps(results, ensure_ascii=False)
    assert "자료구조" not in text and "credit_hours" not in text
