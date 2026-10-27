from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.adaptive.api import custom_chat_models as custom
from backend.adaptive.api.chat_models import ChatModelChoice, normalize_ollama_tag
from backend.adaptive.api.chat_query import CHAT_MODELS_PATH, CHAT_QUERY_PATH, register_chat_query_routes
from backend.adaptive.api.live_chat_models import LiveChatModels
from tests.adaptive.test_chat_model_choice import choices
from tests.adaptive.test_chat_query_endpoint import GOOD_IR, QUESTION, FakeModel, RecordingExecutor
from tests.adaptive.test_legacy_view_partial_mapping import DATABASE_ID, make_context


@pytest.fixture(autouse=True)
def _keys_in_tmp(monkeypatch, tmp_path: Path) -> None:
    """Phase 9-32: the API key file sits next to the store; tests redirect both."""
    monkeypatch.setattr(custom, "_keys_path", lambda _root: tmp_path / "api-keys.json")


def test_normalize_ollama_tag_examples() -> None:
    assert normalize_ollama_tag("text2sql_qwen") == "text2sql_qwen:latest"
    assert normalize_ollama_tag("qwen2.5-coder:7b-instruct") == "qwen2.5-coder:7b-instruct"
    assert normalize_ollama_tag("hf.co/user/model") == "hf.co/user/model:latest"
    assert normalize_ollama_tag("host:5000/ns/model") == "host:5000/ns/model:latest"


def test_private_store_round_trip_and_live_admin_routes(monkeypatch, tmp_path: Path) -> None:
    store = tmp_path / "custom.json"
    monkeypatch.setattr(custom, "_path", lambda _root: store)
    installed = ["qwen2.5:7b", "text2sql_qwen:latest"]
    monkeypatch.setattr(custom, "installed_tags", lambda *_args, **_kwargs: installed)
    monkeypatch.setattr(custom, "_installed_tags_status", lambda *_args, **_kwargs: (installed, True))
    custom_model = FakeModel(GOOD_IR)
    monkeypatch.setattr(custom, "_make_choice", lambda entry, *_args: ChatModelChoice(entry.id, entry.label, entry.tag, custom_model))

    default, tuned = FakeModel(GOOD_IR), FakeModel(GOOD_IR)
    registry = LiveChatModels(choices(default, tuned))
    app = FastAPI()
    register_chat_query_routes(app, pg_contexts={DATABASE_ID: make_context(executor=RecordingExecutor())}, model=None, models=registry)
    custom.register_custom_chat_model_routes(app, repository_root=tmp_path, registry=registry, base_url="http://127.0.0.1:11434", timeout="30")
    client = TestClient(app)

    added = client.post(custom.CUSTOM_CHAT_MODELS_PATH, json={"action": "ADD", "tag": "qwen2.5:7b", "label": "개인 모델", "mode": "general", "reviewer": "관리자"})
    assert added.status_code == 200
    model_id = added.json()["custom"][0]["id"]
    assert client.get(CHAT_MODELS_PATH).json()["models"][-1]["id"] == model_id
    assert client.post(CHAT_QUERY_PATH, json={"database_id": DATABASE_ID, "query": QUESTION, "model": model_id}).status_code == 200
    assert len(custom_model.calls) == 1

    assert client.post(custom.CUSTOM_CHAT_MODELS_PATH, json={"action": "ADD", "tag": "qwen2.5:7b", "label": "중복", "mode": "general", "reviewer": "관리자"}).json()["error"]["code"] == "DUPLICATE"
    assert client.post(custom.CUSTOM_CHAT_MODELS_PATH, json={"action": "ADD", "tag": "missing:latest", "label": "없음", "mode": "general", "reviewer": "관리자"}).json()["error"]["code"] == "NOT_INSTALLED"
    assert client.post(custom.CUSTOM_CHAT_MODELS_PATH, json={"action": "ADD", "tag": "text2sql_qwen:latest", "label": "기본 모델 별칭", "mode": "general", "reviewer": "관리자"}).json()["error"]["code"] == "DUPLICATE"
    assert client.post(custom.CUSTOM_CHAT_MODELS_PATH, json={"action": "REMOVE", "id": "general", "reviewer": "관리자"}).json()["error"]["code"] == "NOT_FOUND"
    removed = client.post(custom.CUSTOM_CHAT_MODELS_PATH, json={"action": "REMOVE", "id": model_id, "reviewer": "관리자"})
    assert removed.status_code == 200 and removed.json()["custom"] == []
    rejected = client.post(CHAT_QUERY_PATH, json={"database_id": DATABASE_ID, "query": QUESTION, "model": model_id})
    assert rejected.status_code == 400
    assert "11434" not in added.text and "http" not in added.text


