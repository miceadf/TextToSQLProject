"""Compatibility re-export; the request formats live in the runtime harness."""

from ..harness.ollama_request_format import (  # noqa: F401
    ALPACA_NO_SYSTEM_PREFIX,
    ALPACA_NO_SYSTEM_SHA256,
    ALPACA_NO_SYSTEM_SUFFIX,
    REQUEST_FORMAT_MODEL_TEMPLATE,
    REQUEST_FORMAT_NO_SYSTEM,
    REQUEST_FORMATS,
    RequestFormatError,
    apply_request_format,
    request_format_label,
)
