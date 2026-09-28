"""Ollama provider for the evaluation-only direct-SQL arm.

Reuses the Phase 7-08 ``OllamaStartupConfig`` and loopback transport (no
proxy, no redirect, bounded response). One ``/api/generate`` call per
attempt, no retry, and the model text is returned untouched for
``direct-sql-extract-v1``. Errors become fixed provider outcomes.
"""

from __future__ import annotations

import socket
from typing import Any

from ..api.ollama_startup import OllamaStartupConfig, build_loopback_transport
from ..harness.ollama import GENERATION_OPTIONS
from .direct_sql import DirectSqlOutcome, DirectSqlProviderError, DirectSqlRequest


_GENERATE_PATH = "/api/generate"
_REPR = "OllamaDirectSqlModel()"


class OllamaDirectSqlModel:
    __slots__ = ("_url", "_model_tag", "_timeout", "_transport")

    def __init__(self, config: OllamaStartupConfig, *, transport: Any = None) -> None:
        bound = build_loopback_transport(config) if transport is None else transport
        if not callable(bound):
            raise ValueError("INVALID_TRANSPORT")
        self._url = config.base_url + _GENERATE_PATH
        self._model_tag = config.model_tag
        self._timeout = config.timeout_seconds
        self._transport = bound

    def __repr__(self) -> str:
        return _REPR

    def __str__(self) -> str:
        return _REPR

    def generate_sql(self, request: DirectSqlRequest) -> object:
        payload: dict[str, object] = {
            "model": self._model_tag,
            "prompt": request.prompt,
            "stream": False,
            "options": dict(GENERATION_OPTIONS),
        }
        timed_out = False
        failed = False
        envelope: object = None
        try:
            envelope = self._transport(self._url, payload, self._timeout)
        except (TimeoutError, socket.timeout):
            timed_out = True
        except Exception:
            failed = True
        if timed_out:
            raise DirectSqlProviderError(DirectSqlOutcome.PROVIDER_TIMEOUT)
        if (
            failed
            or not isinstance(envelope, dict)
            or "error" in envelope
            or not isinstance(envelope.get("response"), str)
        ):
            raise DirectSqlProviderError(DirectSqlOutcome.PROVIDER_ERROR)
        return envelope["response"]
