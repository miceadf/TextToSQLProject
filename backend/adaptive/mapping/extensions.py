"""Per-database extension fields (Phase 8-19).

An administrator may declare, for one registered database, a field the
common ontology does not have (a classroom, say). It is data, not code: a
name ``<Entity>.ext_<key>``, a label, and one of three value types. The
runtime treats it like a common field: QueryIR validation, the compiler,
the SQL policy and the read-only executor all apply unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..ontology.contracts import DataType, Operator

MAX_EXTENSIONS = 10
MAX_LABEL_LENGTH = 20
ENTITIES = ("Course", "Department", "Instructor", "CourseOffering", "Schedule")
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
NAME_PATTERN = re.compile(r"^(" + "|".join(ENTITIES) + r")\.ext_([a-z][a-z0-9_]{0,30})$")
DATA_TYPES: dict[str, DataType] = {
    "string": DataType.STRING,
    "integer": DataType.INTEGER,
    "number": DataType.NUMBER,
}
_COMPARISONS = frozenset(
    {
        Operator.EQUALS,
        Operator.GREATER_THAN,
        Operator.GREATER_THAN_OR_EQUAL,
        Operator.LESS_THAN,
        Operator.LESS_THAN_OR_EQUAL,
    }
)
_TEXT_TYPE = re.compile(r"^(?:character varying|varchar|character|char|bpchar|text)(?:\(\d{1,5}\))?$")
_INTEGER_TYPES = frozenset({"integer", "smallint", "bigint", "int", "int2", "int4", "int8"})
_NUMBER_TYPE = re.compile(r"^(?:numeric|decimal)(?:\(\d{1,4}(?:,\d{1,4})?\))?$|^(?:real|double precision)$")


@dataclass(frozen=True)
class ExtensionField:
    label: str
    data_type: str  # a key of DATA_TYPES


def extension_name(entity: str, key: str) -> str:
    return f"{entity}.ext_{key}"


def dto_key(name: str) -> str:
    """Result-card key of an extension field (``Schedule.ext_room`` -> ``ext_room``)."""
    return name.split(".", 1)[1]


def valid_label(label: Any) -> bool:
    return (
        isinstance(label, str)
        and 1 <= len(label) <= MAX_LABEL_LENGTH
        and label == label.strip()
        and all(ch.isprintable() for ch in label)
    )


def valid_extension(name: Any, field: Any) -> bool:
    return (
        isinstance(name, str)
        and NAME_PATTERN.fullmatch(name) is not None
        and isinstance(field, ExtensionField)
        and valid_label(field.label)
        and field.data_type in DATA_TYPES
    )


def operators_for(data_type: str) -> frozenset[Operator]:
    if DATA_TYPES[data_type] is DataType.STRING:
        return frozenset({Operator.EQUALS, Operator.CONTAINS})
    return _COMPARISONS


def column_fits(data_type: str, declared_type: str) -> bool:
    """Whether a PostgreSQL declared type stores this kind of value."""
    declared = (declared_type or "").strip().lower()
    if data_type == "string":
        return _TEXT_TYPE.fullmatch(declared) is not None
    if data_type == "integer":
        return declared in _INTEGER_TYPES
    if data_type == "number":
        return declared in _INTEGER_TYPES or _NUMBER_TYPE.fullmatch(declared) is not None
    return False
