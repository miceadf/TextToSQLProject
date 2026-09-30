"""Deterministic, all-or-nothing pseudonymization of converted Query IR records."""

from __future__ import annotations

import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable

from pydantic import BaseModel, Field

from backend.adaptive.harness.contracts import (
    FieldMapping, MappingProfile, MappingStatus, Ontology, OntologyField,
)
from backend.adaptive.harness.validator import validate_query_ir
from backend.adaptive.ontology.contracts import DataType, DomainOntology

from .conversion_contracts import ConversionStatus, PrivateConvertedRecord
from .pseudonym_contracts import (
    PseudonymPolicy,
    PseudonymReasonCode,
    PseudonymReviewItem,
    PseudonymStatus,
    PseudonymizationProfile,
    PseudonymizedRecord,
)
from .pseudonym_token import generate_token_map, lineage_token


class PseudonymizationRun(BaseModel):
    records: list[PseudonymizedRecord] = Field(default_factory=list)
    review_items: list[PseudonymReviewItem] = Field(default_factory=list)
    input_count: int = Field(ge=0)
    status_counts: dict[str, int] = Field(default_factory=dict)
    reason_counts: dict[str, int] = Field(default_factory=dict)
    field_unique_counts: dict[str, int] = Field(default_factory=dict)
    field_length_bucket_counts: dict[str, dict[str, int]] = Field(default_factory=dict)
    normalization_count: int = Field(ge=0)
    leak_count: int = Field(ge=0)


@dataclass(frozen=True)
class _Lexeme:
    namespace: str
    surface: str
    token: str


@dataclass(frozen=True)
class _Match:
    start: int
    end: int
    lexeme: _Lexeme


