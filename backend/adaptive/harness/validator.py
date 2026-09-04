from __future__ import annotations

from .contracts import (
    DataType,
    HarnessErrorCode,
    HarnessStage,
    MappingProfile,
    MappingStatus,
    Ontology,
    OntologyField,
    Operator,
    QueryIR,
)
from .errors import HarnessFailure


def validate_mapping_ready(database_id: str, mapping: MappingProfile) -> None:
    if mapping.database_id != database_id:
        raise HarnessFailure(
            HarnessErrorCode.MAPPING_MISSING,
            f"mapping belongs to database '{mapping.database_id}', not '{database_id}'",
            stage=HarnessStage.CONTEXT_READY,
        )
    if mapping.status is not MappingStatus.PUBLISHED:
        raise HarnessFailure(
            HarnessErrorCode.MAPPING_NOT_PUBLISHED,
            f"mapping version {mapping.version} is {mapping.status.value}",
            stage=HarnessStage.CONTEXT_READY,
        )


def _validate_value_type(field: OntologyField, value: object) -> None:
    valid = False
    if field.data_type is DataType.STRING:
        valid = isinstance(value, str)
    elif field.data_type is DataType.NUMBER:
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif field.data_type is DataType.INTEGER:
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif field.data_type is DataType.BOOLEAN:
        valid = isinstance(value, bool)
    elif field.data_type in {DataType.DATE, DataType.TIME}:
        valid = isinstance(value, str)

    if not valid:
        raise HarnessFailure(
            HarnessErrorCode.INVALID_VALUE_TYPE,
            f"value for '{field.name}' must be {field.data_type.value}",
            stage=HarnessStage.IR_VALIDATED,
        )


def validate_query_ir(
    query_ir: QueryIR,
    ontology: Ontology,
    mapping: MappingProfile,
) -> None:
    if query_ir.entity not in ontology.entities:
        raise HarnessFailure(
            HarnessErrorCode.UNKNOWN_ENTITY,
            f"unknown ontology entity '{query_ir.entity}'",
            stage=HarnessStage.IR_VALIDATED,
        )

    referenced_fields = [*query_ir.select, *(item.field for item in query_ir.filters)]
    for field_name in referenced_fields:
        field = ontology.fields.get(field_name)
        if field is None:
            raise HarnessFailure(
                HarnessErrorCode.UNKNOWN_FIELD,
                f"unknown ontology field '{field_name}'",
                stage=HarnessStage.IR_VALIDATED,
            )
        if field_name not in mapping.fields:
            raise HarnessFailure(
                HarnessErrorCode.MAPPING_MISSING,
                f"no published physical mapping for '{field_name}'",
                stage=HarnessStage.IR_VALIDATED,
            )

    for item in query_ir.filters:
        field = ontology.fields[item.field]
        if item.operator not in field.allowed_operators:
            raise HarnessFailure(
                HarnessErrorCode.INVALID_OPERATOR,
                f"operator '{item.operator.value}' is not allowed for '{item.field}'",
                stage=HarnessStage.IR_VALIDATED,
            )
        if item.operator is Operator.CONTAINS and field.data_type is not DataType.STRING:
            raise HarnessFailure(
                HarnessErrorCode.INVALID_OPERATOR,
                f"operator 'contains' requires a string field, got '{item.field}'",
                stage=HarnessStage.IR_VALIDATED,
            )
        _validate_value_type(field, item.value)
