"""Deterministic descriptive coverage, never a model accuracy score."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..mapping.contracts import PublishedMappingProfile
from ..ontology.contracts import DomainOntology
from ..relations.contracts import JoinPlanStatus, PhysicalRelationGraph
from ..relations.planner import plan_join_path
from .contracts import EvaluationSet, ReviewStatus
from .reference import ontology_fingerprint, query_ir_fingerprint


class CaseCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    selected_fields: list[str]
    filter_fields: list[str]
    operators: list[str]
    filter_count: int = Field(ge=0)
    normalized_table_count: int = Field(ge=1)
    normalized_join_steps: int = Field(ge=0)
    result_cardinality: int = Field(ge=0)
    intent_family: str
    paraphrase_family: str | None
    review_status: ReviewStatus


class CoverageReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["fixture-evaluation-coverage-v1"] = "fixture-evaluation-coverage-v1"
    benchmark_id: str
    benchmark_version: int
    seed_fingerprint: str
    ontology_fingerprint: str
    query_ir_fingerprint: str
    gold_set_fingerprint: str
    case_count: int = Field(ge=0)
    field_case_counts: dict[str, int]
    operator_case_counts: dict[str, int]
    normalized_join_step_counts: dict[str, int]
    cardinality_bucket_counts: dict[str, int]
    filter_count_distribution: dict[str, int]
    intent_family_counts: dict[str, int]
    paraphrase_family_counts: dict[str, int]
    review_status_counts: dict[str, int]
    coverage_gaps: list[str]
    cases: list[CaseCoverage]


def evaluation_set_fingerprint(evaluation_set: EvaluationSet) -> str:
    payload = json.dumps(
        evaluation_set.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_coverage_report(
    evaluation_set: EvaluationSet,
    ontology: DomainOntology,
    normalized_graph: PhysicalRelationGraph,
    normalized_mapping: PublishedMappingProfile,
) -> CoverageReport:
    """Count semantics and FK-backed JOIN difficulty from approved fixture mapping."""

    if not ontology.accepts_version(evaluation_set.ontology_version):
        raise ValueError("evaluation ontology version mismatch")
    if evaluation_set.ontology_fingerprint != ontology_fingerprint(
        ontology.at_version(evaluation_set.ontology_version)
    ):
        raise ValueError("evaluation ontology fingerprint mismatch")
    if evaluation_set.query_ir_fingerprint != query_ir_fingerprint():
        raise ValueError("evaluation QueryIR fingerprint mismatch")
    if not ontology.accepts_version(normalized_mapping.ontology_version):
        raise ValueError("normalized mapping ontology version mismatch")
    field_counts: Counter[str] = Counter()
    operator_counts: Counter[str] = Counter()
    join_counts: Counter[str] = Counter()
    cardinality_counts: Counter[str] = Counter()
    filter_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    paraphrase_counts: Counter[str] = Counter()
    review_counts: Counter[str] = Counter()
    cases: list[CaseCoverage] = []

    for case in evaluation_set.cases:
        query = case.gold_query_ir
        selected = list(query.select)
        filtered = [item.field for item in query.filters]
        semantic_fields = sorted(set([*selected, *filtered]))
        plan = plan_join_path(normalized_graph, normalized_mapping, semantic_fields)
        if plan.status is not JoinPlanStatus.RESOLVED or plan.plan is None:
            raise ValueError("gold case lacks a unique normalized JOIN path")
        join_steps = len(plan.plan.steps)
        cardinality = case.expected.cardinality
        bucket = "zero" if cardinality == 0 else "one" if cardinality == 1 else "multiple"

        field_counts.update(semantic_fields)
        operator_counts.update({item.operator.value for item in query.filters})
        join_counts[str(join_steps)] += 1
        cardinality_counts[bucket] += 1
        filter_counts[str(len(query.filters))] += 1
        family_counts[case.intent_family] += 1
        if case.paraphrase_family:
            paraphrase_counts[case.paraphrase_family] += 1
        review_counts[case.review_status.value] += 1
        cases.append(CaseCoverage(
            case_id=case.case_id,
            selected_fields=selected,
            filter_fields=filtered,
            operators=[item.operator.value for item in query.filters],
            filter_count=len(query.filters),
            normalized_table_count=1 + join_steps,
            normalized_join_steps=join_steps,
            result_cardinality=cardinality,
            intent_family=case.intent_family,
            paraphrase_family=case.paraphrase_family,
            review_status=case.review_status,
        ))

    # Phase 8-18: the fields the evaluation set's ontology version had.
    missing_fields = sorted(set(ontology.fields_at(evaluation_set.ontology_version)) - set(field_counts))
    gaps = [f"unused semantic fields: {', '.join(missing_fields)}"] if missing_fields else []
    if not any(case.normalized_join_steps >= 2 for case in cases):
        gaps.append("no multi-step normalized JOIN case")
    if not cardinality_counts["zero"]:
        gaps.append("no zero-result case")
    if review_counts[ReviewStatus.NEEDS_HUMAN_REVIEW.value]:
        gaps.append("gold questions and answers still need independent human review")
    gaps.extend([
        "only six synthetic offerings; no institution- or schema-disjoint generalization",
        "all seed offerings share year 2026 and semester 1, so these filters have no discriminatory power",
    ])
    return CoverageReport(
        benchmark_id=evaluation_set.benchmark_id,
        benchmark_version=evaluation_set.version,
        seed_fingerprint=evaluation_set.seed_fingerprint,
        ontology_fingerprint=evaluation_set.ontology_fingerprint,
        query_ir_fingerprint=evaluation_set.query_ir_fingerprint,
        gold_set_fingerprint=evaluation_set_fingerprint(evaluation_set),
        case_count=len(cases),
        field_case_counts=dict(sorted(field_counts.items())),
        operator_case_counts=dict(sorted(operator_counts.items())),
        normalized_join_step_counts=dict(sorted(join_counts.items())),
        cardinality_bucket_counts=dict(sorted(cardinality_counts.items())),
        filter_count_distribution=dict(sorted(filter_counts.items())),
        intent_family_counts=dict(sorted(family_counts.items())),
        paraphrase_family_counts=dict(sorted(paraphrase_counts.items())),
        review_status_counts=dict(sorted(review_counts.items())),
        coverage_gaps=gaps,
        cases=cases,
    )


def render_review_table(evaluation_set: EvaluationSet, coverage: CoverageReport) -> str:
    """Human-readable draft checklist; does not imply human approval."""

    by_id = {item.case_id: item for item in coverage.cases}
    lines = [
        "# Phase 2 fixture v1 gold review table",
        "",
        "> Synthetic development fixture only. Automated tests do not grant human approval.",
        "",
        "| ID | 한국어 질문 | Gold select / filters | 기대 key | 기대 semantic rows | 행 | normalized JOIN | 상태 | 검토 근거 |",
        "| --- | --- | --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for case in evaluation_set.cases:
        info = by_id[case.case_id]
        filters = ", ".join(
            f"{item.field} {item.operator.value} {item.value}"
            for item in case.gold_query_ir.filters
        ) or "없음"
        keys = ", ".join(
            f"{item.year}/{item.semester}/{item.course_code}/{item.section}"
            for item in case.expected.offering_keys
        ) or "∅"
        question = case.question_ko.replace("|", "\\|").replace("\n", " ")
        intent = f"{', '.join(case.gold_query_ir.select)} / {filters}".replace("|", "\\|")
        expected_rows = json.dumps(
            case.expected.rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).replace("|", "\\|")
        rationale = case.review_rationale.replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {case.case_id} | {question} | {intent} | {keys} | {expected_rows} | "
            f"{case.expected.cardinality} | {info.normalized_join_steps} | "
            f"{case.review_status.value} | {rationale} |"
        )
    return "\n".join(lines) + "\n"
