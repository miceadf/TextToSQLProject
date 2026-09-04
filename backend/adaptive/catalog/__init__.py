"""Database schema catalog and adapters."""

from .adapter import DatabaseAdapter
from .contracts import (
    ColumnCatalog,
    DatabaseCatalog,
    ForeignKeyCatalog,
    SQLiteAffinity,
    TableCatalog,
)
from .errors import CatalogErrorCode, SchemaInspectionError
from .sqlite import SQLiteAdapter

__all__ = [
    "CatalogErrorCode",
    "ColumnCatalog",
    "DatabaseAdapter",
    "DatabaseCatalog",
    "ForeignKeyCatalog",
    "SQLiteAdapter",
    "SQLiteAffinity",
    "SchemaInspectionError",
    "TableCatalog",
]
