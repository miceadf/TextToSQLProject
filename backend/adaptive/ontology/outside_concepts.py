"""Course-domain concepts outside the common ontology (Phase 8-20).

A user may ask about information no common field holds ("강의실",
"수업 방식"). The concepts are data (``outside-concepts.yaml``): a key, a
label for the answer, the words that name them, and optional patterns.
The chat answers "this database has no such information" for them unless
the database declares an extension field covering the concept.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .contracts import DomainOntology

KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MIN_ALIAS_LENGTH = 2

__all__ = [
    "OutsideConcept",
    "OutsideConcepts",
    "concept_key",
    "default_outside_concepts",
    "load_outside_concepts",
    "ontology_overlaps",
]


def concept_key(text: str) -> str:
    """NFKC, case-folded, without whitespace (the same key as value lookups)."""
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


class OutsideConcept(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(pattern=KEY_PATTERN.pattern)
    label: str = Field(min_length=1, max_length=20)
    aliases: tuple[str, ...] = Field(min_length=1, max_length=30)
    patterns: tuple[str, ...] = Field(default=(), max_length=5)

    @field_validator("aliases")
    @classmethod
    def _aliases(cls, aliases: tuple[str, ...]) -> tuple[str, ...]:
        keys = [concept_key(alias) for alias in aliases]
        if any(len(key) < MIN_ALIAS_LENGTH or len(key) > 20 for key in keys):
            raise ValueError("alias length")
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate alias")
        return aliases

    @field_validator("patterns")
    @classmethod
    def _patterns(cls, patterns: tuple[str, ...]) -> tuple[str, ...]:
        for pattern in patterns:
            if not pattern or len(pattern) > 200:
                raise ValueError("pattern length")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError("invalid pattern") from exc
        return patterns

    def names(self) -> frozenset[str]:
        """Keys of the label and every alias."""
        return frozenset({concept_key(self.label), *(concept_key(alias) for alias in self.aliases)})

    def compiled_patterns(self) -> tuple[re.Pattern[str], ...]:
        return tuple(re.compile(pattern) for pattern in self.patterns)


class OutsideConcepts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    concepts: tuple[OutsideConcept, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _unique(self) -> "OutsideConcepts":
        keys = [concept.key for concept in self.concepts]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate concept key")
        seen: set[str] = set()
        for concept in self.concepts:
            names = concept.names()
            if names & seen:
                raise ValueError("a word names two concepts")
            seen |= names
        return self


def ontology_overlaps(concepts: OutsideConcepts, ontology: DomainOntology) -> list[str]:
    """Concept words that would also fire on a common entity or field name.

    A concept word may contain a common word ("강의실" holds "강의"), but no
    common label or alias may contain a concept word: asking about the
    common field would then read as the outside concept.
    """
    common: set[str] = set()
    for entity in ontology.entities:
        common.add(concept_key(entity.label))
        common.update(concept_key(alias) for alias in entity.aliases)
        for field in entity.fields:
            common.add(concept_key(field.label))
            common.update(concept_key(alias) for alias in field.aliases)
    return sorted(
        name
        for concept in concepts.concepts
        for name in concept.names()
        if any(name in word for word in common)
    )


def default_outside_concepts_path() -> Path:
    return Path(__file__).with_name("outside-concepts.yaml")


def load_outside_concepts(path: str | Path) -> OutsideConcepts:
    with Path(path).open("r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    return OutsideConcepts.model_validate(data)


@lru_cache(maxsize=1)
def default_outside_concepts() -> OutsideConcepts:
    """The shipped list, refused when it overlaps the common ontology."""
    from .loader import load_default_ontology

    concepts = load_outside_concepts(default_outside_concepts_path())
    if ontology_overlaps(concepts, load_default_ontology()):
        raise ValueError("outside concepts overlap the common ontology")
    return concepts
