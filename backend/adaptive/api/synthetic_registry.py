"""Synthetic active-database registry and canonical course catalog (synthetic-only).

Phase 6-16 server-owned registry for the clarification prototype:

- exactly the server registers the synthetic databases (flat + legacy);
- callers cannot inject file paths, mappings, SQL, or physical identifiers;
- the catalog reads through the existing read-only executor plus the
  Published mapping / safe compiler path and converts rows to one
  canonical course DTO defined here;
- unknown databases, mapping/schema drift, and execution failures close
  with fixed redacted error codes only.

No legacy import, no PostgreSQL, no Ollama, no .env, no external network,
no stdout/logs. Nothing here reflects request text, paths, SQL, rows, or
exception text.
"""

from __future__ import annotations

import copy
import json
import re
from enum import Enum
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator


SYNTHETIC_REGISTRY_VERSION = "synthetic-database-registry-v1"
SYNTHETIC_CATALOG_VERSION = "synthetic-course-catalog-v1"

DATABASES_PATH = "/api/adaptive/v1/databases"
CATALOG_PATH = "/api/adaptive/v1/courses"

MAX_DATABASES = 8
MAX_ITEMS = 200
MAX_KEYWORD_LENGTH = 100
MAX_LABEL_LENGTH = 64

_DB_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _().-]{0,63}$")
_SCHEMA_VALUES = frozenset({"flat", "legacy"})

# Canonical semantic refs (server-owned, never caller-supplied).
_COURSE_CODE_REF = "Course.code"
_COURSE_NAME_REF = "Course.name"
_COURSE_CREDITS_REF = "Course.credits"
_COURSE_CATEGORY_REF = "Course.category"
_DEPT_CODE_REF = "Department.code"
_DEPT_NAME_REF = "Department.name"
_INSTRUCTOR_NAME_REF = "Instructor.name"
_OFFERING_YEAR_REF = "CourseOffering.year"
_OFFERING_SEMESTER_REF = "CourseOffering.semester"
_OFFERING_SECTION_REF = "CourseOffering.section"
_OFFERING_CAPACITY_REF = "CourseOffering.capacity"
_OFFERING_ENROLLED_REF = "CourseOffering.enrolled"
_SCHEDULE_DAY_REF = "Schedule.day"
_SCHEDULE_START_REF = "Schedule.start_time"
_SCHEDULE_END_REF = "Schedule.end_time"

CANONICAL_SELECT = (
    _COURSE_CODE_REF,
    _COURSE_NAME_REF,
    _COURSE_CREDITS_REF,
    _COURSE_CATEGORY_REF,
    _DEPT_CODE_REF,
    _DEPT_NAME_REF,
    _INSTRUCTOR_NAME_REF,
    _OFFERING_YEAR_REF,
    _OFFERING_SEMESTER_REF,
    _OFFERING_SECTION_REF,
    _OFFERING_CAPACITY_REF,
    _OFFERING_ENROLLED_REF,
    _SCHEDULE_DAY_REF,
    _SCHEDULE_START_REF,
    _SCHEDULE_END_REF,
)

# semantic ref -> canonical DTO key (single centralized mapping).
_REF_TO_DTO_KEY = {
    _COURSE_CODE_REF: "course_code",
    _COURSE_NAME_REF: "course_name",
    _COURSE_CREDITS_REF: "credits",
    _COURSE_CATEGORY_REF: "category",
    _DEPT_CODE_REF: "department_code",
    _DEPT_NAME_REF: "department_name",
    _INSTRUCTOR_NAME_REF: "instructor_name",
    _OFFERING_YEAR_REF: "year",
    _OFFERING_SEMESTER_REF: "semester",
    _OFFERING_SECTION_REF: "section",
    _OFFERING_CAPACITY_REF: "capacity",
    _OFFERING_ENROLLED_REF: "enrolled",
    _SCHEDULE_DAY_REF: "day",
    _SCHEDULE_START_REF: "start_time",
    _SCHEDULE_END_REF: "end_time",
}

_FLAT_COLUMNS = {
    _COURSE_CODE_REF: ("course_offerings", "course_code"),
    _COURSE_NAME_REF: ("course_offerings", "course_name"),
    _COURSE_CREDITS_REF: ("course_offerings", "credits"),
    _COURSE_CATEGORY_REF: ("course_offerings", "category"),
    _DEPT_CODE_REF: ("course_offerings", "department_code"),
    _DEPT_NAME_REF: ("course_offerings", "department_name"),
    _INSTRUCTOR_NAME_REF: ("course_offerings", "instructor_name"),
    _OFFERING_YEAR_REF: ("course_offerings", "year"),
    _OFFERING_SEMESTER_REF: ("course_offerings", "semester"),
    _OFFERING_SECTION_REF: ("course_offerings", "section"),
    _OFFERING_CAPACITY_REF: ("course_offerings", "capacity"),
    _OFFERING_ENROLLED_REF: ("course_offerings", "enrolled"),
    _SCHEDULE_DAY_REF: ("course_offerings", "day"),
    _SCHEDULE_START_REF: ("course_offerings", "start_time"),
    _SCHEDULE_END_REF: ("course_offerings", "end_time"),
}

