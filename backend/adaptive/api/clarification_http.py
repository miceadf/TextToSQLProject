"""Synthetic clarification HTTP boundary (clarification-http-v1).

Small FastAPI sub-application exposing Phase 6-11
`ClarificationSessionCoordinator.start/submit` over exactly two POST
endpoints. Independent from legacy `backend.main`: no import, no mount,
no change to legacy global app, CORS, auth, or startup warmup.

Scope: synthetic verification of route shape, server-owned dependency
lifecycle, fixed error-to-HTTP-status mapping, and response redaction.
Out of scope: access-log policy, proxy logs, TLS, auth, React UI,
Ollama, PostgreSQL, CNU material, file/network access. SQLite execution
happens only through the test-injected read-only executor.

This module writes no stdout/logs and never reflects request bodies,
tokens, questions, identifiers, SQL, rows, or exception text.
"""

from __future__ import annotations

import json
import math
import re
from enum import Enum
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..harness.ambiguity_detector import (
    AmbiguityDetectorError,
    AmbiguityDetectorErrorCode,
    DetectorRuleSet,
)
from ..harness.clarification import (
    ClarificationChoice,
    ClarificationError,
    ClarificationErrorCode,
)
from ..harness.clarification_execution_adapter import (
    ClarificationExecutionAdapterError,
    ClarificationExecutionAdapterErrorCode,
)
from ..harness.clarification_query_constraint import ClarificationQueryConstraintError, ClarificationQueryConstraintErrorCode
from ..harness.clarification_session import (
    ClarificationSessionError,
    ClarificationSessionErrorCode,
    ClarificationSessionStartStatus,
    ClarificationSessionSubmitStatus,
)
from ..harness.contracts import (
    HarnessErrorCode,
    HarnessRequest,
    HarnessStage,
    HarnessStatus,
    MappingProfile,
    MappingStatus,
    Ontology,
)


CLARIFICATION_HTTP_VERSION = "clarification-http-v1"

START_PATH = "/api/adaptive/v1/clarifications/start"
SUBMIT_PATH = "/api/adaptive/v1/clarifications/submit"


class ClarificationHttpErrorCode(str, Enum):
    INVALID_CONFIGURATION = "CLARIFICATION_HTTP_INVALID_CONFIGURATION"
    INVALID_REQUEST = "CLARIFICATION_HTTP_INVALID_REQUEST"
    CONTEXT_UNAVAILABLE = "CLARIFICATION_HTTP_CONTEXT_UNAVAILABLE"
    INTERNAL_ERROR = "CLARIFICATION_HTTP_INTERNAL_ERROR"


class ClarificationHttpConfigError(Exception):
    """Fail-closed factory configuration error with a fixed code only."""

    def __init__(self, code: ClarificationHttpErrorCode = ClarificationHttpErrorCode.INVALID_CONFIGURATION) -> None:
        self.code = code.value
        super().__init__(self.code)


class StartRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    request_id: str = Field(min_length=1, max_length=200)
    database_id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("request_id", "database_id")
    @classmethod
    def _ids_have_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identifier must not be blank")
        return value

    @field_validator("question")
    @classmethod
    def _question_has_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("question must not be blank")
        return normalized


class SubmitRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    session_token: str = Field(pattern=r"^[A-Za-z0-9_-]{43,256}$")
    selection: list[
        Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    ] = Field(default_factory=list, max_length=16)
    cancelled: bool = False


class HttpOption(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    option_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantic_ref: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)?$")


class StartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: Literal["clarification-http-v1"] = CLARIFICATION_HTTP_VERSION
    status: ClarificationSessionStartStatus = Field(strict=False)
    session_token: str | None = None
    options: list[HttpOption] | None = None

    @model_validator(mode="before")
    @classmethod
    def _revalidate_options(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("options"), list):
            nested = dict(data)
            nested["options"] = [
                option.model_dump(mode="json", warnings=False)
                if isinstance(option, BaseModel)
                else option
                for option in data["options"]
            ]
            return nested
        return data

    @model_validator(mode="after")
    def _check_state(self) -> "StartResponse":
        if self.status is ClarificationSessionStartStatus.NOT_REQUIRED:
            if self.session_token is not None or self.options is not None:
                raise ValueError("not-required must not carry token or options")
        else:
            if self.session_token is None or self.options is None:
                raise ValueError("pending must carry token and options")
            if not re.fullmatch(r"[A-Za-z0-9_-]{43,256}", self.session_token):
                raise ValueError("pending carries malformed token")
            if not (2 <= len(self.options) <= 16):
                raise ValueError("pending options out of range")
            option_ids = [option.option_id for option in self.options]
            semantic_refs = [option.semantic_ref for option in self.options]
            if len(option_ids) != len(set(option_ids)) or len(semantic_refs) != len(
                set(semantic_refs)
            ):
                raise ValueError("pending options must be distinct")
        return self


