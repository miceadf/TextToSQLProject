"""ChatGPT API QueryIR model (Phase 9-09, ADR-099).

One Chat Completions call per question to the fixed OpenAI endpoint. It uses
the general model's prompt (``query-ir-v3``) and value hints, and asks for
the QueryIR JSON schema as the response format (not strict: the schema uses
keywords strict mode does not take). The answer goes through the same
QueryIR validation, compiler, policy and executor as any other model.

The API key is used only in the request header. It never appears in a repr,
an error, or a log; failures carry a fixed code only.
"""

from __future__ import annotations

import json
import re
import socket
from urllib.parse import urlsplit
from typing import Any, Callable
from urllib import error, request

from .contracts import HarnessRequest, MappingProfile, MappingStatus, Ontology
from .ollama import (
    _finite_float,
    _prompt,
    _reject_duplicate_keys,
    _reject_non_finite,
    queryir_schema,
)
from .recorded import RecordedFailureCode
from .recording_orchestrator import RecordingProviderError

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_OPENAI_MODEL = "gpt-5-mini"
OPENAI_PROMPT_VERSION = "query-ir-v3"
OPENAI_TIMEOUT_SECONDS = 60.0
MAX_OPENAI_RESPONSE_BYTES = 1024 * 1024
_MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")
_KEY_PATTERN = re.compile(r"[A-Za-z0-9_\-]{20,300}")
_REPR = "OpenAIQueryModel()"
_CUSTOM_MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_CUSTOM_KEY_PATTERN = re.compile(r"[!-~]{8,500}")

OpenAITransport = Callable[[str, dict[str, str], dict[str, object], float], object]

__all__ = ["DEFAULT_OPENAI_MODEL", "OPENAI_CHAT_URL", "OpenAIQueryModel", "valid_openai_settings", "valid_api_model", "valid_api_key", "validate_api_base_url"]


def valid_api_model(model: object) -> bool:
    return isinstance(model, str) and _CUSTOM_MODEL_PATTERN.fullmatch(model) is not None


def valid_api_key(api_key: object) -> bool:
    return api_key == "" or api_key is None or (isinstance(api_key, str) and _CUSTOM_KEY_PATTERN.fullmatch(api_key) is not None)


def validate_api_base_url(base_url: object) -> str:
    """Validate an OpenAI-compatible API base and return its chat endpoint."""
    # Phase 9-32: permit TLS hosts and cleartext loopback only.
    if not isinstance(base_url, str) or not base_url or any(ord(ch) < 33 or ord(ch) > 126 for ch in base_url):
        raise ValueError("INVALID_API_BASE_URL")
    try:
        parsed = urlsplit(base_url)
        host = parsed.hostname
        port = parsed.port
    except Exception:
        raise ValueError("INVALID_API_BASE_URL") from None
    loopback = host in {"127.0.0.1", "localhost", "::1"}
    if (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback)) or not host or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment or "?" in base_url or "#" in base_url or parsed.path.endswith("//") or (parsed.path and not parsed.path.startswith("/")):
        raise ValueError("INVALID_API_BASE_URL")
    if parsed.netloc.endswith(":") or (port is not None and not 1 <= port <= 65535):
        raise ValueError("INVALID_API_BASE_URL")
    return base_url.rstrip("/") + "/chat/completions"


def valid_openai_settings(api_key: object, model: object) -> bool:
    """Shape check only; nothing is sent."""
    return (
        isinstance(api_key, str)
        and _KEY_PATTERN.fullmatch(api_key) is not None
        and isinstance(model, str)
        and _MODEL_PATTERN.fullmatch(model) is not None
    )


class _RejectRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    return isinstance(exc, error.URLError) and isinstance(exc.reason, (TimeoutError, socket.timeout))


def _https_transport(url: str, headers: dict[str, str], payload: dict[str, object], timeout: float, *, allowed_url: str = OPENAI_CHAT_URL) -> object:
    """POST only to this model's endpoint: no proxy lookup or redirects."""
    if url != allowed_url:
        raise RecordingProviderError(RecordedFailureCode.PROVIDER_ERROR)
    opener = request.build_opener(request.ProxyHandler({}), _RejectRedirect())
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    http_request = request.Request(url, data=body, headers=headers, method="POST")
    # Errors are raised after the except blocks so no original exception
    # (which could carry request details) stays in ``__context__``.
    failure: RecordedFailureCode | None = None
    raw = b""
    try:
        with opener.open(http_request, timeout=timeout) as response:
            raw = response.read(MAX_OPENAI_RESPONSE_BYTES + 1)
    except error.HTTPError as exc:
        try:
            exc.close()
        except Exception:
            pass
        failure = RecordedFailureCode.PROVIDER_ERROR
    except Exception as exc:
        failure = RecordedFailureCode.TIMEOUT if _is_timeout(exc) else RecordedFailureCode.PROVIDER_ERROR
    if failure is None and len(raw) > MAX_OPENAI_RESPONSE_BYTES:
        failure = RecordedFailureCode.PROVIDER_ERROR
    if failure is not None:
        raise RecordingProviderError(failure)
    parsed: object = None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        failure = RecordedFailureCode.PROVIDER_ERROR
    if failure is not None:
        raise RecordingProviderError(failure)
    return parsed


