"""Phase 9-09 (ADR-099): the ChatGPT API as one more chat model choice."""

from __future__ import annotations

import json
import socket
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.adaptive.api import dev_launcher
from backend.adaptive.api.chat_models import CHAT_MODELS_PATH, ChatModelChoice
from backend.adaptive.api.chat_query import CHAT_QUERY_PATH, register_chat_query_routes
from backend.adaptive.harness.openai_chat import (
    OPENAI_CHAT_URL,
    OpenAIQueryModel,
    _https_transport,
    valid_openai_settings,
    validate_api_base_url,
)
from backend.adaptive.harness.recorded import RecordedFailureCode
from backend.adaptive.harness.recording_orchestrator import RecordingProviderError
from tests.adaptive.test_chat_query_endpoint import GOOD_IR, QUESTION, FakeModel, RecordingExecutor
from tests.adaptive.test_legacy_view_partial_mapping import DATABASE_ID, make_context

KEY = "sk-test-" + "A1b2C3d4" * 4


def envelope(content: Any, finish: Any = "stop", **message: Any) -> dict[str, Any]:
    return {"choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": content, **message}}]}


class Transport:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, dict[str, str], dict[str, Any], float]] = []

    def __call__(self, url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float) -> Any:
        self.calls.append((url, headers, payload, timeout))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _case() -> Any:
    from tests.adaptive.test_ollama_query_model import OllamaQueryModelTests

    case = OllamaQueryModelTests()
    case.setUp()
    return case


def generate(transport: Transport, **kwargs: Any) -> Any:
    case = _case()
    model = OpenAIQueryModel(KEY, "gpt-5-mini", transport=transport)
    return model.generate_ir(case.request, case.ontology, case.mapping, **kwargs)


def test_one_request_with_the_prompt_schema_and_hints() -> None:
    transport = Transport(envelope(json.dumps(GOOD_IR)))
    hints = [{"text": "자료", "field": "Course.name", "value": "자료구조"}]
    assert generate(transport, value_hints=hints) == GOOD_IR
    [(url, headers, payload, timeout)] = transport.calls
    assert url == OPENAI_CHAT_URL == "https://api.openai.com/v1/chat/completions"
    assert headers["Authorization"] == f"Bearer {KEY}" and timeout == 60.0
    assert payload["model"] == "gpt-5-mini"
    [message] = payload["messages"]
    assert message["role"] == "user"
    assert "Prompt version: query-ir-v3" in message["content"] and "자료구조" in message["content"]
    assert "PRIVATE" not in json.dumps(payload, ensure_ascii=False)  # no physical names
    response_format = payload["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is False
    assert response_format["json_schema"]["schema"]["required"] == ["entity", "select", "filters", "limit"]
    assert "temperature" not in payload  # gpt-5 models take only the default


@pytest.mark.parametrize("base,valid", [
    ("https://api.example.com/v1", True), ("http://127.0.0.1:1234/v1", True),
    ("http://localhost/v1", True), ("http://[::1]:1234/v1", True),
    ("http://example.com/v1", False), ("https://user:pass@example.com/v1", False),
    ("https://example.com/v1?x=1", False), ("https://example.com/v1#frag", False),
])
def test_custom_api_base_url_validation(base: str, valid: bool) -> None:
    if valid:
        assert validate_api_base_url(base) == base.rstrip("/") + "/chat/completions"
    else:
        with pytest.raises(ValueError): validate_api_base_url(base)


def test_custom_endpoint_and_optional_authentication() -> None:
    endpoint = "http://127.0.0.1:1234/v1/chat/completions"
    transport = Transport(envelope(json.dumps({"entity": "Course", "select": [], "filters": [], "limit": 10})))
    model = OpenAIQueryModel(None, "meta-llama/Llama-3.1-8B-Instruct", endpoint=endpoint, transport=transport)
    case = _case(); model.generate_ir(case.request, case.ontology, case.mapping)
    [(url, headers, payload, _timeout)] = transport.calls
    assert url == endpoint and "Authorization" not in headers
    assert payload["model"] == "meta-llama/Llama-3.1-8B-Instruct" and payload["response_format"]["type"] == "json_schema"
    auth_transport = Transport(envelope(json.dumps({"entity": "Course", "select": [], "filters": [], "limit": 10})))
    OpenAIQueryModel("custom-key-12345", "org/model", endpoint=endpoint, transport=auth_transport).generate_ir(case.request, case.ontology, case.mapping)
    assert auth_transport.calls[0][1]["Authorization"] == "Bearer custom-key-12345"


def test_custom_endpoint_transport_rejects_another_url() -> None:
    with pytest.raises(RecordingProviderError):
        _https_transport("https://evil.example/chat/completions", {}, {}, 1, allowed_url="https://safe.example/chat/completions")


def test_text_that_is_not_json_is_passed_on_for_the_chat_to_refuse() -> None:
    assert generate(Transport(envelope("Here is the query"))) == "Here is the query"


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        (envelope(""), RecordedFailureCode.EMPTY_RESPONSE),
        (envelope(json.dumps(GOOD_IR), finish="length"), RecordedFailureCode.PROVIDER_ERROR),
        (envelope(json.dumps(GOOD_IR), finish="content_filter"), RecordedFailureCode.PROVIDER_ERROR),
        (envelope(json.dumps(GOOD_IR), finish="tool_calls"), RecordedFailureCode.PROVIDER_ERROR),
        (envelope(json.dumps(GOOD_IR), finish=None), RecordedFailureCode.PROVIDER_ERROR),
        (RecordingProviderError(RecordedFailureCode.TIMEOUT), RecordedFailureCode.TIMEOUT),
        (envelope(None, refusal="I can't help"), RecordedFailureCode.PROVIDER_ERROR),
        (envelope('{"a": 1, "a": 2}'), RecordedFailureCode.PROVIDER_ERROR),
        ({"error": {"message": "bad key"}}, RecordedFailureCode.PROVIDER_ERROR),
        ({"choices": []}, RecordedFailureCode.PROVIDER_ERROR),
        (TimeoutError(), RecordedFailureCode.TIMEOUT),
        (socket.timeout(), RecordedFailureCode.TIMEOUT),
        (RuntimeError(KEY), RecordedFailureCode.PROVIDER_ERROR),
    ],
)
def test_failures_carry_a_fixed_code_and_never_the_key(outcome: Any, code: RecordedFailureCode) -> None:
    with pytest.raises(RecordingProviderError) as caught:
        generate(Transport(outcome))
    assert caught.value.failure_code is code
    assert KEY not in repr(caught.value) and KEY not in str(caught.value)
    # Luna 9-09 review: no original exception is kept, not even as context.
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_the_key_never_shows_in_the_model() -> None:
    model = OpenAIQueryModel(KEY)
    assert KEY not in repr(model) and KEY not in str(model)
    assert not hasattr(model, "__dict__")


