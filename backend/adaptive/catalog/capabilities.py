"""DBMS capability contract for schema-adaptive boundaries.

Phase 7-02 synthetic scope only: SQLite reflects the current local
implementation; PostgreSQL reflects synthetic fake-connector verified
inspector/compiler/policy/executor support. No live PostgreSQL, Ollama,
or CNU data is used. Oracle/MySQL are explicitly unsupported and fail
closed via :func:`get_capability`.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CAPABILITY_SCHEMA_VERSION = "dbms-capability-v1"

SUPPORTED_QUERY_IR_FEATURES: tuple[str, ...] = ("select", "filters", "joins", "limit")
UNSUPPORTED_QUERY_IR_FEATURES: tuple[str, ...] = (
    "order_by",
    "group_by",
    "aggregate",
    "subquery",
    "cte",
    "write",
)

# Upper bounds mirror the current SQLite read-only execution boundary
# (SQLiteReadOnlyExecutor defaults and QueryIR limit ceiling).
MAX_ROWS_UPPER_BOUND = 200
MAX_TIMEOUT_SECONDS_UPPER_BOUND = 2.0


class DbmsDialect(str, Enum):
    SQLITE = "sqlite"
    POSTGRES = "postgres"


class CapabilityErrorCode(str, Enum):
    UNKNOWN_DIALECT = "UNKNOWN_DIALECT"


class CapabilityError(Exception):
    def __init__(self, code: CapabilityErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DbmsCapability(BaseModel):
    """Versioned frozen capability statement for one DBMS adapter."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["dbms-capability-v1"] = CAPABILITY_SCHEMA_VERSION
    adapter: Literal["sqlite", "postgres"]
    dialect: Literal["sqlite", "postgres"]
    supports_schema_inspection: bool
    supports_compiler: bool
    supports_policy: bool
    supports_executor: bool
    supported_query_ir_features: tuple[
        Literal["select", "filters", "joins", "limit"], ...
    ] = Field(min_length=4, max_length=4)
    unsupported_query_ir_features: tuple[
        Literal["order_by", "group_by", "aggregate", "subquery", "cte", "write"],
        ...,
    ] = Field(min_length=6, max_length=6)
    read_only: Literal[True] = True
    parameterized_only: Literal[True] = True
    max_rows: int = Field(ge=1, le=200)
    max_timeout_seconds: float = Field(ge=0.1, le=2.0)

    @model_validator(mode="after")
    def validate_adapter_contract(self) -> "DbmsCapability":
        if self.adapter != self.dialect:
            raise ValueError("adapter and dialect must match")
        if sorted(self.supported_query_ir_features) != sorted(
            SUPPORTED_QUERY_IR_FEATURES
        ):
            raise ValueError("supported QueryIR features must match the contract")
        if sorted(self.unsupported_query_ir_features) != sorted(
            UNSUPPORTED_QUERY_IR_FEATURES
        ):
            raise ValueError("unsupported QueryIR features must match the contract")
        return self


def sqlite_capability() -> DbmsCapability:
    return DbmsCapability(
        adapter="sqlite",
        dialect="sqlite",
        supports_schema_inspection=True,
        supports_compiler=True,
        supports_policy=True,
        supports_executor=True,
        supported_query_ir_features=SUPPORTED_QUERY_IR_FEATURES,
        unsupported_query_ir_features=UNSUPPORTED_QUERY_IR_FEATURES,
        read_only=True,
        parameterized_only=True,
        max_rows=MAX_ROWS_UPPER_BOUND,
        max_timeout_seconds=MAX_TIMEOUT_SECONDS_UPPER_BOUND,
    )


def postgres_capability() -> DbmsCapability:
    return DbmsCapability(
        adapter="postgres",
        dialect="postgres",
        supports_schema_inspection=True,
        supports_compiler=True,
        supports_policy=True,
        supports_executor=True,
        supported_query_ir_features=SUPPORTED_QUERY_IR_FEATURES,
        unsupported_query_ir_features=UNSUPPORTED_QUERY_IR_FEATURES,
        read_only=True,
        parameterized_only=True,
        max_rows=MAX_ROWS_UPPER_BOUND,
        max_timeout_seconds=MAX_TIMEOUT_SECONDS_UPPER_BOUND,
    )


def get_capability(adapter_or_dialect: str) -> DbmsCapability:
    """Return the deterministic capability for a known adapter.

    Unknown values fail closed with a fixed error and no fallback.
    """

    if adapter_or_dialect == "sqlite":
        return sqlite_capability()
    if adapter_or_dialect == "postgres":
        return postgres_capability()
    raise CapabilityError(
        CapabilityErrorCode.UNKNOWN_DIALECT,
        "Unknown database adapter",
    )
