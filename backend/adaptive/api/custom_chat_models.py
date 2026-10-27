"""Private custom Ollama chat models managed from the admin screen (Phase 9-31)."""

from __future__ import annotations

import json
import re
import threading
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from ..registration import postgres_registrations as pg
from .chat_models import (
    ChatModelChoice,
    MAX_CUSTOM_CHOICES,
    MODEL_ID_PATTERN,
    _TAG_PATTERN,
    normalize_ollama_tag,
)
from .live_chat_models import LiveChatModels
from .review_publish import _parse_strict_body, _read_limited_body

CUSTOM_CHAT_MODELS_PATH = "/api/adaptive/v1/chat/custom-models"
CUSTOM_CHAT_MODELS_VERSION = "adaptive-custom-chat-models-v1"
_SLUG = re.compile(r"[^a-z0-9]+")
_MAX_TAGS_BYTES = 256 * 1024
_TAG_TIMEOUT = 2.0
_CHANGE_LOCK = threading.Lock()


class CustomChatModelError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class StoredCustomModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^custom-[a-z][a-z0-9-]{0,24}$")
    label: str = Field(min_length=1, max_length=40)
    tag: str = Field(pattern=_TAG_PATTERN.pattern)
    mode: str = Field(pattern=r"^(general|tuned)$")
    added_by: str = Field(min_length=1, max_length=64)
    added_at: str = Field(min_length=1, max_length=40)
    provider: str = Field(default="ollama", pattern=r"^(ollama|openai-compatible)$")
    base_url: str | None = None
    has_key: bool = False


