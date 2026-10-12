"""Extension fields declared by administrators for PostgreSQL registrations (Phase 8-19).

A field the common ontology lacks (a classroom, say) is declared as data:
a label, a key, the entity it belongs to, the column, and a value type.
Entries live beside the review draft until publishing puts them into the
mapping and the profile, like value dictionaries (Phase 8-07).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..mapping import ReviewDecision
from ..mapping.extensions import (
    DATA_TYPES,
    ENTITIES,
    KEY_PATTERN,
    MAX_EXTENSIONS,
    ExtensionField,
    column_fits,
    extension_name,
    valid_label,
)
from . import postgres_registrations as pg

EXTENSIONS_VERSION = "private-postgres-extensions-v1"
REVIEW_REASON = "Phase 8-19 administrator-declared extension field"


class StoredExtension(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(pattern=KEY_PATTERN.pattern)
    label: str = Field(min_length=1, max_length=20)
    entity: str
    table: str = Field(min_length=1, max_length=63)
    column: str = Field(min_length=1, max_length=63)
    data_type: str
    reviewer: str = Field(min_length=1, max_length=64)

    @property
    def name(self) -> str:
        return extension_name(self.entity, self.key)


class StoredExtensions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(pattern=r"^private-postgres-extensions-v1$")
    database_id: str
    fields: list[StoredExtension] = Field(max_length=MAX_EXTENSIONS)


def _path(repository_root: Any, database_id: str) -> Any:
    return pg._private_dir(repository_root, pg.REGISTRATION_ROOT, database_id)[1] / "extensions.json"  # noqa: SLF001


def load_extensions(*, repository_root: Any, database_id: str) -> list[StoredExtension]:
    pg.load_postgres_registration(repository_root=repository_root, database_id=database_id)
    path = _path(repository_root, database_id)
    if not path.exists():
        return []
    stored = pg._strict_model(pg._read(path), StoredExtensions)  # noqa: SLF001
    if stored.database_id != database_id:
        raise pg.PostgresRegistrationError("ARTIFACT_MISMATCH")
    return list(stored.fields)


def _save(repository_root: Any, database_id: str, fields: list[StoredExtension]) -> None:
    document = StoredExtensions(
        schema_version=EXTENSIONS_VERSION,
        database_id=database_id,
        fields=sorted(fields, key=lambda item: item.name),
    )
    pg._write_atomic(_path(repository_root, database_id), pg._json_bytes(document.model_dump(mode="json")))  # noqa: SLF001


def columns(loaded: pg.LoadedPostgresRegistration) -> list[dict[str, Any]]:
    """Tables and their columns with declared types, for the declaration form."""
    return [
        {
            "name": table.name,
            "columns": [
                {"name": column.name, "type": str(column.declared_type or "")} for column in table.columns
            ],
        }
        for table in loaded.catalog.tables
    ]


def reviewed_columns(draft: Any) -> set[tuple[str, str]]:
    """Columns already given to a common field by the review."""
    from ..ontology import load_default_ontology

    return {
        (entry.table or "", entry.column or "")
        for name, entry in getattr(draft, "fields", {}).items()
        if name not in load_default_ontology().retired_fields
        if entry.decision in (ReviewDecision.APPROVED, ReviewDecision.REMAPPED)
    }


def set_extension(
    *,
    repository_root: Any,
    database_id: str,
    key: Any,
    label: Any,
    entity: Any,
    table: Any,
    column: Any,
    data_type: Any,
    reviewer: str,
) -> list[StoredExtension]:
    """Add or replace one extension field; raises ``INVALID_INPUT`` or ``EXTENSION_CONFLICT``."""
    loaded = pg.load_postgres_registration(repository_root=repository_root, database_id=database_id)
    if (
        not isinstance(key, str)
        or KEY_PATTERN.fullmatch(key) is None
        or not valid_label(label)
        or not isinstance(entity, str)
        or entity not in ENTITIES
        or not isinstance(data_type, str)
        or data_type not in DATA_TYPES
        or not isinstance(table, str)
        or not isinstance(column, str)
    ):
        raise pg.PostgresRegistrationError("INVALID_INPUT")
    declared = {(t.name, c.name): str(c.declared_type or "") for t in loaded.catalog.tables for c in t.columns}
    if (table, column) not in declared or not column_fits(data_type, declared[(table, column)]):
        raise pg.PostgresRegistrationError("INVALID_INPUT")
    current = load_extensions(repository_root=repository_root, database_id=database_id)
    others = [item for item in current if item.key != key]
    draft = pg.load_draft(repository_root=repository_root, database_id=database_id)
    taken = reviewed_columns(draft) | {(item.table, item.column) for item in others}
    if (table, column) in taken or any(item.label == label for item in others):
        raise pg.PostgresRegistrationError("EXTENSION_CONFLICT")
    if len(others) >= MAX_EXTENSIONS:
        raise pg.PostgresRegistrationError("LIMIT_REACHED")
    entry = StoredExtension(
        key=key, label=label, entity=entity, table=table, column=column, data_type=data_type, reviewer=reviewer
    )
    updated = [*others, entry]
    _save(repository_root, database_id, updated)
    return sorted(updated, key=lambda item: item.name)


def remove_extension(*, repository_root: Any, database_id: str, key: Any) -> list[StoredExtension]:
    current = load_extensions(repository_root=repository_root, database_id=database_id)
    if not isinstance(key, str) or all(item.key != key for item in current):
        raise pg.PostgresRegistrationError("NOT_FOUND")
    remaining = [item for item in current if item.key != key]
    _save(repository_root, database_id, remaining)
    return remaining


def runtime_fields(stored: list[StoredExtension]) -> dict[str, ExtensionField]:
    return {item.name: ExtensionField(label=item.label, data_type=item.data_type) for item in stored}
