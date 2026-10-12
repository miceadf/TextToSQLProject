"""Seed-only gold consistency checks; no compiler or database is consulted."""

from __future__ import annotations

import json
import math
import re
import tempfile
import unicodedata
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from ..catalog.contracts import DatabaseCatalog
from ..fixtures import build_fixture_bundle
from ..fixtures.contracts import FixtureSeed, LogicalOffering
from ..ontology.contracts import DataType, DomainOntology, Operator
from .contracts import EvaluationSet, ReviewStatus
from .reference import (
    FIELD_TO_ATTRIBUTE,
    offering_index,
    ontology_fingerprint,
    query_ir_fingerprint,
    seed_fingerprint,
    semantic_row,
)


@lru_cache(maxsize=1)
def _physical_names() -> tuple[str, ...]:
    """Read fixture Catalogs, so the denylist follows actual physical schemas."""

    names: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="fixture-identifiers-") as directory:
        bundle = build_fixture_bundle(Path(directory) / "databases")
        for artifact in bundle.artifacts.values():
            catalog = DatabaseCatalog.model_validate_json(
                artifact.catalog_path.read_text(encoding="utf-8")
            )
            for table in catalog.tables:
                names.add(table.name)
                names.update(column.name for column in table.columns)
    return tuple(sorted(names))


def _value_matches_data_type(value: object, data_type: DataType) -> bool:
    if data_type is DataType.INTEGER:
        return type(value) is int
    if data_type is DataType.NUMBER:
        return type(value) in {int, float} and math.isfinite(float(value))
    if data_type is DataType.BOOLEAN:
        return type(value) is bool
    if data_type in {DataType.STRING, DataType.DATE, DataType.TIME}:
        return type(value) is str
    return False


def _same_typed_value(actual: object, gold: object, data_type: DataType) -> bool:
    if not _value_matches_data_type(actual, data_type) or not _value_matches_data_type(gold, data_type):
        return False
    if data_type is DataType.NUMBER:
        return Decimal(str(actual)) == Decimal(str(gold))
    return type(actual) is type(gold) and actual == gold


def _matches_filter(actual: object, operator: Operator, expected: object) -> bool:
    if operator is Operator.EQUALS:
        return actual == expected
    if operator is Operator.NOT_EQUALS:
        return actual != expected
    if operator is Operator.CONTAINS:
        return isinstance(actual, str) and isinstance(expected, str) and expected in actual
    if operator is Operator.GREATER_THAN:
        return actual > expected
    if operator is Operator.GREATER_THAN_OR_EQUAL:
        return actual >= expected
    if operator is Operator.LESS_THAN:
        return actual < expected
    if operator is Operator.LESS_THAN_OR_EQUAL:
        return actual <= expected
    raise ValueError(f"unsupported operator: {operator}")


def _matching_offerings(case, seed: FixtureSeed) -> list[LogicalOffering]:
    matches = []
    for offering in seed.offerings:
        if all(
            _matches_filter(
                getattr(offering, FIELD_TO_ATTRIBUTE[condition.field]),
                condition.operator,
                condition.value,
            )
            for condition in case.gold_query_ir.filters
        ):
            matches.append(offering)
    return sorted(matches, key=lambda offering: offering.offering_key)


def _intent_identity(ir) -> str:
    """AND filter order is not a distinct user intent; projection order remains."""

    filters = [
        {
            "field": item.field,
            "operator": item.operator.value,
            "value_type": type(item.value).__name__,
            "value": item.value,
        }
        for item in ir.filters
    ]
    filters.sort(key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False))
    return json.dumps(
        {"entity": ir.entity, "select": ir.select, "filters": filters, "limit": ir.limit},
        sort_keys=True,
        ensure_ascii=False,
    )


