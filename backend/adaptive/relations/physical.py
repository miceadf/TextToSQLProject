from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from backend.adaptive.catalog.contracts import DatabaseCatalog, ForeignKeyCatalog

from .contracts import JoinColumnPair, PhysicalRelation, PhysicalRelationGraph
from .errors import RelationErrorCode, RelationGraphError, RelationIssue


def build_physical_relation_graph(
    catalog: DatabaseCatalog,
) -> PhysicalRelationGraph:
    """Build a deterministic relation graph from catalog foreign keys."""

    tables_by_name = {table.name: table for table in catalog.tables}
    if len(tables_by_name) != len(catalog.tables):
        raise _invalid_foreign_key(
            "catalog table names must be unique",
            database_id=catalog.database_id,
        )

    columns_by_table: dict[str, set[str]] = {}
    for table in catalog.tables:
        column_names = [column.name for column in table.columns]
        if len(column_names) != len(set(column_names)):
            raise _invalid_foreign_key(
                f"table '{table.name}' has duplicate column names",
                table=table.name,
            )
        columns_by_table[table.name] = set(column_names)

    relations: list[PhysicalRelation] = []
    for source_table in sorted(catalog.tables, key=lambda table: table.name):
        foreign_keys_by_id: dict[int, list[ForeignKeyCatalog]] = defaultdict(list)
        for foreign_key in source_table.foreign_keys:
            foreign_keys_by_id[foreign_key.id].append(foreign_key)

        for foreign_key_id in sorted(foreign_keys_by_id):
            relations.append(
                _build_relation(
                    source_table=source_table.name,
                    foreign_key_id=foreign_key_id,
                    foreign_keys=foreign_keys_by_id[foreign_key_id],
                    columns_by_table=columns_by_table,
                )
            )

    relations.sort(key=lambda relation: relation.relation_id)
    return PhysicalRelationGraph(
        database_id=catalog.database_id,
        schema_fingerprint=catalog.schema_fingerprint,
        tables=sorted(tables_by_name),
        relations=relations,
    )


def _build_relation(
    *,
    source_table: str,
    foreign_key_id: int,
    foreign_keys: Iterable[ForeignKeyCatalog],
    columns_by_table: dict[str, set[str]],
) -> PhysicalRelation:
    ordered_foreign_keys = sorted(
        foreign_keys,
        key=lambda foreign_key: foreign_key.sequence,
    )
    first = ordered_foreign_keys[0]
    target_table = first.referenced_table
    expected_options = (first.on_update, first.on_delete, first.match)

    sequences = [foreign_key.sequence for foreign_key in ordered_foreign_keys]
    if len(sequences) != len(set(sequences)):
        raise _invalid_foreign_key(
            "composite foreign key contains duplicate sequence values",
            source_table=source_table,
            foreign_key_id=foreign_key_id,
            sequences=sequences,
        )
    expected_sequences = list(range(len(ordered_foreign_keys)))
    if sequences != expected_sequences:
        raise _invalid_foreign_key(
            "composite foreign key sequence must start at zero and be contiguous",
            source_table=source_table,
            foreign_key_id=foreign_key_id,
            sequences=sequences,
            expected_sequences=expected_sequences,
        )

    if target_table not in columns_by_table:
        raise _invalid_foreign_key(
            f"foreign key references unknown table '{target_table}'",
            source_table=source_table,
            foreign_key_id=foreign_key_id,
            target_table=target_table,
        )

    column_pairs: list[JoinColumnPair] = []
    seen_source_columns: set[str] = set()
    seen_target_columns: set[str] = set()
    for foreign_key in ordered_foreign_keys:
        if foreign_key.referenced_table != target_table:
            raise _invalid_foreign_key(
                "composite foreign key rows reference different tables",
                source_table=source_table,
                foreign_key_id=foreign_key_id,
            )
        options = (
            foreign_key.on_update,
            foreign_key.on_delete,
            foreign_key.match,
        )
        if options != expected_options:
            raise _invalid_foreign_key(
                "composite foreign key rows have inconsistent actions",
                source_table=source_table,
                foreign_key_id=foreign_key_id,
            )
        if foreign_key.column not in columns_by_table[source_table]:
            raise _invalid_foreign_key(
                f"foreign key references unknown source column '{foreign_key.column}'",
                source_table=source_table,
                foreign_key_id=foreign_key_id,
                source_column=foreign_key.column,
            )
        if foreign_key.column in seen_source_columns:
            raise _invalid_foreign_key(
                "composite foreign key source columns must be unique",
                source_table=source_table,
                foreign_key_id=foreign_key_id,
                source_column=foreign_key.column,
            )
        if not foreign_key.referenced_column:
            raise _invalid_foreign_key(
                "foreign key has no referenced column",
                source_table=source_table,
                foreign_key_id=foreign_key_id,
                source_column=foreign_key.column,
                target_table=target_table,
            )
        if foreign_key.referenced_column not in columns_by_table[target_table]:
            raise _invalid_foreign_key(
                "foreign key references unknown target column "
                f"'{foreign_key.referenced_column}'",
                source_table=source_table,
                foreign_key_id=foreign_key_id,
                target_table=target_table,
                target_column=foreign_key.referenced_column,
            )
        if foreign_key.referenced_column in seen_target_columns:
            raise _invalid_foreign_key(
                "composite foreign key target columns must be unique",
                source_table=source_table,
                foreign_key_id=foreign_key_id,
                target_table=target_table,
                target_column=foreign_key.referenced_column,
            )
        seen_source_columns.add(foreign_key.column)
        seen_target_columns.add(foreign_key.referenced_column)
        column_pairs.append(
            JoinColumnPair(
                source_column=foreign_key.column,
                target_column=foreign_key.referenced_column,
            )
        )

    relation_id = f"{source_table}:fk:{foreign_key_id}:{target_table}"
    return PhysicalRelation(
        relation_id=relation_id,
        source_table=source_table,
        target_table=target_table,
        column_pairs=column_pairs,
        on_update=first.on_update,
        on_delete=first.on_delete,
        match=first.match,
    )


def _invalid_foreign_key(message: str, **context: object) -> RelationGraphError:
    return RelationGraphError(
        RelationIssue(
            code=RelationErrorCode.INVALID_FOREIGN_KEY,
            message=message,
            context=context,
        )
    )
