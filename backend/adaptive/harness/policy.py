from __future__ import annotations

import re
from typing import Protocol

from .contracts import (
    CompiledQuery,
    HarnessErrorCode,
    HarnessStage,
    MappingProfile,
    QueryIR,
)
from .errors import HarnessFailure


BLOCKED_KEYWORDS = (
    "attach",
    "alter",
    "create",
    "delete",
    "detach",
    "drop",
    "insert",
    "pragma",
    "replace",
    "truncate",
    "update",
    "vacuum",
)


class SqlPolicy(Protocol):
    def validate(
        self,
        query: CompiledQuery,
        *,
        query_ir: QueryIR | None = None,
        mapping: MappingProfile | None = None,
    ) -> None:
        """Validate compiler output with optional semantic context."""
        ...


class ConservativeSqlPolicy:
    """Small dependency-free gate for compiler-owned SQL.

    This is not a general SQL parser. The executor adds a separate read-only DB
    boundary. An AST policy can replace this gate when sqlglot is available in
    the development environment.
    """

    def validate(
        self,
        query: CompiledQuery,
        *,
        query_ir: QueryIR | None = None,
        mapping: MappingProfile | None = None,
    ) -> None:
        del query_ir, mapping
        normalized = query.sql.strip()
        if not re.match(r"^select\b", normalized, flags=re.IGNORECASE):
            self._reject("only SELECT statements are allowed")
        if ";" in normalized:
            self._reject("multiple or terminated statements are not allowed")
        if not re.search(r"\blimit\s+\?\s*$", normalized, flags=re.IGNORECASE):
            self._reject("parameterized LIMIT is required")
        for keyword in BLOCKED_KEYWORDS:
            if re.search(rf"\b{re.escape(keyword)}\b", normalized, flags=re.IGNORECASE):
                self._reject(f"blocked SQL keyword: {keyword}")
        if normalized.count("?") != len(query.parameters):
            self._reject("SQL placeholder count does not match parameters")

    @staticmethod
    def _reject(message: str) -> None:
        raise HarnessFailure(
            HarnessErrorCode.UNSAFE_SQL,
            message,
            stage=HarnessStage.SQL_VALIDATED,
        )
