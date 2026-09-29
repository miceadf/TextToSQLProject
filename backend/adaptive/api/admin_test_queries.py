"""Fixed test-query preview on a published, active private mapping (Phase 8-04).

Surface on the loopback synthetic prototype:

- ``GET /api/adaptive/v1/registrations/test-queries`` lists the fixed set.
- ``GET /api/adaptive/v1/registrations/test-query?database_id=..&query_id=..``
  runs one fixed QueryIR through the existing validation, compiler, AST
  policy and read-only executor against the strictly reloaded activation.

The browser chooses only a query ID; it cannot send SQL, QueryIR or filter
values. Responses carry semantic column names, at most five sorted rows, a
row count and a result hash; never SQL, physical names, paths or tracebacks.
"""

from __future__ import annotations

import hashlib
import json
import math
from enum import Enum
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..compiler import SchemaAdaptiveSQLiteCompiler
from ..harness.contracts import (
    HarnessRequest,
    HarnessStatus,
    Ontology,
    OntologyField,
    QueryIR,
)
from ..harness.engine import ExecutionHarness
from ..harness.executor import SQLiteReadOnlyExecutor
from ..harness.model import FakeModel
from ..mapping import to_harness_mapping
from ..ontology.loader import load_default_ontology
from ..policy import SchemaAdaptiveAstPolicy
from ..registration import reviews as review_storage
from ..relations.physical import build_physical_relation_graph
from .review_publish import _parse_query_params


TEST_QUERY_SET_VERSION = "admin-test-queries-v1"
TEST_QUERY_RESULT_VERSION = "admin-test-query-result-v1"
TEST_QUERIES_PATH = "/api/adaptive/v1/registrations/test-queries"
TEST_QUERY_PATH = "/api/adaptive/v1/registrations/test-query"
MAX_PREVIEW_ROWS = 5
MAX_RESULT_ROWS = 200

# Fixed, server-owned QueryIRs over canonical fields only; no filters. The
# compiler derives the row grain from the selected fields' join tree, so every
# query selects an offering-level field (CourseOffering.section): otherwise a
# course-only select returns 6 rows on a flat table but 5 on a normalized one.
# A fixed grain keeps results comparable across versions and schemas.
TEST_QUERIES: dict[str, dict[str, Any]] = {
    "offerings": {
        "title": "개설강좌 목록",
        "entity": "CourseOffering",
        "select": ["Course.code", "Course.name", "CourseOffering.section", "Instructor.name"],
    },
    "courses": {
        "title": "개설강좌별 과목 정보",
        "entity": "CourseOffering",
        "select": [
            "Course.code", "CourseOffering.section", "Course.name",
            "Course.credits", "Course.category",
        ],
    },
    "departments": {
        "title": "개설강좌별 학과",
        "entity": "CourseOffering",
        "select": ["Course.code", "CourseOffering.section", "Department.code", "Department.name"],
    },
    "schedules": {
        "title": "강의 시간",
        "entity": "CourseOffering",
        "select": [
            "Course.code", "CourseOffering.section", "Schedule.day",
            "Schedule.start_time", "Schedule.end_time",
        ],
    },
    "seats": {
        "title": "정원과 신청 인원",
        "entity": "CourseOffering",
        "select": [
            "Course.code", "CourseOffering.section",
            "CourseOffering.capacity", "CourseOffering.enrolled",
        ],
    },
}


class AdminTestQueryErrorCode(str, Enum):
    INVALID_REQUEST = "ADMIN_TEST_QUERY_INVALID_REQUEST"
    NOT_FOUND = "ADMIN_TEST_QUERY_NOT_FOUND"
    FAILED = "ADMIN_TEST_QUERY_FAILED"
    INTERNAL_ERROR = "ADMIN_TEST_QUERY_INTERNAL_ERROR"


class AdminTestQueryError(Exception):
    def __init__(self, code: AdminTestQueryErrorCode, failure_code: str | None = None) -> None:
        super().__init__(code.value)
        self.code = code
        self.failure_code = failure_code


def _error(exc: AdminTestQueryError) -> JSONResponse:
    status = {
        AdminTestQueryErrorCode.INVALID_REQUEST: 400,
        AdminTestQueryErrorCode.NOT_FOUND: 404,
        AdminTestQueryErrorCode.FAILED: 422,
    }.get(exc.code, 500)
    error: dict[str, Any] = {"code": exc.code.value}
    if exc.failure_code is not None:
        error["failure_code"] = exc.failure_code
    return JSONResponse(status_code=status, content={"error": error})


def _runtime_ontology() -> Ontology:
    domain = load_default_ontology()
    return Ontology(
        entities={entity.name for entity in domain.entities},
        fields={
            name: OntologyField(
                name=name, data_type=field.data_type, allowed_operators=field.allowed_operators
            )
            for name, field in domain.fields.items()
        },
    )


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise AdminTestQueryError(AdminTestQueryErrorCode.FAILED, "RESULT_VALUE_UNSUPPORTED")


def _cell_key(item: Any) -> tuple[int, Any]:
    # NULL first, then booleans, numbers by exact value (no float rounding of
    # large integers), strings.
    if item is None:
        return (0, 0)
    if isinstance(item, bool):
        return (1, int(item))
    if isinstance(item, (int, float)):
        return (2, item)
    return (3, item)


def _sort_key(row: list[Any]) -> tuple[Any, ...]:
    return tuple(_cell_key(item) for item in row)


def _tagged(row: list[Any]) -> list[str]:
    # NULL and the string 'NULL' must hash differently.
    return ["N:" if item is None else f"V:{type(item).__name__}:{item!r}" for item in row]


