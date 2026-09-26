"""Adaptive API package (synthetic only, no legacy mount)."""

from .clarification_http import (
    CLARIFICATION_HTTP_VERSION,
    START_PATH,
    SUBMIT_PATH,
    ClarificationHttpConfigError,
    ClarificationHttpErrorCode,
    create_clarification_http_app,
)

__all__ = [
    "CLARIFICATION_HTTP_VERSION",
    "START_PATH",
    "SUBMIT_PATH",
    "ClarificationHttpConfigError",
    "ClarificationHttpErrorCode",
    "create_clarification_http_app",
]
