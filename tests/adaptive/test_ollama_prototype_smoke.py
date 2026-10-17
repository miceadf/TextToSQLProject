"""Phase 7-09: synthetic prototype Ollama live smoke CLI (synthetic transport only)."""

from __future__ import annotations

import json
from typing import Any

import pytest

import backend.adaptive.api.ollama_prototype_smoke as smoke
import backend.adaptive.api.ollama_startup as startup
import backend.adaptive.api.app_factory as proto
from tests.adaptive.test_ollama_constraint_provider import CountingTransport, _smart_transport


TAG = "synthetic-p709-tag:latest"
SECRET_MARKER = "p709secret"


def _run(argv: list[str], capsys: Any) -> tuple[int, dict[str, Any], str]:
    try:
        code = smoke.main(argv)
    except SystemExit as exc:
        code = exc.code
    out, err = capsys.readouterr()
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) <= 1, "stdout must be at most one JSON line"
    payload = json.loads(lines[0]) if lines else {}
    return code, payload, out + err


def _record_http(monkeypatch: Any) -> dict[str, list[str]]:
    """Capture tokens, option IDs, and row values the smoke saw internally."""
    seen: dict[str, list[str]] = {"tokens": [], "option_ids": [], "values": []}
    real_start, real_submit = smoke.StartResponse, smoke.SubmitResponse

    class _Start:
        @staticmethod
        def model_validate(data: Any) -> Any:
            out = real_start.model_validate(data)
            if out.session_token:
                seen["tokens"].append(out.session_token)
            seen["option_ids"].extend(o.option_id for o in out.options or [])
            return out

    class _Submit:
        @staticmethod
        def model_validate(data: Any) -> Any:
            out = real_submit.model_validate(data)
            execution = out.execution
            for row in (execution.rows or []) if execution is not None else []:
                seen["values"].extend(str(v) for v in row.values())
            return out

    monkeypatch.setattr(smoke, "StartResponse", _Start)
    monkeypatch.setattr(smoke, "SubmitResponse", _Submit)
    return seen


def _live(capsys: Any, *extra: str) -> tuple[int, dict[str, Any], str]:
    return _run(
        ["--live-synthetic", "--ollama-model-tag", TAG, "--ollama-request-format", "alpaca-raw-no-system-v1", *extra],
        capsys,
    )


def test_live_smoke_success_path(monkeypatch: Any, capsys: Any) -> None:
    transport = _smart_transport()
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _config: transport)
    seen = _record_http(monkeypatch)
    code, payload, text = _live(capsys, "--ollama-timeout", "60")
    assert len(seen["tokens"]) == 5
    assert len(seen["option_ids"]) == 10
    assert len(seen["values"]) == 24
    for secret in (*seen["tokens"], *seen["option_ids"], *set(seen["values"])):
        assert secret not in text
    assert code == 0
    assert payload["status"] == "COMPLETED"
    assert payload["version"] == "ollama-prototype-smoke-v1"
    assert payload["model_tag"] == TAG
    assert payload["prompt_version"] == "query-ir-v1"
    assert payload["request_format"] == "alpaca-raw-no-system-v1"
    assert payload["timeout_seconds"] == 60.0
    assert payload["summary"] == {
        "submit_cases": 4,
        "executed_succeeded": 4,
        "columns_match_constraint": 4,
        "model_calls": 4,
        "expected_model_calls": 4,
    }
    assert len(transport.calls) == 4
    by_id = {case["case_id"]: case for case in payload["cases"]}
    assert set(by_id) == {
        "p709-flat-course",
        "p709-flat-instructor",
        "p709-legacy-course",
        "p709-legacy-instructor",
        "p709-flat-cancel",
        "p709-flat-no-match",
    }
    for case_id in ("p709-flat-course", "p709-legacy-instructor"):
        case = by_id[case_id]
        assert case["start_status"] == "PENDING"
        assert case["http_status"] == 200
        assert case["outcome"] == "EXECUTED_SUCCEEDED"
        assert case["columns_match_constraint"] is True
        assert case["row_count"] == 6
        assert case["model_calls"] == 1
        assert case["boundary_ok"] is True
    assert by_id["p709-flat-cancel"]["outcome"] == "CANCELLED"
    assert by_id["p709-flat-cancel"]["model_calls"] == 0
    assert by_id["p709-flat-no-match"]["start_status"] == "NOT_REQUIRED"
    assert by_id["p709-flat-no-match"]["model_calls"] == 0
    # Each constrained prompt carries exactly the selected semantic field.
    for (_url, payload_sent, timeout), ref in zip(
        transport.calls, ["Course.name", "Instructor.name", "Course.name", "Instructor.name"]
    ):
        assert timeout == 60.0
        assert payload_sent["raw"] is True
        context = json.loads(payload_sent["prompt"].split("Context: ", 1)[1].split("\nQuestion:", 1)[0])
        assert context["available_fields"] == [ref]
    for secret in ("127.0.0.1", "11434", "강의", "SELECT", "session_token", "option_id"):
        assert secret not in text


