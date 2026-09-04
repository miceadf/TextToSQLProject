from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class Operator(str, Enum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    CONTAINS = "contains"


class DataType(str, Enum):
    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    DATE = "date"
    TIME = "time"


class OntologyField(BaseModel):
    name: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    data_type: DataType
    aliases: list[str] = Field(default_factory=list)
    physical_name_hints: list[str] = Field(default_factory=list)
    allowed_operators: set[Operator]
    required: bool = False


class EntityDefinition(BaseModel):
    name: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    table_name_hints: list[str] = Field(default_factory=list)
    fields: list[OntologyField] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_field_names(self) -> "EntityDefinition":
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise ValueError(f"entity '{self.name}' has duplicate fields")
        expected_prefix = self.name + "."
        if any(not name.startswith(expected_prefix) for name in names):
            raise ValueError(
                f"all fields of '{self.name}' must start with '{expected_prefix}'"
            )
        return self


class DomainOntology(BaseModel):
    version: int = Field(ge=1)
    domain: str = Field(min_length=1)
    entities: list[EntityDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_names(self) -> "DomainOntology":
        entity_names = [entity.name for entity in self.entities]
        if len(entity_names) != len(set(entity_names)):
            raise ValueError("ontology entity names must be unique")
        field_names = [field.name for entity in self.entities for field in entity.fields]
        if len(field_names) != len(set(field_names)):
            raise ValueError("ontology field names must be unique")
        return self

    @property
    def fields(self) -> dict[str, OntologyField]:
        return {
            field.name: field
            for entity in self.entities
            for field in entity.fields
        }

    def entity_for_field(self, field_name: str) -> EntityDefinition:
        entity_name = field_name.split(".", 1)[0]
        for entity in self.entities:
            if entity.name == entity_name:
                return entity
        raise KeyError(field_name)