@pytest.mark.parametrize(
    ("key", "model"),
    [("", "gpt-5-mini"), ("short", "gpt-5-mini"), (KEY + " ", "gpt-5-mini"), (KEY, ""), (KEY, "gpt 5"), (KEY, "a/b")],
)
def test_bad_settings_are_refused(key: str, model: str) -> None:
    assert not valid_openai_settings(key, model)
    with pytest.raises(ValueError):
        OpenAIQueryModel(key, model)


def test_the_real_transport_only_posts_to_the_fixed_endpoint() -> None:
    with pytest.raises(RecordingProviderError):
        _https_transport("https://example.com/v1/chat/completions", {}, {}, 5.0)


def test_an_openai_choice_answers_through_the_chat_route() -> None:
    class Fake(OpenAIQueryModel):
        __slots__ = ()

    transport = Transport(envelope(json.dumps(GOOD_IR)))
    openai = Fake(KEY, transport=transport)
    local = FakeModel(GOOD_IR)
    app = FastAPI()
    register_chat_query_routes(
        app,
        pg_contexts={DATABASE_ID: make_context(executor=RecordingExecutor())},
        model=None,
        models=[
            ChatModelChoice(id="general", label="일반 모델", tag="qwen2.5-coder:7b-instruct", model=local),
            ChatModelChoice(id="openai", label="ChatGPT(외부 API)", tag="gpt-5-mini", model=openai),
        ],
    )
    client = TestClient(app, raise_server_exceptions=False)
    listing = client.get(CHAT_MODELS_PATH)
    assert listing.json()["models"][1] == {"id": "openai", "label": "ChatGPT(외부 API)", "tag": "gpt-5-mini"}
    assert KEY not in listing.text
    response = client.post(CHAT_QUERY_PATH, json={"database_id": DATABASE_ID, "query": QUESTION, "model": "openai"})
    assert response.status_code == 200 and response.json()["status"] == "SUCCEEDED"
    assert len(transport.calls) == 1 and local.calls == []
    assert KEY not in response.text
    transport.outcome = TimeoutError()
    failed = client.post(CHAT_QUERY_PATH, json={"database_id": DATABASE_ID, "query": QUESTION, "model": "openai"})
    assert failed.status_code == 503 and failed.json()["error"]["failure_code"] == "TIMEOUT"
    assert KEY not in failed.text


