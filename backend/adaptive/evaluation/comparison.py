"""Typed, order-insensitive semantic row comparison with multiplicity."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from ..ontology.contracts import DataType, DomainOntology


def semantic_row_multiset(
    rows: Sequence[Mapping[str, Any]],
    selected_fields: Sequence[str],
    ontology: DomainOntology,
) -> Counter[tuple[tuple[str, str, str], ...]]:
    """Normalize only ontology NUMBER's 3 versus 3.0; preserve row duplicates."""

    selected = list(selected_fields)
    if not selected or len(selected) != len(set(selected)):
        raise ValueError("selected semantic fields must be non-empty and unique")
    result: Counter[tuple[tuple[str, str, str], ...]] = Counter()
    for row in rows:
        if set(row) != set(selected):
            raise ValueError("semantic row fields differ from QueryIR selection")
        encoded: list[tuple[str, str, str]] = []
        for field in selected:
            definition = ontology.fields.get(field)
            if definition is None:
                raise ValueError("semantic row references an unknown ontology field")
            value = row[field]
            kind = definition.data_type
            if kind in {DataType.STRING, DataType.DATE, DataType.TIME}:
                if not isinstance(value, str):
                    raise ValueError("semantic string value has the wrong type")
                scalar = value
            elif kind is DataType.BOOLEAN:
                if not isinstance(value, bool):
                    raise ValueError("semantic boolean value has the wrong type")
                scalar = "true" if value else "false"
            elif kind is DataType.INTEGER:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError("semantic integer value has the wrong type")
                scalar = str(value)
            elif kind is DataType.NUMBER:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError("semantic number value has the wrong type")
                try:
                    number = Decimal(str(value))
                except InvalidOperation:
                    raise ValueError("semantic number is invalid") from None
                if not number.is_finite():
                    raise ValueError("semantic number must be finite")
                scalar = str(number.normalize())
            else:
                raise ValueError("unsupported semantic data type")
            encoded.append((field, kind.value, scalar))
        result[tuple(encoded)] += 1
    return result
