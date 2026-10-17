"""DB catalog와 ontology를 받아 근거·신뢰도가 있는 연결 후보를 만든다.

해당 ontology 버전의 활성 항목을 이름·자료형 규칙으로 비교해 MappingProposal을 반환한다.
자동 승인 후보도 사람의 검토·게시를 거쳐야 실행에 쓰인다.
"""

from __future__ import annotations

import re

from ..catalog import ColumnCatalog, DatabaseCatalog, SQLiteAffinity, TableCatalog
from ..ontology import DataType, DomainOntology, EntityDefinition, OntologyField
from .contracts import (
    MappingCandidate,
    MappingEvidence,
    MappingProposal,
    MappingReviewStatus,
    MappingSignal,
)


def normalize_name(value: str) -> str:
    with_word_boundaries = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    return " ".join(
        token for token in re.split(r"[^\w]+", with_word_boundaries.casefold()) if token
    )


def name_similarity(actual: str, hints: list[str]) -> tuple[float, str]:
    normalized_actual = normalize_name(actual)
    actual_tokens = set(normalized_actual.split())
    best_score = 0.0
    best_hint = ""
    for hint in hints:
        normalized_hint = normalize_name(hint)
        if not normalized_hint:
            continue
        if normalized_actual == normalized_hint:
            return 1.0, hint
        hint_tokens = set(normalized_hint.split())
        union = actual_tokens | hint_tokens
        score = len(actual_tokens & hint_tokens) / len(union) if union else 0.0
        if score > best_score:
            best_score = score
            best_hint = hint
    return best_score, best_hint


def type_compatibility(data_type: DataType, affinity: SQLiteAffinity) -> float:
    if data_type is DataType.STRING:
        return 1.0 if affinity is SQLiteAffinity.TEXT else 0.2
    if data_type is DataType.NUMBER:
        return 1.0 if affinity in {
            SQLiteAffinity.INTEGER,
            SQLiteAffinity.REAL,
            SQLiteAffinity.NUMERIC,
        } else 0.0
    if data_type is DataType.INTEGER:
        if affinity is SQLiteAffinity.INTEGER:
            return 1.0
        return 0.7 if affinity is SQLiteAffinity.NUMERIC else 0.0
    if data_type is DataType.BOOLEAN:
        return 1.0 if affinity in {
            SQLiteAffinity.INTEGER,
            SQLiteAffinity.NUMERIC,
        } else 0.0
    if data_type in {DataType.DATE, DataType.TIME}:
        if affinity in {SQLiteAffinity.TEXT, SQLiteAffinity.NUMERIC}:
            return 1.0
        return 0.5 if affinity in {SQLiteAffinity.INTEGER, SQLiteAffinity.REAL} else 0.0
    return 0.0


class RuleBasedSchemaAligner:
    """Generate explainable mapping candidates from names and SQLite types."""

    def __init__(
        self,
        *,
        auto_approve_threshold: float = 0.90,
        review_threshold: float = 0.60,
        minimum_candidate_confidence: float = 0.35,
        candidates_per_field: int = 3,
    ) -> None:
        if not 0 <= minimum_candidate_confidence <= review_threshold <= auto_approve_threshold <= 1:
            raise ValueError("mapping thresholds must be ordered between 0 and 1")
        if candidates_per_field < 1:
            raise ValueError("candidates_per_field must be at least 1")
        self._auto_approve_threshold = auto_approve_threshold
        self._review_threshold = review_threshold
        self._minimum_candidate_confidence = minimum_candidate_confidence
        self._candidates_per_field = candidates_per_field

    def align(
        self,
        catalog: DatabaseCatalog,
        ontology: DomainOntology,
    ) -> MappingProposal:
        candidates: dict[str, list[MappingCandidate]] = {}
        unmapped_fields: list[str] = []

        for entity in ontology.at_version(ontology.version).entities:
            for field in entity.fields:
                ranked = self._rank_field(catalog, entity, field)
                candidates[field.name] = ranked[: self._candidates_per_field]
                if not ranked or ranked[0].confidence < self._review_threshold:
                    unmapped_fields.append(field.name)

        return MappingProposal(
            database_id=catalog.database_id,
            schema_fingerprint=catalog.schema_fingerprint,
            ontology_version=ontology.version,
            candidates=candidates,
            unmapped_fields=unmapped_fields,
        )

    def score_column(
        self,
        ontology: DomainOntology,
        semantic_field: str,
        table: TableCatalog,
        column: ColumnCatalog,
    ) -> MappingCandidate:
        """The rule score of one given column, even below the candidate minimum."""
        for entity in ontology.at_version(ontology.version).entities:
            for field in entity.fields:
                if field.name == semantic_field:
                    return self._score_candidate(entity, field, table, column)
        raise ValueError("unknown semantic field")

    def _rank_field(
        self,
        catalog: DatabaseCatalog,
        entity: EntityDefinition,
        field: OntologyField,
    ) -> list[MappingCandidate]:
        ranked: list[MappingCandidate] = []
        for table in catalog.tables:
            for column in table.columns:
                if column.hidden:
                    continue
                candidate = self._score_candidate(entity, field, table, column)
                if candidate.confidence >= self._minimum_candidate_confidence:
                    ranked.append(candidate)
        return sorted(
            ranked,
            key=lambda item: (-item.confidence, item.table, item.column),
        )

    def _score_candidate(
        self,
        entity: EntityDefinition,
        field: OntologyField,
        table: TableCatalog,
        column: ColumnCatalog,
    ) -> MappingCandidate:
        column_score, column_hint = name_similarity(
            column.name,
            [*field.physical_name_hints, *field.aliases, field.label],
        )
        table_score, table_hint = name_similarity(
            table.name,
            [*entity.table_name_hints, *entity.aliases, entity.label],
        )
        data_type_score = type_compatibility(field.data_type, column.affinity)
        confidence = round(
            min(1.0, column_score * 0.70 + data_type_score * 0.20 + table_score * 0.10),
            4,
        )

        if confidence >= self._auto_approve_threshold:
            status = MappingReviewStatus.AUTO_APPROVE_CANDIDATE
        elif confidence >= self._review_threshold:
            status = MappingReviewStatus.NEEDS_REVIEW
        else:
            status = MappingReviewStatus.UNCERTAIN

        return MappingCandidate(
            semantic_field=field.name,
            table=table.name,
            column=column.name,
            confidence=confidence,
            review_status=status,
            evidence=[
                MappingEvidence(
                    signal=MappingSignal.COLUMN_NAME,
                    score=column_score,
                    detail=(
                        f"column '{column.name}' matched hint '{column_hint}'"
                        if column_hint
                        else f"column '{column.name}' matched no known hint"
                    ),
                ),
                MappingEvidence(
                    signal=MappingSignal.DATA_TYPE,
                    score=data_type_score,
                    detail=(
                        f"semantic type '{field.data_type.value}' vs "
                        f"SQLite affinity '{column.affinity.value}'"
                    ),
                ),
                MappingEvidence(
                    signal=MappingSignal.TABLE_NAME,
                    score=table_score,
                    detail=(
                        f"table '{table.name}' matched hint '{table_hint}'"
                        if table_hint
                        else f"table '{table.name}' matched no known hint"
                    ),
                ),
            ],
        )
