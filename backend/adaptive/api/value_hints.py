"""Chat value hints: read a database's own values, ground the question (Phase 8-13).

The chat model sees field names only, so it cannot tell that a word in the
question names a department or that an abbreviation stands for one. This module gives it the same kind
of help the legacy pipeline had, without tying code to one database:

- the *mechanism* is generic: values come from whichever database is
  registered (bounded distinct values of low-cardinality text fields), and
  the question is matched against whole values, word pieces of values, the
  database's own dictionary aliases, and near-miss spellings;
- the *knowledge* is data: abbreviations live in that database's value
  dictionaries (Phase 8-06), never in this code.

Hints go into the single model call. The model output is then repaired by
closed, ontology-driven rules (an invented ``Course.department`` becomes
``Department.name``; a value that exists only in another hinted field moves
there) and still passes the unchanged QueryIR validation, compiler, policy
and executor. No condition is ever added here (Phase 8-25 time words, read by
closed rules in ``time_hints``, are the one exception).
"""

from __future__ import annotations

import difflib
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from ..mapping.values import ValueDictionary, lookup_key
from ..ontology.contracts import DataType, DomainOntology

MAX_INDEXED_VALUES = 500  # a field with more distinct values is not indexed
MAX_HINTS = 8
MIN_KEY_LENGTH = 2
FUZZY_MIN_LENGTH = 4
FUZZY_RATIO = 0.85
MAX_RUN_WORDS = 3
MAX_HINT_TEXT = 200  # the prompt refuses longer hint strings
RETRY_SECONDS = 60.0  # an index built while a read failed is rebuilt after this
IDENTITY_FIELDS = frozenset({"Course.code", "CourseOffering.section", "Department.code"})

# Common Korean particles and endings, longest first (language, not data).
PARTICLES = (
    "으로부터", "에서부터", "에서는", "에서의", "으로는", "이라는", "에게서",
    "부터", "까지", "에서", "으로", "에게", "한테", "처럼", "보다", "이나", "이랑", "하고", "이고",
    "은", "는", "이", "가", "을", "를", "의", "에", "로", "와", "과", "도", "만", "랑", "나",
)
_WORD = re.compile(r"[^\W_]+")

__all__ = [
    "ChatValueIndexes",
    "ValueHint",
    "ValueIndex",
    "hint_fields",
    "repair_output",
]


@dataclass(frozen=True)
class ValueHint:
    text: str
    field: str
    value: str
    fuzzy: bool = False

    def payload(self) -> dict[str, str]:
        return {"text": self.text, "field": self.field, "value": self.value}


def _variants(word: str) -> list[str]:
    """The word, then the word without one trailing particle."""
    variants = [word]
    for particle in PARTICLES:
        if word.endswith(particle) and len(word) - len(particle) >= MIN_KEY_LENGTH:
            variants.append(word[: -len(particle)])
            break
    return variants


def _shared_prefixes(dictionary: ValueDictionary) -> dict[str, set[str]]:
    """Phase 8-21: the start shared by two or more one-word labels names their
    group (``전공`` for ``전공필수`` and ``전공선택``) when the column stores codes.
    Stored names already give their word pieces, so only coded entries count."""
    labels = [
        (lookup_key(entry.label), entry.stored)
        for entry in dictionary.entries
        if len(entry.label.split()) == 1 and entry.label != entry.stored
    ]
    shared: dict[str, set[str]] = {}
    for key, stored in labels:
        for end in range(MIN_KEY_LENGTH, len(key)):
            shared.setdefault(key[:end], set()).add(stored)
    return {prefix: stored for prefix, stored in shared.items() if len(stored) >= 2}


