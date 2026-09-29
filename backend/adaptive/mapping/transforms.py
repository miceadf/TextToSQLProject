"""Declared value transforms for published mapping fields (Phase 7-12).

A transform bridges a representation difference between a physical column
and its canonical field (for example credits stored as ``"3/3/0"`` text).
The set is closed and every SQL expression comes from a fixed template in
this module: no model, request, or profile can supply expression text.
Only the PostgreSQL runtime uses transforms today.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Any

from ..ontology.contracts import DataType, Operator

__all__ = [
    "FieldTransform",
    "TRANSFORM_OPERATORS",
    "column_type_supports",
    "normalize_transformed_value",
    "render_postgres_transform",
    "transform_supports_data_type",
]


class FieldTransform(str, Enum):
    # "3/3/0" (credits/lecture/lab) -> 3 as NUMERIC.
    SLASH_FIRST_NUMERIC = "SLASH_FIRST_NUMERIC"
    # TIME -> "HH:MM" text, matching the canonical time format.
    TIME_TO_HHMM = "TIME_TO_HHMM"


_POSTGRES_TEMPLATES = {
    FieldTransform.SLASH_FIRST_NUMERIC: "CAST(SPLIT_PART({column}, '/', 1) AS NUMERIC)",
    FieldTransform.TIME_TO_HHMM: "TO_CHAR({column}, 'HH24:MI')",
}

_DATA_TYPES = {
    FieldTransform.SLASH_FIRST_NUMERIC: frozenset({DataType.NUMBER}),
    FieldTransform.TIME_TO_HHMM: frozenset({DataType.TIME}),
}

# Transformed values are compared, never pattern-matched.
TRANSFORM_OPERATORS = frozenset(
    {
        Operator.EQUALS,
        Operator.NOT_EQUALS,
        Operator.GREATER_THAN,
        Operator.GREATER_THAN_OR_EQUAL,
        Operator.LESS_THAN,
        Operator.LESS_THAN_OR_EQUAL,
    }
)


def render_postgres_transform(transform: FieldTransform, qualified_column: str) -> str:
    """Apply the fixed template to an already quoted column reference."""
    return _POSTGRES_TEMPLATES[FieldTransform(transform)].format(column=qualified_column)


def transform_supports_data_type(transform: FieldTransform, data_type: DataType) -> bool:
    return data_type in _DATA_TYPES[FieldTransform(transform)]


def column_type_supports(transform: FieldTransform, declared_type: str) -> bool:
    """Check the physical declared type the template expects."""
    normalized = (declared_type or "").strip().lower()
    transform = FieldTransform(transform)
    if transform is FieldTransform.SLASH_FIRST_NUMERIC:
        return normalized.startswith(("character varying", "varchar", "text", "character", "char"))
    if transform is FieldTransform.TIME_TO_HHMM:
        return normalized.startswith("time ") or normalized == "time"
    return False


def normalize_transformed_value(transform: FieldTransform, value: Any) -> Any:
    """Turn driver values of a transformed field into JSON-safe canonical values."""
    if value is None:
        return None
    if FieldTransform(transform) is FieldTransform.SLASH_FIRST_NUMERIC and isinstance(value, Decimal):
        # NUMERIC accepts 'NaN'/'Infinity' text, which JSON cannot carry.
        if not value.is_finite():
            return None
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    return value
