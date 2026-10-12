"""Server-owned Ollama startup configuration for the loopback prototype.

Parses the three explicit CLI values (model tag, loopback base URL, timeout)
into an immutable config and assembles the existing ``OllamaQueryModel`` with
a loopback-only transport. Nothing here performs network, file, or
environment access at import, parse, or build time; the transport only
connects when the clarification flow calls the model.

The transport exists because the default ``urllib.request.urlopen`` path
honours system/environment proxies and follows POST redirects to arbitrary
hosts. Prompt construction, envelope checks, and QueryIR JSON parsing stay in
``OllamaQueryModel``; this is not a second adapter.

Every failure uses a fixed code and message and never keeps the input,
URL, tag, timeout, or underlying exception text.
"""

from __future__ import annotations

import json
import math
import re
import socket
from dataclasses import dataclass
from urllib import error, request

from ..harness.ollama import (
    OUTPUT_FORMAT_NONE,
    OUTPUT_FORMATS,
    PROMPT_VERSIONS,
    OllamaQueryModel,
    _finite_float,
    _reject_duplicate_keys,
    _reject_non_finite,
)
from ..harness.ollama_request_format import (
    REQUEST_FORMAT_MODEL_TEMPLATE,
    REQUEST_FORMATS,
)
from ..harness.recording_bundle_contracts import SAFE_LABEL_PATTERN


OLLAMA_STARTUP_CONFIG_VERSION = "ollama-startup-config-v2"
OLLAMA_STARTUP_PROMPT_VERSION = "query-ir-v1"
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_TIMEOUT = "30"
MIN_OLLAMA_TIMEOUT_SECONDS = 1.0
MAX_OLLAMA_TIMEOUT_SECONDS = 120.0
MAX_OLLAMA_RESPONSE_BYTES = 1024 * 1024

_GENERATE_PATH = "/api/generate"
_BASE_URL_PATTERN = re.compile(r"http://(?:127\.0\.0\.1|\[::1\]):([1-9][0-9]{0,4})")
_TIMEOUT_PATTERN = re.compile(r"[1-9][0-9]{0,2}(?:\.[0-9]{1,3})?")
_MODEL_TAG_PATTERN = re.compile(SAFE_LABEL_PATTERN)

_CONFIG_REPR = f"OllamaStartupConfig(version='{OLLAMA_STARTUP_CONFIG_VERSION}')"
_TRANSPORT_REPR = f"OllamaLoopbackTransport(version='{OLLAMA_STARTUP_CONFIG_VERSION}')"


class OllamaStartupConfigError(Exception):
    """Invalid startup configuration; fixed code and message only."""

    code = "OLLAMA_STARTUP_CONFIG_INVALID"

    def __init__(self) -> None:
        super().__init__(self.code)


class OllamaLoopbackTransportError(Exception):
    """Loopback transport failure; fixed code and message only."""

    code = "OLLAMA_LOOPBACK_TRANSPORT_ERROR"

    def __init__(self) -> None:
        super().__init__(self.code)


@dataclass(frozen=True, repr=False)
class OllamaStartupConfig:
    """Validated server-owned Ollama settings. Never printed with values."""

    base_url: str
    model_tag: str
    timeout_seconds: float
    request_format: str = REQUEST_FORMAT_MODEL_TEMPLATE
    # Phase 9-08: ``queryir-schema-v1`` for a general model (ADR-098).
    output_format: str = OUTPUT_FORMAT_NONE
    prompt_version: str = OLLAMA_STARTUP_PROMPT_VERSION

    def __repr__(self) -> str:
        return _CONFIG_REPR

    def __str__(self) -> str:
        return _CONFIG_REPR


def _valid_base_url(value: object) -> bool:
    if type(value) is not str:
        return False
    match = _BASE_URL_PATTERN.fullmatch(value)
    return match is not None and 1 <= int(match.group(1)) <= 65535


def _valid_model_tag(value: object) -> bool:
    return (
        type(value) is str
        and value.isascii()
        and _MODEL_TAG_PATTERN.fullmatch(value) is not None
    )


def _valid_timeout_seconds(value: object) -> bool:
    return (
        type(value) is float
        and math.isfinite(value)
        and MIN_OLLAMA_TIMEOUT_SECONDS <= value <= MAX_OLLAMA_TIMEOUT_SECONDS
    )


def _parse_timeout(value: object) -> float:
    if type(value) is not str or _TIMEOUT_PATTERN.fullmatch(value) is None:
        raise OllamaStartupConfigError()
    seconds = float(value)
    if not _valid_timeout_seconds(seconds):
        raise OllamaStartupConfigError()
    return seconds