class ValueIndex:
    """Match keys of one database's indexed values (immutable after build)."""

    def __init__(
        self,
        values: dict[str, tuple[str, ...]],
        dictionaries: dict[str, ValueDictionary] | None = None,
        *,
        nouns: frozenset[str] = frozenset(),
    ) -> None:
        self._values = {field: tuple(items) for field, items in values.items()}
        self._dictionaries = dict(dictionaries or {})
        self._nouns = frozenset(lookup_key(noun) for noun in nouns if noun)
        # field -> key -> (display text, stored values, piece only)
        self._keys: dict[str, dict[str, tuple[str, set[str], bool]]] = {}
        for field, items in self._values.items():
            for stored in items:
                self._add(field, stored, stored, piece=False)
                words = _WORD.findall(stored)
                for start in range(len(words)):
                    for end in range(start + 1, len(words) + 1):
                        if end - start == len(words):
                            continue
                        self._add(field, " ".join(words[start:end]), stored, piece=True)
        for field, dictionary in self._dictionaries.items():
            for entry in dictionary.entries:
                for text in (entry.stored, entry.label, *entry.aliases):
                    self._add(field, text, entry.stored, piece=False, display=entry.stored)
            for prefix, stored in _shared_prefixes(dictionary).items():
                for value in stored:
                    self._add(field, prefix, value, piece=True, display=prefix)

    def _add(self, field: str, text: str, stored: str, *, piece: bool, display: str | None = None) -> None:
        key = lookup_key(text)
        if len(key) < MIN_KEY_LENGTH:
            return
        keys = self._keys.setdefault(field, {})
        shown, targets, piece_only = keys.get(key, (display or text, set(), True))
        targets.add(stored)
        keys[key] = (display or shown, targets, piece_only and piece)

    @property
    def fields(self) -> frozenset[str]:
        return frozenset(self._keys)

    def has_values(self, field: str) -> bool:
        return field in self._values

    def contains(self, field: str, value: Any) -> bool:
        """True when some stored value (or dictionary spelling) holds the value."""
        if not isinstance(value, str):
            return False
        key = lookup_key(value)
        dictionary = self._dictionaries.get(field)
        if dictionary is not None and dictionary.knows(value):
            return True
        return any(key in lookup_key(stored) for stored in self._values.get(field, ()))

    def _match(self, key: str) -> dict[str, tuple[str, set[str], bool]]:
        return {field: keys[key] for field, keys in self._keys.items() if key in keys}

    def _hints(
        self, text: str, matches: dict[str, tuple[str, set[str], bool]], fuzzy: bool = False
    ) -> list[ValueHint]:
        """A whole value or alias names its stored value; a word piece stays a
        piece, so the containment search finds every value holding it."""
        hints = []
        for field in sorted(matches):
            shown, stored, piece_only = matches[field]
            value = next(iter(stored)) if len(stored) == 1 and not piece_only else shown
            hints.append(ValueHint(text=text, field=field, value=value, fuzzy=fuzzy))
        return hints

    def _fuzzy(self, word: str) -> list[ValueHint]:
        key = lookup_key(word)
        if len(key) < FUZZY_MIN_LENGTH:
            return []
        scored: list[tuple[float, str, str]] = []
        for field, keys in self._keys.items():
            for candidate in keys:
                if len(candidate) >= FUZZY_MIN_LENGTH:
                    ratio = difflib.SequenceMatcher(None, key, candidate).ratio()
                    if ratio >= FUZZY_RATIO:
                        scored.append((ratio, field, candidate))
        if not scored:
            return []
        best = max(score for score, _field, _key in scored)
        top = [(field, candidate) for score, field, candidate in scored if score == best]
        if len(top) != 1:
            return []  # a tie is ambiguous
        field, candidate = top[0]
        return self._hints(word, {field: self._keys[field][candidate]}, fuzzy=True)

    def _prefix_split(self, word: str) -> tuple[list[ValueHint], str] | None:
        """``전공과목`` → hint for ``전공`` when the rest is a domain noun or a value."""
        for cut in range(len(word) - MIN_KEY_LENGTH, MIN_KEY_LENGTH - 1, -1):
            head, rest = word[:cut], word[cut:]
            matches = self._match(lookup_key(head))
            if not matches:
                continue
            rest_keys = [lookup_key(variant) for variant in _variants(rest)]
            if any(k in self._nouns or self._match(k) for k in rest_keys):
                return self._hints(head, matches), rest
        return None

    def find(self, question: str, *, allowed_fields: set[str] | None = None) -> list[ValueHint]:
        words = _WORD.findall(question or "")
        hints: list[ValueHint] = []
        queue = list(words)
        index = 0
        while index < len(queue):
            matched = False
            for length in range(min(MAX_RUN_WORDS, len(queue) - index), 0, -1):
                head = queue[index : index + length - 1]
                for last in _variants(queue[index + length - 1]):
                    matches = self._match(lookup_key("".join([*head, last])))
                    if matches:
                        hints += self._hints(" ".join([*head, last]), matches)
                        index += length
                        matched = True
                        break
                if matched:
                    break
            if matched:
                continue
            word = queue[index]
            split = self._prefix_split(word)
            if split is not None:
                found, rest = split
                hints += found
                queue[index] = rest  # scan the rest as its own word
                continue
            if not any(lookup_key(v) in self._nouns for v in _variants(word)):
                for variant in _variants(word):
                    fuzzy = self._fuzzy(variant)
                    if fuzzy:
                        hints += fuzzy
                        break
            index += 1
        unique: list[ValueHint] = []
        seen: set[tuple[str, str]] = set()
        for hint in hints:
            if allowed_fields is not None and hint.field not in allowed_fields:
                continue
            if len(hint.text) > MAX_HINT_TEXT or len(hint.value) > MAX_HINT_TEXT:
                continue
            if (hint.field, hint.value) in seen:
                continue
            seen.add((hint.field, hint.value))
            unique.append(hint)
        return unique[:MAX_HINTS]