def test_live_smoke_model_failures_are_recorded_with_fixed_codes(
    monkeypatch: Any, capsys: Any
) -> None:
    transport = CountingTransport(
        [
            TimeoutError(SECRET_MARKER),
            {"response": f"SELECT * FROM {SECRET_MARKER}"},
            {"response": json.dumps({"entity": "Instructor", "select": ["Instructor.name"], "filters": [], "limit": 5})},
            RuntimeError(SECRET_MARKER),
        ]
    )
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _config: transport)
    code, payload, text = _live(capsys)
    assert code == 0
    assert payload["status"] == "COMPLETED"
    assert payload["summary"]["executed_succeeded"] == 0
    assert payload["summary"]["model_calls"] == 4
    outcomes = [case["outcome"] for case in payload["cases"][:4]]
    assert outcomes == [
        "CONSTRAINT_PROVIDER_ERROR",
        "CONSTRAINT_INVALID_QUERY_IR",
        "CONSTRAINT_VIOLATION",
        "CONSTRAINT_PROVIDER_ERROR",
    ]
    assert [case["http_status"] for case in payload["cases"][:4]] == [502, 422, 422, 502]
    assert all(case["boundary_ok"] for case in payload["cases"])
    assert SECRET_MARKER not in text
    assert "SELECT" not in text


def test_success_with_extra_canonical_column_is_observation_only(
    monkeypatch: Any, capsys: Any
) -> None:
    both = {"entity": "Course", "select": ["Course.name", "Instructor.name"], "filters": [], "limit": 50}
    transport = CountingTransport([{"response": json.dumps(both)}] * 4)
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _config: transport)
    code, payload, _text = _live(capsys)
    assert code == 0
    assert payload["status"] == "COMPLETED"
    assert payload["summary"]["executed_succeeded"] == 4
    assert payload["summary"]["columns_match_constraint"] == 0
    assert all(case["boundary_ok"] for case in payload["cases"])


def test_physical_name_in_response_is_boundary_violation(
    monkeypatch: Any, capsys: Any
) -> None:
    transport = _smart_transport()
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _config: transport)
    real_client_cls = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient

    class _LeakyResponse:
        def __init__(self, inner: Any) -> None:
            self._inner = inner
            self.status_code = inner.status_code
            self.text = inner.text + " lecture_info"

        def json(self) -> Any:
            return self._inner.json()

    class _LeakyClient(real_client_cls):  # type: ignore[misc, valid-type]
        def post(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
            return _LeakyResponse(super().post(*args, **kwargs))

    monkeypatch.setattr("fastapi.testclient.TestClient", _LeakyClient)
    code, payload, text = _live(capsys)
    assert code == 1
    assert payload["status"] == "BOUNDARY_VIOLATION"
    assert not any(case["boundary_ok"] for case in payload["cases"])
    assert "lecture_info" not in text


def test_budget_violation_is_detected(monkeypatch: Any, capsys: Any) -> None:
    inner = _smart_transport()

    class _Twice:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        def __call__(self, url: str, payload: dict[str, Any], timeout: float) -> Any:
            self.calls.append(url)
            return inner(url, payload, timeout)

    twice = _Twice()
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _config: twice)
    original = smoke._CountingTransport.__call__

    def _double(self: Any, url: str, payload: dict[str, object], timeout: float) -> object:
        original(self, url, payload, timeout)
        return original(self, url, payload, timeout)

    monkeypatch.setattr(smoke._CountingTransport, "__call__", _double)
    code, payload, _text = _live(capsys)
    assert code == 1
    assert payload["status"] == "BOUNDARY_VIOLATION"
    assert payload["summary"]["model_calls"] == 8
    assert not all(case["boundary_ok"] for case in payload["cases"])


