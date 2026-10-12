"""Bounded distinct text values of one mapped PostgreSQL column (Phase 8-13).

A server-internal read used only to build chat value hints. It is not part
of the QueryIR execution path: the SQL has one fixed shape, the identifiers
come from a published mapping that startup already matched against the
catalog, and the connection is read-only with the configured statement
timeout. Failures return ``None`` and never carry details. Identifiers are
quoted here, so importing this module never imports the database driver.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..catalog.postgres import PostgresConnectionConfig
from .postgres import _production_connector

MAX_VALUE_LENGTH = 200


def _quote(identifier: str) -> str:
    """PostgreSQL quoted identifier (the driver is imported only to connect)."""
    if not isinstance(identifier, str) or not identifier or "\x00" in identifier:
        raise ValueError("invalid identifier")
    return '"' + identifier.replace('"', '""') + '"'


class _OverLimit:
    def __repr__(self) -> str:
        return "OVER_LIMIT"


# The column has more distinct values than asked for: a stable answer, unlike
# ``None`` (unavailable now; worth asking again later).
OVER_LIMIT: Any = _OverLimit()


class PostgresDistinctValueReader:
    """``read(database_id, table, column, limit)`` → sorted values, ``OVER_LIMIT``, or ``None``.

    ``None`` means the values could not be read (retry later).
    """

    def __init__(
        self,
        configs: dict[str, PostgresConnectionConfig],
        *,
        connector: Callable[[PostgresConnectionConfig], Any] | None = None,
    ) -> None:
        if not isinstance(configs, dict) or not all(
            isinstance(config, PostgresConnectionConfig) for config in configs.values()
        ):
            raise ValueError("invalid reader configuration")
        self._configs = dict(configs)
        self._connector = connector or _production_connector

    def register(self, database_id: str, config: PostgresConnectionConfig) -> None:
        """Phase 8-15: a database published while the server runs."""
        if not isinstance(database_id, str) or not isinstance(config, PostgresConnectionConfig):
            raise ValueError("invalid reader configuration")
        self._configs = {**self._configs, database_id: config}

    def unregister(self, database_id: str) -> None:
        """Forget the connection of a deleted PostgreSQL registration."""
        self._configs = {key: value for key, value in self._configs.items() if key != database_id}

    def present(self, database_id: str, table: str, column: str, values: list[str]) -> set[str] | None:
        """Phase 8-07: which of these exact values the column holds (``None`` = unavailable)."""
        config = self._configs.get(database_id)
        if config is None or not isinstance(values, list) or not 1 <= len(values) <= 64:
            return None
        if not all(isinstance(value, str) for value in values):
            return None
        connection = None
        try:
            quoted = _quote(column)
            query = (
                # Phase 8-21: as text, so integer period columns compare too.
                f"SELECT DISTINCT CAST({quoted} AS text) FROM {_quote(config.schema)}.{_quote(table)} "
                f"WHERE CAST({quoted} AS text) = ANY(%s)"
            )
            connection = self._connector(config)
            set_encoding = getattr(connection, "set_client_encoding", None)
            if callable(set_encoding):
                set_encoding("UTF8")
            cursor = connection.cursor()
            cursor.execute(query, (list(values),))
            rows = cursor.fetchall()
        except Exception:
            return None
        finally:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
                try:
                    connection.close()
                except Exception:
                    pass
        return {row[0] for row in rows if isinstance(row, (tuple, list)) and len(row) == 1 and isinstance(row[0], str)}

    def pairs(
        self,
        database_id: str,
        table: str,
        code_column: str,
        name_column: str,
        group: tuple[str, str] | None,
        limit: int,
    ) -> Any:
        """Phase 8-24: distinct (code, name) rows of a code table as text.

        ``group`` (column, value) keeps one kind of a common-code table.
        Returns a list, ``OVER_LIMIT`` or ``None`` (unavailable).
        """
        config = self._configs.get(database_id)
        if config is None or not isinstance(limit, int) or not 1 <= limit <= 5000:
            return None
        connection = None
        try:
            code, name = _quote(code_column), _quote(name_column)
            query = (
                f"SELECT DISTINCT CAST({code} AS text), CAST({name} AS text) "
                f"FROM {_quote(config.schema)}.{_quote(table)} WHERE {code} IS NOT NULL AND {name} IS NOT NULL"
            )
            params: list[Any] = []
            if group is not None:
                query += f" AND CAST({_quote(group[0])} AS text) = %s"
                params.append(group[1])
            query += " LIMIT %s"
            params.append(limit + 1)
            connection = self._connector(config)
            set_encoding = getattr(connection, "set_client_encoding", None)
            if callable(set_encoding):
                set_encoding("UTF8")
            cursor = connection.cursor()
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
        except Exception:
            return None
        finally:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
                try:
                    connection.close()
                except Exception:
                    pass
        if len(rows) > limit:
            return OVER_LIMIT
        return sorted(
            (row[0], row[1])
            for row in rows
            if isinstance(row, (tuple, list))
            and len(row) == 2
            and all(isinstance(item, str) and 0 < len(item) <= MAX_VALUE_LENGTH for item in row)
        )

    def read(self, database_id: str, table: str, column: str, limit: int, *, sample: bool = False, ordered: bool = False) -> Any:
        """``sample`` (Phase 8-21): up to ``limit`` values even when the column
        holds more, for format checks that only need examples."""
        config = self._configs.get(database_id)
        if config is None or not isinstance(limit, int) or not 1 <= limit <= 5000:
            return None
        connection = None
        try:
            quoted = _quote(column)
            value_sql = f"CAST({quoted} AS text)" + (' COLLATE "C"' if ordered else '')
            query = (
                f"SELECT DISTINCT {value_sql} FROM {_quote(config.schema)}.{_quote(table)} "
                f"WHERE {quoted} IS NOT NULL"
            )
            # Evaluation sampling needs a stable subset before LIMIT. The
            # existing runtime path keeps its original query by default.
            query += (" ORDER BY 1" if ordered else "") + " LIMIT %s"
            connection = self._connector(config)
            set_encoding = getattr(connection, "set_client_encoding", None)
            if callable(set_encoding):
                set_encoding("UTF8")
            cursor = connection.cursor()
            cursor.execute(query, (limit + 1,))
            rows = cursor.fetchall()
        except Exception:
            return None
        finally:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
                try:
                    connection.close()
                except Exception:
                    pass
        if len(rows) > limit:
            if not sample:
                return OVER_LIMIT
            rows = rows[:limit]
        values = {
            row[0]
            for row in rows
            if isinstance(row, (tuple, list))
            and len(row) == 1
            and isinstance(row[0], str)
            and 0 < len(row[0]) <= MAX_VALUE_LENGTH
        }
        return sorted(values)