_LEGACY_COLUMNS = {
    _COURSE_CODE_REF: ("lecture_info", "sbjt_cd"),
    _COURSE_NAME_REF: ("lecture_info", "sbjt_nm"),
    _COURSE_CREDITS_REF: ("lecture_info", "pnt"),
    _COURSE_CATEGORY_REF: ("lecture_info", "isu_gb"),
    _DEPT_CODE_REF: ("lecture_info", "dept_cd"),
    _DEPT_NAME_REF: ("lecture_info", "dept_nm"),
    _INSTRUCTOR_NAME_REF: ("lecture_info", "prof_nm"),
    _OFFERING_YEAR_REF: ("lecture_info", "yyyy"),
    _OFFERING_SEMESTER_REF: ("lecture_info", "hakgi"),
    _OFFERING_SECTION_REF: ("lecture_info", "bunban"),
    _OFFERING_CAPACITY_REF: ("lecture_info", "limit_count"),
    _OFFERING_ENROLLED_REF: ("lecture_info", "applicant_count"),
    _SCHEDULE_DAY_REF: ("lecture_info", "lecture_day"),
    _SCHEDULE_START_REF: ("lecture_info", "begin_time"),
    _SCHEDULE_END_REF: ("lecture_info", "finish_time"),
}


class SyntheticCatalogErrorCode(str, Enum):
    INVALID_REQUEST = "SYNTHETIC_CATALOG_INVALID_REQUEST"
    CONTEXT_UNAVAILABLE = "SYNTHETIC_CATALOG_CONTEXT_UNAVAILABLE"
    INTERNAL_ERROR = "SYNTHETIC_CATALOG_INTERNAL_ERROR"


class SyntheticCatalogConfigError(Exception):
    def __init__(self) -> None:
        super().__init__(SyntheticCatalogErrorCode.INTERNAL_ERROR.value)


class DatabaseEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, populate_by_name=True)

    database_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    label: str = Field(min_length=1, max_length=64)
    schema_variant: str = Field(alias="schema", pattern=r"^(flat|legacy)$")


