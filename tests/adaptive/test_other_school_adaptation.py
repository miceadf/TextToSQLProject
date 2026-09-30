"""Phase 8-17: defects the second-school check found, fixed for any database."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from backend.adaptive.api.postgres_runtime import PostgresRuntimeError, PostgresRuntimeProfile
from backend.adaptive.catalog.postgres import PostgresConnectionConfig
from backend.adaptive.mapping.contracts import PublishedFieldMapping, PublishedMappingProfile
from backend.adaptive.mapping.transforms import (
    FieldTransform,
    column_type_supports,
    normalize_filter_value,
    render_postgres_transform,
    transform_operators,
)
from backend.adaptive.ontology import DataType, Operator
from backend.adaptive.registration import postgres_registrations as pg
from backend.adaptive.relations.contracts import JoinPlanStatus
from backend.adaptive.relations.physical import build_physical_relation_graph
from backend.adaptive.relations.planner import plan_join_path
from tests.adaptive.test_anchor_left_join import (
    DATABASE_ID,
    FINGERPRINT,
    SECRET_DSN,
    FakeExecutor,
    compile_and_validate,
    make_catalog,
    make_context,
    query_ir,
)

TEXT = "character varying(40)"

# Professors belong to departments too: two FK routes reach ``dept`` from the
# offering (through the course, or through the professor).
TABLES: dict[str, dict[str, Any]] = {
    "offering": {
        "columns": {"offering_id": "integer", "course_no": TEXT, "prof_no": "integer", "sec": TEXT},
        "pk": ["offering_id"],
        "fks": [(0, "course_no", "course", "course_no"), (1, "prof_no", "prof", "prof_no")],
    },
    "course": {
        "columns": {"course_no": TEXT, "title": TEXT, "dept_no": TEXT},
        "pk": ["course_no"],
        "fks": [(0, "dept_no", "dept", "dept_no")],
    },
    "prof": {
        "columns": {"prof_no": "integer", "prof_title": TEXT, "dept_no": TEXT},
        "pk": ["prof_no"],
        "fks": [(0, "dept_no", "dept", "dept_no")],
    },
    "dept": {"columns": {"dept_no": TEXT, "dept_title": TEXT}, "pk": ["dept_no"], "fks": []},
    "timetable": {
        "columns": {"slot_id": "integer", "offering_id": "integer", "starts": "character(4)"},
        "pk": ["slot_id"],
        "fks": [(0, "offering_id", "offering", "offering_id")],
    },
}
FIELDS = {
    "CourseOffering.section": ("offering", "sec"),
    "Course.code": ("course", "course_no"),
    "Course.name": ("course", "title"),
    "Instructor.name": ("prof", "prof_title"),
    "Department.name": ("dept", "dept_title"),
    "Schedule.start_time": ("timetable", "starts"),
}


def published(fields: dict[str, tuple[str, str]] = FIELDS) -> PublishedMappingProfile:
    return PublishedMappingProfile(
        database_id=DATABASE_ID,
        version=1,
        schema_fingerprint=FINGERPRINT,
        ontology_version=1,
        published_by="synthetic-test",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        fields={
            name: PublishedFieldMapping(table=table, column=column, reviewer="synthetic-test")
            for name, (table, column) in fields.items()
        },
    )


GRAPH = build_physical_relation_graph(make_catalog(TABLES))


# --- join planning ---------------------------------------------------------------------------


def test_the_course_department_wins_over_the_professor_department() -> None:
    result = plan_join_path(GRAPH, published(), sorted(FIELDS), anchor_table="offering")
    assert result.status is JoinPlanStatus.RESOLVED
    joined = {(step.traversal_from, step.traversal_to) for step in result.plan.steps}
    assert ("course", "dept") in joined and ("prof", "dept") not in joined


def test_plans_through_unmapped_tables_stay_ambiguous() -> None:
    # Without Instructor.name the professor table holds no mapped entity:
    # its FK is not confirmed, and neither plan is preferred over the other
    # unless the other is fully confirmed.
    tables = {name: dict(spec) for name, spec in TABLES.items()}
    tables["link"] = {
        "columns": {"course_no": TEXT, "dept_no": TEXT},
        "pk": ["course_no", "dept_no"],
        "fks": [(0, "course_no", "course", "course_no"), (1, "dept_no", "dept", "dept_no")],
    }
    tables["course"] = {**TABLES["course"], "fks": []}
    graph = build_physical_relation_graph(make_catalog(tables))
    fields = {name: target for name, target in FIELDS.items() if name != "Instructor.name"}
    result = plan_join_path(graph, published(fields), ["Course.name", "Department.name"], anchor_table="offering")
    assert result.status is JoinPlanStatus.AMBIGUOUS


def test_a_single_shortest_plan_is_unchanged() -> None:
    result = plan_join_path(GRAPH, published(), ["Instructor.name", "Department.name"], anchor_table="offering")
    # Two edges through the professor beat three through the course: the
    # preference only breaks ties, it never lengthens a plan.
    assert result.status is JoinPlanStatus.RESOLVED
    assert [step.traversal_to for step in result.plan.steps] == ["prof", "dept"]


def profile(transforms: dict[str, FieldTransform] | None = None) -> PostgresRuntimeProfile:
    canonical = published()
    return PostgresRuntimeProfile(
        database_id=DATABASE_ID,
        label="Two Routes",
        connection_config=PostgresConnectionConfig(SECRET_DSN),
        published_mapping=canonical,
        field_transforms=transforms or {},
        unsupported_fields=sorted(_all_fields() - set(FIELDS)),
        anchor_table="offering",
    )


def _all_fields() -> set[str]:
    from backend.adaptive.ontology import load_default_ontology

    return set(load_default_ontology().fields_at(1))  # the mapping below is ontology version 1


def test_the_runtime_now_accepts_two_department_routes() -> None:
    context = make_context(profile(), catalog=make_catalog(TABLES))
    compiled = compile_and_validate(context, query_ir(["Course.name", "Department.name", "Instructor.name"]))
    assert compiled.sql == (
        'SELECT "t1"."title" AS "Course.name", "t2"."dept_title" AS "Department.name", '
        '"t3"."prof_title" AS "Instructor.name" FROM "offering" AS "t0" '
        'LEFT JOIN "course" AS "t1" ON "t0"."course_no" = "t1"."course_no" '
        'LEFT JOIN "dept" AS "t2" ON "t1"."dept_no" = "t2"."dept_no" '
        'LEFT JOIN "prof" AS "t3" ON "t0"."prof_no" = "t3"."prof_no" LIMIT %s'
    )


# --- four-digit text times -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "fits"),
    [(["0900", "1330", "2359"], True), (["900"], False), (["09:00"], False), (["2400"], False), (["1260"], False), ([], False)],
)
def test_hhmm_text_fits_only_four_digit_times(values: list[str], fits: bool) -> None:
    assert pg.transform_fits(FieldTransform.HHMM_TEXT_TO_HHMM, "character(4)", DataType.TIME, values) is fits


def test_hhmm_text_rules() -> None:
    transform = FieldTransform.HHMM_TEXT_TO_HHMM
    assert column_type_supports(transform, "character(4)") and column_type_supports(transform, "text")
    assert not column_type_supports(transform, "time without time zone") and not column_type_supports(transform, "integer")
    assert render_postgres_transform(transform, '"t5"."si_st"') == (
        '(SUBSTRING("t5"."si_st" FROM 1 FOR 2) || \':\' || SUBSTRING("t5"."si_st" FROM 3 FOR 2))'
    )
    assert normalize_filter_value(transform, "12:00:00") == "12:00"
    assert normalize_filter_value(transform, "9:05") == "09:05"
    with pytest.raises(ValueError):
        normalize_filter_value(transform, "오전")
    assert Operator.LESS_THAN in transform_operators(transform) and Operator.CONTAINS not in transform_operators(transform)


def test_publish_picks_the_hhmm_transform_for_text_times() -> None:
    catalog = make_catalog(TABLES)
    mapped = {name: PublishedFieldMapping(table=t, column=c, reviewer="r") for name, (t, c) in FIELDS.items()}
    chosen = pg.decide_transforms(mapped, catalog, lambda _t, _c: ["0900", "1230"])
    assert chosen == {"Schedule.start_time": FieldTransform.HHMM_TEXT_TO_HHMM}
    assert pg.decide_transforms(mapped, catalog, lambda _t, _c: ["0900", "9:30"]) == {}


def test_a_text_time_is_compared_as_hh_mm() -> None:
    transforms = {"Schedule.start_time": FieldTransform.HHMM_TEXT_TO_HHMM}
    context = make_context(profile(transforms), catalog=make_catalog(TABLES), executor=FakeExecutor())
    ir = query_ir(["Course.name"], [{"field": "Schedule.start_time", "operator": "less_than", "value": "12:00:00"}])
    compiled = compile_and_validate(context, ir)
    assert "SUBSTRING(" in compiled.sql and compiled.parameters[0] == "12:00"
    # "1230" < "12:00" as raw text; as HH:MM, 12:30 is not before noon.
    assert "1230" < "12:00:00" and not "12:30" < "12:00"


def test_the_runtime_refuses_hhmm_on_a_time_column() -> None:
    tables = {name: dict(spec) for name, spec in TABLES.items()}
    tables["timetable"] = {**TABLES["timetable"], "columns": {**TABLES["timetable"]["columns"], "starts": "time"}}
    with pytest.raises(PostgresRuntimeError):
        make_context(profile({"Schedule.start_time": FieldTransform.HHMM_TEXT_TO_HHMM}), catalog=make_catalog(tables))
