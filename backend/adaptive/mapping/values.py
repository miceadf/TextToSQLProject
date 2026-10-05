"""Reviewed per-field value dictionaries (Phase 8-06).

The same meaning is stored differently per database: ``전공(핵심)`` in one,
``01`` in another. Closed-list transforms handle regular formats (credits,
times, weekdays); a value list that differs per database needs a reviewed
lookup table instead. A dictionary maps every spelling a user may type onto
one stored value, and maps stored values back to a display label.

Only whole values are looked up; there is no substring rewriting.
Phase 8-21: a ``contains`` condition also matches the coded stored values
whose label holds the word (``교양`` → ``21``, ``22``), because a coded
column cannot hold the label's words.
"""

from __future__ import annotations

import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..ontology.contracts import Operator
from .transforms import FieldTransform, normalize_filter_value

MAX_ENTRIES = 64
MAX_ALIASES = 16
MAX_VALUE_LENGTH = 100

__all__ = [
    "ValueDictionary",
    "ValueEntry",
    "bind_filter_value",
    "contains_label_values",
    "lookup_key",
]


def lookup_key(value: str) -> str:
    """NFKC, case-folded, with all whitespace removed."""
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


class ValueEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stored: str = Field(min_length=1, max_length=MAX_VALUE_LENGTH)
    label: str = Field(min_length=1, max_length=MAX_VALUE_LENGTH)
    aliases: tuple[str, ...] = Field(default=(), max_length=MAX_ALIASES)

    @model_validator(mode="after")
    def validate_text(self) -> "ValueEntry":
        for text in (self.stored, self.label, *self.aliases):
            if not text.strip() or len(text) > MAX_VALUE_LENGTH:
                raise ValueError("dictionary values must be non-blank and bounded")
            # Cc: control characters; Cf: invisible format characters such as
            # bidirectional overrides that can disguise what is displayed.
            if any(unicodedata.category(char) in ("Cc", "Cf") for char in text):
                raise ValueError("dictionary values must not contain control or format characters")
        return self


class ValueDictionary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entries: tuple[ValueEntry, ...] = Field(min_length=1, max_length=MAX_ENTRIES)
    unknown: Literal["pass", "reject"] = "pass"
    reviewer: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_keys(self) -> "ValueDictionary":
        stored = [entry.stored for entry in self.entries]
        if len(stored) != len(set(stored)):
            raise ValueError("stored values must be unique")
        keys: dict[str, str] = {}
        for entry in self.entries:
            for text in (entry.stored, entry.label, *entry.aliases):
                key = lookup_key(text)
                if not key:
                    raise ValueError("dictionary spellings must not be blank")
                if keys.setdefault(key, entry.stored) != entry.stored:
                    raise ValueError("one spelling must lead to exactly one stored value")
        return self

    def _keys(self) -> dict[str, str]:
        return {
            lookup_key(text): entry.stored
            for entry in self.entries
            for text in (entry.stored, entry.label, *entry.aliases)
        }

    def knows(self, value: Any) -> bool:
        """True when the whole value is one of the declared spellings."""
        return isinstance(value, str) and lookup_key(value) in self._keys()

    def resolve(self, value: Any) -> Any:
        """Stored value for a whole-value match; ``ValueError`` if rejected."""
        if isinstance(value, str):
            stored = self._keys().get(lookup_key(value))
            if stored is not None:
                return stored
        if self.unknown == "reject":
            raise ValueError("value is not in the dictionary")
        return value

    def containing(self, value: Any) -> tuple[str, ...]:
        """Coded stored values whose label holds the value (Phase 8-21).

        Only entries whose label differs from the stored value count: a stored
        name is already found by ``LIKE``, so databases that store names keep
        exactly their previous SQL. Aliases are whole spellings, never searched
        by parts.
        """
        if not isinstance(value, str):
            return ()
        key = lookup_key(value)
        if not key:
            return ()
        return tuple(
            sorted(
                entry.stored
                for entry in self.entries
                if entry.label != entry.stored and key in lookup_key(entry.label)
            )
        )

    def label_for(self, stored: Any) -> Any:
        """Display label for a stored value; other values unchanged."""
        if isinstance(stored, str):
            for entry in self.entries:
                if entry.stored == stored:
                    return entry.label
        return stored


def bind_filter_value(
    field: str,
    value: Any,
    *,
    field_transforms: dict[str, FieldTransform],
    value_dictionaries: dict[str, ValueDictionary],
) -> Any:
    """The value the compiler binds and the policy expects (before ``contains`` wrapping).

    A field has at most one of a transform and a dictionary. Raises
    ``ValueError`` when the value cannot be represented.
    """
    transform = field_transforms.get(field)
    if transform is not None:
        return normalize_filter_value(transform, value)
    dictionary = value_dictionaries.get(field)
    if dictionary is not None:
        return dictionary.resolve(value)
    return value


def contains_label_values(
    field: str,
    operator: Any,
    value: Any,
    *,
    value_dictionaries: dict[str, ValueDictionary],
) -> tuple[str, ...]:
    """Stored values a ``contains`` condition also matches through labels (Phase 8-21).

    Empty unless the field has a dictionary, the operator is ``contains`` and
    the value is not itself a declared spelling (that one binds exactly).
    The compiler and the SQL policy both call this, so they agree on the
    predicate ``(column LIKE pattern OR column IN (values))``.
    """
    dictionary = value_dictionaries.get(field)
    if dictionary is None or operator is not Operator.CONTAINS or dictionary.knows(value):
        return ()
    return dictionary.containing(value)