def pseudonymize_records(
    records: Iterable[PrivateConvertedRecord],
    profile: PseudonymizationProfile,
    ontology: DomainOntology,
    key: bytes,
    dataset_id: str,
    key_id: str,
) -> PseudonymizationRun:
    """Transform only fully verifiable records; failures contain no partial output."""

    source_records = list(records)
    if not ontology.accepts_version(profile.ontology_version):
        raise ValueError("pseudonymization profile ontology version mismatch")
    if not key or len(key) < 32:
        raise ValueError("pseudonymization key must contain at least 32 bytes")
    if not dataset_id or not key_id:
        raise ValueError("dataset id and key id are required")
    _assert_metadata_consistency(source_records)

    policies = profile.policies_by_field
    runtime_ontology = Ontology(
        entities={entity.name for entity in ontology.entities},
        fields={
            name: OntologyField(
                name=name,
                data_type=field.data_type,
                allowed_operators=field.allowed_operators,
            )
            for name, field in ontology.fields.items()
        },
    )
    # Ephemeral membership-only mapping for the existing IR validator. This is
    # never published, persisted, or used for SQL generation.
    runtime_mapping = MappingProfile(
        database_id="pseudonymization-validation",
        version=profile.version,
        status=MappingStatus.PUBLISHED,
        fields={name: FieldMapping(table="offline", column=name) for name in ontology.fields},
    )
    lexicon_pairs: set[tuple[str, str]] = set()
    raw_by_pair: dict[tuple[str, str], set[str]] = defaultdict(set)
    field_values: dict[str, set[str]] = defaultdict(set)
    for record in source_records:
        for item in record.query_ir.filters:
            policy = policies.get(item.field)
            if policy is None or policy.policy != PseudonymPolicy.PSEUDONYMIZE:
                continue
            if not isinstance(item.value, str):
                continue
            if not _unicode_ok(item.value):
                continue
            surface = _normalize(item.value)
            if not surface.strip():
                continue
            pair = (policy.namespace, surface)
            lexicon_pairs.add(pair)
            raw_by_pair[pair].add(item.value)
            field_values[item.field].add(surface)

    token_map = generate_token_map(lexicon_pairs, key=key, profile=profile)
    lexemes = [
        _Lexeme(namespace, surface, token_map[(namespace, surface)])
        for namespace, surface in lexicon_pairs
    ]
    lexemes.sort(key=lambda item: (-len(item.surface), item.namespace, item.token))
    surface_namespaces: dict[str, set[str]] = defaultdict(set)
    for item in lexemes:
        surface_namespaces[item.surface].add(item.namespace)
    normalized_collisions = {
        pair for pair, raw_values in raw_by_pair.items() if len(raw_values) > 1
    }

    output: list[PseudonymizedRecord] = []
    reviews: list[PseudonymReviewItem] = []
    reason_counts: Counter[str] = Counter()
    normalization_count = 0
    leak_count = 0
    source_line_counts = Counter(record.source_line for record in source_records)
    record_ids = [
        lineage_token(
            "record", f"{dataset_id}:{record.dataset_content_sha256}:{record.source_line}",
            key=key, profile=profile,
        )
        for record in source_records
    ]
    record_id_counts = Counter(record_ids)

    for record, record_id in zip(source_records, record_ids):
        reasons: set[PseudonymReasonCode] = set()
        if record.conversion_status not in {
            ConversionStatus.CONVERTED_EXACT,
            ConversionStatus.CONVERTED_WITH_EXECUTION_POLICY,
        }:
            reasons.add(PseudonymReasonCode.NON_CONVERTED_INPUT)
        if record.duplicate_provenance.question_conflict_group_id is not None:
            reasons.add(PseudonymReasonCode.QUESTION_SQL_CONFLICT_PRESENT)
        if source_line_counts[record.source_line] > 1:
            reasons.add(PseudonymReasonCode.OUTPUT_VALIDATION_FAILED)
        if record_id_counts[record_id] > 1:
            reasons.add(PseudonymReasonCode.OUTPUT_VALIDATION_FAILED)
        if not ontology.accepts_version(record.ontology_version):
            reasons.add(PseudonymReasonCode.ONTOLOGY_VERSION_MISMATCH)
        if not _unicode_ok(record.question) or not _unicode_ok(record.instruction):
            reasons.add(PseudonymReasonCode.OUTPUT_VALIDATION_FAILED)
        try:
            validate_query_ir(record.query_ir, runtime_ontology, runtime_mapping)
        except Exception:
            reasons.add(PseudonymReasonCode.OUTPUT_VALIDATION_FAILED)

        sensitive_filters: list[tuple[int, tuple[str, str]]] = []
        for field_name in [*record.query_ir.select, *(f.field for f in record.query_ir.filters)]:
            if field_name not in ontology.fields:
                reasons.add(PseudonymReasonCode.UNKNOWN_SEMANTIC_FIELD)
            if field_name not in policies:
                reasons.add(PseudonymReasonCode.FIELD_POLICY_MISSING)
            elif policies[field_name].policy == PseudonymPolicy.REVIEW:
                reasons.add(PseudonymReasonCode.FIELD_REQUIRES_REVIEW)

        for index, item in enumerate(record.query_ir.filters):
            policy = policies.get(item.field)
            ontology_field = ontology.fields.get(item.field)
            if isinstance(item.value, str) and not _unicode_ok(item.value):
                reasons.add(PseudonymReasonCode.OUTPUT_VALIDATION_FAILED)
            if ontology_field is not None:
                if item.operator not in ontology_field.allowed_operators:
                    reasons.add(PseudonymReasonCode.OUTPUT_VALIDATION_FAILED)
                if not _value_matches_type(item.value, ontology_field.data_type):
                    reasons.add(PseudonymReasonCode.OUTPUT_VALIDATION_FAILED)
            if policy is None or policy.policy != PseudonymPolicy.PSEUDONYMIZE:
                continue
            if not isinstance(item.value, str):
                reasons.add(PseudonymReasonCode.NON_STRING_SENSITIVE_VALUE)
                continue
            if not _unicode_ok(item.value):
                reasons.add(PseudonymReasonCode.OUTPUT_VALIDATION_FAILED)
                continue
            surface = _normalize(item.value)
            if not surface.strip():
                reasons.add(PseudonymReasonCode.EMPTY_SENSITIVE_VALUE)
                continue
            pair = (policy.namespace, surface)
            sensitive_filters.append((index, pair))
            if pair in normalized_collisions:
                reasons.add(PseudonymReasonCode.NORMALIZATION_COLLISION)

        question = _normalize(record.question)
        instruction = _normalize(record.instruction)
        changed = (
            question != record.question
            or instruction != record.instruction
            or any(
                _normalize(item.value) != item.value
                for item in record.query_ir.filters
                if isinstance(item.value, str)
            )
        )
        if changed:
            normalization_count += 1
        question_matches = _find_matches(question, lexemes)
        instruction_matches = _find_matches(instruction, lexemes)
        all_matches = [*question_matches, *instruction_matches]
        if _has_overlap(question_matches) or _has_overlap(instruction_matches):
            reasons.add(PseudonymReasonCode.OVERLAPPING_TEXT_MATCH)
        if any(len(surface_namespaces[m.lexeme.surface]) > 1 for m in all_matches):
            reasons.add(PseudonymReasonCode.AMBIGUOUS_SURFACE_NAMESPACE)
        matched_pairs = {
            (m.lexeme.namespace, m.lexeme.surface) for m in all_matches
        }
        filter_pairs = {pair for _, pair in sensitive_filters}
        if any(pair not in matched_pairs for pair in filter_pairs):
            reasons.add(PseudonymReasonCode.SENSITIVE_VALUE_NOT_FOUND_IN_TEXT)
        if matched_pairs - filter_pairs:
            # A globally recognized name in text but absent from this record's
            # IR is not enough evidence to rewrite the question's intent.
            reasons.add(PseudonymReasonCode.QUESTION_IR_TOKEN_MISMATCH)
        if any(
            (match.lexeme.namespace, match.lexeme.surface) in normalized_collisions
            for match in all_matches
        ):
            reasons.add(PseudonymReasonCode.NORMALIZATION_COLLISION)
        # A preserved filter sharing a matched surface cannot be rewritten without
        # changing its visible text-to-IR correspondence.
        for item in record.query_ir.filters:
            policy = policies.get(item.field)
            if policy is not None and policy.policy == PseudonymPolicy.PRESERVE:
                if isinstance(item.value, str) and _normalize(item.value) in {
                    m.lexeme.surface for m in all_matches
                }:
                    reasons.add(PseudonymReasonCode.AMBIGUOUS_SURFACE_NAMESPACE)

        if not reasons:
            transformed_question = _rewrite(question, question_matches)
            transformed_instruction = _rewrite(instruction, instruction_matches)
            ir = record.query_ir.model_copy(deep=True)
            for index, pair in sensitive_filters:
                ir.filters[index].value = token_map[pair]
            if _structure(record.query_ir) != _structure(ir):
                reasons.add(PseudonymReasonCode.QUERY_IR_STRUCTURE_CHANGED)
            for old, new in zip(record.query_ir.filters, ir.filters):
                policy = policies.get(old.field)
                if policy is not None and policy.policy == PseudonymPolicy.PRESERVE:
                    if type(old.value) is not type(new.value) or old.value != new.value:
                        reasons.add(PseudonymReasonCode.QUERY_IR_STRUCTURE_CHANGED)
            for _, pair in sensitive_filters:
                token = token_map[pair]
                if token not in transformed_question and token not in transformed_instruction:
                    reasons.add(PseudonymReasonCode.QUESTION_IR_TOKEN_MISMATCH)
            if _find_matches(transformed_question, lexemes) or _find_matches(
                transformed_instruction, lexemes
            ):
                reasons.add(PseudonymReasonCode.ORIGINAL_VALUE_REMAINS)
            for item in ir.filters:
                if isinstance(item.value, str) and _find_matches(item.value, lexemes):
                    reasons.add(PseudonymReasonCode.ORIGINAL_VALUE_REMAINS)
                    break
            try:
                ir = type(record.query_ir).model_validate(ir.model_dump(mode="python"))
                validate_query_ir(ir, runtime_ontology, runtime_mapping)
            except Exception:
                reasons.add(PseudonymReasonCode.OUTPUT_VALIDATION_FAILED)

        if reasons:
            ordered = _ordered_reasons(reasons)
            reviews.append(PseudonymReviewItem(source_line=record.source_line, reason_codes=ordered))
            reason_counts.update(reason.value for reason in ordered)
            continue

        try:
            output.append(PseudonymizedRecord(
                dataset_id=dataset_id,
                profile_id=profile.profile_id,
                profile_version=profile.version,
                ontology_version=ontology.version,
                key_id=key_id,
                record_id=record_id,
                canonical_lineage=lineage_token("canonical-sql", record.canonical_hash, key=key, profile=profile),
                shape_lineage=lineage_token("shape-sql", record.shape_hash, key=key, profile=profile),
                exact_duplicate_lineage=_group_lineage("exact-duplicate", record.duplicate_provenance.exact_duplicate_group_id, key, profile),
                question_sql_duplicate_lineage=_group_lineage("question-sql-duplicate", record.duplicate_provenance.question_sql_duplicate_group_id, key, profile),
                instruction=transformed_instruction,
                question=transformed_question,
                query_ir=ir,
                conversion_status=record.conversion_status,
                limit_provenance=record.limit_provenance,
                normalization_changed=changed,
                collision_resolved=any(len(token_map[pair].split("_")[-1][:-1]) > 12 for _, pair in sensitive_filters),
            ))
        except Exception:
            # Validation exceptions may include private values.
            reviews.append(PseudonymReviewItem(
                source_line=record.source_line,
                reason_codes=[PseudonymReasonCode.OUTPUT_VALIDATION_FAILED],
            ))
            reason_counts[PseudonymReasonCode.OUTPUT_VALIDATION_FAILED.value] += 1

    output.sort(key=lambda item: item.record_id)
    reviews.sort(key=lambda item: item.source_line)
    leak_count = sum(
        bool(
            _find_matches(item.question, lexemes)
            or _find_matches(item.instruction, lexemes)
            or any(
                isinstance(f.value, str) and _find_matches(f.value, lexemes)
                for f in item.query_ir.filters
            )
        )
        for item in output
    )
    if leak_count:
        raise ValueError("known sensitive value remains in transformed output")
    counts = {
        PseudonymStatus.PSEUDONYMIZED_EXACT.value: len(output),
        PseudonymStatus.REVIEW_REQUIRED.value: len(reviews),
        PseudonymStatus.UNSUPPORTED.value: 0,
    }
    return PseudonymizationRun(
        records=output,
        review_items=reviews,
        input_count=len(source_records),
        status_counts=counts,
        reason_counts=dict(sorted(reason_counts.items())),
        field_unique_counts={field: len(values) for field, values in sorted(field_values.items())},
        field_length_bucket_counts={
            field: dict(sorted(Counter(_length_bucket(len(value)) for value in values).items()))
            for field, values in sorted(field_values.items())
        },
        normalization_count=normalization_count,
        leak_count=leak_count,
    )


