"""Fixed error contract for the private SQLite registration boundary."""

from __future__ import annotations

from enum import Enum


class RegistrationErrorCode(str, Enum):
    INVALID_INPUT = "INVALID_INPUT"
    UNSAFE_SOURCE = "UNSAFE_SOURCE"
    SOURCE_TOO_LARGE = "SOURCE_TOO_LARGE"
    INVALID_SQLITE = "INVALID_SQLITE"
    PRIVATE_STORAGE_INVALID = "PRIVATE_STORAGE_INVALID"
    REGISTRATION_CONFLICT = "REGISTRATION_CONFLICT"
    ARTIFACT_MISMATCH = "ARTIFACT_MISMATCH"
    STORAGE_ERROR = "STORAGE_ERROR"
    INSPECTION_FAILED = "INSPECTION_FAILED"
    MAPPING_FAILED = "MAPPING_FAILED"


class RegistrationError(ValueError):
    """Registration failure carrying only a fixed enum code.

    The string form is the code itself: no source paths, row values,
    sample values, or SQLite/internal exception payloads are attached.
    """

    def __init__(self, code: RegistrationErrorCode) -> None:
        self.code = code
        super().__init__(code.value)
