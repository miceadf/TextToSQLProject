"""Database schema catalog and adapters."""

from .adapter import DatabaseAdapter
from .capabilities import (
    CapabilityError,
    CapabilityErrorCode,
    DbmsCapability,
    DbmsDialect,
    get_capability,
    postgres_capability,
    sqlite_capability,
)
from .contracts import (
    ColumnCatalog,
    DatabaseCatalog,
    ForeignKeyCatalog,
    SQLiteAffinity,
    TableCatalog,
)
from .errors import CatalogErrorCode, SchemaInspectionError
from .postgres import (
    PostgresAdapter,
    PostgresConnectionConfig,
    postgres_affinity,
)
from .sqlite import SQLiteAdapter

__all__ = [
    "CapabilityError",
    "CapabilityErrorCode",
    "CatalogErrorCode",
    "ColumnCatalog",
    "DatabaseAdapter",
    "DatabaseCatalog",
    "DbmsCapability",
    "DbmsDialect",
    "ForeignKeyCatalog",
    "PostgresAdapter",
    "PostgresConnectionConfig",
    "SQLiteAdapter",
    "SQLiteAffinity",
    "SchemaInspectionError",
    "TableCatalog",
    "get_capability",
    "postgres_affinity",
    "postgres_capability",
    "sqlite_capability",
]
