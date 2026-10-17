"""Opt-in synthetic end-to-end live smoke for the Ollama clarification prototype.

Drives the Phase 7-08 prototype (``create_synthetic_clarification_prototype``
with a server-owned loopback ``OllamaQueryModel``) in-process through fixed,
fully synthetic questions: start -> option -> submit -> QueryIR validation ->
compile -> policy -> read-only SQLite execution. This checks call budgets,
strict response shapes, and redaction against a live local model. It is a
format/boundary smoke, not an accuracy or performance evaluation.

Only CLI arguments are read. No environment, private artifact, PostgreSQL,
port binding, retry, response storage, or raw model/prompt/QueryIR/SQL/row
output. stdout carries one fixed-shape JSON line.
"""

from __future__ import annotations

import json
import time
from typing import Any

from . import ollama_startup
from .clarification_http import (
    START_PATH,
    SUBMIT_PATH,
    StartResponse,
    SubmitResponse,
    _ERROR_STATUS,
)
from .ollama_startup import (
    OLLAMA_STARTUP_PROMPT_VERSION,
    build_startup_ollama_model,
    parse_ollama_startup_config,
)
from .app_factory import (
    SYNTHETIC_LEGACY_DATABASE_ID,
    SYNTHETIC_PROTOTYPE_DATABASE_ID,
    _CLI_CONFIG_FAILURE,
    _StoreOnce,
    _StrictArgumentParser,
    create_synthetic_clarification_prototype,
)


OLLAMA_PROTOTYPE_SMOKE_VERSION = "ollama-prototype-smoke-v1"

_COURSE_REF = "Course.name"
_INSTRUCTOR_REF = "Instructor.name"
_CANONICAL_COLUMNS = frozenset({_COURSE_REF, _INSTRUCTOR_REF})
# Physical names of the synthetic flat/legacy mappings; never allowed in responses.
_PHYSICAL_MARKERS = ("course_offerings", "course_name", "instructor_name", "lecture_info", "sbjt_nm", "prof_nm")
_DATABASES = {
    "flat": SYNTHETIC_PROTOTYPE_DATABASE_ID,
    "legacy": SYNTHETIC_LEGACY_DATABASE_ID,
}
# Fully synthetic fixed questions; "강의" is the prototype ambiguity trigger.
_AMBIGUOUS_QUESTION = "합성 스모크 P709: 강의 목록을 보여줘"
_NO_MATCH_QUESTION = "합성 스모크 P709: 학과 목록을 보여줘"

# (case_id, database key, action, selected ref, question, expected model calls)
_CASES: tuple[tuple[str, str, str, str | None, str, int], ...] = (
    ("p709-flat-course", "flat", "submit", _COURSE_REF, _AMBIGUOUS_QUESTION, 1),
    ("p709-flat-instructor", "flat", "submit", _INSTRUCTOR_REF, _AMBIGUOUS_QUESTION, 1),
    ("p709-legacy-course", "legacy", "submit", _COURSE_REF, _AMBIGUOUS_QUESTION, 1),
    ("p709-legacy-instructor", "legacy", "submit", _INSTRUCTOR_REF, _AMBIGUOUS_QUESTION, 1),
    ("p709-flat-cancel", "flat", "cancel", None, _AMBIGUOUS_QUESTION, 0),
    ("p709-flat-no-match", "flat", "no_match", None, _NO_MATCH_QUESTION, 0),
)
EXPECTED_TOTAL_MODEL_CALLS = sum(case[5] for case in _CASES)

_CONFIG_ERROR = {"status": "CONFIG_ERROR"}
_SMOKE_ERROR = {"status": "SMOKE_ERROR"}


class _CountingTransport:
    """Delegate to the loopback transport and count calls only."""

    __slots__ = ("_inner", "calls")

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls = 0

    def __repr__(self) -> str:
        return "CountingTransport()"

    def __call__(self, url: str, payload: dict[str, object], timeout: float) -> object:
        self.calls += 1
        return self._inner(url, payload, timeout)


def _error_code(response: Any) -> str | None:
    """Return the fixed error code if the body is exactly the error shape."""
    try:
        body = response.json()
    except Exception:
        return None
    if not isinstance(body, dict) or set(body) != {"error"}:
        return None
    error = body["error"]
    if not isinstance(error, dict) or set(error) != {"code"}:
        return None
    code = error["code"]
    if not isinstance(code, str) or _ERROR_STATUS.get(code) != response.status_code:
        return None
    return code


