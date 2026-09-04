from __future__ import annotations

from enum import Enum


class CatalogErrorCode(str, Enum):
    DATABASE_NOT_FOUND = "DATABASE_NOT_FOUND"
    INSPECTION_FAILED = "INSPECTION_FAILED"


class SchemaInspectionError(Exception):
    def __init__(self, code: CatalogErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