def parse_ollama_startup_config(
    model_tag: object,
    base_url: object = None,
    timeout: object = None,
    request_format: object = None,
    output_format: object = None,
    prompt_version: object = None,
) -> OllamaStartupConfig:
    """Build a config from raw CLI strings; ``None`` selects the default.

    ``model_tag`` has no default: it is the explicit activation switch.
    ``request_format`` defaults to ``model-template`` for existing callers;
    runtime CLIs require it explicitly (ADR-059).
    """
    try:
        url = DEFAULT_OLLAMA_BASE_URL if base_url is None else base_url
        raw_timeout = DEFAULT_OLLAMA_TIMEOUT if timeout is None else timeout
        fmt = REQUEST_FORMAT_MODEL_TEMPLATE if request_format is None else request_format
        output = OUTPUT_FORMAT_NONE if output_format is None else output_format
        prompt = OLLAMA_STARTUP_PROMPT_VERSION if prompt_version is None else prompt_version
        if (
            not _valid_model_tag(model_tag)
            or not _valid_base_url(url)
            or not _valid_request_format(fmt)
            or not _valid_output_format(output)
            or not _valid_prompt_version(prompt)
        ):
            raise OllamaStartupConfigError()
        return OllamaStartupConfig(
            base_url=url,  # type: ignore[arg-type]
            model_tag=model_tag,  # type: ignore[arg-type]
            timeout_seconds=_parse_timeout(raw_timeout),
            request_format=fmt,  # type: ignore[arg-type]
            output_format=output,  # type: ignore[arg-type]
            prompt_version=prompt,  # type: ignore[arg-type]
        )
    except OllamaStartupConfigError:
        raise OllamaStartupConfigError() from None
    except Exception:
        raise OllamaStartupConfigError() from None


def _valid_request_format(value: object) -> bool:
    return type(value) is str and value in REQUEST_FORMATS


def _valid_output_format(value: object) -> bool:
    return type(value) is str and value in OUTPUT_FORMATS


def _valid_prompt_version(value: object) -> bool:
    return type(value) is str and value in PROMPT_VERSIONS


def _validate_config(config: object) -> OllamaStartupConfig:
    if (
        type(config) is not OllamaStartupConfig
        or not _valid_base_url(config.base_url)
        or not _valid_model_tag(config.model_tag)
        or not _valid_timeout_seconds(config.timeout_seconds)
        or not _valid_request_format(config.request_format)
        or not _valid_output_format(config.output_format)
        or not _valid_prompt_version(config.prompt_version)
    ):
        raise OllamaStartupConfigError()
    return config


class _RejectRedirectHandler(request.HTTPRedirectHandler):
    """Never follow a redirect; the 3xx then surfaces as an HTTPError."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    return isinstance(exc, error.URLError) and isinstance(
        exc.reason, (TimeoutError, socket.timeout)
    )


class _LoopbackTransport:
    """``OllamaTransport`` bound to one validated loopback endpoint."""

    __slots__ = ("_url", "_opener")

    def __init__(self, config: OllamaStartupConfig) -> None:
        self._url = config.base_url + _GENERATE_PATH
        # Explicit empty ProxyHandler: no proxy lookup from environment or registry.
        self._opener = request.build_opener(
            request.ProxyHandler({}), _RejectRedirectHandler()
        )

    def __repr__(self) -> str:
        return _TRANSPORT_REPR

    def __str__(self) -> str:
        return _TRANSPORT_REPR

    def __call__(self, url: str, payload: dict[str, object], timeout: float) -> object:
        if (
            type(url) is not str
            or url != self._url
            or not isinstance(payload, dict)
            or not _valid_timeout_seconds(timeout)
        ):
            raise OllamaLoopbackTransportError()
        try:
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except Exception:
            raise OllamaLoopbackTransportError() from None
        http_request = request.Request(
            self._url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        timed_out = False
        failed = False
        raw = b""
        try:
            with self._opener.open(http_request, timeout=timeout) as response:
                status = getattr(response, "status", None)
                if type(status) is not int or not 200 <= status <= 299:
                    failed = True
                else:
                    raw = response.read(MAX_OLLAMA_RESPONSE_BYTES + 1)
        except error.HTTPError as exc:
            try:
                exc.close()
            except Exception:
                pass
            failed = True
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise
            timed_out = _is_timeout(exc)
            failed = True
        if timed_out:
            raise TimeoutError() from None
        if failed or len(raw) > MAX_OLLAMA_RESPONSE_BYTES:
            raise OllamaLoopbackTransportError() from None
        try:
            return json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite,
                parse_float=_finite_float,
            )
        except Exception:
            raise OllamaLoopbackTransportError() from None


def build_loopback_transport(config: OllamaStartupConfig) -> _LoopbackTransport:
    """Create the loopback-only transport; performs no network access."""
    return _LoopbackTransport(_validate_config(config))


def build_startup_ollama_model(
    config: OllamaStartupConfig, *, transport: object = None
) -> OllamaQueryModel:
    """Assemble the existing adapter without calling it."""
    try:
        valid = _validate_config(config)
        bound = build_loopback_transport(valid) if transport is None else transport
        return OllamaQueryModel(
            valid.base_url,
            valid.model_tag,
            valid.timeout_seconds,
            transport=bound,  # type: ignore[arg-type]
            prompt_version=valid.prompt_version,
            request_format=valid.request_format,
            output_format=valid.output_format,
        )
    except Exception:
        raise OllamaStartupConfigError() from None
