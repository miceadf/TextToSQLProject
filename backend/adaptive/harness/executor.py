from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from .contracts import CompiledQuery, HarnessErrorCode, HarnessStage
from .errors import HarnessFailure

from typing import Protocol


class QueryExecutor(Protocol):
    def execute(
        self,
        database_id: str,
        query: CompiledQuery,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Execute compiler-owned SQL and return columns with mapping rows."""
        ...


DENIED_SQLITE_ACTIONS = {
    sqlite3.SQLITE_ALTER_TABLE,
    sqlite3.SQLITE_ANALYZE,
    sqlite3.SQLITE_ATTACH,
    sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_CREATE_TABLE,
    sqlite3.SQLITE_CREATE_TEMP_INDEX,
    sqlite3.SQLITE_CREATE_TEMP_TABLE,
    sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
    sqlite3.SQLITE_CREATE_TEMP_VIEW,
    sqlite3.SQLITE_CREATE_TRIGGER,
    sqlite3.SQLITE_CREATE_VIEW,
    sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_DETACH,
    sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_DROP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_INDEX,
    sqlite3.SQLITE_DROP_TEMP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_TRIGGER,
    sqlite3.SQLITE_DROP_TEMP_VIEW,
    sqlite3.SQLITE_DROP_TRIGGER,
    sqlite3.SQLITE_DROP_VIEW,
    sqlite3.SQLITE_INSERT,
    sqlite3.SQLITE_PRAGMA,
    sqlite3.SQLITE_REINDEX,
    sqlite3.SQLITE_TRANSACTION,
    sqlite3.SQLITE_UPDATE,
}


class SQLiteReadOnlyExecutor:
    def __init__(
        self,
        databases: dict[str, str | Path],
        *,
        timeout_seconds: float = 2.0,
        max_rows: int = 200,
    ) -> None:
        self._databases = {key: Path(value).resolve() for key, value in databases.items()}
        self._timeout_seconds = timeout_seconds
        self._max_rows = max_rows

    def execute(
        self,
        database_id: str,
        query: CompiledQuery,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        database_path = self._databases.get(database_id)
        if database_path is None or not database_path.is_file():
            raise HarnessFailure(
                HarnessErrorCode.DATABASE_NOT_FOUND,
                f"SQLite database '{database_id}' is not configured",
                stage=HarnessStage.EXECUTED,
            )

        deadline = time.monotonic() + self._timeout_seconds
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                database_path.as_uri() + "?mode=ro",
                uri=True,
                timeout=self._timeout_seconds,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            connection.set_authorizer(self._authorize)
            connection.set_progress_handler(
                lambda: 1 if time.monotonic() >= deadline else 0,
                1000,
            )

            cursor = connection.execute(query.sql, query.parameters)
            columns = [item[0] for item in cursor.description or []]
            fetched = cursor.fetchmany(self._max_rows + 1)
            if len(fetched) > self._max_rows:
                raise HarnessFailure(
                    HarnessErrorCode.RESULT_TOO_LARGE,
                    f"query returned more than {self._max_rows} rows",
                    stage=HarnessStage.EXECUTED,
                )
            return columns, [dict(row) for row in fetched]
        except HarnessFailure:
            raise
        except sqlite3.OperationalError as exc:
            message = str(exc)
            if "interrupted" in message.lower():
                raise HarnessFailure(
                    HarnessErrorCode.EXECUTION_TIMEOUT,
                    "SQLite query exceeded the execution deadline",
                    stage=HarnessStage.EXECUTED,
                ) from exc
            raise HarnessFailure(
                HarnessErrorCode.EXECUTION_ERROR,
                f"SQLite execution failed: {message}",
                stage=HarnessStage.EXECUTED,
            ) from exc
        except sqlite3.DatabaseError as exc:
            raise HarnessFailure(
                HarnessErrorCode.EXECUTION_ERROR,
                f"SQLite execution failed: {exc}",
                stage=HarnessStage.EXECUTED,
            ) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _authorize(action_code, first, second, database, trigger):
        del first, second, database, trigger
        if action_code in DENIED_SQLITE_ACTIONS:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
