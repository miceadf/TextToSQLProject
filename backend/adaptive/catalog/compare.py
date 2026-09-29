"""Read-only schema comparison between two catalogs (Phase 8-02).

Pure functions over :class:`DatabaseCatalog`; dialect independent. Only
structural metadata is compared and returned: sample values, default SQL,
and any row content are never read or echoed. Names match exactly; renames
are reported as a removal plus an addition.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .contracts import ColumnCatalog, DatabaseCatalog


CATALOG_DIFF_VERSION = "catalog-diff-v1"
MAX_DIFF_ENTRIES = 500


class ColumnChangeKind(str, Enum):
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    TYPE_CHANGED = "TYPE_CHANGED"
    NULLABILITY_CHANGED = "NULLABILITY_CHANGED"
    PRIMARY_KEY_CHANGED = "PRIMARY_KEY_CHANGED"


class ForeignKeyChangeKind(str, Enum):
    ADDED = "ADDED"
    REMOVED = "REMOVED"


class FieldImpactStatus(str, Enum):
    UNCHANGED = "UNCHANGED"
    TYPE_CHANGED = "TYPE_CHANGED"
    COLUMN_MISSING = "COLUMN_MISSING"
    TABLE_MISSING = "TABLE_MISSING"


class ColumnShape(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    declared_type: str
    affinity: str
    nullable: bool
    primary_key_position: int = Field(ge=0)


class ColumnChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    table: str
    column: str
    change: ColumnChangeKind
    base: ColumnShape | None
    candidate: ColumnShape | None


class ForeignKeyChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    table: str
    column: str
    referenced_table: str
    referenced_column: str | None
    change: ForeignKeyChangeKind


class CatalogDiff(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["catalog-diff-v1"] = CATALOG_DIFF_VERSION
    base_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    identical: bool
    tables_added: list[str]
    tables_removed: list[str]
    column_changes: list[ColumnChange]
    foreign_key_changes: list[ForeignKeyChange]
    truncated: bool


class FieldImpact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    semantic_field: str
    table: str
    column: str
    status: FieldImpactStatus


def _shape(column: ColumnCatalog) -> ColumnShape:
    return ColumnShape(
        declared_type=column.declared_type,
        affinity=column.affinity.value,
        nullable=column.nullable,
        primary_key_position=column.primary_key_position,
    )


def _columns(catalog: DatabaseCatalog) -> dict[str, dict[str, ColumnCatalog]]:
    return {
        table.name: {column.name: column for column in table.columns}
        for table in catalog.tables
    }


def _foreign_keys(catalog: DatabaseCatalog) -> set[tuple[str, str, str, str | None]]:
    return {
        (table.name, key.column, key.referenced_table, key.referenced_column)
        for table in catalog.tables
        for key in table.foreign_keys
    }


def compare_catalogs(base: DatabaseCatalog, candidate: DatabaseCatalog) -> CatalogDiff:
    base_columns = _columns(base)
    candidate_columns = _columns(candidate)
    tables_added = sorted(set(candidate_columns) - set(base_columns))
    tables_removed = sorted(set(base_columns) - set(candidate_columns))

    column_changes: list[ColumnChange] = []
    for table in sorted(set(base_columns) & set(candidate_columns)):
        before, after = base_columns[table], candidate_columns[table]
        for name in sorted(set(before) | set(after)):
            old, new = before.get(name), after.get(name)
            if old is None:
                column_changes.append(ColumnChange(
                    table=table, column=name, change=ColumnChangeKind.ADDED,
                    base=None, candidate=_shape(new),
                ))
                continue
            if new is None:
                column_changes.append(ColumnChange(
                    table=table, column=name, change=ColumnChangeKind.REMOVED,
                    base=_shape(old), candidate=None,
                ))
                continue
            old_shape, new_shape = _shape(old), _shape(new)
            kinds = []
            if (old_shape.declared_type, old_shape.affinity) != (new_shape.declared_type, new_shape.affinity):
                kinds.append(ColumnChangeKind.TYPE_CHANGED)
            if old_shape.nullable != new_shape.nullable:
                kinds.append(ColumnChangeKind.NULLABILITY_CHANGED)
            if old_shape.primary_key_position != new_shape.primary_key_position:
                kinds.append(ColumnChangeKind.PRIMARY_KEY_CHANGED)
            column_changes.extend(
                ColumnChange(table=table, column=name, change=kind, base=old_shape, candidate=new_shape)
                for kind in kinds
            )

    base_keys, candidate_keys = _foreign_keys(base), _foreign_keys(candidate)

    def _key_order(item: tuple[str, str, str, str | None]) -> tuple[str, str, str, str]:
        return item[0], item[1], item[2], item[3] or ""

    foreign_key_changes = [
        ForeignKeyChange(
            table=key[0], column=key[1], referenced_table=key[2], referenced_column=key[3],
            change=ForeignKeyChangeKind.ADDED,
        )
        for key in sorted(candidate_keys - base_keys, key=_key_order)
    ] + [
        ForeignKeyChange(
            table=key[0], column=key[1], referenced_table=key[2], referenced_column=key[3],
            change=ForeignKeyChangeKind.REMOVED,
        )
        for key in sorted(base_keys - candidate_keys, key=_key_order)
    ]

    budget = MAX_DIFF_ENTRIES
    total = len(tables_added) + len(tables_removed) + len(column_changes) + len(foreign_key_changes)
    kept_added = tables_added[:budget]
    budget -= len(kept_added)
    kept_removed = tables_removed[:budget]
    budget -= len(kept_removed)
    kept_columns = column_changes[:budget]
    budget -= len(kept_columns)
    kept_keys = foreign_key_changes[:budget]
    return CatalogDiff(
        base_fingerprint=base.schema_fingerprint,
        candidate_fingerprint=candidate.schema_fingerprint,
        identical=base.schema_fingerprint == candidate.schema_fingerprint,
        tables_added=kept_added,
        tables_removed=kept_removed,
        column_changes=kept_columns,
        foreign_key_changes=kept_keys,
        truncated=total > MAX_DIFF_ENTRIES,
    )


def mapping_impact(
    mapped_fields: dict[str, tuple[str, str]],
    base: DatabaseCatalog,
    candidate: DatabaseCatalog,
) -> list[FieldImpact]:
    """Classify each ``semantic_field -> (table, column)`` of an active mapping."""
    base_columns = _columns(base)
    candidate_columns = _columns(candidate)
    impacts: list[FieldImpact] = []
    for semantic_field in sorted(mapped_fields):
        table, column = mapped_fields[semantic_field]
        if table not in candidate_columns:
            status = FieldImpactStatus.TABLE_MISSING
        elif column not in candidate_columns[table]:
            status = FieldImpactStatus.COLUMN_MISSING
        else:
            old = base_columns.get(table, {}).get(column)
            new = candidate_columns[table][column]
            if old is not None and (
                (old.declared_type, old.affinity, old.nullable)
                == (new.declared_type, new.affinity, new.nullable)
            ):
                status = FieldImpactStatus.UNCHANGED
            else:
                status = FieldImpactStatus.TYPE_CHANGED
        impacts.append(FieldImpact(
            semantic_field=semantic_field, table=table, column=column, status=status,
        ))
    return impacts