def domain_nouns(ontology: DomainOntology) -> frozenset[str]:
    """Entity labels and aliases ("과목", "강의", ...): words that end a value phrase."""
    nouns = set()
    for entity in ontology.entities:
        nouns.add(entity.label)
        nouns.update(entity.aliases)
    return frozenset(nouns)


def hint_fields(context: Any, ontology: DomainOntology) -> list[str]:
    """Mapped text fields without transforms that do not identify rows.

    Phase 8-19: a database's text extension fields count too.
    """
    mapped = context.published_mapping().fields
    transforms = context.field_transforms()
    fields = ontology.active_fields
    reader = getattr(context, "extension_fields", None)
    extensions = reader() if callable(reader) else {}
    return sorted(
        name
        for name in mapped
        if (
            (name in fields and fields[name].data_type is DataType.STRING)
            or (name in extensions and extensions[name].data_type == "string")
        )
        and name not in transforms
        and name not in IDENTITY_FIELDS
    )


class ChatValueIndexes:
    """Per-database value indexes, built on first use and cached by fingerprint."""

    def __init__(self, reader: Any, ontology: DomainOntology, *, clock: Any = time.monotonic) -> None:
        self._reader = reader
        self._ontology = ontology
        self._nouns = domain_nouns(ontology)
        self._clock = clock
        self._lock = threading.Lock()
        # key -> (index, rebuild after; None = complete, kept)
        self._cache: dict[tuple[str, str, int], tuple[ValueIndex, float | None]] = {}

    def forget(self, database_id: str) -> None:
        """Drop every cached version when a registration is deleted."""
        with self._lock:
            for key in [key for key in self._cache if key[0] == database_id]:
                del self._cache[key]

    def get(self, context: Any) -> ValueIndex | None:
        try:
            # Phase 8-20: a republish (new extension fields or dictionary
            # aliases) keeps the schema fingerprint, so the mapping version
            # is part of the key.
            key = (
                context.database_id,
                context.expected_fingerprint,
                int(context.published_mapping().version),
            )
        except Exception:
            return None
        with self._lock:
            for old in [item for item in self._cache if item[0] == key[0] and item != key]:
                del self._cache[old]  # an earlier version of this database is never asked again
            cached = self._cache.get(key)
            if cached is not None and (cached[1] is None or self._clock() < cached[1]):
                return cached[0]
            try:
                index, complete = self._build(context)
            except Exception:
                return None
            self._cache[key] = (index, None if complete else self._clock() + RETRY_SECONDS)
            return index

    def _build(self, context: Any) -> tuple[ValueIndex, bool]:
        """The index and whether every read answered (a failed read is retried later).

        Dictionary aliases are the database's own data and need no read, so
        they stay available while the values cannot be read.
        """
        mapping = context.published_mapping().fields
        dictionaries = context.value_dictionaries()
        values: dict[str, tuple[str, ...]] = {}
        complete = True
        for field in hint_fields(context, self._ontology):
            target = mapping[field]
            try:
                read = self._reader.read(
                    context.database_id, target.table, target.column, MAX_INDEXED_VALUES
                )
            except Exception:
                read = None
            if read is None:
                complete = False
            elif isinstance(read, (list, tuple)):
                values[field] = tuple(item for item in read if isinstance(item, str))
            # anything else (too many values) leaves the field out for good
        kept = {field: dictionary for field, dictionary in dictionaries.items() if field in mapping}
        return ValueIndex(values, kept, nouns=self._nouns), complete


