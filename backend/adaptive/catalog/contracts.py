from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


CatalogValue = str | int | float | bool


class SQLiteAffinity(str, Enum):
    INTEGER = "INTEGER"
    TEXT = "TEXT"
    BLOB = "BLOB"
    REAL = "REAL"
    NUMERIC = "NUMERIC"


class ColumnCatalog(BaseModel):
    name: str = Field(min_length=1)
    declared_type: str
    affinity: SQLiteAffinity
    nullable: bool
    default_sql: str | None = None
    primary_key_position: int = Field(default=0, ge=0)
    hidden: bool = False
    generated: bool = False
    sample_values: list[CatalogValue] = Field(default_factory=list)


class ForeignKeyCatalog(BaseModel):
    id: int = Field(ge=0)
    sequence: int = Field(ge=0)
    column: str = Field(min_length=1)
    referenced_table: str = Field(min_length=1)
    referenced_column: str | None = None
    on_update: str
    on_delete: str
    match: str


class TableCatalog(BaseModel):
    name: str = Field(min_length=1)
    columns: list[ColumnCatalog]
    primary_key: list[str] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyCatalog] = Field(default_factory=list)


class DatabaseCatalog(BaseModel):
    database_id: str = Field(min_length=1)
    dialect: Literal["sqlite", "postgres"] = "sqlite"
    tables: list[TableCatalog]
    schema_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