class OpenAIQueryModel:
    """``generate_ir`` over the ChatGPT API; one call, no retries."""

    __slots__ = ("_api_key", "_model", "_endpoint", "_timeout", "_transport", "prompt_version")

    def __init__(
        self,
        api_key: str | None,
        model: str = DEFAULT_OPENAI_MODEL,
        *,
        timeout: float = OPENAI_TIMEOUT_SECONDS,
        transport: OpenAITransport = _https_transport,
        endpoint: str | None = None,
    ) -> None:
        # Phase 9-32: explicit endpoints use the custom API validation policy.
        custom_endpoint = endpoint is not None
        endpoint = OPENAI_CHAT_URL if endpoint is None else endpoint
        if not custom_endpoint:
            if not valid_openai_settings(api_key, model):
                raise ValueError("INVALID_OPENAI_SETTINGS")
        else:
            if not valid_api_model(model) or not valid_api_key(api_key):
                raise ValueError("INVALID_OPENAI_SETTINGS")
            try:
                if validate_api_base_url(endpoint.removesuffix("/chat/completions")) != endpoint:
                    raise ValueError("INVALID_API_ENDPOINT")
            except ValueError:
                raise ValueError("INVALID_API_ENDPOINT") from None
        if not isinstance(endpoint, str):
            raise ValueError("INVALID_OPENAI_SETTINGS")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 120:
            raise ValueError("INVALID_OPENAI_TIMEOUT")
        if not callable(transport):
            raise ValueError("INVALID_OPENAI_TRANSPORT")
        self._api_key = api_key or None
        self._model = model
        self._endpoint = endpoint
        self._timeout = float(timeout)
        self._transport = transport
        self.prompt_version = OPENAI_PROMPT_VERSION

    def __repr__(self) -> str:
        return _REPR

    __str__ = __repr__

    def generate_ir(
        self,
        request: HarnessRequest,
        ontology: Ontology,
        mapping: MappingProfile,
        *,
        value_hints: object = None,
    ) -> object:
        if mapping.status is not MappingStatus.PUBLISHED or mapping.database_id != request.database_id:
            raise RecordingProviderError(RecordedFailureCode.PROVIDER_ERROR)
        try:
            prompt = _prompt(request.question, ontology, mapping, self.prompt_version, value_hints)
        except ValueError:
            raise RecordingProviderError(RecordedFailureCode.PROVIDER_ERROR) from None
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
        }
        schema = queryir_schema(ontology, mapping)
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "query_ir", "schema": schema, "strict": False},
            }
        headers = {"Content-Type": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key}"
        # Every failure is raised outside its except block: no original
        # exception (which could carry the header) stays in ``__context__``
        # (Luna 9-09 review).
        failure: RecordedFailureCode | None = None
        envelope: object = None
        try:
            if self._transport is _https_transport:
                envelope = _https_transport(self._endpoint, headers, payload, self._timeout, allowed_url=self._endpoint)
            else:
                envelope = self._transport(self._endpoint, headers, payload, self._timeout)
        except RecordingProviderError as exc:
            failure = exc.failure_code
        except (TimeoutError, socket.timeout):
            failure = RecordedFailureCode.TIMEOUT
        except Exception:
            failure = RecordedFailureCode.PROVIDER_ERROR
        if failure is not None:
            raise RecordingProviderError(failure)
        content = _message_content(envelope)
        if not content.strip():
            raise RecordingProviderError(RecordedFailureCode.EMPTY_RESPONSE)
        parsed: object = content  # text that is not JSON: the chat answers "not understood"
        try:
            parsed = json.loads(
                content,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite,
                parse_float=_finite_float,
            )
        except json.JSONDecodeError:
            pass
        except ValueError:
            failure = RecordedFailureCode.PROVIDER_ERROR
        if failure is not None:
            raise RecordingProviderError(failure)
        return parsed


def _message_content(envelope: object) -> str:
    """``choices[0].message.content`` of a finished answer; anything else fails.

    Only ``finish_reason == "stop"`` is an answer: ``length`` is cut off,
    ``content_filter`` and ``tool_calls`` are not a QueryIR (Luna 9-09 review).
    """
    content: object = None
    ok = False
    try:
        choice = envelope["choices"][0]  # type: ignore[index]
        message = choice["message"]
        content = message["content"]
        ok = choice.get("finish_reason") == "stop" and not message.get("refusal")
    except Exception:
        ok = False
    if not ok or not isinstance(content, str):
        raise RecordingProviderError(RecordedFailureCode.PROVIDER_ERROR)
    return content