class CanonicalCourse(BaseModel):
    """Single centralized canonical course DTO."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    course_code: str = Field(min_length=1, max_length=32)
    course_name: str = Field(min_length=1, max_length=200)
    credits: float = Field(gt=0, le=10)
    category: str = Field(min_length=1, max_length=32)
    department_code: str = Field(min_length=1, max_length=32)
    department_name: str = Field(min_length=1, max_length=200)
    instructor_name: str = Field(min_length=1, max_length=200)
    year: int = Field(ge=2000, le=2100)
    semester: str = Field(min_length=1, max_length=16)
    section: str = Field(min_length=1, max_length=16)
    capacity: int = Field(gt=0, le=10000)
    enrolled: int = Field(ge=0, le=10000)
    day: str = Field(min_length=1, max_length=16)
    start_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(pattern=r"^\d{2}:\d{2}$")

    @model_validator(mode="after")
    def validate_limits_and_time(self) -> "CanonicalCourse":
        if self.enrolled > self.capacity:
            raise ValueError("enrolled exceeds capacity")
        try:
            start_hour, start_minute = (int(part) for part in self.start_time.split(":"))
            end_hour, end_minute = (int(part) for part in self.end_time.split(":"))
        except (TypeError, ValueError):
            raise ValueError("invalid course time") from None
        if (
            start_hour > 23
            or end_hour > 23
            or start_minute > 59
            or end_minute > 59
            or (start_hour, start_minute) >= (end_hour, end_minute)
        ):
            raise ValueError("invalid course time")
        return self


def is_valid_database_id(value: Any) -> bool:
    return isinstance(value, str) and _DB_ID_PATTERN.fullmatch(value) is not None


def is_valid_label(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= MAX_LABEL_LENGTH
        and _LABEL_PATTERN.fullmatch(value) is not None
    )


class SyntheticDatabaseRegistry:
    """Server-owned immutable-view registry (no caller injection)."""

    def __init__(self, entries: list[dict[str, str]]) -> None:
        if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_DATABASES:
            raise SyntheticCatalogConfigError()
        seen: set[str] = set()
        frozen: list[DatabaseEntry] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise SyntheticCatalogConfigError()
            try:
                parsed = DatabaseEntry.model_validate(entry)
            except Exception:
                raise SyntheticCatalogConfigError() from None
            if not is_valid_label(parsed.label) or parsed.schema_variant not in _SCHEMA_VALUES:
                raise SyntheticCatalogConfigError()
            if parsed.database_id in seen:
                raise SyntheticCatalogConfigError()
            seen.add(parsed.database_id)
            frozen.append(parsed)
        self._entries = tuple(frozen)
        self._by_id = {entry.database_id: entry for entry in frozen}

    def database_ids(self) -> tuple[str, ...]:
        return tuple(entry.database_id for entry in self._entries)

    def snapshot(self) -> list[dict[str, str]]:
        """Defensive copy for responses."""
        return [copy.deepcopy(entry.model_dump(mode="json", by_alias=True)) for entry in self._entries]

    def is_known(self, database_id: Any) -> bool:
        return isinstance(database_id, str) and database_id in self._by_id

    def entry_for(self, database_id: str) -> DatabaseEntry | None:
        entry = self._by_id.get(database_id)
        if entry is None:
            return None
        return DatabaseEntry.model_validate(entry.model_dump(mode="json", by_alias=True))


def columns_for_schema(schema: str) -> dict[str, tuple[str, str]] | None:
    if schema == "flat":
        return dict(_FLAT_COLUMNS)
    if schema == "legacy":
        return dict(_LEGACY_COLUMNS)
    return None


def build_catalog_mapping(database_id: str, schema: str) -> Any:
    """Build a Published mapping snapshot for the catalog path (server-owned)."""
    from ..harness.contracts import FieldMapping, MappingProfile, MappingStatus

    columns = columns_for_schema(schema)
    if columns is None:
        raise SyntheticCatalogConfigError()
    fields = {ref: FieldMapping(table=table, column=column) for ref, (table, column) in columns.items()}
    return MappingProfile(
        database_id=database_id,
        version=1,
        status=MappingStatus.PUBLISHED,
        fields=fields,
    )


def coerce_canonical_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Convert one executor row (semantic-ref keys) to a canonical DTO dict."""
    try:
        if not isinstance(row, dict) or set(row.keys()) != set(CANONICAL_SELECT):
            return None
        converted: dict[str, Any] = {}
        for ref in CANONICAL_SELECT:
            key = _REF_TO_DTO_KEY[ref]
            converted[key] = row[ref]
        CanonicalCourse.model_validate(converted)
        return converted
    except Exception:
        return None


def apply_keyword_filter(items: list[dict[str, Any]], keyword: str) -> list[dict[str, Any]]:
    needle = keyword.strip().lower()
    if not needle:
        return list(items)
    if len(needle) > MAX_KEYWORD_LENGTH:
        return list(items)
    result: list[dict[str, Any]] = []
    for item in items:
        haystack = " ".join(
            str(item.get(key, "")) for key in ("course_code", "course_name", "instructor_name", "department_name")
        ).lower()
        if needle in haystack:
            result.append(item)
    return result


def _status_for(code: str) -> int:
    if code == SyntheticCatalogErrorCode.INVALID_REQUEST.value:
        return 400
    if code == SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value:
        return 404
    return 500


def _error_response(code: str) -> JSONResponse:
    return JSONResponse(status_code=_status_for(code), content={"error": {"code": code}})


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _parse_query_params(request: Request) -> dict[str, Any] | None:
    try:
        raw = request.url.query
    except Exception:
        return None
    if raw is None:
        return {}
    if not isinstance(raw, str):
        return None
    if raw == "":
        return {}
    params: dict[str, Any] = {}
    try:
        for part in raw.split("&"):
            if not part:
                continue
            if "=" in part:
                key, _, value = part.partition("=")
            else:
                key, value = part, ""
            from urllib.parse import unquote_plus

            key = unquote_plus(key)
            value = unquote_plus(value)
            if key in params:
                return None
            params[key] = value
    except Exception:
        return None
    return params