def _ordered_rows(rows: list[list[Any]]) -> list[list[Any]]:
    """Total order independent of database scan order.

    Rows equal under the value key (1 and 1.0, True and 1) are broken by the
    tagged form, which is exactly what the hash sees.
    """
    return sorted(rows, key=lambda row: (_sort_key(row), _tagged(row)))


def run_test_query(repository_root: str | Path, database_id: str, query_id: str) -> dict[str, Any]:
    definition = TEST_QUERIES.get(query_id)
    if definition is None:
        raise AdminTestQueryError(AdminTestQueryErrorCode.INVALID_REQUEST)
    try:
        activation = review_storage.load_activation(
            repository_root=repository_root, database_id=database_id
        )
    except review_storage.ReviewError as exc:
        if exc.code is review_storage.ReviewErrorCode.INVALID_INPUT:
            raise AdminTestQueryError(AdminTestQueryErrorCode.INVALID_REQUEST) from None
        if exc.code is review_storage.ReviewErrorCode.ARTIFACT_MISMATCH:
            raise AdminTestQueryError(AdminTestQueryErrorCode.NOT_FOUND) from None
        # Storage and private-root faults are operational, not "no such database".
        raise AdminTestQueryError(AdminTestQueryErrorCode.INTERNAL_ERROR) from None
    except Exception:
        raise AdminTestQueryError(AdminTestQueryErrorCode.INTERNAL_ERROR) from None
    try:
        query_ir = QueryIR.model_validate({
            "entity": definition["entity"],
            "select": list(definition["select"]),
            "filters": [],
            "limit": MAX_RESULT_ROWS,
        })
        graph = build_physical_relation_graph(activation.catalog)
        harness = ExecutionHarness(
            model=FakeModel({query_id: query_ir.model_dump(mode="json")}),
            compiler=SchemaAdaptiveSQLiteCompiler(graph, activation.profile),
            sql_policy=SchemaAdaptiveAstPolicy(graph, activation.profile),
            executor=SQLiteReadOnlyExecutor({database_id: activation.database_path}),
        )
        result = harness.run(
            HarnessRequest(
                request_id=f"admin-test-query:{query_id}",
                database_id=database_id,
                question=query_id,
            ),
            _runtime_ontology(),
            to_harness_mapping(activation.profile),
        )
    except Exception:
        raise AdminTestQueryError(AdminTestQueryErrorCode.INTERNAL_ERROR) from None
    if result.status is not HarnessStatus.SUCCEEDED:
        code = result.error.code.value if result.error is not None else "HARNESS_FAILURE"
        raise AdminTestQueryError(AdminTestQueryErrorCode.FAILED, str(code))
    columns = list(definition["select"])
    if list(result.columns) != columns:
        raise AdminTestQueryError(AdminTestQueryErrorCode.FAILED, "RESULT_SHAPE_MISMATCH")
    rows: list[list[Any]] = []
    for row in result.rows:
        if not isinstance(row, dict) or set(row) != set(columns):
            raise AdminTestQueryError(AdminTestQueryErrorCode.FAILED, "RESULT_SHAPE_MISMATCH")
        rows.append([_scalar(row[name]) for name in columns])
    rows = _ordered_rows(rows)
    digest = hashlib.sha256(
        json.dumps(
            {"columns": columns, "rows": [_tagged(row) for row in rows]},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "version": TEST_QUERY_RESULT_VERSION,
        "database_id": database_id,
        "mapping_version": int(activation.mapping_version),
        "query_id": query_id,
        "columns": columns,
        "rows": rows[:MAX_PREVIEW_ROWS],
        "row_count": len(rows),
        "row_count_capped": len(rows) >= MAX_RESULT_ROWS,
        "result_sha256": digest,
    }


def register_admin_test_query_routes(app: FastAPI) -> None:
    """Uses the repository root the mapping review routes already stored."""

    @app.get(TEST_QUERIES_PATH)
    async def list_test_queries(http_request: Request) -> JSONResponse:
        try:
            if http_request.url.query:
                return _error(AdminTestQueryError(AdminTestQueryErrorCode.INVALID_REQUEST))
            return JSONResponse(status_code=200, content={
                "version": TEST_QUERY_SET_VERSION,
                "queries": [
                    {
                        "query_id": query_id,
                        "title": definition["title"],
                        "entity": definition["entity"],
                        "select": list(definition["select"]),
                    }
                    for query_id, definition in TEST_QUERIES.items()
                ],
            })
        except Exception:
            return _error(AdminTestQueryError(AdminTestQueryErrorCode.INTERNAL_ERROR))

    @app.get(TEST_QUERY_PATH)
    async def run_test_query_route(http_request: Request) -> JSONResponse:
        try:
            params = _parse_query_params(http_request)
            if params is None or set(params) != {"database_id", "query_id"}:
                return _error(AdminTestQueryError(AdminTestQueryErrorCode.INVALID_REQUEST))
            try:
                root = Path(str(http_request.app.state.mapping_review_repository_root))
            except Exception:
                return _error(AdminTestQueryError(AdminTestQueryErrorCode.INTERNAL_ERROR))
            try:
                payload = run_test_query(root, params["database_id"], params["query_id"])
            except AdminTestQueryError as exc:
                return _error(exc)
            text = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
            return JSONResponse(status_code=200, content=json.loads(text))
        except Exception:
            return _error(AdminTestQueryError(AdminTestQueryErrorCode.INTERNAL_ERROR))
