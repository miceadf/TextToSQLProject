"""Server-owned database registry and canonical course catalog.

Lists synthetic SQLite databases and published PostgreSQL databases:

- the server owns database registration and runtime contexts;
- callers cannot inject file paths, mappings, SQL, or physical identifiers;
- the catalog reads through the existing read-only executor plus the
  Published mapping / safe compiler path and converts rows to one
  canonical course DTO defined here;
- unknown databases, mapping/schema drift, and execution failures close
  with fixed redacted error codes only.

PostgreSQL catalogs use the published runtime context and read-only
executor. Errors never reflect request text, paths, SQL, rows, or exception
text in stdout/logs or error responses.
"""

from __future__ import annotations

import copy
import json
import re
from decimal import Decimal
from enum import Enum
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..compiler.contracts import MemberFilter


SYNTHETIC_REGISTRY_VERSION = "synthetic-database-registry-v1"
SYNTHETIC_CATALOG_VERSION = "synthetic-course-catalog-v1"

DATABASES_PATH = "/api/adaptive/v1/databases"
CATALOG_PATH = "/api/adaptive/v1/courses"

MAX_DATABASES = 8
MAX_ITEMS = 200
MAX_CATALOG_ITEMS = 1000
MAX_KEYWORD_LENGTH = 100
MAX_LABEL_LENGTH = 64

_DB_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _().-]{0,63}$")
_SCHEMA_VALUES = frozenset({"flat", "legacy", "private", "postgres"})

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
    _OFFERING_SEMESTER_REF,
    _OFFERING_SECTION_REF,
    _OFFERING_CAPACITY_REF,
    _OFFERING_ENROLLED_REF,
    _SCHEDULE_DAY_REF,
    _SCHEDULE_START_REF,
    _SCHEDULE_END_REF,
)

# Chat cards follow active ontology order. Phase 9-16: the course-list
# projection has fourteen fields; the retired year DTO key stays nullable.
_TARGET_GRADE_REF = "Course.target_grade"
CARD_FIELDS = (
    _COURSE_CODE_REF,
    _COURSE_NAME_REF,
    _COURSE_CREDITS_REF,
    _COURSE_CATEGORY_REF,
    _TARGET_GRADE_REF,
    *CANONICAL_SELECT[4:],
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
    _TARGET_GRADE_REF: "target_grade",
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
    # Phase 7-14: a partially mapped PostgreSQL profile has no full catalog.
    UNSUPPORTED = "SYNTHETIC_CATALOG_UNSUPPORTED"
    INTERNAL_ERROR = "SYNTHETIC_CATALOG_INTERNAL_ERROR"


class SyntheticCatalogConfigError(Exception):
    def __init__(self) -> None:
        super().__init__(SyntheticCatalogErrorCode.INTERNAL_ERROR.value)


class DatabaseEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, populate_by_name=True)

    database_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    label: str = Field(min_length=1, max_length=64)
    schema_variant: str = Field(alias="schema", pattern=r"^(flat|legacy|private|postgres)$")


