"""Phase 9-36: private catalog compiler and policy contract."""

from __future__ import annotations

import pytest

from backend.adaptive.api.postgres_runtime import build_validated_contexts
from backend.adaptive.compiler.contracts import MemberFilter
from backend.adaptive.harness.contracts import HarnessErrorCode, QueryIR
from backend.adaptive.harness.contracts import FilterIR
from backend.adaptive.ontology.contracts import Operator
from backend.adaptive.harness.errors import HarnessFailure
from tests.adaptive.test_postgres_runtime_registry import (
    DATABASE_ID, FakeAdapter, FakeExecutor, make_catalog, make_profile,
)


def _context():
    return build_validated_contexts(
        [make_profile()], adapters_by_id={DATABASE_ID: FakeAdapter(make_catalog())},
        executors_by_id={DATABASE_ID: FakeExecutor()}, expected_ontology_version=1,
    )[DATABASE_ID]


def test_catalog_compilation_and_default_compile_are_separate():
    context = _context()
    mapping = context.harness_mapping()
    ir = QueryIR(entity="Course", select=["Course.code", "Course.name"], limit=20)
    baseline = context.compiler().compile(ir, mapping)
    assert baseline.sql == 'SELECT "t0"."course_code" AS "Course.code", "t0"."course_name" AS "Course.name" FROM "courses" AS "t0" LIMIT %s'
    assert list(baseline.parameters) == [20]
    assert context.compiler().compile(ir, mapping).sql == baseline.sql
    filtered_ir = QueryIR(
        entity="Course", select=["Course.name"],
        filters=[FilterIR(field="Course.category", operator=Operator.EQUALS, value="Major")],
        limit=7,
    )
    filtered = context.compiler().compile(filtered_ir, mapping)
    assert filtered.sql == 'SELECT "t0"."course_name" AS "Course.name" FROM "courses" AS "t0" WHERE "t0"."category" = %s LIMIT %s'
    assert list(filtered.parameters) == ["Major", 7]

    catalog_query = context.compile_catalog(
        ir, mapping, member_filters=(MemberFilter(field="Course.code", values=("CSE101", "CSE102")),), distinct=True,
    )
    assert catalog_query.sql.startswith('SELECT DISTINCT ')
    assert 'WHERE "t0"."course_code" IN (%s, %s) LIMIT %s' in catalog_query.sql
    assert list(catalog_query.parameters) == ["CSE101", "CSE102", 20]
    context.validate_catalog(catalog_query, query_ir=ir, mapping=mapping,
                             member_filters=(MemberFilter(field="Course.code", values=("CSE101", "CSE102")),), distinct=True)

    with pytest.raises(HarnessFailure):
        context.policy().validate(catalog_query, query_ir=ir, mapping=mapping)
    forged_in = catalog_query.model_copy(update={"sql": catalog_query.sql.replace(" LIMIT", " AND \"t0\".\"course_code\" IN (%s) LIMIT"), "parameters": ("CSE101", "CSE102", "CSE101", 20)})
    with pytest.raises(HarnessFailure):
        context.policy().validate(forged_in, query_ir=ir, mapping=mapping)


def test_member_filter_count_and_invalid_values_fail_closed():
    context = _context()
    mapping = context.harness_mapping()
    ir = QueryIR(entity="Course", select=["Course.code"], limit=10)
    valid = (MemberFilter(field="Course.code", values=("CSE101", "CSE102")),)
    compiled = context.compile_catalog(ir, mapping, member_filters=valid, distinct=True)
    mismatch = compiled.model_copy(update={"sql": compiled.sql.replace("%s, %s", "%s"), "parameters": ("CSE101", "CSE102", 10)})
    with pytest.raises(HarnessFailure) as error:
        context.validate_catalog(mismatch, query_ir=ir, mapping=mapping, member_filters=valid, distinct=True)
    assert error.value.code is HarnessErrorCode.INVALID_PLACEHOLDER

    with pytest.raises(HarnessFailure):
        context.compile_catalog(ir, mapping, member_filters=({"field": "Course.code", "values": tuple(str(i) for i in range(51))},), distinct=False)
    with pytest.raises(ValueError):
        MemberFilter(field="Course.code", values=("CSE101", "CSE101"))
    with pytest.raises(HarnessFailure):
        context.compile_catalog(ir, mapping, member_filters=({"field": "Course.code", "values": ("CSE101", "CSE101")},), distinct=False)
    with pytest.raises(HarnessFailure):
        context.compile_catalog(ir, mapping, member_filters=(MemberFilter(field="Unknown.field", values=("x",)),), distinct=False)
