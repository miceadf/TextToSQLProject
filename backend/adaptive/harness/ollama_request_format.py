"""Explicit Ollama request formats for runtime and evaluation calls.

``model-template`` sends the prompt as-is, so Ollama applies the model's
registered TEMPLATE and its built-in SYSTEM. For ``text2sql_qwen`` that
SYSTEM carries the CNU ``courses`` schema, which leaks into every prompt.
``alpaca-raw-no-system-v1`` sends ``raw: true`` with the same registered
Alpaca TEMPLATE reproduced byte-for-byte (CRLF) and an empty instruction, so
only our prompt reaches the model. It is valid only for models whose
registered TEMPLATE is that Alpaca template, so it is never a silent
default; callers choose a format explicitly (ADR-058/059).
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any


REQUEST_FORMAT_MODEL_TEMPLATE = "model-template"
REQUEST_FORMAT_NO_SYSTEM = "alpaca-raw-no-system-v1"
REQUEST_FORMATS = (REQUEST_FORMAT_MODEL_TEMPLATE, REQUEST_FORMAT_NO_SYSTEM)

# Registered text2sql_qwen TEMPLATE with {{ .System }} rendered empty.
ALPACA_NO_SYSTEM_PREFIX = (
    "Below is an instruction that describes a task, paired with an input that "
    "provides further context. Write a response that appropriately completes "
    "the request.\r\n\r\n### Instruction:\r\n\r\n\r\n### Input:\r\n"
)
ALPACA_NO_SYSTEM_SUFFIX = "\r\n\r\n### Response:\r\n"
ALPACA_NO_SYSTEM_SHA256 = hashlib.sha256(
    (ALPACA_NO_SYSTEM_PREFIX + "{prompt}" + ALPACA_NO_SYSTEM_SUFFIX).encode("utf-8")
).hexdigest()


class RequestFormatError(ValueError):
    def __init__(self) -> None:
        super().__init__("INVALID_REQUEST_FORMAT")


class _NoSystemTransport:
    """Wrap one transport; rewrites a copy of the payload, never the original."""

    __slots__ = ("_inner",)

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __repr__(self) -> str:
        return "NoSystemTransport()"

    def __call__(self, url: str, payload: dict[str, object], timeout: float) -> object:
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("prompt"), str)
            or "raw" in payload
            or "system" in payload
            or "template" in payload
        ):
            raise RequestFormatError()
        rewritten = copy.deepcopy(payload)
        rewritten["prompt"] = ALPACA_NO_SYSTEM_PREFIX + payload["prompt"] + ALPACA_NO_SYSTEM_SUFFIX
        rewritten["raw"] = True
        return self._inner(url, rewritten, timeout)


def apply_request_format(transport: Any, request_format: str) -> Any:
    # Check the original transport first so no format can mask a bad one.
    if not callable(transport):
        raise RequestFormatError()
    if request_format == REQUEST_FORMAT_MODEL_TEMPLATE:
        return transport
    if request_format == REQUEST_FORMAT_NO_SYSTEM:
        return _NoSystemTransport(transport)
    raise RequestFormatError()


def request_format_label(request_format: str) -> str:
    return {REQUEST_FORMAT_MODEL_TEMPLATE: "tmpl", REQUEST_FORMAT_NO_SYSTEM: "nosys"}[request_format]
