"""Chat model choices for the QueryIR chat (Phase 9-08, ADR-098).

The server decides which models the chat may call; the browser only names
one of these choices by its fixed id, never a model tag. The first choice is
the default.

- ``general``: a model not trained on any one school's database
  (``CHAT_MODEL``, default ``qwen2.5-coder:7b-instruct``). The adaptive chat
  uses it by default.
- ``legacy-tuned``: the legacy chatbot's fine-tuned model (``OLLAMA_MODEL``),
  kept as a comparison arm. It memorised legacy values (risk 3), so it is
  not the default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

CHAT_MODELS_PATH = "/api/adaptive/v1/chat/models"
CHAT_MODELS_VERSION = "adaptive-chat-models-v1"

GENERAL_MODEL_ID = "general"
LEGACY_TUNED_MODEL_ID = "legacy-tuned"
# Phase 9-09 (ADR-099): the ChatGPT API, offered only when OPENAI_API_KEY is set.
OPENAI_MODEL_ID = "openai"
# A single model passed the pre-9-08 way (``ollama_model`` alone).
DEFAULT_MODEL_ID = "default"
DEFAULT_CHAT_MODEL = "qwen2.5-coder:7b-instruct"

MODEL_LABELS = {
    GENERAL_MODEL_ID: "일반 모델",
    LEGACY_TUNED_MODEL_ID: "기존 모델",  # Phase 9-38: the team's fine-tuned legacy model, named plainly
    OPENAI_MODEL_ID: "ChatGPT(외부 API)",
    DEFAULT_MODEL_ID: "기본 모델",
}

MAX_CHOICES = 7
MAX_CUSTOM_CHOICES = 4
MODEL_ID_PATTERN = re.compile(r"[a-z][a-z0-9-]{0,31}")
_TAG_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_MAX_LABEL = 40

__all__ = [
    "CHAT_MODELS_PATH",
    "CHAT_MODELS_VERSION",
    "DEFAULT_CHAT_MODEL",
    "DEFAULT_MODEL_ID",
    "GENERAL_MODEL_ID",
    "LEGACY_TUNED_MODEL_ID",
    "MODEL_LABELS",
    "MAX_CUSTOM_CHOICES",
    "OPENAI_MODEL_ID",
    "ChatModelChoice",
    "normalize_ollama_tag",
    "validate_choices",
]


# Phase 9-31: Ollama treats an omitted tag as ``latest`` for the last path segment.
def normalize_ollama_tag(tag: str) -> str:
    """Normalize an Ollama model name that omits its implicit latest tag."""
    head, separator, name = tag.rpartition("/")
    if ":" not in name:
        name += ":latest"
    return f"{head}{separator}{name}"


@dataclass(frozen=True)
class ChatModelChoice:
    """One model the chat may call; ``model`` is the callable QueryIR model."""

    id: str
    label: str
    tag: str | None  # None only for a model passed the pre-9-08 way
    model: Any = field(repr=False, compare=False)

    def listing(self) -> dict[str, str | None]:
        return {"id": self.id, "label": self.label, "tag": self.tag}


def validate_choices(choices: object) -> tuple[ChatModelChoice, ...]:
    """Check a choice list; raises ``ValueError`` on anything unexpected."""
    if not isinstance(choices, (list, tuple)) or not 1 <= len(choices) <= MAX_CHOICES:
        raise ValueError("INVALID_CHAT_MODELS")
    seen: set[str] = set()
    for choice in choices:
        if (
            not isinstance(choice, ChatModelChoice)
            or not isinstance(choice.id, str)
            or MODEL_ID_PATTERN.fullmatch(choice.id) is None
            or choice.id in seen
            or not isinstance(choice.label, str)
            or not choice.label.strip()
            or len(choice.label) > _MAX_LABEL
            or not (
                choice.tag is None
                or (isinstance(choice.tag, str) and _TAG_PATTERN.fullmatch(choice.tag) is not None)
            )
            or not callable(getattr(choice.model, "generate_ir", None))
        ):
            raise ValueError("INVALID_CHAT_MODELS")
        seen.add(choice.id)
    return tuple(choices)
