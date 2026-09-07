from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator

from backend.adaptive.ontology.contracts import OntologyRelation


class JoinColumnPair(BaseModel):
    """One predicate pair in the FK's original child-to-parent direction."""

    source_column: str = Field(min_length=1)
    target_column: str = Field(min_length=1)


class PhysicalRelation(BaseModel):
    relation_id: str = Field(min_length=1)
    source_table: str = Field(min_length=1)
    target_table: str = Field(min_length=1)
    column_pairs: list[JoinColumnPair] = Field(min_length=1)
    on_update: str
    on_delete: str
    match: str

    @model_validator(mode="after")
    def validate_column_pairs(self) -> "PhysicalRelation":
        pairs = [
            (pair.source_column, pair.target_column) for pair in self.column_pairs
        ]
        if len(pairs) != len(set(pairs)):
            raise ValueError("relation column pairs must be unique")
        source_columns = [pair.source_column for pair in self.column_pairs]
        target_columns = [pair.target_column for pair in self.column_pairs]
        if len(source_columns) != len(set(source_columns)):
            raise ValueError("relation source columns must be unique")
        if len(target_columns) != len(set(target_columns)):
            raise ValueError("relation target columns must be unique")
        return self


class PhysicalRelationGraph(BaseModel):
    database_id: str = Field(min_length=1)
    schema_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    tables: list[str]
    relations: list[PhysicalRelation]

    @model_validator(mode="after")
    def validate_graph(self) -> "PhysicalRelationGraph":
        if len(self.tables) != len(set(self.tables)):
            raise ValueError("graph table names must be unique")
        relation_ids = [relation.relation_id for relation in self.relations]
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("graph relation ids must be unique")
        known_tables = set(self.tables)
        for relation in self.relations:
            if relation.source_table not in known_tables:
                raise ValueError(
                    f"relation '{relation.relation_id}' has unknown source table"
                )
            if relation.target_table not in known_tables:
                raise ValueError(
                    f"relation '{relation.relation_id}' has unknown target table"
                )
        return self


class OntologyRelationGraph(BaseModel):
    ontology_version: int = Field(ge=1)
    entities: list[str]
    relations: list[OntologyRelation]

    @model_validator(mode="after")
    def validate_graph(self) -> "OntologyRelationGraph":
        if len(self.entities) != len(set(self.entities)):
            raise ValueError("ontology graph entity names must be unique")
        relation_names = [relation.name for relation in self.relations]
        if len(relation_names) != len(set(relation_names)):
            raise ValueError("ontology graph relation names must be unique")
        known_entities = set(self.entities)
        for relation in self.relations:
            if {
                relation.source_entity,
                relation.target_entity,
            } - known_entities:
                raise ValueError(
                    f"ontology relation '{relation.name}' has an unknown endpoint"
                )
        return self


class JoinStep(BaseModel):
    """A traversed edge; relation retains the original FK predicate direction."""

    relation: PhysicalRelation
    traversal_from: str = Field(min_length=1)
    traversal_to: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_traversal(self) -> "JoinStep":
        endpoints = {self.relation.source_table, self.relation.target_table}
        if self.traversal_from == self.traversal_to or {
            self.traversal_from,
            self.traversal_to,
        } != endpoints:
            raise ValueError("join traversal must follow the relation endpoints")
        return self


class JoinPlan(BaseModel):
    anchor_table: str = Field(min_length=1)
    required_tables: list[str] = Field(min_length=1)
    steps: list[JoinStep]

    @model_validator(mode="after")
    def validate_plan(self) -> "JoinPlan":
        if len(self.required_tables) != len(set(self.required_tables)):
            raise ValueError("join plan required tables must be unique")
        if self.anchor_table not in self.required_tables:
            raise ValueError("join plan anchor must be a required table")

        joined = {self.anchor_table}
        relation_ids: set[str] = set()
        for step in self.steps:
            if step.relation.relation_id in relation_ids:
                raise ValueError("join plan cannot repeat a relation")
            if step.traversal_from not in joined or step.traversal_to in joined:
                raise ValueError(
                    "join plan steps must add one table from the connected prefix"
                )
            relation_ids.add(step.relation.relation_id)
            joined.add(step.traversal_to)
        if not set(self.required_tables).issubset(joined):
            raise ValueError("join plan does not connect every required table")
        return self


class JoinPlanStatus(str, Enum):
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"


class JoinPlanningResult(BaseModel):
    status: JoinPlanStatus
    semantic_fields: list[str] = Field(min_length=1)
    required_tables: list[str] = Field(min_length=1)
    plan: JoinPlan | None = None
    alternatives: list[JoinPlan] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status(self) -> "JoinPlanningResult":
        if len(self.semantic_fields) != len(set(self.semantic_fields)):
            raise ValueError("result semantic fields must be unique")
        if len(self.required_tables) != len(set(self.required_tables)):
            raise ValueError("result required tables must be unique")
        if self.status is JoinPlanStatus.RESOLVED:
            if self.plan is None or self.alternatives:
                raise ValueError("resolved result requires one plan and no alternatives")
        elif self.plan is not None or len(self.alternatives) < 2:
            raise ValueError(
                "ambiguous result requires at least two alternatives and no chosen plan"
            )
        plans = [self.plan] if self.plan is not None else self.alternatives
        for plan in plans:
            if plan.required_tables != self.required_tables:
                raise ValueError(
                    "result and join plan required tables must match exactly"
                )
        signatures = [
            tuple(sorted(step.relation.relation_id for step in plan.steps))
            for plan in self.alternatives
        ]
        if len(signatures) != len(set(signatures)):
            raise ValueError("ambiguous alternatives must have unique edge sets")
        return self
