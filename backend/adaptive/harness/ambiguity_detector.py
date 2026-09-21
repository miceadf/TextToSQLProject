"""Versioned synthetic ambiguity ruleset and deterministic detection boundary.

`ambiguity-detector-v1` sits in front of `clarification-gate-v2`: a caller
provides a frozen literal-trigger rule set, `detect_ambiguity` matches the
normalized question against normalized literal triggers, and
`prepare_detected_clarification` / `resolve_detected_clarification` close
the loop through the existing `clarification.prepare` / `clarification.resolve`
without ever accepting a caller-injected `AmbiguitySignal`.

Rule-based literal matching is not natural-language understanding and is not
a complete ambiguity detector: a rule matches only when one of its normalized
literal triggers is a substring of the normalized question.

Trust boundary: every public input is revalidated from plain JSON data
(`model_dump(mode="json", warnings=False)` then `model_validate`), so forged
`model_copy(update=...)` / `model_construct` instances fail closed. Pending
objects stay untrusted bearer data; `resolve_detected_clarification`
re-detects with the same request/ontology/mapping/rule set and lets the
existing clarification gate verify the pending binding.

Privacy: detector results, pending objects, results, errors, and this
module's output never store or reflect the raw question, raw request or
database identifiers (which may be arbitrary path or secret text), rule or
option keys, trigger phrases, physical table/column names, SQL, rows, paths,
or secrets. Option IDs are domain-separated SHA-256 hex digests. The runtime
rule set itself is caller-owned input and is never stored. No low-entropy
phrase/hash guessing resistance or rule-author authentication is claimed.

Synthetic use only: pure functions over the inputs below. No model, repair
provider, executor, file, DB, network, Ollama, PostgreSQL, or CNU material
is touched. Existing harness failure codes are never auto-promoted into
ambiguity. QueryIR edits, re-execution, UI display, and
single-use/expiry/session storage are follow-up steps.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .clarification import (
    AmbiguitySignal,
    ClarificationChoice,
    ClarificationResult,
    PendingClarification,
    prepare as prepare_clarification,
    resolve as resolve_clarification,
)
from .contracts import HarnessRequest, MappingProfile, MappingStatus, Ontology


AMBIGUITY_DETECTOR_VERSION = "ambiguity-detector-v1"

_RULE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
_OPTION_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
_SEMANTIC_REF_PATTERN = r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)?$"
_OPTION_ID_DOMAIN = "ambiguity-detector-v1:option-id:"


class AmbiguityDetectorErrorCode(str, Enum):
    INVALID_RULESET = "AMBIGUITY_INVALID_RULESET"
    CONFLICT = "AMBIGUITY_CONFLICT"
    CONTEXT_MISMATCH = "AMBIGUITY_CONTEXT_MISMATCH"


class AmbiguityDetectorError(Exception):
    """Fail-closed error with a fixed code only; never reflects input."""

    def __init__(self, code: AmbiguityDetectorErrorCode) -> None:
        self.code = code.value
        super().__init__(self.code)


class DetectorOption(BaseModel):
    """One semantic reading: opaque key plus an ontology entity/field ref.

    Display sentences, physical table/column names, SQL, result rows, and
    free-text answers have no field here.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    option_key: str = Field(pattern=_OPTION_KEY_PATTERN)
    semantic_ref: str = Field(min_length=1, max_length=200, pattern=_SEMANTIC_REF_PATTERN)


class DetectorRule(BaseModel):
    """One ambiguity axis: literal triggers plus 2-16 semantic options."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    rule_id: str = Field(pattern=_RULE_ID_PATTERN)
    triggers: list[str] = Field(min_length=1, max_length=16)
    options: list[DetectorOption] = Field(min_length=2, max_length=16)

    @field_validator("triggers")
    @classmethod
    def triggers_have_shape(cls, triggers: list[str]) -> list[str]:
        for trigger in triggers:
            if not isinstance(trigger, str):
                raise ValueError("trigger must be a string")
            if len(trigger) < 1 or len(trigger) > 200:
                raise ValueError("trigger length must be 1..200")
        return triggers

    @model_validator(mode="after")
    def options_are_distinct(self) -> "DetectorRule":
        keys = [option.option_key for option in self.options]
        if len(keys) != len(set(keys)):
            raise ValueError("rule option keys must be distinct")
        refs = [option.semantic_ref for option in self.options]
        if len(refs) != len(set(refs)):
            raise ValueError("rule semantic refs must be distinct")
        return self


class DetectorRuleSet(BaseModel):
    """Caller-owned frozen rule set; never stored by this module."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: Literal["ambiguity-detector-v1"] = AMBIGUITY_DETECTOR_VERSION
    rules: list[DetectorRule] = Field(min_length=1, max_length=32)


