"""Server-owned chat model choices that can grow while the server runs (Phase 9-31)."""

from __future__ import annotations

import threading
from typing import Any

from .chat_models import ChatModelChoice, MAX_CHOICES, MODEL_ID_PATTERN, normalize_ollama_tag, validate_choices


class LiveChatModels:
    """Keep one mutable choice registry shared by the chat and admin routes."""

    def __init__(self, builtins: list[ChatModelChoice] | tuple[ChatModelChoice, ...]) -> None:
        self._lock = threading.RLock()
        self._builtins = validate_choices(builtins)
        if not self._builtins or self._builtins[0].id.startswith("custom-"):
            raise ValueError("INVALID_CHAT_MODELS")
        self._custom: dict[str, ChatModelChoice] = {}

    def snapshot(self) -> tuple[ChatModelChoice, ...]:
        with self._lock:
            return (*self._builtins, *self._custom.values())

    def add(self, choice: ChatModelChoice) -> None:
        with self._lock:
            if (
                not isinstance(choice, ChatModelChoice)
                or not choice.id.startswith("custom-")
                or MODEL_ID_PATTERN.fullmatch(choice.id) is None
                or choice.id in {item.id for item in self._builtins}
                or choice.id in self._custom
                or len(self._custom) >= 4
                or len(self._builtins) + len(self._custom) >= MAX_CHOICES
            ):
                raise ValueError("INVALID_CHAT_MODELS")
            validate_choices([*self._builtins, *self._custom.values(), choice])
            self._custom[choice.id] = choice

    def remove(self, model_id: str) -> bool:
        with self._lock:
            return self._custom.pop(model_id, None) is not None

    def get(self, model_id: str) -> ChatModelChoice | None:
        with self._lock:
            return next((item for item in (*self._builtins, *self._custom.values()) if item.id == model_id), None)

    @property
    def builtin_ids(self) -> frozenset[str]:
        return frozenset(item.id for item in self._builtins)

    @property
    def builtins(self) -> tuple[ChatModelChoice, ...]:
        return self._builtins

    @property
    def builtin_tags(self) -> frozenset[str]:
        return frozenset(normalize_ollama_tag(item.tag) for item in self._builtins if item.tag is not None)