def _leak_free(text: str, forbidden: tuple[str, ...]) -> bool:
    return not any(marker and marker in text for marker in forbidden)


def _run_case(
    client: Any,
    counter: _CountingTransport,
    case: tuple[str, str, str, str | None, str, int],
    forbidden: tuple[str, ...],
) -> dict[str, Any]:
    case_id, db_key, action, selected_ref, question, expected_calls = case
    record: dict[str, Any] = {
        "case_id": case_id,
        "database": db_key,
        "action": action,
    }
    if selected_ref is not None:
        record["selected_ref"] = selected_ref
    ok = True
    calls_before = counter.calls
    started_at = time.perf_counter()

    start = client.post(
        START_PATH,
        json={
            "request_id": f"req-{case_id}",
            "database_id": _DATABASES[db_key],
            "question": question,
        },
    )
    texts = [start.text]
    start_body: Any = None
    if start.status_code == 200:
        try:
            start_body = StartResponse.model_validate(start.json())
        except Exception:
            start_body = None
    if start_body is None:
        ok = False
        record["start_status"] = "INVALID"
    else:
        record["start_status"] = start_body.status.value
    ok = ok and counter.calls == calls_before

    expected_start = "NOT_REQUIRED" if action == "no_match" else "PENDING"
    submit = None
    if start_body is None or record["start_status"] != expected_start:
        ok = False
        record["outcome"] = "UNEXPECTED_START"
    elif action == "no_match":
        record["outcome"] = "NOT_REQUIRED"
    else:
        token = start_body.session_token
        if action == "cancel":
            body = {"session_token": token, "selection": [], "cancelled": True}
        else:
            matches = [o for o in start_body.options or [] if o.semantic_ref == selected_ref]
            if len(matches) != 1:
                ok = False
                body = None
            else:
                body = {"session_token": token, "selection": [matches[0].option_id]}
        if body is None:
            record["outcome"] = "OPTION_MISSING"
        else:
            submit = client.post(SUBMIT_PATH, json=body)
            texts.append(submit.text)
            record["http_status"] = submit.status_code
            ok = ok and token not in submit.text
            if submit.status_code == 200:
                try:
                    parsed = SubmitResponse.model_validate(submit.json())
                except Exception:
                    parsed = None
                if parsed is None:
                    ok = False
                    record["outcome"] = "INVALID_RESPONSE"
                elif action == "cancel":
                    ok = ok and parsed.status.value == "CANCELLED"
                    record["outcome"] = parsed.status.value
                elif parsed.status.value != "EXECUTED" or parsed.execution is None:
                    ok = False
                    record["outcome"] = "INVALID_RESPONSE"
                elif parsed.execution.status.value == "SUCCEEDED":
                    record["outcome"] = "EXECUTED_SUCCEEDED"
                    columns = list(parsed.execution.columns or [])
                    # Canonical semantic columns only; exact [ref] is an observation,
                    # since Phase 6-09 also accepts the ref used only in a filter.
                    ok = ok and bool(columns) and set(columns) <= _CANONICAL_COLUMNS
                    record["columns_match_constraint"] = parsed.execution.columns == [selected_ref]
                    record["row_count"] = len(parsed.execution.rows or [])
                else:
                    error = parsed.execution.error
                    record["outcome"] = error.code.value if error is not None else "INVALID_RESPONSE"
                    ok = ok and error is not None
            else:
                code = _error_code(submit)
                if code is None or action == "cancel":
                    ok = False
                    record["outcome"] = "INVALID_RESPONSE"
                else:
                    record["outcome"] = code

    record["model_calls"] = counter.calls - calls_before
    record["elapsed_ms"] = int((time.perf_counter() - started_at) * 1000)
    ok = ok and record["model_calls"] == expected_calls
    ok = ok and all(_leak_free(text, forbidden) for text in texts)
    record["boundary_ok"] = bool(ok)
    return record


