"""Minimal Ollama QueryModel adapter with an injectable HTTP boundary."""

from __future__ import annotations

import asyncio
import json
import math
import socket
from collections.abc import Callable
from concurrent.futures import CancelledError
from dataclasses import dataclass, field
from urllib import error, parse, request

from .contracts import HarnessRequest, MappingProfile, MappingStatus, Ontology
from .recorded import RecordedFailureCode
from .recording_orchestrator import RecordingProviderError


PROMPT_VERSION = "query-ir-v1"
GENERATION_OPTIONS = {"temperature": 0}
OllamaTransport = Callable[[str, dict[str, object], float], object]


@dataclass(frozen=True)
class OllamaGeneration:
    """In-memory format observation; never persist or print value by default."""

    value: object = field(repr=False)
    json_parsed: bool


def _default_transport(url: str, payload: dict[str, object], timeout: float) -> object:
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    http_request = request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with request.urlopen(http_request, timeout=timeout) as response:
        return json.loads(
            response.read().decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
            parse_float=_finite_float,
        )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _reject_non_finite(_: str) -> None:
    raise ValueError("non-finite number")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite number")
    return parsed


def _prompt(question: str, ontology: Ontology, mapping: MappingProfile) -> str:
    # Never serialize MappingProfile: it contains physical table and column names.
    semantic_fields = sorted(set(ontology.fields).intersection(mapping.fields))
    context = {
        "entities": sorted(ontology.entities),
        "fields": [
            {
                "name": name,
                "type": ontology.fields[name].data_type.value,
                "operators": sorted(item.value for item in ontology.fields[name].allowed_operators),
            }
            for name in sorted(ontology.fields)
        ],
        "available_fields": semantic_fields,
    }
    return (
        f"Prompt version: {PROMPT_VERSION}\n"
        "Return only one JSON value matching QueryIR: "
        '{"entity":"Entity","select":["Entity.field"],'
        '"filters":[{"field":"Entity.field","operator":"equals","value":"text"}],'
        '"limit":50}. Use only available semantic fields and allowed operators. '
        "Do not return SQL, Markdown, or an explanation.\n"
        f"Context: {json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}\n"
        f"Question: {question}"
    )


class OllamaQueryModel:
    """One /api/generate call per request; no retries or model substitution.

    The default urllib transport has no active cancellation API. An injected
    transport can signal a real cancellation with CancelledError.
    """

    def __init__(
        self,
        base_url: str,
        model_tag: str,
        timeout: float,
        *,
        transport: OllamaTransport = _default_transport,
    ) -> None:
        try:
            parsed = parse.urlsplit(base_url)
        except (TypeError, ValueError):
            raise ValueError("INVALID_OLLAMA_BASE_URL") from None
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("INVALID_OLLAMA_BASE_URL")
        if not isinstance(model_tag, str) or not model_tag.strip():
            raise ValueError("INVALID_OLLAMA_MODEL_TAG")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout < float("inf"):
            raise ValueError("INVALID_OLLAMA_TIMEOUT")
        if not callable(transport):
            raise ValueError("INVALID_OLLAMA_TRANSPORT")
        self._url = base_url.rstrip("/") + "/api/generate"
        self._model_tag = model_tag
        self._timeout = float(timeout)
        self._transport = transport

    def generate_ir(
        self, request: HarnessRequest, ontology: Ontology, mapping: MappingProfile
    ) -> object:
        return self.generate_observed(request, ontology, mapping).value

    def generate_observed(
        self, request: HarnessRequest, ontology: Ontology, mapping: MappingProfile
    ) -> OllamaGeneration:
        """Invoke once and expose parse status for private, in-memory diagnostics."""
        if mapping.status is not MappingStatus.PUBLISHED or mapping.database_id != request.database_id:
            raise RecordingProviderError(RecordedFailureCode.PROVIDER_ERROR)
        payload: dict[str, object] = {
            "model": self._model_tag,
            "prompt": _prompt(request.question, ontology, mapping),
            "stream": False,
            "options": dict(GENERATION_OPTIONS),
        }
        failure_code: RecordedFailureCode | None = None
        envelope: object = None
        try:
            envelope = self._transport(self._url, payload, self._timeout)
        except (CancelledError, asyncio.CancelledError):
            failure_code = RecordedFailureCode.CANCELLED
        except (TimeoutError, socket.timeout):
            failure_code = RecordedFailureCode.TIMEOUT
        except error.URLError as exc:
            failure_code = (
                RecordedFailureCode.TIMEOUT
                if isinstance(exc.reason, (TimeoutError, socket.timeout))
                else RecordedFailureCode.PROVIDER_ERROR
            )
        except Exception:
            failure_code = RecordedFailureCode.PROVIDER_ERROR
        if failure_code is not None:
            raise RecordingProviderError(failure_code)

        if (
            not isinstance(envelope, dict)
            or "error" in envelope
            or not isinstance(envelope.get("response"), str)
        ):
            raise RecordingProviderError(RecordedFailureCode.PROVIDER_ERROR)
        output = envelope["response"]
        if not output.strip():
            raise RecordingProviderError(RecordedFailureCode.EMPTY_RESPONSE)
        try:
            parsed_output = json.loads(
                output,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite,
                parse_float=_finite_float,
            )
        except json.JSONDecodeError:
            return OllamaGeneration(value=output, json_parsed=False)
        except ValueError:
            pass
        else:
            return OllamaGeneration(value=parsed_output, json_parsed=True)
        raise RecordingProviderError(RecordedFailureCode.PROVIDER_ERROR)
