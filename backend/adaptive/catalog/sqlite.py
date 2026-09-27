from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .contracts import (
    CatalogValue,
    ColumnCatalog,
    DatabaseCatalog,
    ForeignKeyCatalog,
    SQLiteAffinity,
    TableCatalog,
)
from .errors import CatalogErrorCode, SchemaInspectionError


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def sqlite_affinity(declared_type: str) -> SQLiteAffinity:
    normalized = declared_type.upper()
    if "INT" in normalized:
        return SQLiteAffinity.INTEGER
    if any(token in normalized for token in ("CHAR", "CLOB", "TEXT")):
        return SQLiteAffinity.TEXT
    if not normalized or "BLOB" in normalized:
        return SQLiteAffinity.BLOB
    if any(token in normalized for token in ("REAL", "FLOA", "DOUB")):
        return SQLiteAffinity.REAL
    return SQLiteAffinity.NUMERIC


class SQLiteAdapter:
    """Inspect configured SQLite files through a read-only connection."""

    def __init__(
        self,
        databases: dict[str, str | Path],
        *,
        sample_limit: int = 3,
        max_sample_length: int = 100,
        immutable: bool = False,
    ) -> None:
        if sample_limit < 1:
            raise ValueError("sample_limit must be at least 1")
        if max_sample_length < 1:
            raise ValueError("max_sample_length must be at least 1")
        self._databases = {key: Path(value).resolve() for key, value in databases.items()}
        self._sample_limit = sample_limit
        self._max_sample_length = max_sample_length
        self._immutable = bool(immutable)

    def inspect(
        self,
        database_id: str,
        *,
        include_samples: bool = False,
    ) -> DatabaseCatalog:
        database_path = self._databases.get(database_id)
        if database_path is None or not database_path.is_file():
            raise SchemaInspectionError(
                CatalogErrorCode.DATABASE_NOT_FOUND,
                f"SQLite database '{database_id}' is not configured",
            )

        connection: sqlite3.Connection | None = None
        try:
            suffix = "?mode=ro&immutable=1" if self._immutable else "?mode=ro"
            connection = sqlite3.connect(
                database_path.as_uri() + suffix,
                uri=True,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            tables = self._inspect_tables(connection, include_samples=include_samples)
            return DatabaseCatalog(
                database_id=database_id,
                tables=tables,
                schema_fingerprint=self._fingerprint(tables),
            )
        except SchemaInspectionError:
            raise
        except sqlite3.DatabaseError as exc:
            raise SchemaInspectionError(
                CatalogErrorCode.INSPECTION_FAILED,
                f"SQLite schema inspection failed: {exc}",
            ) from exc
        finally:
            if connection is not None:
                connection.close()

    def _inspect_tables(
        self,
        connection: sqlite3.Connection,
        *,
        include_samples: bool,
    ) -> list[TableCatalog]:
        rows = connection.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [
            self._inspect_table(connection, row["name"], include_samples=include_samples)
            for row in rows
        ]

    def _inspect_table(
        self,
        connection: sqlite3.Connection,
        table_name: str,
        *,
        include_samples: bool,
    ) -> TableCatalog:
        column_rows = connection.execute(
            f"PRAGMA table_xinfo({quote_identifier(table_name)})"
        ).fetchall()
        columns = [
            self._column_catalog(
                connection,
                table_name,
                row,
                include_samples=include_samples,
            )
            for row in column_rows
        ]
        primary_key = [
            column.name
            for column in sorted(columns, key=lambda item: item.primary_key_position)
            if column.primary_key_position > 0
        ]
        foreign_keys = self._foreign_keys(connection, table_name)
        return TableCatalog(
            name=table_name,
            columns=columns,
            primary_key=primary_key,
            foreign_keys=foreign_keys,
        )

    def _column_catalog(
        self,
        connection: sqlite3.Connection,
        table_name: str,
        row: sqlite3.Row,
        *,
        include_samples: bool,
    ) -> ColumnCatalog:
        hidden_code = int(row["hidden"])
        samples = (
            self._sample_values(connection, table_name, row["name"])
            if include_samples and hidden_code == 0
            else []
        )
        return ColumnCatalog(
            name=row["name"],
            declared_type=row["type"] or "",
            affinity=sqlite_affinity(row["type"] or ""),
            nullable=not bool(row["notnull"]),
            default_sql=row["dflt_value"],
            primary_key_position=int(row["pk"]),
            hidden=hidden_code == 1,
            generated=hidden_code in (2, 3),
            sample_values=samples,
        )

    @staticmethod
    def _foreign_keys(
        connection: sqlite3.Connection,
        table_name: str,
    ) -> list[ForeignKeyCatalog]:
        rows = connection.execute(
            f"PRAGMA foreign_key_list({quote_identifier(table_name)})"
        ).fetchall()
        return [
            ForeignKeyCatalog(
                id=int(row["id"]),
                sequence=int(row["seq"]),
                column=row["from"],
                referenced_table=row["table"],
                referenced_column=row["to"] or None,
                on_update=row["on_update"],
                on_delete=row["on_delete"],
                match=row["match"],
            )
            for row in rows
        ]

    def _sample_values(
        self,
        connection: sqlite3.Connection,
        table_name: str,
        column_name: str,
    ) -> list[CatalogValue]:
        table = quote_identifier(table_name)
        column = quote_identifier(column_name)
        rows = connection.execute(
            f"SELECT DISTINCT {column} AS sample FROM {table} "
            f"WHERE {column} IS NOT NULL LIMIT ?",
            (self._sample_limit,),
        ).fetchall()
        return [self._safe_sample(row["sample"]) for row in rows]

    def _safe_sample(self, value: Any) -> CatalogValue:
        if isinstance(value, bytes):
            return value.hex()[: self._max_sample_length]
        if isinstance(value, str):
            return value[: self._max_sample_length]
        if isinstance(value, (int, float, bool)):
            return value
        return str(value)[: self._max_sample_length]

    @staticmethod
    def _fingerprint(tables: list[TableCatalog]) -> str:
        schema_only = []
        for table in tables:
            payload = table.model_dump(mode="json")
            for column in payload["columns"]:
                column.pop("sample_values", None)
            schema_only.append(payload)
        encoded = json.dumps(
            schema_only,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
