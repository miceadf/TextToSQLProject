from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from backend.adaptive.harness.contracts import QueryIR
from backend.adaptive.mapping.contracts import PublishedMappingProfile
from backend.adaptive.mapping.transforms import FieldTransform
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

    @model_validator(mode="after")
    def validate_semantic_fields(self) -> "JoinCompilationRequest":
        if not set(self.field_transforms).issubset(self.mapping.fields):
            raise ValueError("field transforms must target published mapping fields")
        referenced = sorted(
            {
                *self.query_ir.select,
                *(item.field for item in self.query_ir.filters),
            }
        )
        if self.join_result.semantic_fields != referenced:
            raise ValueError(
                "join result semantic fields must match QueryIR references exactly"
            )
        return self
