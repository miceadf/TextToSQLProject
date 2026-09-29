"""PostgreSQL read-only schema inspector (Phase 7-01 synthetic scope).

Only synthetic fake-connector results have been verified; no live
PostgreSQL server, CNU data, or Ollama call is used. The adapter inspects
a single allowed schema (default ``public``) through parameterized
``pg_catalog`` queries inside a read-only transaction. Sample collection
is disabled and ``include_samples=True`` is rejected before any query.
DSN values never appear in repr/str/errors/catalog/capability/logs.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from typing import Any

from .contracts import (
    ColumnCatalog,
    DatabaseCatalog,
    ForeignKeyCatalog,
    SQLiteAffinity,
    TableCatalog,
)
from .errors import CatalogErrorCode, SchemaInspectionError

DEFAULT_SCHEMA = "public"
DATABASE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SCHEMA_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_$]{0,62}$")

MAX_CONNECT_TIMEOUT_SECONDS = 5.0
MAX_STATEMENT_TIMEOUT_MS = 2000
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_STATEMENT_TIMEOUT_MS = 2000
MIN_SERVER_VERSION_NUM = 120000

_FIXED_FAILURE_MESSAGE = "PostgreSQL schema inspection failed"
_FIXED_NOT_CONFIGURED_MESSAGE = "PostgreSQL database is not configured"
_FIXED_SAMPLES_DISABLED_MESSAGE = "PostgreSQL sample collection is disabled"

READ_ONLY_STATEMENT = "SET TRANSACTION READ ONLY"
STATEMENT_TIMEOUT_STATEMENT = "SELECT set_config('statement_timeout', %s, false)"

PG_TABLES_QUERY = (
    "SELECT c.relname AS table_name "
    "FROM pg_catalog.pg_class c "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = %s AND c.relkind IN ('r', 'p') "
    "ORDER BY c.relname"
)

# Opt-in (Phase 7-12): also inspect plain views. The default query above is
# unchanged so existing fingerprints stay identical.
PG_TABLES_WITH_VIEWS_QUERY = (
    "SELECT c.relname AS table_name "
    "FROM pg_catalog.pg_class c "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = %s AND c.relkind IN ('r', 'p', 'v') "
    "ORDER BY c.relname"
)

PG_COLUMNS_QUERY = (
    "SELECT a.attname AS column_name, "
    "pg_catalog.format_type(a.atttypid, a.atttypmod) AS declared_type, "
    "NOT a.attnotnull AS nullable, "
    "pg_catalog.pg_get_expr(d.adbin, d.adrelid) AS default_sql, "
    "a.attgenerated AS generated_kind, "
    "a.attidentity AS identity_kind "
    "FROM pg_catalog.pg_attribute a "
    "JOIN pg_catalog.pg_class c ON c.oid = a.attrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum "
    "WHERE n.nspname = %s AND c.relname = %s "
    "AND a.attnum > 0 AND NOT a.attisdropped "
    "ORDER BY a.attnum"
)

PG_PK_QUERY = (
    "SELECT a.attname AS column_name, u.ord AS position "
    "FROM pg_catalog.pg_constraint co "
    "JOIN pg_catalog.pg_class c ON c.oid = co.conrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "JOIN LATERAL pg_catalog.unnest(co.conkey) WITH ORDINALITY AS u(attnum, ord) ON true "
    "JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attnum = u.attnum "
    "WHERE n.nspname = %s AND c.relname = %s AND co.contype = 'p' "
    "ORDER BY u.ord"
)

PG_FK_QUERY = (
    "SELECT co.conname AS constraint_name, a.attname AS column_name, "
    "rc.relname AS referenced_table, ra.attname AS referenced_column, "
    "co.confupdtype AS update_action, co.confdeltype AS delete_action, "
    "co.confmatchtype AS match_type, u.ord AS position "
    "FROM pg_catalog.pg_constraint co "
    "JOIN pg_catalog.pg_class c ON c.oid = co.conrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "JOIN LATERAL pg_catalog.generate_subscripts(co.conkey, 1) "
    "WITH ORDINALITY AS u(idx, ord) ON true "
    "JOIN pg_catalog.pg_attribute a "
    "ON a.attrelid = c.oid AND a.attnum = co.conkey[u.idx] "
    "JOIN pg_catalog.pg_class rc ON rc.oid = co.confrelid "
    "JOIN pg_catalog.pg_namespace rn ON rn.oid = rc.relnamespace "
    "JOIN pg_catalog.pg_attribute ra "
    "ON ra.attrelid = co.confrelid AND ra.attnum = co.confkey[u.idx] "
    "WHERE n.nspname = %s AND c.relname = %s AND rn.nspname = %s "
    "AND co.contype = 'f' "
    "ORDER BY co.conname, u.ord"
)

_FK_ACTION_LABELS = {
    "a": "NO ACTION",
    "r": "RESTRICT",
    "c": "CASCADE",
    "n": "SET NULL",
    "d": "SET DEFAULT",
}
_FK_MATCH_LABELS = {
    "f": "FULL",
    "p": "PARTIAL",
    "s": "SIMPLE",
}


def postgres_affinity(declared_type: str) -> SQLiteAffinity:
    """Map a PostgreSQL declared type to the existing logical affinity.

    Conservative on purpose so the existing rule-based aligner keeps
    working without changing SQLite public API or affinity serialization.
    """

    normalized = (declared_type or "").upper()
    if "BOOL" in normalized:
        return SQLiteAffinity.INTEGER
    if any(token in normalized for token in ("CHAR", "CLOB", "TEXT")):
        return SQLiteAffinity.TEXT
    if "BYTEA" in normalized or "BLOB" in normalized:
        return SQLiteAffinity.BLOB
    if any(token in normalized for token in ("REAL", "FLOA", "DOUB")):
        return SQLiteAffinity.REAL
    if any(token in normalized for token in ("NUMERIC", "DECIMAL", "MONEY")):
        return SQLiteAffinity.NUMERIC
    if any(
        token in normalized
        for token in (
            "DATE",
            "TIME",
            "INTERVAL",
            "UUID",
            "JSON",
            "XML",
            "INET",
            "CIDR",
            "MACADDR",
            "POINT",
            "LINE",
            "LSEG",
            "BOX",
            "PATH",
            "POLYGON",
            "CIRCLE",
            "TSVECTOR",
            "TSQUERY",
            "ENUM",
            "RANGE",
        )
    ):
        return SQLiteAffinity.TEXT
    if "INT" in normalized or "SERIAL" in normalized or "IDENTITY" in normalized:
        return SQLiteAffinity.INTEGER
    if not normalized:
        return SQLiteAffinity.BLOB
    return SQLiteAffinity.NUMERIC


class PostgresConnectionConfig:
    """Server-owned connection config for one database ID.

    The DSN is stored privately and never rendered in repr/str/errors.
    """

    __slots__ = (
        "_dsn",
        "_schema",
        "_connect_timeout",
        "_statement_timeout_ms",
        "_include_views",
    )

    def __init__(
        self,
        dsn: str,
        *,
        schema: str = DEFAULT_SCHEMA,
        connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
        include_views: bool = False,
    ) -> None:
        if not isinstance(include_views, bool):
            raise ValueError("include_views must be a boolean")
        if not isinstance(dsn, str) or not 1 <= len(dsn) <= 2000:
            raise ValueError("postgres DSN must be a non-empty string")
        if not isinstance(schema, str) or SCHEMA_PATTERN.fullmatch(schema) is None:
            raise ValueError("postgres schema name is invalid")
        if (
            not isinstance(connect_timeout_seconds, (int, float))
            or isinstance(connect_timeout_seconds, bool)
            or not 0 < connect_timeout_seconds <= MAX_CONNECT_TIMEOUT_SECONDS
        ):
            raise ValueError("connect timeout exceeds the allowed upper bound")
        if (
            not isinstance(statement_timeout_ms, int)
            or isinstance(statement_timeout_ms, bool)
            or not 0 < statement_timeout_ms <= MAX_STATEMENT_TIMEOUT_MS
        ):
            raise ValueError("statement timeout exceeds the allowed upper bound")
        self._dsn = dsn
        self._schema = schema
        self._connect_timeout = float(connect_timeout_seconds)
        self._statement_timeout_ms = int(statement_timeout_ms)
        self._include_views = include_views

    @property
    def schema(self) -> str:
        return self._schema

    @property
    def connect_timeout_seconds(self) -> float:
        return self._connect_timeout

    @property
    def statement_timeout_ms(self) -> int:
        return self._statement_timeout_ms

    @property
    def include_views(self) -> bool:
        return self._include_views

    def _redacted(self) -> str:
        return (
            "PostgresConnectionConfig(schema='"
            + self._schema
            + "', connect_timeout_seconds="
            + repr(self._connect_timeout)
            + ", statement_timeout_ms="
            + repr(self._statement_timeout_ms)
            + ", dsn='***')"
        )

    def __repr__(self) -> str:
        return self._redacted()

    def __str__(self) -> str:
        return self._redacted()


def _production_connector(config: PostgresConnectionConfig) -> Any:
    try:
        import psycopg2  # type: ignore[import-not-found]
    except Exception:
        raise SchemaInspectionError(
            CatalogErrorCode.INSPECTION_FAILED, _FIXED_FAILURE_MESSAGE
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
        raise SchemaInspectionError(
            CatalogErrorCode.INSPECTION_FAILED, _FIXED_FAILURE_MESSAGE
        ) from None


def _fail() -> SchemaInspectionError:
    return SchemaInspectionError(
        CatalogErrorCode.INSPECTION_FAILED, _FIXED_FAILURE_MESSAGE
    )


def _field(row: Any, key: str, index: int) -> Any:
    if isinstance(row, Mapping):
        return row[key]
    return row[index]


class PostgresAdapter:
    """Inspect one PostgreSQL schema through a read-only transaction."""

    def __init__(
        self,
        configs: dict[str, PostgresConnectionConfig],
        *,
        connector: Callable[[PostgresConnectionConfig], Any] | None = None,
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
        self._configs = validated
        self._connector = connector or _production_connector

    def inspect(
        self,
        database_id: str,
        *,
        include_samples: bool = False,
    ) -> DatabaseCatalog:
        config = self._configs.get(database_id) if isinstance(database_id, str) else None
        if config is None:
            raise SchemaInspectionError(
                CatalogErrorCode.DATABASE_NOT_FOUND,
                _FIXED_NOT_CONFIGURED_MESSAGE,
            )
        if include_samples:
            raise SchemaInspectionError(
                CatalogErrorCode.INSPECTION_FAILED,
                _FIXED_SAMPLES_DISABLED_MESSAGE,
            )

        try:
            connection = self._connector(config)
        except SchemaInspectionError:
            raise
        except Exception:
            raise _fail() from None

        cursor: Any | None = None
        try:
            server_version = getattr(connection, "server_version", None)
            if (
                not isinstance(server_version, int)
                or server_version < MIN_SERVER_VERSION_NUM
            ):
                raise _fail()
            try:
                cursor = connection.cursor()
            except Exception:
                raise _fail() from None
            try:
                cursor.execute(READ_ONLY_STATEMENT, ())
                cursor.execute(
                    STATEMENT_TIMEOUT_STATEMENT,
                    (f"{config.statement_timeout_ms}ms",),
                )
                tables = self._inspect_tables(
                    cursor, config.schema, include_views=config.include_views
                )
            except SchemaInspectionError:
                raise
            except Exception:
                raise _fail() from None
            return DatabaseCatalog(
                database_id=database_id,
                dialect="postgres",
                tables=tables,
                schema_fingerprint=self._fingerprint(tables),
            )
        except SchemaInspectionError:
            raise
        except Exception:
            raise _fail() from None
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

    def _inspect_tables(
        self, cursor: Any, schema: str, *, include_views: bool = False
    ) -> list[TableCatalog]:
        query = PG_TABLES_WITH_VIEWS_QUERY if include_views else PG_TABLES_QUERY
        cursor.execute(query, (schema,))
        rows = cursor.fetchall()
        table_names = sorted({_field(row, "table_name", 0) for row in rows})
        return [self._inspect_table(cursor, schema, name) for name in table_names]

    def _inspect_table(
        self, cursor: Any, schema: str, table_name: str
    ) -> TableCatalog:
        cursor.execute(PG_COLUMNS_QUERY, (schema, table_name))
        column_rows = cursor.fetchall()
        cursor.execute(PG_PK_QUERY, (schema, table_name))
        pk_rows = cursor.fetchall()
        cursor.execute(PG_FK_QUERY, (schema, table_name, schema))
        fk_rows = cursor.fetchall()

        pk_order: dict[str, int] = {}
        for row in pk_rows:
            name = str(_field(row, "column_name", 0))
            position = int(_field(row, "position", 1))
            if name not in pk_order:
                pk_order[name] = position

        columns = [self._column_catalog(row, pk_order) for row in column_rows]
        primary_key = sorted(pk_order, key=lambda name: pk_order[name])
        foreign_keys = self._foreign_keys(fk_rows)
        return TableCatalog(
            name=table_name,
            columns=columns,
            primary_key=primary_key,
            foreign_keys=foreign_keys,
        )

    @staticmethod
    def _column_catalog(row: Any, pk_order: dict[str, int]) -> ColumnCatalog:
        name = str(_field(row, "column_name", 0))
        declared = str(_field(row, "declared_type", 1) or "")
        nullable_value = _field(row, "nullable", 2)
        nullable = bool(nullable_value)
        default_value = _field(row, "default_sql", 3)
        default_sql = None if default_value is None else str(default_value)
        generated_kind = str(_field(row, "generated_kind", 4) or "")
        identity_kind = str(_field(row, "identity_kind", 5) or "")
        generated = generated_kind == "s" or identity_kind in ("a", "d")
        return ColumnCatalog(
            name=name,
            declared_type=declared,
            affinity=postgres_affinity(declared),
            nullable=nullable,
            default_sql=default_sql,
            primary_key_position=int(pk_order.get(name, 0)),
            hidden=False,
            generated=generated,
            sample_values=[],
        )

    @staticmethod
    def _foreign_keys(rows: Any) -> list[ForeignKeyCatalog]:
        grouped: dict[str, list[Any]] = {}
        for row in rows:
            constraint = str(_field(row, "constraint_name", 0))
            grouped.setdefault(constraint, []).append(row)
        foreign_keys: list[ForeignKeyCatalog] = []
        for constraint_id, constraint_name in enumerate(sorted(grouped)):
            members = sorted(
                grouped[constraint_name], key=lambda row: int(_field(row, "position", 7))
            )
            for row in members:
                sequence = int(_field(row, "position", 7)) - 1
                foreign_keys.append(
                    ForeignKeyCatalog(
                        id=constraint_id,
                        sequence=sequence,
                        column=str(_field(row, "column_name", 1)),
                        referenced_table=str(_field(row, "referenced_table", 2)),
                        referenced_column=str(_field(row, "referenced_column", 3)),
                        on_update=_FK_ACTION_LABELS.get(
                            str(_field(row, "update_action", 4)), "NO ACTION"
                        ),
                        on_delete=_FK_ACTION_LABELS.get(
                            str(_field(row, "delete_action", 5)), "NO ACTION"
                        ),
                        match=_FK_MATCH_LABELS.get(
                            str(_field(row, "match_type", 6)), "SIMPLE"
                        ),
                    )
                )
        return sorted(
            foreign_keys, key=lambda item: (item.id, item.sequence, item.column)
        )

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
