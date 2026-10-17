"""Phase 7-08: server-owned Ollama startup configuration and provider selection.

All model responses come from synthetic transports. Transport-level proxy and
redirect checks use short-lived ``127.0.0.1`` test servers only; no external
host, live Ollama, PostgreSQL server, or CNU data is used.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from fastapi.testclient import TestClient

import backend.adaptive.api.ollama_startup as startup
import backend.adaptive.api.app_factory as proto
from backend.adaptive.api.clarification_http import SUBMIT_PATH
from backend.adaptive.api.ollama_startup import (
    DEFAULT_OLLAMA_BASE_URL,
    OllamaLoopbackTransportError,
    OllamaStartupConfig,
    OllamaStartupConfigError,
    build_loopback_transport,
    build_startup_ollama_model,
    parse_ollama_startup_config,
)
from backend.adaptive.api.app_factory import (
    SYNTHETIC_LEGACY_DATABASE_ID,
    SYNTHETIC_PROTOTYPE_DATABASE_ID,
    create_synthetic_clarification_prototype,
)
from backend.adaptive.harness import OllamaQueryModel
from backend.adaptive.harness.ollama_constraint import OllamaConstraintProvider
from backend.adaptive.harness.ollama_request_format import ALPACA_NO_SYSTEM_PREFIX, ALPACA_NO_SYSTEM_SUFFIX
from tests.adaptive.test_ollama_constraint_provider import (
    PG_DATABASE_ID,
    QUESTION_BASE,
    CountingTransport,
    _PgAdapter,
    _PgExecutor,
    _pg_profile,
    _smart_transport,
    _start,
)


TAG = "synthetic-p708-tag:latest"
FIXED_CLI_ERROR = "synthetic prototype invalid configuration"
SECRET_URL_MARKER = "p708secret"


# --------------------------------------------------------------------------
# config parsing


def test_parse_defaults_and_valid_values() -> None:
    config = parse_ollama_startup_config(TAG)
    assert config.base_url == DEFAULT_OLLAMA_BASE_URL
    assert config.model_tag == TAG
    assert config.timeout_seconds == 30.0
    ipv6 = parse_ollama_startup_config(TAG, "http://[::1]:11500", "1")
    assert ipv6.base_url == "http://[::1]:11500"
    assert ipv6.timeout_seconds == 1.0
    assert parse_ollama_startup_config(TAG, "http://127.0.0.1:65535", "120").timeout_seconds == 120.0
    assert parse_ollama_startup_config(TAG, None, "12.345").timeout_seconds == 12.345


@pytest.mark.parametrize(
    "tag",
    ["", " ", "tag\n", "tag ", "-tag", "태그", "a" * 161, "tag/evil", None, 1, b"tag", True],
)
def test_parse_rejects_bad_model_tag(tag: Any) -> None:
    with pytest.raises(OllamaStartupConfigError) as info:
        parse_ollama_startup_config(tag)
    _assert_fixed_config_error(info.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "http://0.0.0.0:11434",
        "http://10.0.0.5:11434",
        f"http://{SECRET_URL_MARKER}.example:11434",
        "https://127.0.0.1:11434",
        "HTTP://127.0.0.1:11434",
        "http://127.0.0.1:11434/",
        "http://127.0.0.1:11434/api",
        "http://127.0.0.1:11434?x=1",
        "http://127.0.0.1:11434#f",
        "http://user:pw@127.0.0.1:11434",
        "http://127.0.0.1",
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
        "http://127.0.0.1:011434",
        "http://127.1:11434",
        "http://[::1]",
        "http://[0:0:0:0:0:0:0:1]:11434",
        "http://127.0.0.1:11434\n",
        " http://127.0.0.1:11434",
        "",
        1,
        b"http://127.0.0.1:11434",
    ],
)
def test_parse_rejects_non_loopback_or_malformed_url(url: Any) -> None:
    with pytest.raises(OllamaStartupConfigError) as info:
        parse_ollama_startup_config(TAG, url)
    _assert_fixed_config_error(info.value)
    assert SECRET_URL_MARKER not in repr(info.value) + str(info.value)


@pytest.mark.parametrize(
    "timeout",
    ["0", "0.5", "-1", "+5", "120.001", "121", "1e2", "nan", "inf", " 30", "30 ", "30.", ".5",
     "1.2345", "", 30, 30.0, True, None.__class__],
)
def test_parse_rejects_bad_timeout(timeout: Any) -> None:
    with pytest.raises(OllamaStartupConfigError) as info:
        parse_ollama_startup_config(TAG, None, timeout)
    _assert_fixed_config_error(info.value)


def test_config_repr_and_errors_do_not_leak_values() -> None:
    config = parse_ollama_startup_config(TAG, "http://127.0.0.1:11999", "7")
    for text in (repr(config), str(config)):
        assert text == "OllamaStartupConfig(version='ollama-startup-config-v2')"
    with pytest.raises(OllamaStartupConfigError) as info:
        parse_ollama_startup_config(f"{SECRET_URL_MARKER}\n", "http://127.0.0.1:11999")
    _assert_fixed_config_error(info.value)


def _assert_fixed_config_error(exc: BaseException) -> None:
    assert exc.args == ("OLLAMA_STARTUP_CONFIG_INVALID",)
    assert str(exc) == "OLLAMA_STARTUP_CONFIG_INVALID"
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True or exc.__context__ is None


# --------------------------------------------------------------------------
# model assembly


def test_builder_returns_existing_adapter_without_calling_it() -> None:
    transport = CountingTransport([])
    model = build_startup_ollama_model(parse_ollama_startup_config(TAG), transport=transport)
    assert type(model) is OllamaQueryModel
    assert model.prompt_version == "query-ir-v1"
    assert transport.calls == []


def test_builder_default_transport_reads_no_proxy_settings(monkeypatch: Any) -> None:
    def _explode(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("proxy settings must not be read")

    monkeypatch.setattr(urllib.request, "getproxies", _explode)
    monkeypatch.setattr(urllib.request, "urlopen", _explode)
    model = build_startup_ollama_model(parse_ollama_startup_config(TAG))
    assert type(model) is OllamaQueryModel
    transport = build_loopback_transport(parse_ollama_startup_config(TAG))
    assert repr(transport) == str(transport)
    assert "127.0.0.1" not in repr(transport)
    assert TAG not in repr(transport)


def test_builder_rejects_forged_or_constructed_config() -> None:
    forged = [
        object(),
        {"base_url": DEFAULT_OLLAMA_BASE_URL, "model_tag": TAG, "timeout_seconds": 30.0},
        OllamaStartupConfig(f"http://{SECRET_URL_MARKER}.example:1", TAG, 30.0),
        OllamaStartupConfig(DEFAULT_OLLAMA_BASE_URL, "bad tag", 30.0),
        OllamaStartupConfig(DEFAULT_OLLAMA_BASE_URL, TAG, 999.0),
        OllamaStartupConfig(DEFAULT_OLLAMA_BASE_URL, TAG, 30),  # int, not float
        OllamaStartupConfig(DEFAULT_OLLAMA_BASE_URL, TAG, float("nan")),
    ]
    for config in forged:
        with pytest.raises(OllamaStartupConfigError) as info:
            build_startup_ollama_model(config, transport=CountingTransport([]))  # type: ignore[arg-type]
        _assert_fixed_config_error(info.value)
        assert SECRET_URL_MARKER not in str(info.value)
        with pytest.raises(OllamaStartupConfigError):
            build_loopback_transport(config)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# loopback transport (127.0.0.1 test servers only)


class _Server:
    def __init__(self, handler: Any) -> None:
        self.hits: list[str] = []
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def _record(self) -> None:
                outer.hits.append(self.command + " " + self.path)

            def do_POST(self) -> None:  # noqa: N802
                self._record()
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                handler(self)

            def do_GET(self) -> None:  # noqa: N802
                self._record()
                handler(self)

            def log_message(self, *_args: Any) -> None:
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.daemon_threads = True
        # Client-side aborts (oversize/timeout cases) are expected; keep output quiet.
        self.httpd.handle_error = lambda *_args: None  # type: ignore[method-assign]
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _reply(status: int, body: bytes, headers: dict[str, str] | None = None) -> Any:
    def handler(req: BaseHTTPRequestHandler) -> None:
        req.send_response(status)
        for key, value in (headers or {}).items():
            req.send_header(key, value)
        req.send_header("Content-Length", str(len(body)))
        req.end_headers()
        req.wfile.write(body)

    return handler


@pytest.fixture
def servers() -> Any:
    created: list[_Server] = []

    def make(handler: Any) -> _Server:
        server = _Server(handler)
        created.append(server)
        return server

    yield make
    for server in created:
        server.close()


def _transport_for(server: _Server, timeout: str = "5") -> tuple[Any, OllamaStartupConfig]:
    config = parse_ollama_startup_config(TAG, server.base_url, timeout)
    return build_loopback_transport(config), config


def _payload() -> dict[str, object]:
    return {"model": TAG, "prompt": "p", "stream": False, "options": {"temperature": 0}}


def test_transport_success_parses_envelope(servers: Any) -> None:
    server = servers(_reply(200, b'{"response":"{}","done":true}'))
    transport, config = _transport_for(server)
    out = transport(config.base_url + "/api/generate", _payload(), config.timeout_seconds)
    assert out == {"response": "{}", "done": True}
    assert server.hits == ["POST /api/generate"]


def test_transport_ipv6_loopback(monkeypatch: Any) -> None:
    import socket

    class _V6Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6

    hits: list[str] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            hits.append(self.path)
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            _reply(200, b'{"response":"{}"}')(self)

        def log_message(self, *_args: Any) -> None:
            return

    try:
        httpd = _V6Server(("::1", 0), _Handler)
    except OSError:
        pytest.skip("IPv6 loopback unavailable")
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        for name in ("HTTP_PROXY", "http_proxy"):
            monkeypatch.setenv(name, "http://127.0.0.1:9")
        config = parse_ollama_startup_config(TAG, f"http://[::1]:{httpd.server_address[1]}", "5")
        transport = build_loopback_transport(config)
        out = transport(config.base_url + "/api/generate", _payload(), config.timeout_seconds)
        assert out == {"response": "{}"}
        assert hits == ["/api/generate"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_transport_rejects_url_mismatch_without_request(servers: Any) -> None:
    server = servers(_reply(200, b"{}"))
    transport, config = _transport_for(server)
    for url in (
        config.base_url + "/api/chat",
        config.base_url + "/api/generate/",
        f"http://127.0.0.1:{server.port + 1}/api/generate",
    ):
        with pytest.raises(OllamaLoopbackTransportError):
            transport(url, _payload(), config.timeout_seconds)
    with pytest.raises(OllamaLoopbackTransportError):
        transport(config.base_url + "/api/generate", _payload(), 500.0)
    assert server.hits == []


def test_transport_ignores_proxy_environment(servers: Any, monkeypatch: Any) -> None:
    proxy = servers(_reply(200, b'{"response":"from-proxy"}'))
    target = servers(_reply(200, b'{"response":"from-target"}'))
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, proxy.base_url)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    transport, config = _transport_for(target)
    out = transport(config.base_url + "/api/generate", _payload(), config.timeout_seconds)
    assert out == {"response": "from-target"}
    assert proxy.hits == []
    assert target.hits == ["POST /api/generate"]


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_transport_does_not_follow_redirects(servers: Any, status: int) -> None:
    elsewhere = servers(_reply(200, b'{"response":"elsewhere"}'))
    location = elsewhere.base_url + f"/{SECRET_URL_MARKER}"
    server = servers(_reply(status, b"", {"Location": location}))
    transport, config = _transport_for(server)
    with pytest.raises(OllamaLoopbackTransportError) as info:
        transport(config.base_url + "/api/generate", _payload(), config.timeout_seconds)
    assert elsewhere.hits == []
    assert str(info.value) == "OLLAMA_LOOPBACK_TRANSPORT_ERROR"
    assert SECRET_URL_MARKER not in repr(info.value)
    assert info.value.__cause__ is None


@pytest.mark.parametrize(
    "status,body",
    [
        (500, b'{"error":"' + SECRET_URL_MARKER.encode() + b'"}'),
        (404, b"not found"),
        (204, b""),
        (200, b"not json " + SECRET_URL_MARKER.encode()),
        (200, b'{"response":"a","response":"b"}'),
        (200, b'{"response":NaN}'),
        (200, b'{"response":1e999}'),
        (200, b"\xff\xfe"),
        (200, b'{"response":"' + b"x" * (1024 * 1024) + b'"}'),
    ],
    ids=["500", "404", "204", "non-json", "duplicate-key", "nan", "overflow", "non-utf8", "oversize"],
)
def test_transport_rejects_bad_responses_with_fixed_error(
    servers: Any, status: int, body: bytes
) -> None:
    server = servers(_reply(status, body))
    transport, config = _transport_for(server)
    with pytest.raises(OllamaLoopbackTransportError) as info:
        transport(config.base_url + "/api/generate", _payload(), config.timeout_seconds)
    assert info.value.args == ("OLLAMA_LOOPBACK_TRANSPORT_ERROR",)
    assert SECRET_URL_MARKER not in repr(info.value)
    assert info.value.__cause__ is None
    assert len(server.hits) == 1


def test_transport_timeout_is_classified_by_existing_adapter(servers: Any) -> None:
    def slow(req: BaseHTTPRequestHandler) -> None:
        time.sleep(1.6)
        _reply(200, b'{"response":"{}"}')(req)

    server = servers(slow)
    transport, config = _transport_for(server, timeout="1")
    with pytest.raises(TimeoutError) as info:
        transport(config.base_url + "/api/generate", _payload(), config.timeout_seconds)
    assert info.value.args == ()
    assert info.value.__cause__ is None


def test_transport_connection_refused_is_fixed_error() -> None:
    probe = _Server(_reply(200, b"{}"))
    port = probe.port
    probe.close()
    config = parse_ollama_startup_config(TAG, f"http://127.0.0.1:{port}", "2")
    transport = build_loopback_transport(config)
    # Windows retries a refused loopback connect until the timeout expires.
    with pytest.raises((OllamaLoopbackTransportError, TimeoutError)) as info:
        transport(config.base_url + "/api/generate", _payload(), config.timeout_seconds)
    assert info.value.args in {(), ("OLLAMA_LOOPBACK_TRANSPORT_ERROR",)}
    assert info.value.__cause__ is None


# --------------------------------------------------------------------------
# CLI


class _FakeUvicorn:
    def __init__(self, on_run: Any = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.on_run = on_run

    def run(self, app: Any, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))
        if self.on_run is not None:
            self.on_run(app)


class _FakeApp:
    def __init__(self) -> None:
        cleaned: list[bool] = []
        self.cleaned = cleaned

        class _State:
            adaptive_app_cleanup = staticmethod(lambda: cleaned.append(True))

        self.state = _State()


def _install_fakes(monkeypatch: Any, on_run: Any = None) -> tuple[_FakeUvicorn, dict[str, Any]]:
    uvicorn = _FakeUvicorn(on_run)
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn)
    seen: dict[str, Any] = {"factory_calls": 0}

    def _factory(**kwargs: Any) -> _FakeApp:
        seen["factory_calls"] += 1
        seen["kwargs"] = dict(kwargs)
        return _FakeApp()

    monkeypatch.setattr(proto, "create_synthetic_clarification_prototype", _factory)
    return uvicorn, seen


def _explode(*_a: Any, **_k: Any) -> Any:
    raise AssertionError("must not be called")


def test_cli_without_options_keeps_default_and_touches_no_ollama(
    monkeypatch: Any, capsys: Any
) -> None:
    uvicorn, seen = _install_fakes(monkeypatch)
    monkeypatch.setattr(startup, "build_startup_ollama_model", _explode)
    monkeypatch.setattr(startup, "parse_ollama_startup_config", _explode)
    monkeypatch.setattr(startup, "build_loopback_transport", _explode)
    assert proto.main([]) == 0
    assert seen["kwargs"] == {}
    assert uvicorn.calls[0]["host"] == "127.0.0.1"
    out, err = capsys.readouterr()
    assert err == ""


def test_cli_with_options_injects_model_without_calling_it(
    monkeypatch: Any, capsys: Any
) -> None:
    transport = CountingTransport([])
    built: list[Any] = []

    def _fake_transport(config: Any) -> Any:
        built.append(config)
        return transport

    monkeypatch.setattr(startup, "build_loopback_transport", _fake_transport)
    monkeypatch.setattr(urllib.request, "getproxies", _explode)
    uvicorn, seen = _install_fakes(monkeypatch)
    argv = [
        "--ollama-model-tag", TAG,
        "--ollama-base-url", "http://[::1]:11777",
        "--ollama-timeout", "45",
        "--ollama-request-format", "model-template",
    ]
    assert proto.main(argv) == 0
    assert set(seen["kwargs"]) == {"ollama_model"}
    model = seen["kwargs"]["ollama_model"]
    assert type(model) is OllamaQueryModel
    assert model.prompt_version == "query-ir-v1"
    assert len(built) == 1
    assert built[0].base_url == "http://[::1]:11777"
    assert built[0].timeout_seconds == 45.0
    assert transport.calls == []
    assert len(uvicorn.calls) == 1
    out, err = capsys.readouterr()
    for secret in (TAG, "11777", "::1", "45"):
        assert secret not in out + err


def test_cli_with_postgres_and_ollama_passes_both(monkeypatch: Any) -> None:
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _c: CountingTransport([]))
    sentinel_profiles = [object()]

    import backend.adaptive.api.postgres_startup_profile as pg_startup

    monkeypatch.setattr(
        pg_startup, "load_postgres_startup_profiles", lambda **_k: sentinel_profiles
    )
    _uvicorn, seen = _install_fakes(monkeypatch)
    assert proto.main(["--postgres-profile-id", "synth-pg", "--ollama-model-tag", TAG, "--ollama-request-format", "model-template"]) == 0
    assert seen["kwargs"]["postgres_profiles"] is sentinel_profiles
    assert type(seen["kwargs"]["ollama_model"]) is OllamaQueryModel


@pytest.mark.parametrize(
    "argv",
    [
        ["--ollama-base-url", "http://127.0.0.1:11434"],
        ["--ollama-timeout", "30"],
        ["--ollama-model-tag", TAG, "--ollama-model-tag", TAG],
        ["--ollama-model-tag", TAG, "--ollama-timeout", "5", "--ollama-timeout=6"],
        ["--ollama-model-tag=" + TAG, "--ollama-model-tag=" + TAG],
        ["--ollama-model", TAG],
        ["--ollama-model-tag", TAG, "--ollama-base-url", f"http://{SECRET_URL_MARKER}:1"],
        ["--ollama-model-tag", TAG, "--ollama-base-url", "http://localhost:11434"],
        ["--ollama-model-tag", TAG, "--ollama-timeout", "0"],
        ["--ollama-model-tag", f"{SECRET_URL_MARKER} bad"],
        ["--ollama-model-tag="],
        ["--port", "8100", "--port", "8101", f"--unknown-{SECRET_URL_MARKER}"],
        ["--repo", "."],
    ],
)
def test_cli_invalid_configuration_fails_closed(
    monkeypatch: Any, capsys: Any, argv: list[str]
) -> None:
    uvicorn, seen = _install_fakes(monkeypatch)
    import backend.adaptive.api.postgres_startup_profile as pg_startup

    monkeypatch.setattr(pg_startup, "load_postgres_startup_profiles", _explode)
    monkeypatch.setattr(startup, "build_loopback_transport", _explode)
    try:
        code = proto.main(argv)
    except SystemExit as exc:
        code = exc.code
    assert code == 2
    assert seen["factory_calls"] == 0
    assert uvicorn.calls == []
    out, err = capsys.readouterr()
    assert err.strip() == FIXED_CLI_ERROR
    for secret in (SECRET_URL_MARKER, TAG, "localhost", "11434"):
        assert secret not in out + err


def test_cli_real_factory_end_to_end_with_synthetic_transport(
    monkeypatch: Any, capsys: Any
) -> None:
    transport = _smart_transport()
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _c: transport)
    results: dict[str, Any] = {}

    def on_run(app: Any) -> None:
        assert app.state.adaptive_app_provider_kind == "ollama"
        assert isinstance(app.state.adaptive_app_provider, OllamaConstraintProvider)
        assert transport.calls == []
        client = TestClient(app, raise_server_exceptions=False)
        data = _start(client, SYNTHETIC_LEGACY_DATABASE_ID, f"{QUESTION_BASE} 강의 목록 보여줘").json()
        assert data["status"] == "PENDING"
        assert transport.calls == []
        target = [o for o in data["options"] if o["semantic_ref"] == "Instructor.name"][0]
        response = client.post(
            SUBMIT_PATH,
            json={"session_token": data["session_token"], "selection": [target["option_id"]]},
        )
        results["status"] = response.status_code
        results["body"] = response.json()
        results["text"] = response.text

    monkeypatch.setitem(sys.modules, "uvicorn", _FakeUvicorn(on_run))
    assert proto.main(["--ollama-model-tag", TAG, "--ollama-timeout", "9", "--ollama-request-format", "alpaca-raw-no-system-v1"]) == 0
    assert results["status"] == 200
    assert results["body"]["execution"]["status"] == "SUCCEEDED"
    assert results["body"]["execution"]["columns"] == ["Instructor.name"]
    assert len(transport.calls) == 1
    url, payload, timeout = transport.calls[0]
    assert url == DEFAULT_OLLAMA_BASE_URL + "/api/generate"
    assert timeout == 9.0
    assert payload["model"] == TAG
    assert payload["raw"] is True
    assert payload["prompt"].startswith(ALPACA_NO_SYSTEM_PREFIX)
    assert payload["prompt"].endswith(ALPACA_NO_SYSTEM_SUFFIX)
    context = json.loads(payload["prompt"].split("Context: ", 1)[1].split("\nQuestion:", 1)[0])
    assert context["available_fields"] == ["Instructor.name"]
    assert context["entities"] == ["Instructor"]
    out, err = capsys.readouterr()
    for secret in (TAG, "127.0.0.1:11434", "11434"):
        assert secret not in results["text"] + out + err


# --------------------------------------------------------------------------
# factory provider selection and routing


def test_factory_provider_kind_default_and_global_app() -> None:
    app = create_synthetic_clarification_prototype()
    assert app.state.adaptive_app_provider_kind == "deterministic"
    assert type(app.state.adaptive_app_provider).__name__ == "_FixedConstraintProvider"
    assert proto.app.state.adaptive_app_provider_kind == "deterministic"


def test_startup_model_shares_provider_across_sqlite_and_postgres() -> None:
    transport = _smart_transport()
    model = build_startup_ollama_model(
        parse_ollama_startup_config(TAG), transport=transport
    )
    adapter, executor = _PgAdapter(), _PgExecutor()
    app = create_synthetic_clarification_prototype(
        postgres_profiles=[_pg_profile()],
        postgres_adapters={PG_DATABASE_ID: adapter},
        postgres_executors={PG_DATABASE_ID: executor},
        ollama_model=model,
    )
    assert app.state.adaptive_app_provider_kind == "ollama"
    assert transport.calls == []
    client = TestClient(app, raise_server_exceptions=False)
    outputs = {}
    for index, database_id in enumerate(
        (SYNTHETIC_PROTOTYPE_DATABASE_ID, PG_DATABASE_ID, SYNTHETIC_LEGACY_DATABASE_ID)
    ):
        data = _start(
            client, database_id, f"{QUESTION_BASE} 강의 목록 보여줘", request_id=f"req-p708-{index}"
        ).json()
        target = [o for o in data["options"] if o["semantic_ref"] == "Course.name"][0]
        response = client.post(
            SUBMIT_PATH,
            json={"session_token": data["session_token"], "selection": [target["option_id"]]},
        )
        assert response.status_code == 200
        assert response.json()["execution"]["status"] == "SUCCEEDED"
        outputs[database_id] = response.json()["execution"]
    assert len(transport.calls) == 3
    assert executor.calls == 1
    pg_rows = outputs[PG_DATABASE_ID]["rows"]
    assert pg_rows == [{"Course.name": "자료구조"}]
    assert len(outputs[SYNTHETIC_PROTOTYPE_DATABASE_ID]["rows"]) == 6
    contexts = [
        json.loads(call[1]["prompt"].split("Context: ", 1)[1].split("\nQuestion:", 1)[0])
        for call in transport.calls
    ]
    assert contexts[0] == contexts[1] == contexts[2]
    blob = "".join(call[1]["prompt"] for call in transport.calls)
    for secret in ("s3cret-pw", "private_schema", "course_code", "SELECT"):
        assert secret not in blob


def test_startup_model_transport_failure_is_redacted_http_error() -> None:
    failing = CountingTransport([OllamaLoopbackTransportError()])
    model = build_startup_ollama_model(parse_ollama_startup_config(TAG), transport=failing)
    app = create_synthetic_clarification_prototype(ollama_model=model)
    client = TestClient(app, raise_server_exceptions=False)
    data = _start(client, SYNTHETIC_PROTOTYPE_DATABASE_ID, f"{QUESTION_BASE} 강의 목록 보여줘").json()
    target = data["options"][0]
    response = client.post(
        SUBMIT_PATH,
        json={"session_token": data["session_token"], "selection": [target["option_id"]]},
    )
    assert response.status_code == 502
    assert response.json() == {"error": {"code": "CONSTRAINT_PROVIDER_ERROR"}}
    assert len(failing.calls) == 1
    for secret in (TAG, "OLLAMA_LOOPBACK_TRANSPORT_ERROR", "127.0.0.1"):
        assert secret not in response.text


# --------------------------------------------------------------------------
# Phase 7-10: explicit request format


@pytest.mark.parametrize(
    "argv",
    [
        ["--ollama-model-tag", TAG],
        ["--ollama-request-format", "alpaca-raw-no-system-v1"],
        ["--ollama-model-tag", TAG, "--ollama-request-format", "raw"],
        ["--ollama-model-tag", TAG, "--ollama-request-format", "model-template", "--ollama-request-format", "model-template"],
    ],
)
def test_cli_requires_explicit_valid_request_format(monkeypatch: Any, capsys: Any, argv: list[str]) -> None:
    uvicorn, seen = _install_fakes(monkeypatch)
    monkeypatch.setattr(startup, "build_loopback_transport", _explode)
    try:
        code = proto.main(argv)
    except SystemExit as exc:
        code = exc.code
    assert code == 2
    assert seen["factory_calls"] == 0 and uvicorn.calls == []
    out, err = capsys.readouterr()
    assert err.strip() == FIXED_CLI_ERROR


def test_config_request_format_contract() -> None:
    assert parse_ollama_startup_config(TAG).request_format == "model-template"
    assert parse_ollama_startup_config(TAG, None, None, "alpaca-raw-no-system-v1").request_format == "alpaca-raw-no-system-v1"
    for bad in ("raw", "", " model-template", 1, b"model-template"):
        with pytest.raises(OllamaStartupConfigError):
            parse_ollama_startup_config(TAG, None, None, bad)
    forged = OllamaStartupConfig(DEFAULT_OLLAMA_BASE_URL, TAG, 30.0, "raw")
    with pytest.raises(OllamaStartupConfigError):
        build_startup_ollama_model(forged, transport=CountingTransport([]))
    config = parse_ollama_startup_config(TAG, None, None, "alpaca-raw-no-system-v1")
    assert "alpaca" not in repr(config) and TAG not in repr(config)


def test_builder_applies_request_format_once() -> None:
    from backend.adaptive.harness import HarnessRequest
    from backend.adaptive.api.app_factory import _build_mapping, _build_ontology

    request = HarnessRequest(request_id="r710", database_id=SYNTHETIC_PROTOTYPE_DATABASE_ID, question="합성 질문 P710")
    for fmt, raw in (("model-template", None), ("alpaca-raw-no-system-v1", True)):
        transport = CountingTransport([{"response": "{}"}])
        model = build_startup_ollama_model(parse_ollama_startup_config(TAG, None, None, fmt), transport=transport)
        model.generate_ir(request, _build_ontology(), _build_mapping())
        sent = transport.calls[0][1]
        assert sent.get("raw") is raw
        if raw:
            assert sent["prompt"].count(ALPACA_NO_SYSTEM_PREFIX) == 1
            assert sent["prompt"].endswith("합성 질문 P710" + ALPACA_NO_SYSTEM_SUFFIX)
        else:
            assert ALPACA_NO_SYSTEM_PREFIX not in sent["prompt"]


# --------------------------------------------------------------------------
# Phase 7-11: request format inside the model, fail-fast, provenance


@pytest.mark.parametrize("fmt", ["model-template", "alpaca-raw-no-system-v1"])
def test_non_callable_transport_fails_at_build_for_every_format(fmt: str) -> None:
    from backend.adaptive.evaluation.direct_sql_ollama import OllamaDirectSqlModel
    from backend.adaptive.harness.ollama_request_format import RequestFormatError, apply_request_format

    config = parse_ollama_startup_config(TAG, None, None, fmt)
    with pytest.raises(OllamaStartupConfigError) as info:
        build_startup_ollama_model(config, transport=object())
    _assert_fixed_config_error(info.value)
    with pytest.raises(ValueError, match="^INVALID_OLLAMA_TRANSPORT$"):
        OllamaQueryModel(DEFAULT_OLLAMA_BASE_URL, TAG, 30.0, transport=object(), request_format=fmt)  # type: ignore[arg-type]
    with pytest.raises(RequestFormatError):
        apply_request_format(object(), fmt)
    with pytest.raises(ValueError):
        OllamaDirectSqlModel(config, transport=object())


def test_model_rejects_unknown_request_format() -> None:
    for bad in ("raw", "", 1):
        with pytest.raises(ValueError, match="^INVALID_OLLAMA_REQUEST_FORMAT$"):
            OllamaQueryModel(DEFAULT_OLLAMA_BASE_URL, TAG, 30.0, transport=CountingTransport([]), request_format=bad)  # type: ignore[arg-type]


def test_provenance_distinguishes_request_format_and_keeps_template_hash() -> None:
    def _prov(fmt: str | None) -> Any:
        model = OllamaQueryModel(
            "http://127.0.0.1:11434", "text2sql_qwen:latest", 30.0,
            transport=CountingTransport([]), request_format=fmt,
        )
        return model.declared_provenance("0.34.4")

    legacy = _prov(None)
    template = _prov("model-template")
    no_system = _prov("alpaca-raw-no-system-v1")
    # Value recorded before Phase 7-11; keeps 5-15 paired contract and bundles valid.
    assert template.generation_config_sha256 == "48ba68c94123f52c8d0d5f531f69fa29cdea6107a8a2fe69e2d937cfa14126e0"
    assert template.prompt_template_sha256 == "9546599f1b1cb0f733c7ff6a7d02c0d47d6dba6a0767263acc37869bb74774d5"
    assert legacy == template
    assert no_system != template
    assert no_system.generation_config_sha256 != template.generation_config_sha256
    assert no_system == _prov("alpaca-raw-no-system-v1")
    dumped = no_system.model_dump_json()
    for marker in ("127.0.0.1", "11434", "Below is an instruction", "courses"):
        assert marker not in dumped


def test_startup_builder_applies_format_exactly_once_via_model() -> None:
    config = parse_ollama_startup_config(TAG, None, None, "alpaca-raw-no-system-v1")
    transport = CountingTransport([{"response": "{}"}])
    model = build_startup_ollama_model(config, transport=transport)
    assert model.request_format == "alpaca-raw-no-system-v1"
    from backend.adaptive.harness import HarnessRequest
    from backend.adaptive.api.app_factory import _build_mapping, _build_ontology

    model.generate_ir(
        HarnessRequest(request_id="r711", database_id=SYNTHETIC_PROTOTYPE_DATABASE_ID, question="합성 P711"),
        _build_ontology(), _build_mapping(),
    )
    assert transport.calls[0][1]["prompt"].count(ALPACA_NO_SYSTEM_PREFIX) == 1