def validate_evaluation_set(
    evaluation_set: EvaluationSet,
    seed: FixtureSeed,
    ontology: DomainOntology,
    *,
    require_full: bool = True,
) -> None:
    """Reject stale or self-inconsistent gold before cross-schema execution."""
    if evaluation_set.seed_version != seed.version or evaluation_set.seed_fingerprint != seed_fingerprint(seed):
        raise ValueError("evaluation seed version or fingerprint drift")
    if not ontology.accepts_version(evaluation_set.ontology_version):
        raise ValueError("evaluation ontology version drift")
    if evaluation_set.ontology_fingerprint != ontology_fingerprint(
        ontology.at_version(evaluation_set.ontology_version)
    ):
        raise ValueError("evaluation ontology fingerprint drift")
    if evaluation_set.query_ir_fingerprint != query_ir_fingerprint():
        raise ValueError("evaluation QueryIR fingerprint drift")
    if require_full and not 30 <= len(evaluation_set.cases) <= 50:
        raise ValueError("full evaluation set requires 30 to 50 cases")

    fields = ontology.fields_at(evaluation_set.ontology_version)
    entities = {entity.name for entity in ontology.entities}
    known_keys = offering_index(seed)
    normalized_questions: set[str] = set()
    intent_to_family: dict[str, tuple[str, str | None]] = {}
    family_to_intent: dict[str, str] = {}
    family_counts: dict[str, int] = {}

    for case in evaluation_set.cases:
        cid = case.case_id
        question = case.question_ko
        if question != question.strip() or not re.search(r"[가-힣]", question):
            raise ValueError(f"{cid}: question must be trimmed Korean text")
        qkey = unicodedata.normalize("NFC", " ".join(question.split()))
        if qkey in normalized_questions:
            raise ValueError(f"{cid}: duplicate question")
        normalized_questions.add(qkey)
        if any(re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", question, re.I) for name in _physical_names()):
            raise ValueError(f"{cid}: question contains a physical schema identifier")
        if case.review_status is ReviewStatus.HUMAN_APPROVED and case.reviewer_id == case.author_id:
            raise ValueError(f"{cid}: author may not self-approve gold")

        ir = case.gold_query_ir
        if ir.entity not in entities:
            raise ValueError(f"{cid}: unknown root entity")
        if ir.entity != "CourseOffering":
            raise ValueError(f"{cid}: fixture gold root must be CourseOffering")
        for name in [*ir.select, *(flt.field for flt in ir.filters)]:
            if name not in fields or name not in FIELD_TO_ATTRIBUTE:
                raise ValueError(f"{cid}: unknown or unavailable semantic field {name}")
        for flt in ir.filters:
            definition = fields[flt.field]
            if flt.operator not in definition.allowed_operators:
                raise ValueError(f"{cid}: operator not allowed for {flt.field}")
            if not _value_matches_data_type(flt.value, definition.data_type):
                raise ValueError(f"{cid}: filter value type mismatch for {flt.field}")
            if flt.operator is Operator.CONTAINS and definition.data_type is not DataType.STRING:
                raise ValueError(f"{cid}: contains requires string field")
            if isinstance(flt.value, str) and any(
                re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", flt.value, re.I)
                for name in _physical_names()
            ):
                raise ValueError(f"{cid}: gold filter contains a physical schema identifier")

        intent_key = _intent_identity(ir)
        prior = intent_to_family.get(intent_key)
        if prior is not None and (case.paraphrase_family is None or prior != (case.intent_family, case.paraphrase_family)):
            raise ValueError(f"{cid}: duplicate intent requires common paraphrase family")
        intent_to_family[intent_key] = (case.intent_family, case.paraphrase_family)
        if case.paraphrase_family is not None:
            earlier_intent = family_to_intent.setdefault(case.paraphrase_family, intent_key)
            if earlier_intent != intent_key:
                raise ValueError(f"{cid}: paraphrase family spans different intents")
            family_counts[case.paraphrase_family] = family_counts.get(case.paraphrase_family, 0) + 1
            if family_counts[case.paraphrase_family] > 3:
                raise ValueError(f"{cid}: paraphrase family is over-represented")

        actual_offerings = _matching_offerings(case, seed)
        actual_keys = [offering.offering_key for offering in actual_offerings]
        gold_keys = [key.sort_key for key in case.expected.offering_keys]
        if gold_keys != actual_keys:
            raise ValueError(f"{cid}: expected offering keys disagree with seed-only filter interpretation")
        if len(actual_keys) >= ir.limit:
            raise ValueError(f"{cid}: LIMIT could truncate matching seed rows")
        if len(case.expected.rows) != len(actual_offerings):
            raise ValueError(f"{cid}: expected row count mismatch")
        for row_index, (offering, row) in enumerate(zip(actual_offerings, case.expected.rows)):
            if set(row) != set(ir.select) or len(row) != len(ir.select):
                raise ValueError(f"{cid}: row {row_index} projection shape mismatch")
            seed_row = semantic_row(offering, ir.select)
            for name, gold in row.items():
                if not _same_typed_value(seed_row[name], gold, fields[name].data_type):
                    raise ValueError(f"{cid}: row {row_index} value or type mismatch for {name}")
