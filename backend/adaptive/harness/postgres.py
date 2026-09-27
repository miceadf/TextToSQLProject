"""PostgreSQL read-only QueryIR executor (Phase 7-02 synthetic scope).

Only synthetic fake-connector results have been verified; no live
PostgreSQL server, CNU data, or Ollama call is used. The executor runs
compiler-owned parameterized SQL once with tuple parameters inside a
read-only transaction. All failures use fixed codes/messages: DSN, host,
user, password, SQL, parameters, and driver exception text never appear
in errors or logs.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from typing import Any

from backend.adaptive.catalog.postgres import PostgresConnectionConfig

from .contracts import CompiledQuery, HarnessErrorCode, HarnessStage
from .errors import HarnessFailure

DATABASE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

MAX_TIMEOUT_SECONDS = 2.0
MAX_ROWS = 200
MIN_SERVER_VERSION_NUM = 120000

_FIXED_EXECUTION_FAILED = "PostgreSQL query execution failed"
_FIXED_TIMEOUT = "PostgreSQL query exceeded the execution deadline"
_FIXED_NOT_CONFIGURED = "PostgreSQL database is not configured"
_FIXED_TOO_LARGE = "PostgreSQL query returned too many rows"

_READ_ONLY_STATEMENT = "SET TRANSACTION READ ONLY"
_STATEMENT_TIMEOUT_STATEMENT = "SELECT set_config('statement_timeout', %s, false)"
_SEARCH_PATH_STATEMENT = "SELECT pg_catalog.set_config('search_path', %s, true)"

def _production_connector(config: PostgresConnectionConfig) -> Any:
    try:
        import psycopg2  # type: ignore[import-not-found]
    except Exception:
        raise HarnessFailure(
            HarnessErrorCode.EXECUTION_ERROR,
            _FIXED_EXECUTION_FAILED,
            stage=HarnessStage.EXECUTED,
        ) from None
    try:
        return psycopg2.connect(
            config._dsn,  # noqa: SLF001 - server-owned config accessor
            connect_timeout=max(1, math.ceil(config.connect_timeout_seconds)),
            options=(
                "-c statement_timeout="
                f"{config.statement_timeout_ms}ms"
                " -c default_transaction_read_only=on"
            ),
        )
    except Exception:
        raise HarnessFailure(
            HarnessErrorCode.EXECUTION_ERROR,
            _FIXED_EXECUTION_FAILED,
            stage=HarnessStage.EXECUTED,
        ) from None


def _is_timeout_error(exc: BaseException) -> bool:
    for attr in ("pgcode", "sqlstate", "code"):
        value = getattr(exc, attr, None)
        if value is not None and str(value) == "57014":
            return True
    diag = getattr(exc, "diag", None)
    if diag is not None:
        sqlstate = getattr(diag, "sqlstate", None)
        if sqlstate is not None and str(sqlstate) == "57014":
            return True
    return False


class _ExecutorFailure(HarnessFailure):
    """Marker for failures created and redacted inside this executor."""


def _failure(code: HarnessErrorCode, message: str) -> _ExecutorFailure:
    return _ExecutorFailure(code, message, stage=HarnessStage.EXECUTED)


class PostgresReadOnlyExecutor:
    """Execute compiler-owned PostgreSQL SQL through an injected connector."""

    def __init__(
        self,
        configs: dict[str, PostgresConnectionConfig],
        *,
        connector: Callable[[PostgresConnectionConfig], Any] | None = None,
        timeout_seconds: float = MAX_TIMEOUT_SECONDS,
        max_rows: int = MAX_ROWS,
    ) -> None:
        if not isinstance(configs, dict) or not configs:
            raise ValueError("postgres configs must be a non-empty mapping")
        validated: dict[str, PostgresConnectionConfig] = {}
        for database_id, config in configs.items():
            if (
                not isinstance(database_id, str)
                or DATABASE_ID_PATTERN.fullmatch(database_id) is None
            ):
                raise ValueError("postgres database ID is invalid")
            if not isinstance(config, PostgresConnectionConfig):
                raise ValueError("postgres config is invalid")
            validated[database_id] = config
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS
        ):
            raise ValueError("timeout exceeds the allowed upper bound")
        if (
            not isinstance(max_rows, int)
            or isinstance(max_rows, bool)
            or not 1 <= max_rows <= MAX_ROWS
        ):
            raise ValueError("max rows exceeds the allowed upper bound")
        self._configs = validated
        self._connector = connector or _production_connector
        self._timeout_seconds = float(timeout_seconds)
        self._max_rows = int(max_rows)

    def execute(
        self,
        database_id: str,
        query: CompiledQuery,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        config = self._configs.get(database_id) if isinstance(database_id, str) else None
        if config is None:
            raise _failure(
                HarnessErrorCode.DATABASE_NOT_FOUND,
                _FIXED_NOT_CONFIGURED,
            )
        if not isinstance(query, CompiledQuery):
            raise _failure(
                HarnessErrorCode.EXECUTION_ERROR,
                _FIXED_EXECUTION_FAILED,
            )

        try:
            connection = self._connector(config)
        except Exception:
            raise _failure(
                HarnessErrorCode.EXECUTION_ERROR,
                _FIXED_EXECUTION_FAILED,
            ) from None

        cursor: Any | None = None
        try:
            server_version = getattr(connection, "server_version", None)
            if (
                not isinstance(server_version, int)
                or isinstance(server_version, bool)
                or server_version < MIN_SERVER_VERSION_NUM
            ):
                raise _failure(
                    HarnessErrorCode.EXECUTION_ERROR,
                    _FIXED_EXECUTION_FAILED,
                )
            try:
                cursor = connection.cursor()
            except Exception:
                raise _failure(
                    HarnessErrorCode.EXECUTION_ERROR,
                    _FIXED_EXECUTION_FAILED,
                ) from None
            try:
                effective_timeout_ms = min(
                    config.statement_timeout_ms,
                    max(1, math.floor(self._timeout_seconds * 1000)),
                )
                cursor.execute(_READ_ONLY_STATEMENT, ())
                cursor.execute(
                    _STATEMENT_TIMEOUT_STATEMENT,
                    (f"{effective_timeout_ms}ms",),
                )
                # Pin unqualified compiler-owned table names to the inspected schema.
                # set_config takes the schema as a value, so no identifier interpolation
                # or DSN option can alter the transaction-local search path.
                cursor.execute(_SEARCH_PATH_STATEMENT, (config.schema,))
                cursor.execute(query.sql, tuple(query.parameters))
            except Exception as exc:
                if _is_timeout_error(exc):
                    raise _failure(
                        HarnessErrorCode.EXECUTION_TIMEOUT,
                        _FIXED_TIMEOUT,
                    ) from None
                raise _failure(
                    HarnessErrorCode.EXECUTION_ERROR,
                    _FIXED_EXECUTION_FAILED,
                ) from None

            try:
                description = cursor.description or []
                columns = [str(item[0]) for item in description]
                if len(columns) != len(set(columns)):
                    raise ValueError("duplicate result columns")
                fetched = cursor.fetchmany(self._max_rows + 1)
            except Exception as exc:
                if _is_timeout_error(exc):
                    raise _failure(
                        HarnessErrorCode.EXECUTION_TIMEOUT,
                        _FIXED_TIMEOUT,
                    ) from None
                raise _failure(
                    HarnessErrorCode.EXECUTION_ERROR,
                    _FIXED_EXECUTION_FAILED,
                ) from None

            if len(fetched) > self._max_rows:
                raise _failure(
                    HarnessErrorCode.RESULT_TOO_LARGE,
                    _FIXED_TOO_LARGE,
                )
            try:
                return columns, [self._to_row(columns, row) for row in fetched]
            except Exception:
                raise _failure(
                    HarnessErrorCode.EXECUTION_ERROR,
                    _FIXED_EXECUTION_FAILED,
                ) from None
        except _ExecutorFailure:
            raise
        except Exception:
            raise _failure(
                HarnessErrorCode.EXECUTION_ERROR,
                _FIXED_EXECUTION_FAILED,
            ) from None
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass
            try:
                rollback = getattr(connection, "rollback", None)
                if callable(rollback):
                    rollback()
            except Exception:
                pass
            try:
                connection.close()
            except Exception:
                pass

    @staticmethod
    def _to_row(columns: list[str], row: Any) -> dict[str, Any]:
        if isinstance(row, Mapping):
            return {column: row[column] for column in columns}
        values = list(row)
        if len(values) != len(columns):
            raise ValueError("result row width does not match columns")
        return dict(zip(columns, values))