def test_custom_choice_limit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(custom, "_path", lambda _root: tmp_path / "custom.json")
    installed = [f"local-model-{index}:latest" for index in range(5)]
    monkeypatch.setattr(custom, "installed_tags", lambda *_args, **_kwargs: installed)
    monkeypatch.setattr(custom, "_installed_tags_status", lambda *_args, **_kwargs: (installed, True))
    monkeypatch.setattr(custom, "_make_choice", lambda entry, *_args: ChatModelChoice(entry.id, entry.label, entry.tag, FakeModel(GOOD_IR)))
    registry = LiveChatModels([ChatModelChoice("general", "General", "base:latest", FakeModel(GOOD_IR))])
    app = FastAPI()
    custom.register_custom_chat_model_routes(app, repository_root=tmp_path, registry=registry, base_url="http://127.0.0.1:11434", timeout="30")
    client = TestClient(app)
    for index in range(4):
        response = client.post(custom.CUSTOM_CHAT_MODELS_PATH, json={
            "action": "ADD", "tag": installed[index], "label": f"Model {index}", "mode": "general", "reviewer": "admin",
        })
        assert response.status_code == 200
    limited = client.post(custom.CUSTOM_CHAT_MODELS_PATH, json={
        "action": "ADD", "tag": installed[4], "label": "Model 4", "mode": "general", "reviewer": "admin",
    })
    assert limited.json()["error"]["code"] == "LIMIT_REACHED"