class CanonicalCourse(BaseModel):
    """Single centralized canonical course DTO."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    course_code: str = Field(min_length=1, max_length=32)
    course_name: str = Field(min_length=1, max_length=200)
    credits: float | None = Field(default=None, gt=0, le=10)
    category: str | None = Field(default=None, min_length=1, max_length=32)
    department_code: str | None = Field(default=None, min_length=1, max_length=32)
    department_name: str | None = Field(default=None, min_length=1, max_length=200)
    instructor_name: str | None = Field(default=None, min_length=1, max_length=200)
    # Preserve the catalog DTO key for older clients; no current year query.
    year: int | None = Field(default=None, ge=2000, le=2100)
    semester: str | None = Field(default=None, min_length=1, max_length=16)
    section: str | None = Field(default=None, min_length=1, max_length=16)
    capacity: int | None = Field(default=None, gt=0, le=10000)
    enrolled: int | None = Field(default=None, ge=0, le=10000)
    day: str | None = Field(default=None, min_length=1, max_length=16)
    start_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    end_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")

    @model_validator(mode="after")
    def validate_limits_and_time(self) -> "CanonicalCourse":
        if self.enrolled is not None and self.capacity is not None and self.enrolled > self.capacity:
            raise ValueError("enrolled exceeds capacity")
        if self.start_time is None or self.end_time is None:
            return self
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


def is_valid_private_label(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if not 1 <= len(value) <= MAX_LABEL_LENGTH:
        return False
    if not value.strip():
        return False
    return not any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)


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
            if parsed.schema_variant not in _SCHEMA_VALUES:
                raise SyntheticCatalogConfigError()
            if parsed.schema_variant == "private":
                if not is_valid_private_label(parsed.label):
                    raise SyntheticCatalogConfigError()
            elif not is_valid_label(parsed.label):
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


def coerce_canonical_row(
    row: dict[str, Any], unsupported_fields: set[str] | None = None
) -> dict[str, Any] | None:
    """Convert one executor row (semantic-ref keys) to a canonical DTO dict."""
    try:
        if not isinstance(row, dict) or set(row.keys()) != set(CANONICAL_SELECT):
            return None
        allowed_nulls = {_OFFERING_YEAR_REF, *(unsupported_fields or set())}
        if any(row[ref] is None and ref not in allowed_nulls for ref in CANONICAL_SELECT):
            return None
        converted: dict[str, Any] = {}
        for ref in CANONICAL_SELECT:
            key = _REF_TO_DTO_KEY[ref]
            converted[key] = row[ref]
        validated = CanonicalCourse.model_validate(converted)
        return validated.model_dump(mode="json")
    except Exception:
        return None


def coerce_postgres_catalog_row(
    row: dict[str, Any], unsupported_fields: set[str] | None = None
) -> dict[str, Any] | None:
    """Phase 9-35: retain valid identity fields and null invalid optional values."""
    try:
        if not isinstance(row, dict) or set(row.keys()) != set(CANONICAL_SELECT):
            return None
        converted = {_REF_TO_DTO_KEY[ref]: row[ref] for ref in CANONICAL_SELECT}
        code, name = converted["course_code"], converted["course_name"]
        if not isinstance(code, str) or not 1 <= len(code) <= 32:
            return None
        if not isinstance(name, str) or not 1 <= len(name) <= 200:
            return None
        limits = {"credits": (False, lambda n: 0 < n <= 10), "capacity": (True, lambda n: 0 < n <= 10000), "enrolled": (True, lambda n: 0 <= n <= 10000)}
        for key, (whole, valid) in limits.items():
            value = converted[key]
            if isinstance(value, Decimal):  # NUMERIC columns arrive as Decimal
                value = int(value) if value == value.to_integral_value() else float(value)
                converted[key] = value
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or (whole and not float(value).is_integer())
                or not valid(value)
            ):
                converted[key] = None
            elif whole and value is not None:
                converted[key] = int(value)
        for key, max_len in (("category", 32), ("department_code", 32), ("department_name", 200), ("instructor_name", 200), ("semester", 16), ("section", 16), ("day", 16)):
            value = converted[key]
            if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= max_len):
                converted[key] = None
        for key in ("start_time", "end_time"):
            value = converted[key]
            if value is not None and (not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value) or int(value[:2]) > 23 or int(value[3:]) > 59):
                converted[key] = None
        if converted["start_time"] is not None and converted["end_time"] is not None and converted["start_time"] >= converted["end_time"]:
            converted["start_time"] = converted["end_time"] = None
        # Year is retired (ontology v3) and not in the projection; the DTO keeps it null.
        converted["year"] = None
        # The shared DTO validator enforces enrolled <= capacity for legacy
        # callers; this PostgreSQL view deliberately permits over-enrolment.
        capacity, enrolled = converted["capacity"], converted["enrolled"]
        validated_input = {**converted, "capacity": None}
        dto = CanonicalCourse.model_validate(validated_input).model_dump(mode="json")
        dto["capacity"], dto["enrolled"] = capacity, enrolled
        return dto
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
    if code == SyntheticCatalogErrorCode.UNSUPPORTED.value:
        return 409
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


def _discover_private_activations(app: Any, *, fixed_ids: set[str]) -> list[dict[str, str]]:
    """Strict-load valid private activations; hide failures per ID."""
    try:
        root_raw = getattr(getattr(app, "state", None), "mapping_review_repository_root", None)
        if root_raw is None:
            root_raw = getattr(getattr(app, "state", None), "sqlite_upload_repository_root", None)
        if root_raw is None:
            return []
        from ..registration import reviews as _reviews

        activations = _reviews.list_activations(repository_root=str(root_raw))
    except Exception:
        return []
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for activation in activations:
        try:
            database_id = str(activation.database_id)
            label = str(activation.label)
        except Exception:
            continue
        if not is_valid_database_id(database_id) or not is_valid_private_label(label):
            continue
        if database_id in seen or database_id in fixed_ids:
            continue
        seen.add(database_id)
        entries.append({"database_id": database_id, "label": label, "schema": "private"})
    entries.sort(key=lambda item: item["database_id"])
    return entries


def _load_private_activation(app: Any, database_id: Any) -> Any | None:
    """Strict-load one private activation; return None on any failure."""
    try:
        if not is_valid_database_id(database_id):
            return None
        root_raw = getattr(getattr(app, "state", None), "mapping_review_repository_root", None)
        if root_raw is None:
            root_raw = getattr(getattr(app, "state", None), "sqlite_upload_repository_root", None)
        if root_raw is None:
            return None
        from ..registration import reviews as _reviews

        activation = _reviews.load_activation(
            repository_root=str(root_raw), database_id=str(database_id)
        )
        if activation.database_id != database_id:
            return None
        if not is_valid_private_label(activation.label):
            return None
        return activation
    except Exception:
        return None


def register_synthetic_database_routes(
    app: FastAPI,
    *,
    registry: SyntheticDatabaseRegistry,
    executor: Any,
    compiler: Any,
    sql_policy: Any,
    schema_by_id: dict[str, str],
    schema_is_current: Callable[[str], bool],
    postgres_contexts: dict[str, Any] | None = None,
) -> None:
    if not callable(getattr(executor, "execute", None)):
        raise SyntheticCatalogConfigError()
    if not callable(getattr(compiler, "compile", None)):
        raise SyntheticCatalogConfigError()
    if not callable(getattr(sql_policy, "validate", None)):
        raise SyntheticCatalogConfigError()
    if not isinstance(schema_by_id, dict) or not callable(schema_is_current):
        raise SyntheticCatalogConfigError()
    if postgres_contexts is None:
        frozen_postgres: dict[str, Any] = {}
    else:
        if not isinstance(postgres_contexts, dict):
            raise SyntheticCatalogConfigError()
        from .postgres_runtime import shared_or_copy

        frozen_postgres = shared_or_copy(postgres_contexts)
    registered_schemas = {
        entry["database_id"]: entry["schema"] for entry in registry.snapshot()
    }
    if set(schema_by_id) != set(registered_schemas) or any(
        schema_by_id[database_id] != schema
        for database_id, schema in registered_schemas.items()
    ):
        raise SyntheticCatalogConfigError()
    frozen_schema_by_id = dict(registered_schemas)

    fixed_ids = set(registry.database_ids())

    @app.get(DATABASES_PATH)
    async def list_databases(_request: Request) -> JSONResponse:
        try:
            fixed = registry.snapshot()
            private = _discover_private_activations(app, fixed_ids=fixed_ids)
            # Phase 8-15: PostgreSQL databases published while the server runs.
            current_pg = dict(frozen_postgres)  # one snapshot; publishing may add meanwhile
            published_pg = [
                {"database_id": database_id, "label": current_pg[database_id].label, "schema": "postgres"}
                for database_id in sorted(current_pg)
                if database_id not in fixed_ids
            ]
            payload = {
                "version": SYNTHETIC_REGISTRY_VERSION,
                "databases": [*fixed, *private, *published_pg],
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
            if not is_valid_database_id(database_id):
                return _error_response(SyntheticCatalogErrorCode.INVALID_REQUEST.value)
            keyword = params.get("keyword", "")
            if keyword is None:
                keyword = ""
            if not isinstance(keyword, str) or len(keyword) > MAX_KEYWORD_LENGTH:
                return _error_response(SyntheticCatalogErrorCode.INVALID_REQUEST.value)
            postgres_context = frozen_postgres.get(database_id) if isinstance(database_id, str) else None
            if postgres_context is not None:
                try:
                    # Phase 8-18: only the course-list fields decide; a newer
                    # ontology field may be absent without hiding the list.
                    # Phase 9-34: only code/name are required to keep a usable catalog.
                    unsupported_fields = set(postgres_context.unsupported_fields())
                    partial = bool(unsupported_fields & {_COURSE_CODE_REF, _COURSE_NAME_REF})
                except Exception:
                    return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
                if partial:
                    return _error_response(SyntheticCatalogErrorCode.UNSUPPORTED.value)
                try:
                    from .postgres_runtime import is_postgres_context_current
                    from ..harness.contracts import MappingStatus as _PgMappingStatus
                    from ..harness.contracts import QueryIR as _PgQueryIR

                    try:
                        pg_current = is_postgres_context_current(postgres_context)
                    except Exception:
                        pg_current = False
                    if pg_current is not True:
                        return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
                    pg_mapping = postgres_context.harness_mapping()
                    if (
                        pg_mapping.database_id != database_id
                        or pg_mapping.status is not _PgMappingStatus.PUBLISHED
                        or not {_COURSE_CODE_REF, _COURSE_NAME_REF} <= set(pg_mapping.fields)
                    ):
                        return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
                    pg_offering_fields = [
                        ref for ref in CANONICAL_SELECT
                        if ref not in unsupported_fields
                        and ref not in {_SCHEDULE_DAY_REF, _SCHEDULE_START_REF, _SCHEDULE_END_REF}
                    ]
                    pg_query_ir = _PgQueryIR.model_validate(
                        {
                            "entity": "CourseOffering",
                            "select": pg_offering_fields,
                            "filters": [],
                            "limit": MAX_ITEMS,
                        }
                    )
                except Exception:
                    return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
                try:
                    pg_compiled = compiler.compile_catalog(pg_query_ir, pg_mapping, member_filters=(), distinct=True)
                    sql_policy.validate_catalog(
                        pg_compiled, query_ir=pg_query_ir, mapping=pg_mapping,
                        member_filters=(), distinct=True
                    )
                except Exception:
                    return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
                try:
                    pg_columns, pg_rows = executor.execute(database_id, pg_compiled)
                except Exception:
                    return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
                try:
                    if not isinstance(pg_columns, list) or not isinstance(pg_rows, list):
                        return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
                    expected_columns = pg_offering_fields
                    if set(pg_columns) != set(expected_columns) or len(pg_columns) != len(set(pg_columns)):
                        return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
                    if len(pg_rows) > MAX_ITEMS:
                        return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
                    if any(not isinstance(row, dict) or set(row) != set(pg_columns) for row in pg_rows):
                        return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
                    # Phase 9-36: the first query caps distinct offerings, then
                    # bounded schedule queries expand those offerings into times.
                    offering_rows = pg_rows
                    schedule_fields = [ref for ref in (_SCHEDULE_DAY_REF, _SCHEDULE_START_REF, _SCHEDULE_END_REF) if ref not in unsupported_fields]
                    time_rows_by_key: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
                    if schedule_fields and offering_rows:
                        codes = list(dict.fromkeys(row[_COURSE_CODE_REF] for row in offering_rows))
                        time_select = [
                            ref for ref in (_COURSE_CODE_REF, _OFFERING_SECTION_REF, _OFFERING_SEMESTER_REF, *schedule_fields)
                            if ref in pg_mapping.fields
                        ]
                        identity = [ref for ref in (_COURSE_CODE_REF, _OFFERING_SECTION_REF, _OFFERING_SEMESTER_REF) if ref in pg_mapping.fields]
                        # Include the already completed offering query in the bound.
                        queries = 1
                        # Phase 9-36: retry a possibly truncated chunk at half size.
                        def fetch_time_chunk(chunk: list[Any]) -> bool:
                            nonlocal queries
                            if queries >= 40:
                                return False
                            queries += 1
                            time_ir = _PgQueryIR.model_validate({"entity": "CourseOffering", "select": time_select, "filters": [], "limit": MAX_ITEMS})
                            member_filters = (MemberFilter(field=_COURSE_CODE_REF, values=tuple(chunk)),)
                            try:
                                compiled = compiler.compile_catalog(time_ir, pg_mapping, member_filters=member_filters, distinct=True)
                                sql_policy.validate_catalog(compiled, query_ir=time_ir, mapping=pg_mapping, member_filters=member_filters, distinct=True)
                                columns, rows = executor.execute(database_id, compiled)
                            except Exception:
                                return False
                            if not isinstance(columns, list) or set(columns) != set(time_select) or len(columns) != len(set(columns)) or not isinstance(rows, list) or len(rows) > MAX_ITEMS:
                                return False
                            if any(not isinstance(row, dict) or set(row) != set(columns) for row in rows):
                                return False
                            if len(rows) == MAX_ITEMS and len(chunk) > 1:
                                midpoint = len(chunk) // 2
                                return fetch_time_chunk(chunk[:midpoint]) and fetch_time_chunk(chunk[midpoint:])
                            for row in rows:
                                key = tuple(row[field] for field in identity)
                                time_rows_by_key.setdefault(key, []).append(row)
                            return True
                        for offset in range(0, len(codes), 50):
                            if not fetch_time_chunk(codes[offset:offset + 50]):
                                return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
                    pg_items: list[dict[str, Any]] = []
                    for offering in offering_rows:
                        key = tuple(offering[field] for field in [ref for ref in (_COURSE_CODE_REF, _OFFERING_SECTION_REF, _OFFERING_SEMESTER_REF) if ref in pg_mapping.fields])
                        matched = time_rows_by_key.get(key, []) if schedule_fields else [{}]
                        if not matched:
                            matched = [{}]
                        for time_row in matched:
                            pg_normalized = {ref: offering.get(ref) for ref in CANONICAL_SELECT}
                            pg_normalized.update({ref: time_row[ref] for ref in schedule_fields if ref in time_row})
                            # Unsupported values remain null for the tolerant converter.
                            pg_normalized.update({ref: None for ref in unsupported_fields if ref in pg_normalized})
                            pg_dto = coerce_postgres_catalog_row(pg_normalized, unsupported_fields)
                            if pg_dto is None:
                                continue
                            if pg_dto not in pg_items:
                                pg_items.append(pg_dto)
                            if len(pg_items) > MAX_CATALOG_ITEMS:
                                return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
                    pg_items = sorted(
                        pg_items,
                        key=lambda item: (
                            int(item["year"] or 0),
                            str(item["semester"]),
                            str(item["course_code"]),
                            str(item["section"]),
                        ),
                    )
                    pg_items = apply_keyword_filter(pg_items, keyword)
                    pg_payload = {
                        "version": SYNTHETIC_CATALOG_VERSION,
                        "database_id": database_id,
                        "items": pg_items,
                        "total": len(pg_items),
                    }
                    pg_text = json.dumps(pg_payload, ensure_ascii=False, sort_keys=True)
                    return JSONResponse(status_code=200, content=json.loads(pg_text))
                except Exception:
                    return _error_response(SyntheticCatalogErrorCode.INTERNAL_ERROR.value)
            if frozen_schema_by_id.get(database_id) == "postgres":
                return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
            private_activation: Any = None
            if not registry.is_known(database_id):
                private_activation = _load_private_activation(app, database_id)
                if private_activation is None:
                    return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
            if private_activation is None:
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
                        or not set(CANONICAL_SELECT) <= set(mapping.fields)
                    ):
                        return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
                except Exception:
                    return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
            else:
                try:
                    from ..mapping.bridge import to_harness_mapping
                    from ..relations.physical import build_physical_relation_graph

                    mapping = to_harness_mapping(private_activation.profile)
                    if (
                        mapping.database_id != database_id
                        or not set(CANONICAL_SELECT) <= set(mapping.fields)
                    ):
                        return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
                    graph = build_physical_relation_graph(private_activation.catalog)
                    if (
                        graph.database_id != database_id
                        or graph.schema_fingerprint != private_activation.schema_fingerprint
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
            if private_activation is None:
                try:
                    compiled = compiler.compile(query_ir, mapping)
                    sql_policy.validate(compiled, query_ir=query_ir, mapping=mapping)
                except Exception:
                    return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
                try:
                    columns, rows = executor.execute(database_id, compiled)
                except Exception:
                    return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
            else:
                try:
                    from ..compiler.harness import SchemaAdaptiveSQLiteCompiler
                    from ..harness.executor import SQLiteReadOnlyExecutor
                    from ..policy.harness import SchemaAdaptiveAstPolicy

                    adaptive_compiler = SchemaAdaptiveSQLiteCompiler(
                        graph, private_activation.profile
                    )
                    adaptive_policy = SchemaAdaptiveAstPolicy(
                        graph, private_activation.profile
                    )
                    compiled = adaptive_compiler.compile(query_ir, mapping)
                    adaptive_policy.validate(compiled, query_ir=query_ir, mapping=mapping)
                except Exception:
                    return _error_response(SyntheticCatalogErrorCode.CONTEXT_UNAVAILABLE.value)
                try:
                    private_executor = SQLiteReadOnlyExecutor(
                        {database_id: private_activation.database_path}
                    )
                    columns, rows = private_executor.execute(database_id, compiled)
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
                        int(item["year"] or 0),
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
