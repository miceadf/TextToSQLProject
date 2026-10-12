"""Fixed test-query preview on a published, active private mapping (Phase 8-04).

Surface on the loopback synthetic prototype:

- ``GET /api/adaptive/v1/registrations/test-queries`` lists the fixed set.
- ``GET /api/adaptive/v1/registrations/test-query?database_id=..&query_id=..``
  runs one fixed QueryIR through the existing validation, compiler, AST
  policy and read-only executor against the strictly reloaded activation.

The browser chooses only a query ID; it cannot send SQL, QueryIR or filter
values. Responses carry semantic column names, at most five sorted rows, a
row count and a result hash; never SQL, physical names, paths or tracebacks.

Phase 8-05 adds verified baselines (server-computed hashes an admin confirms)
and a regression run of the same fixed queries on the active version or on a
recorded, non-active published version before re-activation. Regression is a
warning only; it never publishes, activates or blocks anything.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
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
from .review_publish import _parse_query_params, _parse_strict_body, _read_limited_body


TEST_QUERY_SET_VERSION = "admin-test-queries-v1"
TEST_QUERY_RESULT_VERSION = "admin-test-query-result-v1"
TEST_QUERIES_PATH = "/api/adaptive/v1/registrations/test-queries"
TEST_QUERY_PATH = "/api/adaptive/v1/registrations/test-query"
VERIFIED_QUERIES_PATH = "/api/adaptive/v1/registrations/verified-queries"
REGRESSION_PATH = "/api/adaptive/v1/registrations/regression"
REGRESSION_VERSION = "verified-query-regression-v1"
VERIFIED_LIST_VERSION = "verified-queries-v1"
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
    CONFLICT = "ADMIN_TEST_QUERY_CONFLICT"
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
        AdminTestQueryErrorCode.CONFLICT: 409,
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
            for name, field in domain.active_fields.items()
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


def _map_review_error(exc: Exception) -> AdminTestQueryError:
    if isinstance(exc, review_storage.ReviewError):
        if exc.code is review_storage.ReviewErrorCode.INVALID_INPUT:
            return AdminTestQueryError(AdminTestQueryErrorCode.INVALID_REQUEST)
        if exc.code in (
            review_storage.ReviewErrorCode.ARTIFACT_MISMATCH,
            review_storage.ReviewErrorCode.PUBLISH_BLOCKED,
        ):
            return AdminTestQueryError(AdminTestQueryErrorCode.NOT_FOUND)
        if exc.code is review_storage.ReviewErrorCode.REVIEW_CONFLICT:
            return AdminTestQueryError(AdminTestQueryErrorCode.CONFLICT)
    # Storage and private-root faults are operational, not "no such database".
    return AdminTestQueryError(AdminTestQueryErrorCode.INTERNAL_ERROR)


def _execute(loaded: Any, database_id: str, query_id: str, *, context: Any = None) -> dict[str, Any]:
    """Run one fixed query on an already strictly loaded published mapping."""
    definition = TEST_QUERIES.get(query_id)
    if definition is None:
        raise AdminTestQueryError(AdminTestQueryErrorCode.INVALID_REQUEST)
    columns = list(definition["select"])
    omitted = []
    if context is not None:
        omitted = [name for name in columns if name in context.unsupported_fields()]
        columns = [name for name in columns if name not in omitted]
        if not columns:
            raise AdminTestQueryError(AdminTestQueryErrorCode.FAILED, "TEST_QUERY_UNSUPPORTED")
    try:
        query_ir = QueryIR.model_validate({
            "entity": definition["entity"],
            "select": columns,
            "filters": [],
            "limit": MAX_RESULT_ROWS,
        })
        if context is None:
            graph = build_physical_relation_graph(loaded.catalog)
            compiler = SchemaAdaptiveSQLiteCompiler(graph, loaded.profile)
            policy = SchemaAdaptiveAstPolicy(graph, loaded.profile)
            executor = SQLiteReadOnlyExecutor({database_id: loaded.database_path})
            mapping = to_harness_mapping(loaded.profile)
        else:
            from .postgres_runtime import DatabaseRoutingExecutor
            compiler = context.compiler()
            policy = context.policy()
            executor = DatabaseRoutingExecutor(context.executor(), {database_id: context})
            mapping = context.harness_mapping()
        harness = ExecutionHarness(
            model=FakeModel({query_id: query_ir.model_dump(mode="json")}),
            compiler=compiler, sql_policy=policy, executor=executor,
        )
        result = harness.run(
            HarnessRequest(
                request_id=f"admin-test-query:{query_id}",
                database_id=database_id,
                question=query_id,
            ),
            _runtime_ontology(),
            mapping,
        )
    except Exception:
        raise AdminTestQueryError(AdminTestQueryErrorCode.INTERNAL_ERROR) from None
    if result.status is not HarnessStatus.SUCCEEDED:
        code = result.error.code.value if result.error is not None else "HARNESS_FAILURE"
        raise AdminTestQueryError(AdminTestQueryErrorCode.FAILED, str(code))
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
        "mapping_version": int(context.published_mapping().version if context is not None else loaded.mapping_version),
        "query_id": query_id,
        "columns": columns,
        "rows": rows[:MAX_PREVIEW_ROWS],
        "row_count": len(rows),
        "row_count_capped": len(rows) >= MAX_RESULT_ROWS,
        "result_sha256": digest,
        **({"omitted_fields": omitted} if context is not None else {}),
    }


def run_test_query(repository_root: str | Path, database_id: str, query_id: str, *, postgres_service: Any = None) -> dict[str, Any]:
    if query_id not in TEST_QUERIES:
        raise AdminTestQueryError(AdminTestQueryErrorCode.INVALID_REQUEST)
    if postgres_service is not None and postgres_service.handles(Path(repository_root), database_id):
        context = postgres_service.test_query_context(Path(repository_root), database_id)
        return _execute(None, database_id, query_id, context=context)
    try:
        activation = review_storage.load_activation(
            repository_root=repository_root, database_id=database_id
        )
    except Exception as exc:
        raise _map_review_error(exc) from None
    return _execute(activation, database_id, query_id)


def _baseline_payload(record: Any) -> list[dict[str, Any]]:
    if record is None:
        return []
    return [item.model_dump(mode="json") for item in record.entries]


def verify_test_query(
    repository_root: str | Path, database_id: str, query_id: str, verified_by: str, *, postgres_service: Any = None
) -> dict[str, Any]:
    """Record the server-computed result of the active version as the baseline."""
    result = run_test_query(repository_root, database_id, query_id, postgres_service=postgres_service)
    try:
        entry = review_storage.VerifiedQueryEntry(
            query_id=query_id,
            mapping_version=int(result["mapping_version"]),
            result_sha256=str(result["result_sha256"]),
            row_count=int(result["row_count"]),
            verified_by=review_storage.validate_reviewer(verified_by),
            verified_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    except review_storage.ReviewError:
        raise AdminTestQueryError(AdminTestQueryErrorCode.INVALID_REQUEST) from None
    except Exception:
        raise AdminTestQueryError(AdminTestQueryErrorCode.INTERNAL_ERROR) from None
    try:
        if postgres_service is not None and postgres_service.handles(Path(repository_root), database_id):
            record = postgres_service.record_verified_query(Path(repository_root), database_id, entry)
        else:
            record = review_storage.record_verified_query(
                repository_root=repository_root, database_id=database_id, entry=entry
            )
    except AdminTestQueryError:
        raise
    except Exception as exc:
        raise _map_review_error(exc) from None
    return {
        "version": VERIFIED_LIST_VERSION,
        "database_id": database_id,
        "entries": _baseline_payload(record),
    }


def run_regression(repository_root: str | Path, database_id: str, mapping_version: int, *, postgres_service: Any = None) -> dict[str, Any]:
    """Compare every fixed query on one published version with its baseline."""
    context = None
    if postgres_service is not None and postgres_service.handles(Path(repository_root), database_id):
        context = postgres_service.test_query_context(Path(repository_root), database_id)
        active = context.published_mapping().version
        if type(mapping_version) is not int or mapping_version != active:
            raise AdminTestQueryError(AdminTestQueryErrorCode.FAILED, "POSTGRES_CURRENT_VERSION_ONLY")
        target = None
        baselines = postgres_service.load_verified_queries(Path(repository_root), database_id)
    else:
        try:
            target = review_storage.load_published_version(
                repository_root=repository_root, database_id=database_id, mapping_version=mapping_version
            )
        except Exception as exc:
            raise _map_review_error(exc) from None
        try:
            active: int | None = review_storage.load_activation(
                repository_root=repository_root, database_id=database_id
            ).mapping_version
        except Exception:
            active = None
        baselines = review_storage.load_verified_queries(repository_root=repository_root, database_id=database_id)
    by_query = {item.query_id: item for item in (baselines.entries if baselines else [])}
    results: list[dict[str, Any]] = []
    for query_id in TEST_QUERIES:
        baseline = by_query.get(query_id)
        entry: dict[str, Any] = {
            "query_id": query_id,
            "baseline_mapping_version": baseline.mapping_version if baseline else None,
            "baseline_row_count": baseline.row_count if baseline else None,
            "row_count": None,
            "failure_code": None,
        }
        try:
            outcome = _execute(target, database_id, query_id, context=context)
        except AdminTestQueryError as exc:
            if exc.code is not AdminTestQueryErrorCode.FAILED:
                raise
            entry["status"] = "FAILED"
            entry["failure_code"] = exc.failure_code
            results.append(entry)
            continue
        entry["row_count"] = int(outcome["row_count"])
        if baseline is None:
            entry["status"] = "UNVERIFIED"
        elif baseline.result_sha256 == outcome["result_sha256"]:
            entry["status"] = "MATCH"
        else:
            entry["status"] = "CHANGED"
        results.append(entry)
    summary = {
        status.lower(): sum(item["status"] == status for item in results)
        for status in ("MATCH", "CHANGED", "FAILED", "UNVERIFIED")
    }
    return {
        "version": REGRESSION_VERSION,
        "database_id": database_id,
        "mapping_version": int(active if context is not None else target.mapping_version),
        "active_mapping_version": active,
        "summary": summary,
        "results": results,
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

    @app.post(VERIFIED_QUERIES_PATH)
    async def verify_route(http_request: Request) -> JSONResponse:
        try:
            raw = await _read_limited_body(http_request)
            payload = _parse_strict_body(bytes(raw)) if isinstance(raw, (bytes, bytearray)) and raw else None
            keys = {"action", "database_id", "query_id", "verified_by"}
            if (
                payload is None
                or set(payload) != keys
                or payload.get("action") != "VERIFY"
                or not all(isinstance(payload[key], str) for key in keys)
            ):
                return _error(AdminTestQueryError(AdminTestQueryErrorCode.INVALID_REQUEST))
            try:
                root = Path(str(http_request.app.state.mapping_review_repository_root))
            except Exception:
                return _error(AdminTestQueryError(AdminTestQueryErrorCode.INTERNAL_ERROR))
            try:
                body = verify_test_query(
                    root, payload["database_id"], payload["query_id"], payload["verified_by"],
                    postgres_service=getattr(app.state, "postgres_registration_service", None)
                )
            except AdminTestQueryError as exc:
                return _error(exc)
            text = json.dumps(body, ensure_ascii=False, sort_keys=True, allow_nan=False)
            return JSONResponse(status_code=200, content=json.loads(text))
        except Exception:
            return _error(AdminTestQueryError(AdminTestQueryErrorCode.INTERNAL_ERROR))

    @app.get(REGRESSION_PATH)
    async def regression_route(http_request: Request) -> JSONResponse:
        try:
            params = _parse_query_params(http_request)
            if params is None or set(params) != {"database_id", "mapping_version"}:
                return _error(AdminTestQueryError(AdminTestQueryErrorCode.INVALID_REQUEST))
            version_text = params["mapping_version"]
            if not version_text.isascii() or not version_text.isdigit() or len(version_text) > 4:
                return _error(AdminTestQueryError(AdminTestQueryErrorCode.INVALID_REQUEST))
            try:
                root = Path(str(http_request.app.state.mapping_review_repository_root))
            except Exception:
                return _error(AdminTestQueryError(AdminTestQueryErrorCode.INTERNAL_ERROR))
            try:
                body = run_regression(root, params["database_id"], int(version_text), postgres_service=getattr(app.state, "postgres_registration_service", None))
            except AdminTestQueryError as exc:
                return _error(exc)
            text = json.dumps(body, ensure_ascii=False, sort_keys=True, allow_nan=False)
            return JSONResponse(status_code=200, content=json.loads(text))
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
                payload = run_test_query(root, params["database_id"], params["query_id"], postgres_service=getattr(app.state, "postgres_registration_service", None))
            except AdminTestQueryError as exc:
                return _error(exc)
            text = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
            return JSONResponse(status_code=200, content=json.loads(text))
        except Exception:
            return _error(AdminTestQueryError(AdminTestQueryErrorCode.INTERNAL_ERROR))
