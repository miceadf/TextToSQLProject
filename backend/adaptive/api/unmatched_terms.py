"""Closed word coverage rules for successful chat answers (Phase 9-21).

This is a notice, not a condition generator or a claim of semantic accuracy.
It consumes the already validated filters and the already collected hints.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

from ..mapping.values import lookup_key
from ..ontology.contracts import DomainOntology
from .value_hints import PARTICLES, domain_nouns

MAX_UNMATCHED_TERMS = 5
# Language/domain scaffolding only; database abbreviations belong in dictionaries.
GENERAL_WORDS = frozenset({
    "과목", "수업", "강의", "강좌", "목록", "전체", "모든", "정보",
    "이상", "이하", "이후", "이전", "초과", "미만", "오전", "오후",
    "저녁", "아침", "밤", "시간", "시각", "요일", "월요일", "화요일",
    "수요일", "목요일", "금요일", "토요일", "일요일", "중", "중에",
    "보여줘", "알려줘", "찾아줘", "있어", "뭐야", "보여주세요",
    "알려주세요", "찾아주세요", "하는", "듣는", "있는", "없는",
    "시작하는", "끝나는", "종료하는", "넘는", "열리는", "개설된", "교수님",
})
_ENDINGS = tuple(sorted({*PARTICLES, "보여줘", "알려줘", "찾아줘", "있어", "뭐야"}, key=len, reverse=True))
GENERAL_VERBS = frozenset({"정리", "추천", "검색", "확인", "비교", "나열", "소개", "보여", "알려", "찾아"})
_REQUEST_ENDINGS = tuple(sorted({"해줘", "줘", "주세요", "해주세요", "해봐", "할래", "해"}, key=len, reverse=True))
_WORD = re.compile(r"[가-힣A-Za-z]{2,}")
_NUMBER_UNIT = re.compile(r"\d+(?:\.\d+)?(?:학점|학년|명|시|분|학기|년)(?:이|가|은|는|에|에서|부터|까지)?")


def _stem(word: str) -> str:
    for ending in _ENDINGS:
        if word.endswith(ending):
            return word[:-len(ending)]
    return word


def find_unmatched_terms(
    question: str,
    ontology: DomainOntology,
    filters: Iterable[Any],
    *,
    hints: Iterable[Any] = (),
    timed: Iterable[Any] = (),
    extensions: dict[str, Any] | None = None,
) -> list[str]:
    """Return at most five unique question words absent from known vocabulary."""
    vocabulary = set(GENERAL_WORDS) | set(domain_nouns(ontology))
    for entity in ontology.entities:
        vocabulary.add(entity.name)
    for field in ontology.active_fields.values():
        vocabulary.update((field.name, field.label, *field.aliases))
    for name, extension in (extensions or {}).items():
        vocabulary.update((name, extension.label, *getattr(extension, "aliases", ())))
    known = {lookup_key(word) for phrase in vocabulary for word in (phrase, *phrase.split())}
    general_stems = known | GENERAL_VERBS
    filters = list(filters)
    values = [str(item.value) for item in filters]
    values += [item.operator.value for item in filters]
    values += [hint.text for hint in hints]
    values += [item.text for item in timed]
    # lookup_key preserves punctuation, so also compare lexical pieces of
    # values such as 전공(핵심) with the natural spelling 전공핵심.
    covered = {lookup_key(value) for value in values}
    covered |= {lookup_key("".join(_WORD.findall(value))) for value in values}
    covered.discard("")
    unmatched: list[str] = []
    seen: set[str] = set()
    for raw in unicodedata.normalize("NFKC", question).split():
        word = raw.strip(".,!?…'\"‘’“”()[]{}:;")
        if word in GENERAL_VERBS:
            continue
        request_stem = next((word[:-len(ending)] for ending in _REQUEST_ENDINGS if word.endswith(ending)), None)
        if request_stem is not None and (not request_stem or lookup_key(request_stem) in general_stems):
            continue
        if _NUMBER_UNIT.fullmatch(word):
            continue
        stem = _stem(word)
        variants = {lookup_key(word), lookup_key(stem)} - {""}
        if variants & known or any(
            key in value or value in key for key in variants for value in covered
        ):
            continue
        if not _WORD.fullmatch(stem):
            continue
        key = lookup_key(stem)
        if key not in seen:
            unmatched.append(stem)
            seen.add(key)
        if len(unmatched) == MAX_UNMATCHED_TERMS:
            break
    return unmatched