def run_prototype_smoke(config: Any) -> dict[str, Any]:
    """Run the fixed synthetic cases once against the configured model."""
    from fastapi.testclient import TestClient

    counter = _CountingTransport(ollama_startup.build_loopback_transport(config))
    model = build_startup_ollama_model(config, transport=counter)
    app = create_synthetic_clarification_prototype(ollama_model=model)
    try:
        if app.state.adaptive_app_provider_kind != "ollama" or counter.calls != 0:
            raise RuntimeError(_SMOKE_ERROR["status"])
        forbidden = (
            config.model_tag,
            config.base_url,
            _AMBIGUOUS_QUESTION,
            _NO_MATCH_QUESTION,
            "SELECT",
            *_PHYSICAL_MARKERS,
        )
        client = TestClient(app, raise_server_exceptions=False)
        cases = [_run_case(client, counter, case, forbidden) for case in _CASES]
    finally:
        cleanup = getattr(app.state, "adaptive_app_cleanup", None)
        if callable(cleanup):
            cleanup()
    submits = [case for case in cases if case["action"] == "submit"]
    boundary_ok = all(case["boundary_ok"] for case in cases) and (
        counter.calls == EXPECTED_TOTAL_MODEL_CALLS
    )
    return {
        "version": OLLAMA_PROTOTYPE_SMOKE_VERSION,
        "status": "COMPLETED" if boundary_ok else "BOUNDARY_VIOLATION",
        "model_tag": config.model_tag,
        "request_format": config.request_format,
        "prompt_version": OLLAMA_STARTUP_PROMPT_VERSION,
        "timeout_seconds": config.timeout_seconds,
        "summary": {
            "submit_cases": len(submits),
            "executed_succeeded": sum(c["outcome"] == "EXECUTED_SUCCEEDED" for c in submits),
            "columns_match_constraint": sum(bool(c.get("columns_match_constraint")) for c in submits),
            "model_calls": counter.calls,
            "expected_model_calls": EXPECTED_TOTAL_MODEL_CALLS,
        },
        "cases": cases,
    }


def check_prototype_smoke(config: Any) -> dict[str, Any]:
    """Assemble everything without calling the model."""
    counter = _CountingTransport(ollama_startup.build_loopback_transport(config))
    model = build_startup_ollama_model(config, transport=counter)
    app = create_synthetic_clarification_prototype(ollama_model=model)
    try:
        ready = app.state.adaptive_app_provider_kind == "ollama"
    finally:
        cleanup = getattr(app.state, "adaptive_app_cleanup", None)
        if callable(cleanup):
            cleanup()
    if not ready or counter.calls != 0:
        raise RuntimeError(_SMOKE_ERROR["status"])
    return {
        "version": OLLAMA_PROTOTYPE_SMOKE_VERSION,
        "status": "CHECKED",
        "model_tag": config.model_tag,
        "request_format": config.request_format,
        "prompt_version": OLLAMA_STARTUP_PROMPT_VERSION,
        "timeout_seconds": config.timeout_seconds,
        "model_calls": 0,
    }


def _build_parser() -> Any:
    parser = _StrictArgumentParser(
        prog="backend.adaptive.api.ollama_prototype_smoke",
        description="Opt-in synthetic Ollama prototype live smoke (loopback only).",
        allow_abbrev=False,
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--live-synthetic", action="store_true", dest="live_synthetic")
    parser.add_argument("--ollama-model-tag", action=_StoreOnce, default=None, metavar="TAG")
    parser.add_argument("--ollama-base-url", action=_StoreOnce, default=None, metavar="URL")
    parser.add_argument("--ollama-timeout", action=_StoreOnce, default=None, metavar="SECONDS")
    parser.add_argument("--ollama-request-format", action=_StoreOnce, default=None, metavar="FORMAT")
    return parser


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if bool(args.check) == bool(args.live_synthetic):
        _emit(_CONFIG_ERROR)
        return 2
    try:
        if args.ollama_model_tag is None or args.ollama_request_format is None:
            raise ValueError(_CLI_CONFIG_FAILURE)
        config = parse_ollama_startup_config(
            args.ollama_model_tag,
            args.ollama_base_url,
            args.ollama_timeout,
            args.ollama_request_format,
        )
    except Exception:
        _emit(_CONFIG_ERROR)
        return 2
    try:
        if args.check:
            _emit(check_prototype_smoke(config))
            return 0
        result = run_prototype_smoke(config)
    except Exception:
        _emit(_SMOKE_ERROR)
        return 1
    _emit(result)
    return 0 if result["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