class HttpExecutionError(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    code: HarnessErrorCode = Field(strict=False)
    stage: HarnessStage = Field(strict=False)


class HttpExecution(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    status: HarnessStatus = Field(strict=False)
    columns: list[str] | None = None
    rows: list[dict[str, JsonValue]] | None = None
    error: HttpExecutionError | None = None

    @model_validator(mode="before")
    @classmethod
    def _revalidate_error(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("error"), BaseModel):
            nested = dict(data)
            nested["error"] = data["error"].model_dump(
                mode="json", warnings=False
            )
            return nested
        return data

    @field_validator("rows", mode="before")
    @classmethod
    def _rows_are_finite_json(cls, value: list[dict[str, JsonValue]] | None) -> list[dict[str, JsonValue]] | None:
        if value is not None and not _walk_json_finite(value):
            raise ValueError("rows must be finite JSON values")
        return value

    @model_validator(mode="after")
    def _check_state(self) -> "HttpExecution":
        if self.status is HarnessStatus.SUCCEEDED:
            if self.error is not None or self.columns is None or self.rows is None:
                raise ValueError("success must carry columns/rows only")
            if len(self.columns) != len(set(self.columns)):
                raise ValueError("success columns must be distinct")
            expected = set(self.columns)
            if any(set(row) != expected for row in self.rows):
                raise ValueError("row keys must match columns")
        else:
            if self.error is None or self.columns is not None or self.rows is not None:
                raise ValueError("failure must carry error only")
        return self


class SubmitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: Literal["clarification-http-v1"] = CLARIFICATION_HTTP_VERSION
    status: ClarificationSessionSubmitStatus = Field(strict=False)
    execution: HttpExecution | None = None

    @model_validator(mode="before")
    @classmethod
    def _revalidate_execution(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("execution"), BaseModel):
            nested = dict(data)
            nested["execution"] = data["execution"].model_dump(
                mode="json", warnings=False
            )
            return nested
        return data

    @model_validator(mode="after")
    def _check_state(self) -> "SubmitResponse":
        if self.status is ClarificationSessionSubmitStatus.CANCELLED:
            if self.execution is not None:
                raise ValueError("cancelled must not carry execution")
        else:
            if self.execution is None:
                raise ValueError("executed must carry execution")
        return self


_ERROR_STATUS: dict[str, int] = {
    ClarificationHttpErrorCode.INVALID_REQUEST.value: 400,
    ClarificationSessionErrorCode.INVALID_INPUT.value: 400,
    ClarificationErrorCode.INVALID_CHOICE.value: 400,
    ClarificationErrorCode.INVALID_SIGNAL.value: 400,
    AmbiguityDetectorErrorCode.INVALID_RULESET.value: 400,
    ClarificationSessionErrorCode.NOT_FOUND.value: 404,
    ClarificationSessionErrorCode.EXPIRED.value: 410,
    ClarificationSessionErrorCode.ALREADY_CONSUMED.value: 409,
    AmbiguityDetectorErrorCode.CONFLICT.value: 409,
    AmbiguityDetectorErrorCode.CONTEXT_MISMATCH.value: 409,
    ClarificationErrorCode.CONTEXT_MISMATCH.value: 409,
    ClarificationQueryConstraintErrorCode.CANCELLED.value: 409,
    ClarificationQueryConstraintErrorCode.INVALID_INPUT.value: 422,
    ClarificationQueryConstraintErrorCode.INVALID_QUERY_IR.value: 422,
    ClarificationQueryConstraintErrorCode.SEMANTIC_INVALID.value: 422,
    ClarificationQueryConstraintErrorCode.CONSTRAINT_VIOLATION.value: 422,
    ClarificationQueryConstraintErrorCode.PROVIDER_ERROR.value: 502,
    ClarificationHttpErrorCode.CONTEXT_UNAVAILABLE.value: 503,
    ClarificationExecutionAdapterErrorCode.INVALID_INPUT.value: 500,
    ClarificationExecutionAdapterErrorCode.INTERNAL_ERROR.value: 500,
    ClarificationSessionErrorCode.INTERNAL_ERROR.value: 500,
    ClarificationHttpErrorCode.INTERNAL_ERROR.value: 500,
    ClarificationHttpErrorCode.INVALID_CONFIGURATION.value: 500,
}


def _status_for(code: str) -> int:
    return _ERROR_STATUS.get(code, 500)


def _fixed_code(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, Enum):
        candidate = str(code.value)
    elif isinstance(code, str) and code:
        candidate = code
    else:
        return ClarificationHttpErrorCode.INTERNAL_ERROR.value
    if candidate in _ERROR_STATUS:
        return candidate
    return ClarificationHttpErrorCode.INTERNAL_ERROR.value


def _error_response(code: str) -> JSONResponse:
    return JSONResponse(status_code=_status_for(code), content={"error": {"code": code}})


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _parse_json_object(raw: bytes) -> dict[str, Any] | None:
    try:
        text = raw.decode("utf-8")
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _revalidate(model_cls: type[Any], value: Any) -> Any | None:
    try:
        if isinstance(value, model_cls):
            payload = value.model_dump(mode="json", warnings=False)
        elif isinstance(value, dict):
            payload = value
        else:
            return None
        return model_cls.model_validate(payload)
    except Exception:
        return None


def _walk_json_finite(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_walk_json_finite(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _walk_json_finite(item) for key, item in value.items())
    return False


def _load_server_context(loader: Any, database_id: str) -> tuple[Any, Any, Any] | None:
    try:
        loaded = loader.load(database_id)
    except Exception:
        return None
    items: Any = None
    if isinstance(loaded, (tuple, list)) and len(loaded) == 3:
        items = (loaded[0], loaded[1], loaded[2])
    else:
        return None
    ontology = _revalidate(Ontology, items[0])
    mapping = _revalidate(MappingProfile, items[1])
    rules = _revalidate(DetectorRuleSet, items[2])
    if ontology is None or mapping is None or rules is None:
        return None
    if mapping.status is not MappingStatus.PUBLISHED:
        return None
    if mapping.database_id != database_id:
        return None
    return ontology, mapping, rules


def create_clarification_http_app(
    coordinator: Any,
    context_loader: Any,
    provider: Any,
    executor: Any,
    *,
    compiler: Any | None = None,
    sql_policy: Any | None = None,
) -> FastAPI:
    """Create an independent clarification HTTP sub-application."""
    if not callable(getattr(coordinator, "start", None)) or not callable(getattr(coordinator, "submit", None)):
        raise ClarificationHttpConfigError()
    if not callable(getattr(context_loader, "load", None)):
        raise ClarificationHttpConfigError()
    if not callable(getattr(provider, "generate_ir", None)):
        raise ClarificationHttpConfigError()
    if not callable(getattr(executor, "execute", None)):
        raise ClarificationHttpConfigError()
    if compiler is not None and not callable(getattr(compiler, "compile", None)):
        raise ClarificationHttpConfigError()
    if sql_policy is not None and not callable(getattr(sql_policy, "validate", None)):
        raise ClarificationHttpConfigError()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_: Request, __: Exception) -> JSONResponse:
        return _error_response(ClarificationHttpErrorCode.INVALID_REQUEST.value)

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        if int(getattr(exc, "status_code", 500)) == 400:
            return _error_response(ClarificationHttpErrorCode.INVALID_REQUEST.value)
        return _error_response(ClarificationHttpErrorCode.INTERNAL_ERROR.value)

    @app.post(START_PATH)
    async def start_clarification(http_request: Request) -> JSONResponse:
        try:
            payload = _parse_json_object(await http_request.body())
            if payload is None:
                return _error_response(ClarificationHttpErrorCode.INVALID_REQUEST.value)
            try:
                body = StartRequestBody.model_validate(payload)
            except Exception:
                return _error_response(ClarificationHttpErrorCode.INVALID_REQUEST.value)
            try:
                harness_request = HarnessRequest.model_validate(
                    {
                        "request_id": body.request_id,
                        "database_id": body.database_id,
                        "question": body.question,
                    }
                )
            except Exception:
                return _error_response(ClarificationHttpErrorCode.INVALID_REQUEST.value)
            loaded = _load_server_context(context_loader, body.database_id)
            if loaded is None:
                return _error_response(ClarificationHttpErrorCode.CONTEXT_UNAVAILABLE.value)
            ontology, mapping, rules = loaded
            try:
                started = coordinator.start(harness_request, ontology, mapping, rules)
            except (ClarificationSessionError, ClarificationError, AmbiguityDetectorError) as exc:
                return _error_response(_fixed_code(exc))
            except Exception:
                return _error_response(ClarificationHttpErrorCode.INTERNAL_ERROR.value)
            try:
                if started.status is ClarificationSessionStartStatus.NOT_REQUIRED:
                    out = StartResponse.model_validate({"version": CLARIFICATION_HTTP_VERSION, "status": "NOT_REQUIRED"})
                else:
                    token = getattr(started, "session_token", None)
                    if not isinstance(token, str):
                        return _error_response(ClarificationHttpErrorCode.INTERNAL_ERROR.value)
                    selectable_options = getattr(started, "options", None)
                    if selectable_options is None:
                        return _error_response(ClarificationHttpErrorCode.INTERNAL_ERROR.value)
                    options = [
                        {"option_id": opt.option_id, "semantic_ref": opt.semantic_ref}
                        for opt in selectable_options
                    ]
                    out = StartResponse.model_validate(
                        {
                            "version": CLARIFICATION_HTTP_VERSION,
                            "status": "PENDING",
                            "session_token": token,
                            "options": options,
                        }
                    )
            except Exception:
                return _error_response(ClarificationHttpErrorCode.INTERNAL_ERROR.value)
            try:
                return JSONResponse(status_code=200, content=out.model_dump(mode="json", warnings=False, exclude_none=True))
            except Exception:
                return _error_response(ClarificationHttpErrorCode.INTERNAL_ERROR.value)
        except Exception:
            return _error_response(ClarificationHttpErrorCode.INTERNAL_ERROR.value)

    @app.post(SUBMIT_PATH)
    async def submit_clarification(http_request: Request) -> JSONResponse:
        try:
            payload = _parse_json_object(await http_request.body())
            if payload is None:
                return _error_response(ClarificationHttpErrorCode.INVALID_REQUEST.value)
            try:
                body = SubmitRequestBody.model_validate(payload)
            except Exception:
                return _error_response(ClarificationHttpErrorCode.INVALID_REQUEST.value)
            try:
                choice = ClarificationChoice.model_validate(
                    {"selection": list(body.selection), "cancelled": bool(body.cancelled)}
                )
            except Exception:
                return _error_response(ClarificationHttpErrorCode.INVALID_REQUEST.value)
            extra: dict[str, Any] = {}
            if compiler is not None:
                extra["compiler"] = compiler
            if sql_policy is not None:
                extra["sql_policy"] = sql_policy
            try:
                submitted = coordinator.submit(body.session_token, choice, provider, executor, **extra)
            except (ClarificationSessionError, ClarificationError, AmbiguityDetectorError) as exc:
                return _error_response(_fixed_code(exc))
            except ClarificationQueryConstraintError as exc:
                return _error_response(_fixed_code(exc))
            except ClarificationExecutionAdapterError as exc:
                return _error_response(_fixed_code(exc))
            except Exception as exc:
                candidate = getattr(exc, "code", None)
                if isinstance(candidate, (str, Enum)) and candidate:
                    return _error_response(_fixed_code(exc))
                return _error_response(ClarificationHttpErrorCode.INTERNAL_ERROR.value)
            try:
                if submitted.status is ClarificationSessionSubmitStatus.CANCELLED:
                    out = SubmitResponse.model_validate(
                        {"version": CLARIFICATION_HTTP_VERSION, "status": "CANCELLED"}
                    )
                else:
                    execution = getattr(submitted, "execution", None)
                    harness_result = getattr(execution, "harness_result", None) if execution is not None else None
                    if harness_result is None:
                        return _error_response(ClarificationHttpErrorCode.INTERNAL_ERROR.value)
                    if harness_result.status is HarnessStatus.SUCCEEDED:
                        columns = list(getattr(harness_result, "columns", None) or [])
                        rows = getattr(harness_result, "rows", None)
                        if not isinstance(columns, list) or not isinstance(rows, list):
                            return _error_response(ClarificationHttpErrorCode.INTERNAL_ERROR.value)
                        if any(not isinstance(col, str) for col in columns):
                            return _error_response(ClarificationHttpErrorCode.INTERNAL_ERROR.value)
                        if any(not isinstance(row, dict) for row in rows):
                            return _error_response(ClarificationHttpErrorCode.INTERNAL_ERROR.value)
                        if not _walk_json_finite(rows):
                            return _error_response(ClarificationHttpErrorCode.INTERNAL_ERROR.value)
                        try:
                            checked_rows = [
                                {str(key): value for key, value in row.items()} for row in rows
                            ]
                            HttpExecution.model_validate(
                                {
                                    "status": "SUCCEEDED",
                                    "columns": columns,
                                    "rows": checked_rows,
                                    "error": None,
                                }
                            )
                        except Exception:
                            return _error_response(ClarificationHttpErrorCode.INTERNAL_ERROR.value)
                        out = SubmitResponse.model_validate(
                            {
                                "version": CLARIFICATION_HTTP_VERSION,
                                "status": "EXECUTED",
                                "execution": {
                                    "status": "SUCCEEDED",
                                    "columns": columns,
                                    "rows": checked_rows,
                                    "error": None,
                                },
                            }
                        )
                    else:
                        err = getattr(harness_result, "error", None)
                        if err is None or getattr(err, "code", None) is None:
                            return _error_response(ClarificationHttpErrorCode.INTERNAL_ERROR.value)
                        raw_code = err.code.value if isinstance(err.code, Enum) else str(err.code)
                        raw_stage = getattr(err, "stage", None)
                        if raw_stage is None:
                            return _error_response(
                                ClarificationHttpErrorCode.INTERNAL_ERROR.value
                            )
                        stage_value = (
                            raw_stage.value
                            if isinstance(raw_stage, Enum)
                            else str(raw_stage)
                        )
                        error_payload: dict[str, Any] = {
                            "code": raw_code,
                            "stage": stage_value,
                        }
                        try:
                            HttpExecution.model_validate(
                                {"status": "FAILED", "columns": None, "rows": None, "error": error_payload}
                            )
                        except Exception:
                            return _error_response(ClarificationHttpErrorCode.INTERNAL_ERROR.value)
                        out = SubmitResponse.model_validate(
                            {
                                "version": CLARIFICATION_HTTP_VERSION,
                                "status": "EXECUTED",
                                "execution": {
                                    "status": "FAILED",
                                    "columns": None,
                                    "rows": None,
                                    "error": error_payload,
                                },
                            }
                        )
            except Exception:
                return _error_response(ClarificationHttpErrorCode.INTERNAL_ERROR.value)
            try:
                return JSONResponse(status_code=200, content=out.model_dump(mode="json", warnings=False, exclude_none=True))
            except Exception:
                return _error_response(ClarificationHttpErrorCode.INTERNAL_ERROR.value)
        except Exception:
            return _error_response(ClarificationHttpErrorCode.INTERNAL_ERROR.value)

    return app
