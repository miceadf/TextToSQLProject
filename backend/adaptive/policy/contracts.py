from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, computed_field

from backend.adaptive.harness.contracts import CompiledQuery, QueryIR
from backend.adaptive.mapping.contracts import PublishedMappingProfile
from backend.adaptive.mapping.transforms import FieldTransform
from backend.adaptive.mapping.values import ValueDictionary
from backend.adaptive.relations.contracts import JoinPlanningResult
from backend.adaptive.compiler.contracts import MemberFilter


class AstPolicyContext(BaseModel):
    """Source-of-truth inputs for strict compiler-output validation."""

    query: CompiledQuery
    query_ir: QueryIR
    mapping: PublishedMappingProfile
    join_result: JoinPlanningResult
    dialect: Literal["sqlite", "postgres"] = "sqlite"
    # Declared per-field value transforms; only the postgres runtime sets them.
    field_transforms: dict[str, FieldTransform] = Field(default_factory=dict)
    # Declared row anchor (postgres runtime only): every JOIN must then be a
    # plain LEFT JOIN from this table; without it only INNER JOIN is allowed.
    anchor_table: str | None = Field(default=None, min_length=1)
    # Reviewed per-field value dictionaries (postgres runtime only); the
    # expected bound value is computed exactly as the compiler does.
    value_dictionaries: dict[str, ValueDictionary] = Field(default_factory=dict)
    # Phase 9-36: enabled only by the internal catalog validation entry point.
    member_filters: tuple[MemberFilter, ...] = ()
    distinct: bool = False

    @computed_field
    @property
    def allowed_tables(self) -> list[str]:
        plan = self.join_result.plan
        if plan is None:
            return []
        return [plan.anchor_table, *(step.traversal_to for step in plan.steps)]

    @computed_field
    @property
    def allowed_columns(self) -> dict[str, list[str]]:
        columns: dict[str, set[str]] = {
            table: set() for table in self.allowed_tables
        }
        for field in self.mapping.fields.values():
            if field.table in columns:
                columns[field.table].add(field.column)
        plan = self.join_result.plan
        if plan is not None:
            for step in plan.steps:
                relation = step.relation
                for pair in relation.column_pairs:
                    columns[relation.source_table].add(pair.source_column)
                    columns[relation.target_table].add(pair.target_column)
        return {
            table: sorted(table_columns)
            for table, table_columns in columns.items()
        }