def register_synthetic_database_routes(
    app: FastAPI,
    *,
    registry: SyntheticDatabaseRegistry,
    executor: Any,
    compiler: Any,
    sql_policy: Any,
    schema_by_id: dict[str, str],
    schema_is_current: Callable[[str], bool],
) -> None:
    if not callable(getattr(executor, "execute", None)):
        raise SyntheticCatalogConfigError()
    if not callable(getattr(compiler, "compile", None)):
        raise SyntheticCatalogConfigError()
    if not callable(getattr(sql_policy, "validate", None)):
        raise SyntheticCatalogConfigError()
    if not isinstance(schema_by_id, dict) or not callable(schema_is_current):
        raise SyntheticCatalogConfigError()
    registered_schemas = {
        entry["database_id"]: entry["schema"] for entry in registry.snapshot()
    }
    if set(schema_by_id) != set(registered_schemas) or any(
        schema_by_id[database_id] != schema
        for database_id, schema in registered_schemas.items()
    ):
        raise SyntheticCatalogConfigError()
    frozen_schema_by_id = dict(registered_schemas)

    @app.get(DATABASES_PATH)
    async def list_databases(_request: Request) -> JSONResponse:
        try:
            payload = {
                "version": SYNTHETIC_REGISTRY_VERSION,
                "databases": registry.snapshot(),
            }
            text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            return JSONResponse(status_code=200, content=json.loads(text))
        except Exception:
            return _error_response(SyntheticCatalogErrorCode.INTERNAL_ERROR.value)

    @app.get(CATALOG_PATH)
    async def get_catalog(http_request: Request) -> JSONResponse:
        try:
            params = _parse_query_params(http_request)
            if params is None:
                return _error_response(SyntheticCatalogErrorCode.INVALID_REQUEST.value)
            allowed = {"database_id", "keyword"}
            if any(key not in allowed for key in params):
                return _error_response(SyntheticCatalogErrorCode.INVALID_REQUEST.value)
            database_id = params.get("database_id")
            if not is_valid_database_id(database_id) or not registry.is_known(database_id):
                if not is_valid_database_id(database_id):
                    return _error_response(SyntheticCatalogErrorCode.INVALID_REQUEST.value)
                return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
            keyword = params.get("keyword", "")
            if keyword is None:
                keyword = ""
            if not isinstance(keyword, str) or len(keyword) > MAX_KEYWORD_LENGTH:
                return _error_response(SyntheticCatalogErrorCode.INVALID_REQUEST.value)
            try:
                current_schema = schema_is_current(database_id)
            except Exception:
                current_schema = False
            if current_schema is not True:
                return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
            schema = frozen_schema_by_id.get(database_id)
            if schema not in _SCHEMA_VALUES:
                return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
            try:
                mapping = build_catalog_mapping(database_id, schema)
                from ..harness.contracts import MappingStatus

                if (
                    mapping.database_id != database_id
                    or mapping.status is not MappingStatus.PUBLISHED
                    or set(mapping.fields) != set(CANONICAL_SELECT)
                ):
                    return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
            except Exception:
                return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
            try:
                from ..harness.contracts import QueryIR

                query_ir = QueryIR.model_validate(
                    {
                        "entity": "CourseOffering",
                        "select": list(CANONICAL_SELECT),
                        "filters": [],
                        "limit": MAX_ITEMS,
                    }
                )
            except Exception:
                return _error_response(SyntheticCatalogErrorCode.INTERNAL_ERROR.value)
            try:
                compiled = compiler.compile(query_ir, mapping)
                sql_policy.validate(compiled, query_ir=query_ir, mapping=mapping)
            except Exception:
                return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
            try:
                columns, rows = executor.execute(database_id, compiled)
            except Exception:
                return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
            try:
                if not isinstance(columns, list) or not isinstance(rows, list):
                    return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
                if set(columns) != set(CANONICAL_SELECT) or len(columns) != len(set(columns)):
                    return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
                if len(rows) > MAX_ITEMS:
                    return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
                items: list[dict[str, Any]] = []
                for row in rows:
                    if not isinstance(row, dict) or set(row.keys()) != set(columns):
                        return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
                    normalized = {str(key): row[key] for key in columns}
                    dto = coerce_canonical_row(normalized)
                    if dto is None:
                        return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
                    items.append(dto)
                items = sorted(
                    items,
                    key=lambda item: (
                        int(item["year"]),
                        str(item["semester"]),
                        str(item["course_code"]),
                        str(item["section"]),
                    ),
                )
                items = apply_keyword_filter(items, keyword)
                payload = {
                    "version": SYNTHETIC_CATALOG_VERSION,
                    "database_id": database_id,
                    "items": items,
                    "total": len(items),
                }
                text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                return JSONResponse(status_code=200, content=json.loads(text))
            except Exception:
                return _error_response(SyntheticCatalogErrorCode.INTERNAL_ERROR.value)
        except Exception:
            return _error_response(SyntheticCatalogErrorCode.INTERNAL_ERROR.value)
