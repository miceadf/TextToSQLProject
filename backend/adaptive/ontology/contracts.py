"""학교별 물리 schema와 독립된 공통 항목·자료형·연산자의 검증 계약.

활성 항목은 since_version <= v이고 retired_in_version이 없거나 v보다 큰 항목이다.
현재 v3는 개설 연도를 제외하며 v1·v2 기록은 해당 버전의 항목으로 읽는다.
"""

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


class OntologyCardinality(str, Enum):
    ONE_TO_ONE = "one_to_one"
    ONE_TO_MANY = "one_to_many"
    MANY_TO_ONE = "many_to_one"
    MANY_TO_MANY = "many_to_many"


class OntologyRelation(BaseModel):
    name: str = Field(min_length=1)
    source_entity: str = Field(min_length=1)
    target_entity: str = Field(min_length=1)
    cardinality: OntologyCardinality


class OntologyField(BaseModel):
    name: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    data_type: DataType
    aliases: list[str] = Field(default_factory=list)
    physical_name_hints: list[str] = Field(default_factory=list)
    allowed_operators: set[Operator]
    required: bool = False
    # Keep definitions for historical records, including retired fields.
    since_version: int = Field(default=1, ge=1)
    retired_in_version: int | None = Field(default=None, ge=1)


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
    relations: list[OntologyRelation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_names(self) -> "DomainOntology":
        entity_names = [entity.name for entity in self.entities]
        if len(entity_names) != len(set(entity_names)):
            raise ValueError("ontology entity names must be unique")
        field_names = [field.name for entity in self.entities for field in entity.fields]
        if len(field_names) != len(set(field_names)):
            raise ValueError("ontology field names must be unique")
        relation_names = [relation.name for relation in self.relations]
        if len(relation_names) != len(set(relation_names)):
            raise ValueError("ontology relation names must be unique")
        for entity in self.entities:
            for field in entity.fields:
                if field.since_version > self.version:
                    raise ValueError(f"field '{field.name}' is newer than the ontology")
                if field.retired_in_version is not None and field.retired_in_version <= field.since_version:
                    raise ValueError(f"field '{field.name}' retires before it is introduced")
        known_entities = set(entity_names)
        for relation in self.relations:
            if relation.source_entity not in known_entities:
                raise ValueError(
                    f"relation '{relation.name}' has unknown source entity "
                    f"'{relation.source_entity}'"
                )
            if relation.target_entity not in known_entities:
                raise ValueError(
                    f"relation '{relation.name}' has unknown target entity "
                    f"'{relation.target_entity}'"
                )
        return self

    @property
    def fields(self) -> dict[str, OntologyField]:
        return {
            field.name: field
            for entity in self.entities
            for field in entity.fields
        }

    def accepts_version(self, version: object) -> bool:
        """Whether a record made under ``version`` is valid under this ontology (Phase 8-18)."""
        return (
            isinstance(version, int)
            and not isinstance(version, bool)
            and 1 <= version <= self.version
        )

    @property
    def active_fields(self) -> dict[str, OntologyField]:
        return self.fields_at(self.version)

    @property
    def retired_fields(self) -> set[str]:
        return {
            name for name, field in self.fields.items()
            if field.retired_in_version is not None and field.retired_in_version <= self.version
        }

    def fields_at(self, version: int) -> dict[str, OntologyField]:
        """The fields a record made under ``version`` knows about."""
        if not self.accepts_version(version):
            raise ValueError("ontology version is not accepted")
        return {
            name: field
            for name, field in self.fields.items()
            if field.since_version <= version
            and (field.retired_in_version is None or field.retired_in_version > version)
        }

    def at_version(self, version: int) -> "DomainOntology":
        """This ontology as it stood at ``version``."""
        if not self.accepts_version(version):
            raise ValueError("ontology version is not accepted")
        entities = [
            entity.model_copy(update={"fields": [
                f.model_copy(update={"retired_in_version": None})
                if f.retired_in_version is not None and f.retired_in_version > version else f
                for f in entity.fields if f.name in self.fields_at(version)
            ]})
            for entity in self.entities
        ]
        return self.model_copy(update={"version": version, "entities": entities})

    def entity_for_field(self, field_name: str) -> EntityDefinition:
        entity_name = field_name.split(".", 1)[0]
        for entity in self.entities:
            if entity.name == entity_name:
                return entity
        raise KeyError(field_name)
