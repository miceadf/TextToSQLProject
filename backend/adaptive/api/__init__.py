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
    "SYNTHETIC_CLARIFICATION_PROTOTYPE_VERSION",
    "SYNTHETIC_PROTOTYPE_DATABASE_ID",
    "ClarificationHttpConfigError",
    "ClarificationHttpErrorCode",
    "create_clarification_http_app",
    "create_synthetic_clarification_prototype",
]

_LAZY_SYNTHETIC_NAMES = frozenset(
    {
        "SYNTHETIC_CLARIFICATION_PROTOTYPE_VERSION",
        "SYNTHETIC_PROTOTYPE_DATABASE_ID",
        "create_synthetic_clarification_prototype",
    }
)


def __getattr__(name: str):
    if name in _LAZY_SYNTHETIC_NAMES:
        from . import synthetic_prototype as _prototype

        return getattr(_prototype, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
