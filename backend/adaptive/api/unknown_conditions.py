"""Conditions a database cannot answer (Phase 8-20).

Two closed, deterministic checks around the single model call:

- *Outside concepts*: the question names a concept no common field holds
  ("강의실") and the database declares no extension field for it. The chat
  answers UNSUPPORTED instead of running whatever field the model stuffed
  the condition into. A word that is only part of a longer value the model
  or the value hints found ("건물에너지" holds "건물") does not count.
- *Empty-result diagnosis* (in ``chat_query``): a query returned nothing,
  so each condition runs alone to name the one that matches no row.

Neither check adds a condition or calls a model.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

from ..ontology.outside_concepts import OutsideConcept, OutsideConcepts, concept_key

__all__ = ["Mention", "find_mentions", "mentioned_fields", "outside_concepts", "uncovered"]


@dataclass(frozen=True)
class Mention:
    """One occurrence, located in the question without whitespace (as a key)."""

    concept: OutsideConcept
    key: str  # the matched words, as a key
    start: int
    end: int
    pattern: bool  # matched by a pattern, not an alias


def _compact(question: str) -> tuple[str, str, list[int]]:
    """The normalized question, the same without whitespace, and where each
    normalized character lands in the compact text."""
    text = unicodedata.normalize("NFKC", question or "").casefold()
    landing = []
    count = 0
    for char in text:
        landing.append(count)
        if not char.isspace():
            count += 1
    landing.append(count)
    return text, "".join(text.split()), landing


def find_mentions(question: str, concepts: OutsideConcepts) -> list[Mention]:
    """Every alias or pattern occurrence. Aliases and patterns both ignore
    whitespace ("온라인 강의", "3 0 5호"); patterns also run on the spaced text."""
    text, compact, landing = _compact(question)
    mentions: list[Mention] = []
    for concept in concepts.concepts:
        for alias in concept.aliases:
            key = concept_key(alias)
            found = compact.find(key)
            while found >= 0:
                mentions.append(Mention(concept, key, found, found + len(key), pattern=False))
                found = compact.find(key, found + 1)
        spans: set[tuple[int, int]] = set()
        for pattern in concept.compiled_patterns():
            spans.update(match.span() for match in pattern.finditer(compact))
            spans.update(
                (landing[match.start()], landing[match.end()]) for match in pattern.finditer(text)
            )
        for start, end in sorted(spans):
            if end > start:
                mentions.append(Mention(concept, compact[start:end], start, end, pattern=True))
    return mentions


def uncovered(concepts: OutsideConcepts, extensions: dict[str, Any]) -> list[OutsideConcept]:
    """Concepts no extension field of this database covers.

    An extension covers a concept when its key is the concept key, or its
    label is, or contains, the concept's label or an alias.
    """
    declared = []
    for name, extension in extensions.items():
        attribute = name.split(".", 1)[-1]
        key = attribute[4:] if attribute.startswith("ext_") else attribute
        declared.append((key, concept_key(str(getattr(extension, "label", "")))))
    result = []
    for concept in concepts.concepts:
        names = concept.names()
        if any(
            key == concept.key or any(name in label for name in names) for key, label in declared
        ):
            continue
        result.append(concept)
    return result


def _part_of_value(mention: Mention, compact: str, values: Iterable[str]) -> bool:
    """The match lies inside a longer value written at that place in the question."""
    for value in values:
        if len(value) <= len(mention.key) or mention.key not in value:
            continue
        found = compact.find(value)
        while found >= 0:
            if found <= mention.start and mention.end <= found + len(value):
                return True
            found = compact.find(value, found + 1)
    return False


def outside_concepts(
    question: str,
    concepts: OutsideConcepts,
    *,
    extensions: dict[str, Any],
    filter_values: Iterable[Any] = (),
    hint_texts: Iterable[str] = (),
) -> list[OutsideConcept]:
    """Concepts the question names that this database cannot answer.

    A pattern match (a room number, say) is excused only by the database's
    own values (hints); the model putting it into some text field is the
    failure this guards against.
    """
    candidates = {concept.key for concept in uncovered(concepts, extensions)}
    if not candidates:
        return []
    model_values = [concept_key(value) for value in filter_values if isinstance(value, str)]
    hinted = [concept_key(text) for text in hint_texts if isinstance(text, str)]
    _text, compact, _landing = _compact(question)
    named: set[str] = set()
    for mention in find_mentions(question, concepts):
        if mention.concept.key not in candidates or mention.concept.key in named:
            continue
        excused = _part_of_value(mention, compact, hinted) or (
            not mention.pattern and _part_of_value(mention, compact, model_values)
        )
        if not excused:
            named.add(mention.concept.key)
    return [concept for concept in concepts.concepts if concept.key in named]


def output_filter_values(output: Any) -> list[str]:
    """String values of the model's filters, read leniently (the output may be invalid)."""
    if not isinstance(output, dict) or not isinstance(output.get("filters"), list):
        return []
    return [
        item["value"]
        for item in output["filters"]
        if isinstance(item, dict) and isinstance(item.get("value"), str)
    ]


def mentioned_fields(question: str, ontology: Any, fields: Iterable[str]) -> list[str]:
    """The fields whose label or an alias the question names."""
    compact = concept_key(question or "")
    by_name = {field.name: field for entity in ontology.entities for field in entity.fields}
    result = []
    for name in fields:
        field = by_name.get(name)
        if field is None:
            continue
        words = {concept_key(field.label), *(concept_key(alias) for alias in field.aliases)}
        if any(len(word) >= 2 and word in compact for word in words):
            result.append(name)
    return result