class StoredCustomModels(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(pattern=r"^adaptive-custom-chat-models-v1$")
    custom: list[StoredCustomModel] = Field(max_length=MAX_CUSTOM_CHOICES)


def _path(repository_root: str | Path) -> Path:
    _root, directory = pg._private_dir(repository_root, (), "chat-models")  # noqa: SLF001
    return directory / "custom.json"


def _keys_path(repository_root: str | Path) -> Path:
    # Phase 9-32: API credentials live outside the listing store.
    _root, directory = pg._private_dir(repository_root, (), "chat-models")  # noqa: SLF001
    return directory / "api-keys.json"


def _load_keys(repository_root: str | Path) -> dict[str, str]:
    path = _keys_path(repository_root)
    if not path.exists(): return {}
    value = pg._strict_json(pg._read(path))  # noqa: SLF001
    if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
        raise CustomChatModelError("STORE_INVALID")
    return value


def _save_keys(repository_root: str | Path, keys: dict[str, str]) -> None:
    path = _keys_path(repository_root); path.parent.mkdir(parents=True, exist_ok=True)
    pg._write_atomic(path, pg._json_bytes(keys))  # noqa: SLF001


def _restore_file(path: Path, previous: bytes | None) -> None:
    # Luna 9-32: restore exact prior bytes when either store write fails.
    if previous is None:
        path.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(previous)


def load_custom_models(repository_root: str | Path) -> list[StoredCustomModel]:
    path = _path(repository_root)
    if not path.exists():
        return []
    raw = pg._strict_json(pg._read(path))  # noqa: SLF001
    if isinstance(raw, dict) and raw.get("version") == CUSTOM_CHAT_MODELS_VERSION and isinstance(raw.get("custom"), list):
        # Phase 9-32: entries saved before API models carry none of the new fields.
        raw = {**raw, "custom": [
            {"provider": "ollama", "base_url": None, "has_key": False, **entry}
            if isinstance(entry, dict) and "provider" not in entry else entry
            for entry in raw["custom"]
        ]}
    stored = pg._strict_model(pg._json_bytes(raw), StoredCustomModels)  # noqa: SLF001
    identities = [(item.provider, normalize_ollama_tag(item.tag) if item.provider == "ollama" else (str(item.base_url).rstrip("/").lower(), item.tag)) for item in stored.custom]
    if len({item.id for item in stored.custom}) != len(stored.custom) or len(set(identities)) != len(stored.custom):
        raise CustomChatModelError("STORE_INVALID")
    return list(stored.custom)


def _save(repository_root: str | Path, entries: list[StoredCustomModel]) -> None:
    path = _path(repository_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = StoredCustomModels(version=CUSTOM_CHAT_MODELS_VERSION, custom=entries)
    pg._write_atomic(path, pg._json_bytes(payload.model_dump(mode="json")))  # noqa: SLF001


def _installed_tags_status(base_url: str, *, opener: Any = None) -> tuple[list[str], bool]:
    """Read a bounded /api/tags response over the configured loopback endpoint."""
    from .ollama_startup import parse_ollama_startup_config

    try:
        base_url = parse_ollama_startup_config(
            "chat-model-probe:latest", base_url, "2", "model-template", "none", "query-ir-v1"
        ).base_url
    except Exception:
        return [], False
    if opener is None:
        from .ollama_startup import _RejectRedirectHandler

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RejectRedirectHandler())
    request = urllib.request.Request(base_url + "/api/tags", headers={"Accept": "application/json"})
    try:
        with opener.open(request, timeout=_TAG_TIMEOUT) as response:
            raw = response.read(_MAX_TAGS_BYTES + 1)
        if len(raw) > _MAX_TAGS_BYTES:
            return [], False
        value = json.loads(raw.decode("utf-8"))
        models = value.get("models") if isinstance(value, dict) else None
        if not isinstance(models, list):
            return [], False
        names = {item["name"] for item in models if isinstance(item, dict) and isinstance(item.get("name"), str) and _TAG_PATTERN.fullmatch(item["name"])}
        return sorted(names)[:100], True
    except Exception:
        return [], False


def installed_tags(base_url: str, *, opener: Any = None) -> list[str]:
    return _installed_tags_status(base_url, opener=opener)[0]


def _make_choice(entry: StoredCustomModel, base_url: str, timeout: str) -> ChatModelChoice:
    if entry.provider != "ollama":
        # API entries need their key; ADD_API and load_live_custom build them.
        raise ValueError("NOT_AN_OLLAMA_ENTRY")
    from .ollama_startup import build_startup_ollama_model, parse_ollama_startup_config

    if entry.mode == "general":
        request_format, output_format, prompt_version = "model-template", "queryir-schema-v1", "query-ir-v3"
    else:
        from ..harness.ollama_request_format import REQUEST_FORMAT_NO_SYSTEM

        request_format, output_format, prompt_version = REQUEST_FORMAT_NO_SYSTEM, "none", "query-ir-v1"
    config = parse_ollama_startup_config(entry.tag, base_url, timeout, request_format, output_format, prompt_version)
    model = build_startup_ollama_model(config)
    return ChatModelChoice(entry.id, entry.label, entry.tag, model)


def load_live_custom(repository_root: str | Path, registry: LiveChatModels, base_url: str, timeout: str) -> dict[str, str]:
    status: dict[str, str] = {}
    tags: set[str] | None = None
    try:
        path = _path(repository_root)
        if not path.exists():
            return status
        document = pg._strict_json(pg._read(path))  # noqa: SLF001
        raw_entries = document.get("custom") if isinstance(document, dict) else None
        if (
            not isinstance(document, dict)
            or set(document) != {"version", "custom"}
            or document.get("version") != CUSTOM_CHAT_MODELS_VERSION
            or not isinstance(raw_entries, list)
            or len(raw_entries) > MAX_CUSTOM_CHOICES
        ):
            return {"chat_model.custom": "disabled (STORE_INVALID)"}
    except Exception:
        return {"chat_model.custom": "disabled (STORE_INVALID)"}
    entries: list[StoredCustomModel] = []
    seen_ids: set[str] = set()
    seen_identities: set[object] = set()
    for index, raw_entry in enumerate(raw_entries):
        try:
            normalized_entry = {"provider": "ollama", "base_url": None, "has_key": False, **raw_entry} if isinstance(raw_entry, dict) and "provider" not in raw_entry else raw_entry
            entry = StoredCustomModel.model_validate(normalized_entry)
            identity = (entry.provider, normalize_ollama_tag(entry.tag) if entry.provider == "ollama" else (entry.base_url, entry.tag))
            if normalized_entry != entry.model_dump(mode="json") or entry.id in seen_ids or identity in seen_identities:
                raise ValueError("INVALID_ENTRY")
        except Exception:
            candidate_id = raw_entry.get("id") if isinstance(raw_entry, dict) else None
            key = candidate_id if isinstance(candidate_id, str) and MODEL_ID_PATTERN.fullmatch(candidate_id) else f"custom-entry-{index + 1}"
            status[f"chat_model.{key}"] = "disabled (INVALID_ENTRY)"
            continue
        entries.append(entry)
        seen_ids.add(entry.id)
        seen_identities.add((entry.provider, normalize_ollama_tag(entry.tag) if entry.provider == "ollama" else (entry.base_url, entry.tag)))
    for entry in entries:
        if entry.provider == "openai-compatible":
            try:
                keys = _load_keys(repository_root)
                if entry.has_key and entry.id not in keys:
                    status[f"chat_model.{entry.id}"] = "disabled (KEY_MISSING)"; continue
                if entry.base_url is None:
                    raise ValueError()
                from ..harness.openai_chat import OpenAIQueryModel, validate_api_base_url
                api_key = keys.get(entry.id) if entry.has_key else None
                choice = ChatModelChoice(entry.id, entry.label, entry.tag, OpenAIQueryModel(api_key, entry.tag, endpoint=validate_api_base_url(entry.base_url)))
                registry.add(choice); status[f"chat_model.{entry.id}"] = "enabled"
            except Exception:
                status[f"chat_model.{entry.id}"] = "disabled (INVALID_OR_STARTUP_FAILED)"
            continue
        if tags is None:
            tags = {normalize_ollama_tag(tag) for tag in installed_tags(base_url)}
        if normalize_ollama_tag(entry.tag) in registry.builtin_tags:
            status[f"chat_model.{entry.id}"] = "disabled (DUPLICATE)"
            continue
        if normalize_ollama_tag(entry.tag) not in tags:
            status[f"chat_model.{entry.id}"] = "disabled (NOT_INSTALLED)"
            continue
        try:
            registry.add(_make_choice(entry, base_url, timeout))
        except Exception:
            status[f"chat_model.{entry.id}"] = "disabled (INVALID_OR_STARTUP_FAILED)"
        else:
            status[f"chat_model.{entry.id}"] = "enabled"
    return status


def register_custom_chat_model_routes(
    app: FastAPI, *, repository_root: str | Path, registry: LiveChatModels, base_url: str, timeout: str,
    opener: Any = None,
) -> None:
    """Strict admin routes; all failures use fixed codes."""
    builtins = registry.builtin_ids
    builtin_tags = registry.builtin_tags

    def response_payload() -> dict[str, Any]:
        tags, available = _installed_tags_status(base_url, opener=opener)
        installed = {normalize_ollama_tag(tag) for tag in tags}
        from ..harness.ollama import OllamaQueryModel
        from ..harness.openai_chat import OpenAIQueryModel
        builtin = []
        for index, choice in enumerate(registry.builtins):
            if isinstance(choice.model, OllamaQueryModel):
                provider = "ollama"
                is_installed = normalize_ollama_tag(choice.tag) in installed if choice.tag is not None and available else None
            elif isinstance(choice.model, OpenAIQueryModel):
                provider, is_installed = "openai", None
            else:
                provider, is_installed = "other", None
            builtin.append({"id": choice.id, "label": choice.label, "tag": choice.tag, "provider": provider,
                            "default": index == 0, "installed": is_installed})
        try:
            custom = load_custom_models(repository_root)
        except Exception:
            custom = []
        return {
            "version": CUSTOM_CHAT_MODELS_VERSION, "installed": tags, "unavailable": not available, "builtin": builtin,
            "custom": [{**entry.model_dump(mode="json"), "active": registry.get(entry.id) is not None} for entry in custom], "limit": MAX_CUSTOM_CHOICES,
        }

    @app.get(CUSTOM_CHAT_MODELS_PATH)
    async def list_custom_models() -> JSONResponse:
        return JSONResponse(status_code=200, content=await run_in_threadpool(response_payload))

    @app.post(CUSTOM_CHAT_MODELS_PATH)
    async def change_custom_models(http_request: Request) -> JSONResponse:
        try:
            raw = await _read_limited_body(http_request)
            body = None if raw is None else _parse_strict_body(raw)
            if not isinstance(body, dict) or not isinstance(body.get("action"), str):
                raise CustomChatModelError("INVALID_INPUT")
            action = body["action"]
            if action == "ADD" and set(body) == {"action", "tag", "label", "mode", "reviewer"}:
                tag, label, mode, reviewer = (body.get(key) for key in ("tag", "label", "mode", "reviewer"))
                if not all(isinstance(value, str) for value in (tag, label, mode, reviewer)):
                    raise CustomChatModelError("INVALID_INPUT")
                if _TAG_PATTERN.fullmatch(tag) is None:
                    raise CustomChatModelError("NOT_INSTALLED")
                if not isinstance(label, str) or not label.strip() or len(label) > 40 or any(ord(ch) < 32 for ch in label):
                    raise CustomChatModelError("INVALID_INPUT")
                if not isinstance(reviewer, str) or not 1 <= len(reviewer.strip()) <= 64 or len(reviewer) > 64:
                    raise CustomChatModelError("INVALID_INPUT")
                if mode not in ("general", "tuned"):
                    raise CustomChatModelError("INVALID_INPUT")
                def add_model() -> dict[str, Any]:
                    with _CHANGE_LOCK:
                        if normalize_ollama_tag(tag) not in {normalize_ollama_tag(item) for item in installed_tags(base_url, opener=opener)}:
                            raise CustomChatModelError("NOT_INSTALLED")
                        entries = load_custom_models(repository_root)
                        normalized_tag = normalize_ollama_tag(tag)
                        if normalized_tag in builtin_tags or any(normalize_ollama_tag(entry.tag) == normalized_tag for entry in entries):
                            raise CustomChatModelError("DUPLICATE")
                        if len(entries) >= MAX_CUSTOM_CHOICES:
                            raise CustomChatModelError("LIMIT_REACHED")
                        base_slug = _SLUG.sub("-", tag.lower()).strip("-")[:20].strip("-") or "model"
                        slug, suffix = base_slug, 2
                        ids = builtins | {entry.id for entry in entries}
                        while f"custom-{slug}" in ids:
                            slug = f"{base_slug[:16]}-{suffix}"
                            suffix += 1
                        model_id = f"custom-{slug}"
                        if MODEL_ID_PATTERN.fullmatch(model_id) is None:
                            raise CustomChatModelError("INVALID_INPUT")
                        entry = StoredCustomModel(id=model_id, label=label.strip(), tag=tag, mode=mode,
                            added_by=reviewer.strip(), added_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
                        try:
                            choice = _make_choice(entry, base_url, timeout)
                        except Exception:
                            raise CustomChatModelError("INVALID_INPUT") from None
                        _save(repository_root, [*entries, entry])
                        try:
                            registry.add(choice)
                        except Exception:
                            _save(repository_root, entries)
                            raise CustomChatModelError("LIMIT_REACHED") from None
                    return response_payload()
                return JSONResponse(status_code=200, content=await run_in_threadpool(add_model))
            if action == "ADD_API" and set(body) == {"action", "label", "base_url", "model", "api_key", "reviewer"}:
                # Phase 9-32: registration validates locally and never probes the provider.
                from ..harness.openai_chat import OpenAIQueryModel, valid_api_key, valid_api_model, validate_api_base_url
                label, base, model, key, reviewer = (body.get(k) for k in ("label", "base_url", "model", "api_key", "reviewer"))
                if not all(isinstance(v, str) for v in (label, base, model, key, reviewer)):
                    raise CustomChatModelError("INVALID_INPUT")
                if not label.strip() or len(label) > 40 or any(ord(c) < 32 for c in label) or not 1 <= len(reviewer.strip()) <= 64 or len(reviewer) > 64:
                    raise CustomChatModelError("INVALID_INPUT")
                try:
                    endpoint = validate_api_base_url(base)
                    if not valid_api_model(model) or not valid_api_key(key): raise ValueError()
                except ValueError:
                    raise CustomChatModelError("INVALID_API_SETTINGS") from None
                def add_api_model() -> dict[str, Any]:
                    with _CHANGE_LOCK:
                        entries = load_custom_models(repository_root)
                        norm = (base.rstrip("/").lower(), model)
                        if any(e.provider == "openai-compatible" and (str(e.base_url).rstrip("/").lower(), e.tag) == norm for e in entries):
                            raise CustomChatModelError("DUPLICATE")
                        if len(entries) >= MAX_CUSTOM_CHOICES: raise CustomChatModelError("LIMIT_REACHED")
                        slug = _SLUG.sub("-", model.lower()).strip("-")[:20].strip("-") or "api"
                        ids = builtins | {e.id for e in entries}; suffix = 2; base_slug = slug
                        while f"custom-{slug}" in ids:
                            slug = f"{base_slug[:16]}-{suffix}"; suffix += 1
                        model_id = f"custom-{slug}"
                        entry = StoredCustomModel(id=model_id, label=label.strip(), tag=model, mode="general", provider="openai-compatible", base_url=base.rstrip("/"), has_key=bool(key), added_by=reviewer.strip(), added_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
                        choice = ChatModelChoice(model_id, entry.label, model, OpenAIQueryModel(key or None, model, endpoint=endpoint))
                        keys = _load_keys(repository_root)
                        key_path, store_path = _keys_path(repository_root), _path(repository_root)
                        old_keys = key_path.read_bytes() if key_path.exists() else None
                        old_store = store_path.read_bytes() if store_path.exists() else None
                        if key: keys[model_id] = key
                        # Luna 9-32: persist credentials before publishing the listing entry.
                        try:
                            _save_keys(repository_root, keys)
                            _save(repository_root, [*entries, entry])
                            registry.add(choice)
                        except Exception:
                            _restore_file(key_path, old_keys)
                            _restore_file(store_path, old_store)
                            raise CustomChatModelError("LIMIT_REACHED") from None
                    return response_payload()
                return JSONResponse(status_code=200, content=await run_in_threadpool(add_api_model))
            if action == "REMOVE" and set(body) == {"action", "id", "reviewer"}:
                model_id, reviewer = body.get("id"), body.get("reviewer")
                if not isinstance(model_id, str) or not isinstance(reviewer, str) or not 1 <= len(reviewer.strip()) <= 64 or len(reviewer) > 64:
                    raise CustomChatModelError("INVALID_INPUT")
                if model_id in builtins:
                    raise CustomChatModelError("NOT_FOUND")
                def remove_model() -> dict[str, Any]:
                    with _CHANGE_LOCK:
                        entries = load_custom_models(repository_root)
                        if not any(entry.id == model_id for entry in entries) and registry.get(model_id) is None:
                            raise CustomChatModelError("NOT_FOUND")
                        keys = _load_keys(repository_root)
                        key_path, store_path = _keys_path(repository_root), _path(repository_root)
                        old_keys = key_path.read_bytes() if key_path.exists() else None
                        old_store = store_path.read_bytes() if store_path.exists() else None
                        if model_id in keys:
                            keys.pop(model_id)
                        try:
                            # Luna 9-32: remove credentials before the listing and registry.
                            if old_keys is not None and model_id in _load_keys(repository_root):
                                _save_keys(repository_root, keys)
                            _save(repository_root, [entry for entry in entries if entry.id != model_id])
                            registry.remove(model_id)
                        except Exception:
                            _restore_file(key_path, old_keys)
                            _restore_file(store_path, old_store)
                            raise CustomChatModelError("LIMIT_REACHED") from None
                    return response_payload()
                return JSONResponse(status_code=200, content=await run_in_threadpool(remove_model))
            raise CustomChatModelError("INVALID_INPUT")
        except CustomChatModelError as exc:
            return JSONResponse(status_code=400, content={"error": {"code": exc.code}})
        except Exception:
            return JSONResponse(status_code=500, content={"error": {"code": "INTERNAL_ERROR"}})


def register_custom_chat_models_unavailable(app: FastAPI) -> None:
    """Return one fixed response when Ollama chat is not configured."""
    async def unavailable() -> JSONResponse:
        return JSONResponse(status_code=503, content={"error": {"code": "OLLAMA_NOT_CONFIGURED"}})

    app.add_api_route(CUSTOM_CHAT_MODELS_PATH, unavailable, methods=["GET", "POST"])
