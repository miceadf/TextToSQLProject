from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

class MemberFilter(BaseModel):
    """Phase 9-36: catalog-only bound membership predicate."""
    field: str = Field(min_length=1)
    values: tuple[str | int | float, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_values(self) -> "MemberFilter":
        if any(isinstance(value, bool) or not isinstance(value, (str, int, float)) for value in self.values):
            raise ValueError("member filter values must be scalar")
        if len(set(self.values)) != len(self.values):
            raise ValueError("member filter values must be distinct")
        return self

from backend.adaptive.harness.contracts import QueryIR
from backend.adaptive.mapping.contracts import PublishedMappingProfile
from backend.adaptive.mapping.transforms import FieldTransform
from backend.adaptive.mapping.values import ValueDictionary
from backend.adaptive.relations.contracts import JoinPlanningResult


class TableAlias(BaseModel):
    table: str = Field(min_length=1)
    alias: str = Field(pattern=r"^t\d+$")


class JoinCompilationRequest(BaseModel):
    """Immutable compiler boundary after validation and JOIN planning."""

    query_ir: QueryIR
    mapping: PublishedMappingProfile
    join_result: JoinPlanningResult
    # Declared per-field value transforms (PostgreSQL runtime only).
    field_transforms: dict[str, FieldTransform] = Field(default_factory=dict)
    # Declared row anchor (PostgreSQL runtime only): every JOIN is a LEFT
    # JOIN from this table so anchor rows are never dropped.
    anchor_table: str | None = Field(default=None, min_length=1)
    # Reviewed per-field value dictionaries (PostgreSQL runtime only).
    value_dictionaries: dict[str, ValueDictionary] = Field(default_factory=dict)
    # Phase 9-36: internal catalog-only query capabilities.
    member_filters: tuple[MemberFilter, ...] = ()
    distinct: bool = False

    @model_validator(mode="after")
    def validate_semantic_fields(self) -> "JoinCompilationRequest":
        if not set(self.field_transforms).issubset(self.mapping.fields):
            raise ValueError("field transforms must target published mapping fields")
        if not set(self.value_dictionaries).issubset(self.mapping.fields):
            raise ValueError("value dictionaries must target published mapping fields")
        if set(self.value_dictionaries) & set(self.field_transforms):
            raise ValueError("a field cannot have both a transform and a value dictionary")
        if any(item.field not in self.mapping.fields for item in self.member_filters):
            raise ValueError("member filters must target published mapping fields")
        referenced = sorted(
            {
                *self.query_ir.select,
                *(item.field for item in self.query_ir.filters),
                *(item.field for item in self.member_filters),
            }
        )
        if self.join_result.semantic_fields != referenced:
            raise ValueError(
                "join result semantic fields must match QueryIR references exactly"
            )
        return self