# --- launcher -----------------------------------------------------------------------------------


def test_the_launcher_adds_chatgpt_only_with_a_key() -> None:
    choices, status = dev_launcher.plan_chat_models({})
    assert [item["id"] for item in choices or []] == ["general"] and "chat_model.openai" not in status
    choices, status = dev_launcher.plan_chat_models({"OPENAI_API_KEY": KEY})
    assert [item["id"] for item in choices or []] == ["general", "openai"]
    assert choices[-1]["model_tag"] == "gpt-5-mini" and status["chat_model.openai"] == "enabled"
    choices, _status = dev_launcher.plan_chat_models({"OPENAI_API_KEY": KEY, "OPENAI_MODEL": "gpt-4.1-mini"})
    assert choices[-1]["model_tag"] == "gpt-4.1-mini"
    choices, status = dev_launcher.plan_chat_models({"OPENAI_API_KEY": "bad key"})
    assert [item["id"] for item in choices or []] == ["general"]
    assert status["chat_model.openai"] == "disabled (INVALID_CONFIG)"
    assert KEY not in json.dumps(status)
    # Luna 9-09 review: the startup spec holding the key does not show it.
    choices, _status = dev_launcher.plan_chat_models({"OPENAI_API_KEY": KEY})
    assert KEY not in repr(choices) and KEY not in str(choices)
    assert choices[-1]["api_key"].value == KEY


def test_chatgpt_alone_still_starts_the_chat() -> None:
    env = {"OPENAI_API_KEY": KEY, "OLLAMA_BASE_URL": "http://gpu-box:11434"}
    choices, status = dev_launcher.plan_chat_models(env)
    assert [item["id"] for item in choices or []] == ["openai"]
    assert status["ollama"] == "disabled (INVALID_OR_NON_LOOPBACK_CONFIG)"


def test_main_never_prints_the_key(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / ".env").write_text(f"OPENAI_API_KEY={KEY}\n", encoding="utf-8")
    seen: list[Any] = []

    class App:
        class state:  # noqa: N801
            adaptive_app_cleanup = None

    def fake_create(profiles: Any, model: Any, mapping_model: Any = None, show_sql: bool = False,
                    postgres_sources: Any = None, **_custom_chat_models: Any) -> Any:
        seen.append(model)
        return App()

    def build(spec: dict[str, str]) -> Any:
        return OpenAIQueryModel(spec["api_key"].value, spec["model_tag"]) if spec["id"] == "openai" else "local"

    monkeypatch.setattr(dev_launcher, "_build_model", build)
    monkeypatch.setattr(dev_launcher, "_create_app", fake_create)
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: None)
    assert dev_launcher.main(["--repository-root", str(tmp_path)]) == 0
    assert [choice.id for choice in seen[0]] == ["general", "openai"]
    out = capsys.readouterr()
    assert "chat_model.openai=enabled" in out.out
    assert KEY not in out.out + out.err


def test_the_prototype_accepts_an_openai_choice_and_keeps_clarification_local() -> None:
    from backend.adaptive.api.ollama_startup import build_startup_ollama_model, parse_ollama_startup_config
    from backend.adaptive.api.app_factory import create_synthetic_clarification_prototype

    local = build_startup_ollama_model(
        parse_ollama_startup_config("qwen2.5-coder:7b-instruct", None, "30"), transport=lambda *_args: {}
    )
    openai = OpenAIQueryModel(KEY, transport=Transport(envelope("{}")))
    choices = [
        ChatModelChoice(id="openai", label="ChatGPT(외부 API)", tag="gpt-5-mini", model=openai),
        ChatModelChoice(id="general", label="일반 모델", tag="qwen2.5-coder:7b-instruct", model=local),
    ]
    app = create_synthetic_clarification_prototype(chat_models=choices)
    try:
        assert app.state.adaptive_app_provider._model is local  # noqa: SLF001
    finally:
        app.state.adaptive_app_cleanup()
    app = create_synthetic_clarification_prototype(chat_models=choices[:1])
    try:
        assert app.state.adaptive_app_provider_kind == "deterministic"
    finally:
        app.state.adaptive_app_cleanup()