# --- output repair ----------------------------------------------------------------------------


def _field_repairs(ontology: DomainOntology) -> dict[str, set[str]]:
    """Attribute word → ontology fields it can only mean (hints from the ontology)."""
    by_word: dict[str, set[str]] = {}
    for entity in ontology.at_version(ontology.version).entities:
        name_field = f"{entity.name}.name"
        for field in entity.fields:
            for hint in field.physical_name_hints:
                by_word.setdefault(hint.lower(), set()).add(field.name)
        if any(field.name == name_field for field in entity.fields):
            for hint in (*entity.table_name_hints, entity.name):
                by_word.setdefault(hint.lower(), set()).add(name_field)
    return by_word


def _repair_ref(ref: Any, known: set[str], by_word: dict[str, set[str]]) -> Any:
    if not isinstance(ref, str) or ref in known or "." not in ref:
        return ref
    attribute = ref.split(".", 1)[1].strip().lower()
    targets = by_word.get(attribute, set())
    return next(iter(targets)) if len(targets) == 1 else ref


def repair_output(
    output: Any,
    ontology: DomainOntology,
    *,
    index: ValueIndex | None = None,
    hints: list[ValueHint] | None = None,
    available_fields: list[str] | None = None,
) -> Any:
    """Closed repairs before QueryIR validation; anything unexpected is left as is.

    Phase 8-07: a select wildcard (``Schedule.*``) of a known entity means
    "every column"; the chat replaces ``select`` with its fixed card anyway,
    so wildcards are dropped (an empty select falls back to the first
    available field).
    """
    if not isinstance(output, dict):
        return output
    # Phase 8-19: a database's extension fields are known, never "repaired".
    known = set(ontology.active_fields) | set(available_fields or [])
    by_word = _field_repairs(ontology)
    entities = {entity.name for entity in ontology.entities}
    repaired = dict(output)
    if isinstance(output.get("select"), list):
        select = [
            _repair_ref(ref, known, by_word)
            for ref in output["select"]
            if not (isinstance(ref, str) and ref.endswith(".*") and ref[:-2] in entities)
        ]
        if not select and output["select"] and available_fields:
            select = [available_fields[0]]
        repaired["select"] = select
    filters = output.get("filters")
    if not isinstance(filters, list):
        return repaired
    new_filters = []
    for item in filters:
        if not isinstance(item, dict):
            new_filters.append(item)
            continue
        entry = dict(item)
        entry["field"] = _repair_ref(entry.get("field"), known, by_word)
        moved = _move_by_value(entry, index, hints or [])
        if moved is not None:
            entry["field"] = moved
        new_filters.append(entry)
    repaired["filters"] = new_filters
    return repaired


def _move_by_value(entry: dict[str, Any], index: ValueIndex | None, hints: list[ValueHint]) -> str | None:
    field, value = entry.get("field"), entry.get("value")
    if index is None or not isinstance(value, str):
        return None
    # Phase 9-08: identity fields (codes, sections) are never indexed, so a
    # department name the model put into a section cannot be checked against
    # stored values; only a value equal to one hint moves.
    identity = field in IDENTITY_FIELDS
    if not identity and (not index.has_values(str(field)) or index.contains(str(field), value)):
        return None
    key = lookup_key(value)
    if len(key) < MIN_KEY_LENGTH:
        return None
    targets = {
        hint.field
        for hint in hints
        if hint.field != field
        and (
            key in (lookup_key(hint.text), lookup_key(hint.value))
            or (not identity and key in lookup_key(hint.value))
        )
    }
    return next(iter(targets)) if len(targets) == 1 else None