def test_check_mode_assembles_without_calls(monkeypatch: Any, capsys: Any) -> None:
    transport = CountingTransport([])
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _config: transport)
    cleaned: list[bool] = []
    real_factory = proto.create_synthetic_clarification_prototype

    def _factory(**kwargs: Any) -> Any:
        app = real_factory(**kwargs)
        inner = app.state.adaptive_app_cleanup

        def _cleanup() -> None:
            cleaned.append(True)
            inner()

        app.state.adaptive_app_cleanup = _cleanup
        return app

    monkeypatch.setattr(smoke, "create_synthetic_clarification_prototype", _factory)
    code, payload, _text = _run(["--check", "--ollama-model-tag", TAG, "--ollama-request-format", "model-template"], capsys)
    assert code == 0
    assert payload["status"] == "CHECKED"
    assert payload["model_calls"] == 0
    assert transport.calls == []
    assert cleaned == [True]


def test_cleanup_runs_when_smoke_raises(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _config: _smart_transport())
    cleaned: list[bool] = []
    real_factory = proto.create_synthetic_clarification_prototype

    def _factory(**kwargs: Any) -> Any:
        app = real_factory(**kwargs)
        inner = app.state.adaptive_app_cleanup

        def _cleanup() -> None:
            cleaned.append(True)
            inner()

        app.state.adaptive_app_cleanup = _cleanup
        return app

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError(SECRET_MARKER)

    monkeypatch.setattr(smoke, "create_synthetic_clarification_prototype", _factory)
    monkeypatch.setattr(smoke, "_run_case", _boom)
    code, payload, text = _live(capsys)
    assert code == 1
    assert payload == {"status": "SMOKE_ERROR"}
    assert cleaned == [True]
    assert SECRET_MARKER not in text


@pytest.mark.parametrize(
    "argv",
    [
        ["--ollama-model-tag", TAG],
        ["--check", "--live-synthetic", "--ollama-model-tag", TAG],
        ["--live-synthetic"],
        ["--live-synthetic", "--ollama-model-tag", f"{SECRET_MARKER} bad"],
        ["--live-synthetic", "--ollama-model-tag", TAG, "--ollama-base-url", f"http://{SECRET_MARKER}:1"],
        ["--live-synthetic", "--ollama-model-tag", TAG, "--ollama-base-url", "http://localhost:11434"],
        ["--live-synthetic", "--ollama-model-tag", TAG, "--ollama-timeout", "0"],
    ],
)
def test_config_errors_are_fixed_and_do_not_call(
    monkeypatch: Any, capsys: Any, argv: list[str]
) -> None:
    def _explode(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("must not assemble")

    monkeypatch.setattr(startup, "build_loopback_transport", _explode)
    monkeypatch.setattr(smoke, "create_synthetic_clarification_prototype", _explode)
    code, payload, text = _run(argv, capsys)
    assert code == 2
    assert payload == {"status": "CONFIG_ERROR"}
    assert SECRET_MARKER not in text


@pytest.mark.parametrize(
    "argv",
    [
        ["--live-synthetic", "--ollama-model-tag", TAG, "--ollama-model-tag", TAG],
        ["--live-synthetic", "--ollama-model", TAG],
        ["--live-synthetic", "--ollama-model-tag", TAG, f"--unknown-{SECRET_MARKER}"],
    ],
)
def test_argparse_errors_are_fixed(monkeypatch: Any, capsys: Any, argv: list[str]) -> None:
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _c: CountingTransport([]))
    with pytest.raises(SystemExit) as info:
        smoke.main(argv)
    assert info.value.code == 2
    out, err = capsys.readouterr()
    assert err.strip() == "synthetic prototype invalid configuration"
    assert SECRET_MARKER not in out + err
    assert TAG not in out + err



def test_request_format_is_required(monkeypatch: Any, capsys: Any) -> None:
    def _explode(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("must not assemble")

    monkeypatch.setattr(startup, "build_loopback_transport", _explode)
    for argv in (
        ["--live-synthetic", "--ollama-model-tag", TAG],
        ["--check", "--ollama-model-tag", TAG, "--ollama-request-format", "raw"],
    ):
        code, payload, _ = _run(argv, capsys)
        assert code == 2 and payload == {"status": "CONFIG_ERROR"}