def _assert_metadata_consistency(records: list[PrivateConvertedRecord]) -> None:
    if not records:
        return
    first = records[0]
    expected = (
        first.dataset_id, first.dataset_content_sha256, first.profile_id,
        first.profile_version, first.ontology_version,
    )
    for record in records[1:]:
        actual = (
            record.dataset_id, record.dataset_content_sha256, record.profile_id,
            record.profile_version, record.ontology_version,
        )
        if actual != expected:
            raise ValueError("private artifact metadata mismatch")


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def _unicode_ok(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _find_matches(text: str, lexemes: list[_Lexeme]) -> list[_Match]:
    matches: list[_Match] = []
    for lexeme in lexemes:
        position = 0
        while True:
            start = text.find(lexeme.surface, position)
            if start < 0:
                break
            end = start + len(lexeme.surface)
            if _boundary_ok(text, start, end, lexeme.surface):
                matches.append(_Match(start, end, lexeme))
            position = start + 1
    return sorted(matches, key=lambda m: (m.start, -(m.end - m.start), m.lexeme.namespace, m.lexeme.token))


def _boundary_ok(text: str, start: int, end: int, surface: str) -> bool:
    if not surface:
        return True
    def identifier_char(char: str) -> bool:
        return char.isascii() and (char.isalnum() or char == "_")

    return (
        not identifier_char(surface[0])
        or start == 0
        or not identifier_char(text[start - 1])
    ) and (
        not identifier_char(surface[-1])
        or end == len(text)
        or not identifier_char(text[end])
    )


def _has_overlap(matches: list[_Match]) -> bool:
    return any(current.start < previous.end for previous, current in zip(matches, matches[1:]))


def _rewrite(text: str, matches: list[_Match]) -> str:
    pieces: list[str] = []
    cursor = 0
    for match in matches:
        pieces.extend((text[cursor:match.start], match.lexeme.token))
        cursor = match.end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _structure(ir: object) -> tuple[object, ...]:
    return (
        ir.entity, tuple(ir.select),
        tuple((item.field, item.operator) for item in ir.filters), ir.limit,
    )


def _value_matches_type(value: object, data_type: DataType) -> bool:
    if data_type in {DataType.STRING, DataType.DATE, DataType.TIME}:
        return isinstance(value, str)
    if data_type == DataType.BOOLEAN:
        return isinstance(value, bool)
    if data_type == DataType.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if data_type == DataType.NUMBER:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def _group_lineage(domain: str, value: str | None, key: bytes, profile: PseudonymizationProfile) -> str | None:
    return None if value is None else lineage_token(domain, value, key=key, profile=profile)


def _ordered_reasons(reasons: set[PseudonymReasonCode]) -> list[PseudonymReasonCode]:
    return [reason for reason in PseudonymReasonCode if reason in reasons]


def _length_bucket(length: int) -> str:
    if length <= 2:
        return "1-2"
    if length <= 5:
        return "3-5"
    if length <= 10:
        return "6-10"
    return "11+"