def test_add_rejects_tag_alias_of_builtin(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(custom, "_path", lambda _root: tmp_path / "custom.json")
    monkeypatch.setattr(custom, "installed_tags", lambda *_args, **_kwargs: ["text2sql_qwen:latest"])
    registry = LiveChatModels([ChatModelChoice("general", "General", "text2sql_qwen", FakeModel(GOOD_IR))])
    app = FastAPI()
    custom.register_custom_chat_model_routes(app, repository_root=tmp_path, registry=registry,
        base_url="http://127.0.0.1:11434", timeout="30")
    result = TestClient(app).post(custom.CUSTOM_CHAT_MODELS_PATH, json={
        "action": "ADD", "tag": "text2sql_qwen:latest", "label": "Alias", "mode": "general", "reviewer": "admin",
    })
    assert result.json()["error"]["code"] == "DUPLICATE"


def test_startup_skips_custom_tag_missing_from_ollama(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(custom, "_path", lambda _root: tmp_path / "custom.json")
    entry = custom.StoredCustomModel(
        id="custom-local-model", label="Local", tag="local:latest", mode="general",
        added_by="admin", added_at="2026-10-27T00:00:00+00:00",
    )
    custom._save(tmp_path, [entry])
    monkeypatch.setattr(custom, "installed_tags", lambda *_args, **_kwargs: [])
    registry = LiveChatModels([ChatModelChoice("general", "General", "base:latest", FakeModel(GOOD_IR))])
    assert custom.load_live_custom(tmp_path, registry, "http://127.0.0.1:11434", "30") == {
        "chat_model.custom-local-model": "disabled (NOT_INSTALLED)"
    }
    assert [item.id for item in registry.snapshot()] == ["general"]

    app = FastAPI()
    monkeypatch.setattr(custom, "_installed_tags_status", lambda *_args, **_kwargs: ([], True))
    custom.register_custom_chat_model_routes(app, repository_root=tmp_path, registry=registry,
        base_url="http://127.0.0.1:11434", timeout="30")
    removed = TestClient(app).post(custom.CUSTOM_CHAT_MODELS_PATH, json={
        "action": "REMOVE", "id": entry.id, "reviewer": "admin",
    })
    assert removed.status_code == 200
    assert custom.load_custom_models(tmp_path) == []
    builtin = TestClient(app).post(custom.CUSTOM_CHAT_MODELS_PATH, json={
        "action": "REMOVE", "id": "general", "reviewer": "admin",
    })
    assert builtin.status_code == 400 and builtin.json()["error"]["code"] == "NOT_FOUND"


def test_startup_skips_custom_tag_alias_of_builtin(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(custom, "_path", lambda _root: tmp_path / "custom.json")
    entry = custom.StoredCustomModel(
        id="custom-alias", label="Alias", tag="text2sql_qwen:latest", mode="general",
        added_by="admin", added_at="2026-10-27T00:00:00+00:00",
    )
    custom._save(tmp_path, [entry])
    monkeypatch.setattr(custom, "installed_tags", lambda *_args, **_kwargs: ["text2sql_qwen:latest"])
    registry = LiveChatModels([ChatModelChoice("legacy-tuned", "Legacy", "text2sql_qwen", FakeModel(GOOD_IR))])
    assert custom.load_live_custom(tmp_path, registry, "http://127.0.0.1:11434", "30") == {
        "chat_model.custom-alias": "disabled (DUPLICATE)"
    }
    assert [item.id for item in registry.snapshot()] == ["legacy-tuned"]


def test_app_factory_loads_stored_custom_model_into_chat_listing(monkeypatch, tmp_path: Path) -> None:
    from backend.adaptive.api.app_factory import create_synthetic_clarification_prototype
    from backend.adaptive.api.ollama_startup import build_startup_ollama_model, parse_ollama_startup_config

    store = tmp_path / "custom.json"
    monkeypatch.setattr(custom, "_path", lambda _root: store)
    entry = custom.StoredCustomModel(
        id="custom-local-model", label="Local", tag="local:latest", mode="general",
        added_by="admin", added_at="2026-10-27T00:00:00+00:00",
    )
    custom._save(tmp_path, [entry])
    monkeypatch.setattr(custom, "installed_tags", lambda *_args, **_kwargs: ["local:latest"])
    monkeypatch.setattr(custom, "_make_choice", lambda item, *_args: ChatModelChoice(item.id, item.label, item.tag, FakeModel(GOOD_IR)))
    model = build_startup_ollama_model(
        parse_ollama_startup_config("base:latest", None, "30"), transport=lambda *_args: {}
    )
    app = create_synthetic_clarification_prototype(
        chat_models=[ChatModelChoice("general", "General", "base:latest", model)],
        chat_model_repository_root=tmp_path, chat_model_base_url="http://127.0.0.1:11434",
    )
    try:
        listing = TestClient(app).get(CHAT_MODELS_PATH).json()
        assert listing["models"][-1]["id"] == "custom-local-model"
        assert app.state.custom_chat_model_status == {"chat_model.custom-local-model": "enabled"}
    finally:
        app.state.adaptive_app_cleanup()


def test_phase_9_32_api_add_secret_duplicate_remove_and_startup(monkeypatch, tmp_path: Path) -> None:
    store, keys_file = tmp_path / "custom.json", tmp_path / "api-keys.json"
    monkeypatch.setattr(custom, "_path", lambda _root: store)
    monkeypatch.setattr(custom, "_keys_path", lambda _root: keys_file)
    monkeypatch.setattr(custom, "installed_tags", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("API startup probed Ollama")))
    monkeypatch.setattr(custom, "_installed_tags_status", lambda *_a, **_k: ([], True))
    registry = LiveChatModels([ChatModelChoice("general", "General", "base:latest", FakeModel(GOOD_IR))])
    app = FastAPI()
    custom.register_custom_chat_model_routes(app, repository_root=tmp_path, registry=registry, base_url="http://127.0.0.1:11434", timeout="30")
    client = TestClient(app); key = "secret-custom-api-key-123"
    payload = {"action": "ADD_API", "label": "Remote", "base_url": "http://127.0.0.1:1234/v1", "model": "meta-llama/Llama-3.1-8B-Instruct", "api_key": key, "reviewer": "admin"}
    added = client.post(custom.CUSTOM_CHAT_MODELS_PATH, json=payload)
    assert added.status_code == 200 and key not in added.text and added.json()["custom"][0]["has_key"] is True
    assert key not in store.read_text(encoding="utf-8") and key in keys_file.read_text(encoding="utf-8")
    assert client.post(custom.CUSTOM_CHAT_MODELS_PATH, json=payload).json()["error"]["code"] == "DUPLICATE"
    model_id = added.json()["custom"][0]["id"]
    restarted = LiveChatModels([ChatModelChoice("general", "General", "base:latest", FakeModel(GOOD_IR))])  # a fresh start
    assert custom.load_live_custom(tmp_path, restarted, "http://127.0.0.1:11434", "30")[f"chat_model.{model_id}"] == "enabled"
    assert restarted.get(model_id) is not None
    assert client.get(custom.CUSTOM_CHAT_MODELS_PATH).text.find(key) == -1
    removed = client.post(custom.CUSTOM_CHAT_MODELS_PATH, json={"action": "REMOVE", "id": model_id, "reviewer": "admin"})
    assert removed.status_code == 200 and key not in removed.text and key not in keys_file.read_text(encoding="utf-8")
    for index in range(4):
        result = client.post(custom.CUSTOM_CHAT_MODELS_PATH, json={"action": "ADD_API", "label": f"API {index}", "base_url": f"https://api.example.com/v{index + 1}", "model": f"model-{index}", "api_key": "", "reviewer": "admin"})
        assert result.status_code == 200
    limited = client.post(custom.CUSTOM_CHAT_MODELS_PATH, json={"action": "ADD_API", "label": "API 5", "base_url": "https://api.example.com/v5", "model": "model-5", "api_key": "", "reviewer": "admin"})
    assert limited.json()["error"]["code"] == "LIMIT_REACHED"


def test_phase_9_32_legacy_entry_without_provider_loads(monkeypatch, tmp_path: Path) -> None:
    store = tmp_path / "custom.json"; monkeypatch.setattr(custom, "_path", lambda _root: store)
    store.write_text('{"version":"adaptive-custom-chat-models-v1","custom":[{"id":"custom-legacy","label":"Legacy","tag":"legacy:latest","mode":"general","added_by":"admin","added_at":"2026-10-27T00:00:00+00:00"}]}', encoding="utf-8")
    assert custom.load_custom_models(tmp_path)[0].provider == "ollama"


def test_legacy_entry_loads_live_when_installed(monkeypatch, tmp_path: Path) -> None:
    store = tmp_path / "custom.json"; monkeypatch.setattr(custom, "_path", lambda _root: store)
    store.write_text('{"version":"adaptive-custom-chat-models-v1","custom":[{"id":"custom-legacy","label":"Legacy","tag":"legacy:latest","mode":"general","added_by":"admin","added_at":"2026-10-27T00:00:00+00:00"}]}', encoding="utf-8")
    monkeypatch.setattr(custom, "installed_tags", lambda *_a, **_k: ["legacy:latest"])
    monkeypatch.setattr(custom, "_make_choice", lambda entry, *_a: ChatModelChoice(entry.id, entry.label, entry.tag, FakeModel(GOOD_IR)))
    registry = LiveChatModels([ChatModelChoice("general", "General", "base:latest", FakeModel(GOOD_IR))])
    assert custom.load_live_custom(tmp_path, registry, "http://127.0.0.1:11434", "30")["chat_model.custom-legacy"] == "enabled"


def test_api_name_validator_matches_listing_pattern_and_rejects_at(monkeypatch, tmp_path: Path) -> None:
    from backend.adaptive.api.chat_models import _TAG_PATTERN
    from backend.adaptive.harness.openai_chat import valid_api_model
    for name in ("gpt-4o", "meta-llama/Llama-3.1-8B", "model:v1", "model_v1"):
        assert valid_api_model(name) and _TAG_PATTERN.fullmatch(name)
    assert not valid_api_model("model@v1")
    registry = LiveChatModels([ChatModelChoice("general", "General", "base:latest", FakeModel(GOOD_IR))])
    app = FastAPI(); custom.register_custom_chat_model_routes(app, repository_root=tmp_path, registry=registry, base_url="http://127.0.0.1:11434", timeout="30")
    result = TestClient(app).post(custom.CUSTOM_CHAT_MODELS_PATH, json={"action":"ADD_API","label":"Remote","base_url":"https://api.example.com/v1","model":"model@v1","api_key":"","reviewer":"admin"})
    assert result.json()["error"]["code"] == "INVALID_API_SETTINGS"


def test_api_store_write_failures_restore_both_files(monkeypatch, tmp_path: Path) -> None:
    store, keys_file = tmp_path / "custom.json", tmp_path / "api-keys.json"
    monkeypatch.setattr(custom, "_path", lambda _root: store); monkeypatch.setattr(custom, "_keys_path", lambda _root: keys_file)
    registry = LiveChatModels([ChatModelChoice("general", "General", "base:latest", FakeModel(GOOD_IR))])
    app = FastAPI(); custom.register_custom_chat_model_routes(app, repository_root=tmp_path, registry=registry, base_url="http://127.0.0.1:11434", timeout="30")
    client = TestClient(app)
    before_store = store.read_bytes() if store.exists() else None
    before_keys = keys_file.read_bytes() if keys_file.exists() else None
    original_save = custom._save
    monkeypatch.setattr(custom, "_save", lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk")))
    add = client.post(custom.CUSTOM_CHAT_MODELS_PATH, json={"action":"ADD_API","label":"Remote","base_url":"https://api.example.com/v1","model":"model-v1","api_key":"secret-key-12345678","reviewer":"admin"})
    assert add.json()["error"]["code"] == "LIMIT_REACHED"
    assert (store.read_bytes() if store.exists() else None) == before_store
    assert (keys_file.read_bytes() if keys_file.exists() else None) == before_keys
    monkeypatch.setattr(custom, "_save", original_save)
    add = client.post(custom.CUSTOM_CHAT_MODELS_PATH, json={"action":"ADD_API","label":"Remote","base_url":"https://api.example.com/v1","model":"model-v1","api_key":"secret-key-12345678","reviewer":"admin"})
    model_id = add.json()["custom"][0]["id"]
    before_store, before_keys = store.read_bytes(), keys_file.read_bytes()
    monkeypatch.setattr(custom, "_save", lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk")))
    removed = client.post(custom.CUSTOM_CHAT_MODELS_PATH, json={"action":"REMOVE","id":model_id,"reviewer":"admin"})
    assert removed.json()["error"]["code"] == "LIMIT_REACHED"
    assert store.read_bytes() == before_store and keys_file.read_bytes() == before_keys


def test_custom_limit_is_shared_between_providers(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(custom, "_path", lambda _root: tmp_path / "custom.json")
    installed = [f"ollama-{i}:latest" for i in range(2)]
    monkeypatch.setattr(custom, "installed_tags", lambda *_a, **_k: installed)
    monkeypatch.setattr(custom, "_installed_tags_status", lambda *_a, **_k: (installed, True))
    monkeypatch.setattr(custom, "_make_choice", lambda entry, *_a: ChatModelChoice(entry.id, entry.label, entry.tag, FakeModel(GOOD_IR)))
    registry = LiveChatModels([ChatModelChoice("general", "General", "base:latest", FakeModel(GOOD_IR))])
    app = FastAPI(); custom.register_custom_chat_model_routes(app, repository_root=tmp_path, registry=registry, base_url="http://127.0.0.1:11434", timeout="30")
    client = TestClient(app)
    for i in range(2):
        assert client.post(custom.CUSTOM_CHAT_MODELS_PATH, json={"action":"ADD","tag":installed[i],"label":f"O{i}","mode":"general","reviewer":"admin"}).status_code == 200
        assert client.post(custom.CUSTOM_CHAT_MODELS_PATH, json={"action":"ADD_API","label":f"A{i}","base_url":f"https://api.example.com/v{i+1}","model":f"api-{i}","api_key":"","reviewer":"admin"}).status_code == 200
    fifth = client.post(custom.CUSTOM_CHAT_MODELS_PATH, json={"action":"ADD_API","label":"fifth","base_url":"https://api.example.com/v5","model":"api-5","api_key":"","reviewer":"admin"})
    assert fifth.json()["error"]["code"] == "LIMIT_REACHED"


def test_phase_9_33_builtin_listing_marks_default_and_install_state(monkeypatch, tmp_path: Path) -> None:
    """Phase 9-33: built-ins are listed read-only; only the first one is the default."""
    from backend.adaptive.api.ollama_startup import build_startup_ollama_model, parse_ollama_startup_config
    from backend.adaptive.harness.openai_chat import OpenAIQueryModel

    def ollama(tag: str) -> object:
        return build_startup_ollama_model(parse_ollama_startup_config(tag, None, "30", "model-template"), transport=lambda *_args: {})

    key = "sk-test-builtin-key-0123456789"
    registry = LiveChatModels([
        ChatModelChoice("general", "일반 모델", "qwen2.5-coder:7b-instruct", ollama("qwen2.5-coder:7b-instruct")),
        ChatModelChoice("legacy-tuned", "레거시 학습 모델", "text2sql_qwen", ollama("text2sql_qwen")),
        ChatModelChoice("openai", "ChatGPT", "gpt-5-mini", OpenAIQueryModel(key, "gpt-5-mini", transport=lambda *_args: {})),
    ])
    monkeypatch.setattr(custom, "_path", lambda _root: tmp_path / "custom.json")
    status = {"tags": (["qwen2.5-coder:7b-instruct"], True)}
    monkeypatch.setattr(custom, "_installed_tags_status", lambda *_a, **_k: status["tags"])
    app = FastAPI()
    custom.register_custom_chat_model_routes(app, repository_root=tmp_path, registry=registry, base_url="http://127.0.0.1:11434", timeout="30")
    client = TestClient(app)

    listing = client.get(custom.CUSTOM_CHAT_MODELS_PATH)
    assert [(item["id"], item["provider"], item["default"], item["installed"]) for item in listing.json()["builtin"]] == [
        ("general", "ollama", True, True), ("legacy-tuned", "ollama", False, False), ("openai", "openai", False, None),
    ]
    assert key not in listing.text and "11434" not in listing.text

    status["tags"] = ([], False)  # Ollama unreachable: install state unknown, not "missing"
    assert [item["installed"] for item in client.get(custom.CUSTOM_CHAT_MODELS_PATH).json()["builtin"]] == [None, None, None]
