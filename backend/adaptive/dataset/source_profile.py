from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from backend.adaptive.harness.contracts import (
    FieldMapping,
    MappingProfile,
    MappingStatus,
    Ontology,
    OntologyField,
)
from backend.adaptive.ontology.contracts import DataType, DomainOntology

from .conversion_contracts import (
    DatasetSchemaProfile,
    SourceLiteralType,
    SourceProfileStatus,
)


_COMPATIBLE_LITERAL_TYPES: dict[DataType, frozenset[SourceLiteralType]] = {
    DataType.STRING: frozenset({SourceLiteralType.STRING}),
    DataType.NUMBER: frozenset(
        {SourceLiteralType.DECIMAL, SourceLiteralType.INTEGER}
    ),
    DataType.INTEGER: frozenset({SourceLiteralType.INTEGER}),
    DataType.BOOLEAN: frozenset({SourceLiteralType.BOOLEAN}),
    DataType.DATE: frozenset({SourceLiteralType.STRING}),
    DataType.TIME: frozenset({SourceLiteralType.STRING}),
}
_SUPPORTED_SOURCE_DIALECTS = frozenset({"postgres"})


def load_dataset_schema_profile(
    path: str | Path,
    ontology: DomainOntology,
    expected_dialect: str | None = None,
) -> DatasetSchemaProfile:
    """Load and validate reviewed metadata for the legacy dataset schema.

    This profile is an offline conversion input.  It must not be stored or
    treated as a runtime ``PublishedMappingProfile``.
    """

    profile_path = Path(path)
    with profile_path.open("r", encoding="utf-8") as stream:
        raw_profile = yaml.safe_load(stream)

    if not isinstance(raw_profile, dict):
        raise ValueError("dataset schema profile must contain a YAML object")

    profile = DatasetSchemaProfile.model_validate(raw_profile)
    _validate_profile_against_ontology(
        profile,
        ontology,
        expected_dialect=expected_dialect,
    )
    return profile


def profile_sha256(profile: DatasetSchemaProfile) -> str:
    payload = json.dumps(
        profile.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_runtime_validation_context(
    profile: DatasetSchemaProfile,
    ontology: DomainOntology,
) -> tuple[Ontology, MappingProfile]:
    """Build an ephemeral harness view used only to validate converted IR."""

    _validate_profile_against_ontology(profile, ontology)
    runtime_ontology = Ontology(
        entities={entity.name for entity in ontology.entities},
        fields={
            field_name: OntologyField(
                name=field.name,
                data_type=field.data_type,
                allowed_operators=field.allowed_operators,
            )
            for field_name, field in sorted(ontology.fields.items())
        },
    )
    runtime_mapping = MappingProfile(
        database_id=f"dataset-source:{profile.profile_id}",
        version=profile.version,
        status=MappingStatus.PUBLISHED,
        fields={
            mapping.semantic_field: FieldMapping(
                table=mapping.table,
                column=mapping.column,
            )
            for mapping in profile.mappings
        },
    )
    return runtime_ontology, runtime_mapping


def _validate_profile_against_ontology(
    profile: DatasetSchemaProfile,
    ontology: DomainOntology,
    *,
    expected_dialect: str | None = None,
) -> None:
    if profile.status is not SourceProfileStatus.APPROVED:
        raise ValueError("dataset schema profile must be approved")
    if profile.dialect not in _SUPPORTED_SOURCE_DIALECTS:
        raise ValueError(
            f"unsupported source profile dialect '{profile.dialect}'"
        )
    if expected_dialect is not None and profile.dialect != expected_dialect:
        raise ValueError(
            f"source profile dialect '{profile.dialect}' does not match "
            f"expected dialect '{expected_dialect}'"
        )
    if not ontology.accepts_version(profile.ontology_version):
        raise ValueError(
            f"source profile ontology version {profile.ontology_version} does "
            f"not match ontology version {ontology.version}"
        )

    entity_names = {entity.name for entity in ontology.entities}
    if profile.root_entity not in entity_names:
        raise ValueError(
            f"source profile has unknown root entity '{profile.root_entity}'"
        )

    # Phase 8-18: only the fields the profile's ontology version had.
    ontology_fields = ontology.fields_at(profile.ontology_version)
    for mapping in profile.mappings:
        ontology_field = ontology_fields.get(mapping.semantic_field)
        if ontology_field is None:
            raise ValueError(
                "source profile maps unknown semantic field "
                f"'{mapping.semantic_field}'"
            )
        compatible_types = _COMPATIBLE_LITERAL_TYPES[ontology_field.data_type]
        incompatible_types = set(mapping.allowed_literal_types) - compatible_types
        if incompatible_types:
            incompatible = ", ".join(
                item.value
                for item in sorted(
                    incompatible_types,
                    key=lambda item: item.value,
                )
            )
            raise ValueError(
                f"source literal type(s) {incompatible} are incompatible with "
                f"'{mapping.semantic_field}' ({ontology_field.data_type.value})"
            )