def normalize_text(value: str) -> str:
    """Single normalization source: NFKC + casefold + whitespace collapse."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _revalidate(model_cls: type[Any], value: Any) -> Any:
    if isinstance(value, model_cls):
        payload = value.model_dump(mode="json", warnings=False)
    elif isinstance(value, dict):
        payload = value
    else:
        raise ValueError("unsupported boundary input")
    return model_cls.model_validate(payload)


def _normalized_trigger_is_usable(normalized: str) -> bool:
    if not normalized or len(normalized) < 2 or len(normalized) > 200:
        return False
    for char in normalized:
        if unicodedata.category(char) in ("Cc", "Cf", "Cs"):
            return False
    if not normalized.strip():
        return False
    return True


def _ruleset_fingerprint(rules: DetectorRuleSet) -> str:
    canonical_rules = []
    for rule in rules.rules:
        canonical_rules.append(
            {
                "rule_id": normalize_text(rule.rule_id),
                "triggers": sorted(normalize_text(trigger) for trigger in rule.triggers),
                "options": sorted(
                    (
                        normalize_text(option.option_key),
                        option.semantic_ref,
                    )
                    for option in rule.options
                ),
            }
        )
    payload = json.dumps(
        {
            "version": rules.version,
            "rules": sorted(canonical_rules, key=lambda item: item["rule_id"]),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _derived_option_id(
    ruleset_sha256: str,
    rule_id_norm: str,
    option_key_norm: str,
    semantic_ref: str,
) -> str:
    payload = (
        ruleset_sha256
        + "\x00"
        + rule_id_norm
        + "\x00"
        + option_key_norm
        + "\x00"
        + semantic_ref
    )
    return hashlib.sha256((_OPTION_ID_DOMAIN + payload).encode("utf-8")).hexdigest()


def _is_known_semantic_ref(semantic_ref: str, ontology: Ontology) -> bool:
    if semantic_ref in ontology.entities:
        return True
    return semantic_ref in ontology.fields


def _validated_context(
    request_data: HarnessRequest | dict[str, Any],
    ontology: Ontology,
    mapping: MappingProfile,
    rules_data: DetectorRuleSet | dict[str, Any],
) -> tuple[HarnessRequest, Ontology, MappingProfile, DetectorRuleSet]:
    try:
        request = _revalidate(HarnessRequest, request_data)
        validated_ontology = _revalidate(Ontology, ontology)
        validated_mapping = _revalidate(MappingProfile, mapping)
        validated_rules = _revalidate(DetectorRuleSet, rules_data)
    except Exception:
        raise AmbiguityDetectorError(AmbiguityDetectorErrorCode.INVALID_RULESET) from None
    if validated_mapping.status is not MappingStatus.PUBLISHED:
        raise AmbiguityDetectorError(AmbiguityDetectorErrorCode.INVALID_RULESET)
    if validated_mapping.database_id != request.database_id:
        raise AmbiguityDetectorError(AmbiguityDetectorErrorCode.INVALID_RULESET)

    seen_rule_ids: set[str] = set()
    seen_triggers: set[str] = set()
    for rule in validated_rules.rules:
        rule_norm = normalize_text(rule.rule_id)
        if not rule_norm or rule_norm in seen_rule_ids:
            raise AmbiguityDetectorError(AmbiguityDetectorErrorCode.INVALID_RULESET)
        seen_rule_ids.add(rule_norm)
        seen_option_keys: set[str] = set()
        seen_refs: set[str] = set()
        seen_refs_norm: set[str] = set()
        for option in rule.options:
            key_norm = normalize_text(option.option_key)
            if not key_norm or key_norm in seen_option_keys:
                raise AmbiguityDetectorError(AmbiguityDetectorErrorCode.INVALID_RULESET)
            seen_option_keys.add(key_norm)
            ref_norm = normalize_text(option.semantic_ref)
            if option.semantic_ref in seen_refs or ref_norm in seen_refs_norm:
                raise AmbiguityDetectorError(AmbiguityDetectorErrorCode.INVALID_RULESET)
            seen_refs.add(option.semantic_ref)
            seen_refs_norm.add(ref_norm)
            if not _is_known_semantic_ref(option.semantic_ref, validated_ontology):
                raise AmbiguityDetectorError(AmbiguityDetectorErrorCode.INVALID_RULESET)
        for trigger in rule.triggers:
            trigger_norm = normalize_text(trigger)
            if not _normalized_trigger_is_usable(trigger_norm):
                raise AmbiguityDetectorError(AmbiguityDetectorErrorCode.INVALID_RULESET)
            if trigger_norm in seen_triggers:
                raise AmbiguityDetectorError(AmbiguityDetectorErrorCode.INVALID_RULESET)
            seen_triggers.add(trigger_norm)
    return request, validated_ontology, validated_mapping, validated_rules


def _match_rules(normalized_question: str, rules: DetectorRuleSet) -> list[DetectorRule]:
    matched: list[DetectorRule] = []
    for rule in rules.rules:
        for trigger in rule.triggers:
            if normalize_text(trigger) in normalized_question:
                matched.append(rule)
                break
    return matched


def _signal_for_rule(rule: DetectorRule, rules: DetectorRuleSet) -> AmbiguitySignal:
    rule_norm = normalize_text(rule.rule_id)
    ruleset_sha256 = _ruleset_fingerprint(rules)
    derived = sorted(
        (
            (
                _derived_option_id(
                    ruleset_sha256,
                    rule_norm,
                    normalize_text(option.option_key),
                    option.semantic_ref,
                ),
                option.semantic_ref,
            )
            for option in rule.options
        ),
        key=lambda pair: pair[0],
    )
    return AmbiguitySignal(
        options=[
            {"option_id": option_id, "semantic_ref": semantic_ref}
            for option_id, semantic_ref in derived
        ]
    )


def detect_ambiguity(
    request_data: HarnessRequest | dict[str, Any],
    ontology: Ontology,
    mapping: MappingProfile,
    rules_data: DetectorRuleSet | dict[str, Any],
) -> AmbiguitySignal | None:
    """Match the normalized question against literal triggers.

    Zero matches return `None` with no signal and no side effects. Exactly
    one match builds a deterministic `AmbiguitySignal`. Two or more matches
    fail closed with `AMBIGUITY_CONFLICT` instead of merging distinct
    ambiguity axes into one choice set.
    """
    request, _, _, rules = _validated_context(request_data, ontology, mapping, rules_data)
    normalized_question = normalize_text(request.question)
    matched = _match_rules(normalized_question, rules)
    if not matched:
        return None
    if len(matched) > 1:
        raise AmbiguityDetectorError(AmbiguityDetectorErrorCode.CONFLICT)
    return _signal_for_rule(matched[0], rules)


def prepare_detected_clarification(
    request_data: HarnessRequest | dict[str, Any],
    ontology: Ontology,
    mapping: MappingProfile,
    rules_data: DetectorRuleSet | dict[str, Any],
) -> PendingClarification | None:
    """Detect with the given rule set, then bind via `clarification.prepare`.

    No caller-injected `AmbiguitySignal` is accepted: the trusted signal is
    always reconstructed by this module. Existing clarification errors keep
    their original fixed codes.
    """
    request, validated_ontology, validated_mapping, rules = _validated_context(
        request_data, ontology, mapping, rules_data
    )
    normalized_question = normalize_text(request.question)
    matched = _match_rules(normalized_question, rules)
    if not matched:
        return None
    if len(matched) > 1:
        raise AmbiguityDetectorError(AmbiguityDetectorErrorCode.CONFLICT)
    signal = _signal_for_rule(matched[0], rules)
    return prepare_clarification(request, validated_ontology, validated_mapping, signal)


def resolve_detected_clarification(
    request_data: HarnessRequest | dict[str, Any],
    ontology: Ontology,
    mapping: MappingProfile,
    rules_data: DetectorRuleSet | dict[str, Any],
    pending: PendingClarification | dict[str, Any],
    choice: ClarificationChoice | dict[str, Any],
) -> ClarificationResult:
    """Re-detect with identical inputs, then confirm via `clarification.resolve`.

    Re-detection proves the pending binding still matches the current
    question/context/rule set: no-match and multi-match never promote to a
    confirmation, and rule/option/trigger/context drift fails closed. Caller
    signals cannot be injected; the trusted signal is rebuilt here.
    The `choice` selection carries the opaque derived option IDs returned
    by `detect_ambiguity` (64-char lowercase hex), never raw rule IDs,
    option keys, or trigger phrases.
    """
    request, validated_ontology, validated_mapping, rules = _validated_context(
        request_data, ontology, mapping, rules_data
    )
    try:
        validated_pending = _revalidate(PendingClarification, pending)
    except Exception:
        from .clarification import ClarificationError, ClarificationErrorCode

        raise ClarificationError(ClarificationErrorCode.CONTEXT_MISMATCH) from None
    try:
        validated_choice = _revalidate(ClarificationChoice, choice)
    except Exception:
        from .clarification import ClarificationError, ClarificationErrorCode

        raise ClarificationError(ClarificationErrorCode.INVALID_CHOICE) from None
    normalized_question = normalize_text(request.question)
    matched = _match_rules(normalized_question, rules)
    if not matched:
        raise AmbiguityDetectorError(AmbiguityDetectorErrorCode.CONTEXT_MISMATCH)
    if len(matched) > 1:
        raise AmbiguityDetectorError(AmbiguityDetectorErrorCode.CONFLICT)
    signal = _signal_for_rule(matched[0], rules)
    return resolve_clarification(
        request, validated_ontology, validated_mapping, validated_pending, signal, validated_choice
    )
