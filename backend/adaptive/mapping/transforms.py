"""Declared value transforms for published mapping fields (Phase 7-12).

A transform bridges a representation difference between a physical column
and its canonical field (for example credits stored as ``"3/3/0"`` text).
The set is closed and every SQL expression comes from a fixed template in
this module: no model, request, or profile can supply expression text.
Only the PostgreSQL runtime uses transforms today.
"""

from __future__ import annotations

import re
from decimal import Decimal
from enum import Enum
from typing import Any

from ..ontology.contracts import DataType, Operator

__all__ = [
    "FieldTransform",
    "TRANSFORM_OPERATORS",
    "column_type_supports",
    "normalize_filter_value",
    "normalize_transformed_value",
    "render_postgres_transform",
    "transform_operators",
    "transform_supports_data_type",
]


class FieldTransform(str, Enum):
    # "3/3/0" (credits/lecture/lab) -> 3 as NUMERIC.
    SLASH_FIRST_NUMERIC = "SLASH_FIRST_NUMERIC"
    # TIME -> "HH:MM" text, matching the canonical time format.
    TIME_TO_HHMM = "TIME_TO_HHMM"
    # Single-character Korean weekday column ("월"); filter values such as
    # "월요일" or "Mon" are normalized to it (Phase 7-16).
    KOREAN_WEEKDAY = "KOREAN_WEEKDAY"
    # Four-digit text time ("0900") -> "HH:MM" text (Phase 8-17); comparing
    # "1230" with "12:00" as raw text would misorder them.
    HHMM_TEXT_TO_HHMM = "HHMM_TEXT_TO_HHMM"
    # Phase 8-21: a whole timetable in one text column ("월 09:00-10:15, 수 ...");
    # a weekday is found by containment ("월요일" -> contains "월").
    PACKED_WEEKDAY = "PACKED_WEEKDAY"


_POSTGRES_TEMPLATES = {
    FieldTransform.SLASH_FIRST_NUMERIC: "CAST(SPLIT_PART({column}, '/', 1) AS NUMERIC)",
    FieldTransform.TIME_TO_HHMM: "TO_CHAR({column}, 'HH24:MI')",
    # The column already holds the canonical form; only filter values change.
    FieldTransform.KOREAN_WEEKDAY: "{column}",
    FieldTransform.HHMM_TEXT_TO_HHMM: "(SUBSTRING({column} FROM 1 FOR 2) || ':' || SUBSTRING({column} FROM 3 FOR 2))",
    FieldTransform.PACKED_WEEKDAY: "{column}",
}

_DATA_TYPES = {
    FieldTransform.SLASH_FIRST_NUMERIC: frozenset({DataType.NUMBER}),
    FieldTransform.TIME_TO_HHMM: frozenset({DataType.TIME}),
    FieldTransform.KOREAN_WEEKDAY: frozenset({DataType.STRING}),
    FieldTransform.HHMM_TEXT_TO_HHMM: frozenset({DataType.TIME}),
    FieldTransform.PACKED_WEEKDAY: frozenset({DataType.STRING}),
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


_TRANSFORM_OPERATORS = {
    FieldTransform.SLASH_FIRST_NUMERIC: TRANSFORM_OPERATORS,
    FieldTransform.TIME_TO_HHMM: TRANSFORM_OPERATORS,
    FieldTransform.KOREAN_WEEKDAY: frozenset(
        {Operator.EQUALS, Operator.NOT_EQUALS, Operator.CONTAINS}
    ),
    FieldTransform.HHMM_TEXT_TO_HHMM: TRANSFORM_OPERATORS,
    # Only containment means something inside a packed timetable.
    FieldTransform.PACKED_WEEKDAY: frozenset({Operator.CONTAINS}),
}


def transform_operators(transform: FieldTransform) -> frozenset[Operator]:
    """Operators a transformed field accepts."""
    return _TRANSFORM_OPERATORS[FieldTransform(transform)]


def render_postgres_transform(transform: FieldTransform, qualified_column: str) -> str:
    """Apply the fixed template to an already quoted column reference."""
    return _POSTGRES_TEMPLATES[FieldTransform(transform)].format(column=qualified_column)


def transform_supports_data_type(transform: FieldTransform, data_type: DataType) -> bool:
    return data_type in _DATA_TYPES[FieldTransform(transform)]


def column_type_supports(transform: FieldTransform, declared_type: str) -> bool:
    """Check the physical declared type the template expects."""
    normalized = (declared_type or "").strip().lower()
    transform = FieldTransform(transform)
    if transform in (
        FieldTransform.SLASH_FIRST_NUMERIC,
        FieldTransform.KOREAN_WEEKDAY,
        FieldTransform.HHMM_TEXT_TO_HHMM,
        FieldTransform.PACKED_WEEKDAY,
    ):
        return normalized.startswith(("character varying", "varchar", "text", "character", "char"))
    if transform is FieldTransform.TIME_TO_HHMM:
        return normalized.startswith("time ") or normalized == "time"
    return False


_TIME_TEXT = re.compile(r"^(\d{1,2}):(\d{2})(?::00)?$")

_KOREAN_WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")
_ENGLISH_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_ENGLISH_WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_WEEKDAY_ALIASES = {
    **{day: day for day in _KOREAN_WEEKDAYS},
    **{day + "요일": day for day in _KOREAN_WEEKDAYS},
    **dict(zip(_ENGLISH_WEEKDAYS, _KOREAN_WEEKDAYS)),
    **dict(zip(_ENGLISH_WEEKDAY_NAMES, _KOREAN_WEEKDAYS)),
}


def normalize_filter_value(transform: FieldTransform, value: Any) -> Any:
    """Bring a filter literal into the transform's output format.

    ``TIME_TO_HHMM`` compares zero-padded ``HH:MM`` text, so ``"9:00"`` and
    ``"14:00:00"`` must become ``"09:00"`` and ``"14:00"``; otherwise the
    string comparison silently misorders them. A value minute text cannot
    represent (non-zero seconds, ``"오후"``, ``"25:00"``) raises ``ValueError``
    instead of being compared as arbitrary text.

    ``KOREAN_WEEKDAY`` maps ``"월요일"``, ``"Mon"``, ``"monday"`` and ``"월"``
    to ``"월"``; any other value (``"주말"``) raises ``ValueError``.
    """
    if FieldTransform(transform) in (FieldTransform.KOREAN_WEEKDAY, FieldTransform.PACKED_WEEKDAY):
        day = _WEEKDAY_ALIASES.get(value.strip().lower()) if isinstance(value, str) else None
        if day is None:
            raise ValueError("weekday filter value is not a weekday")
        return day
    if FieldTransform(transform) in (FieldTransform.TIME_TO_HHMM, FieldTransform.HHMM_TEXT_TO_HHMM):
        match = _TIME_TEXT.fullmatch(value.strip()) if isinstance(value, str) else None
        if match is None:
            raise ValueError("time filter value is not HH:MM")
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            raise ValueError("time filter value is out of range")
        return f"{hour:02d}:{minute:02d}"
    return value


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
