"""Deterministic aggregate of synthetic evaluation attempts.

Only counts, stable identifiers, fingerprints, and controlled reason codes leave
this boundary. Execution rows, SQL, prompts, exceptions, and elapsed times do not.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Sequence

from .contracts import EvaluationSet, ReviewStatus
from .coverage import CoverageReport, evaluation_set_fingerprint
from .run_contracts import (
    AttemptMetrics,
    CaseAttempt,
    CaseConsistency,
    ComparisonStatus,
    EvaluationRunConfig,
    EvaluationRunReport,
    FixtureRunMetadata,
    Metric,
)


def _metric(count: int, denominator: int) -> Metric:
    return Metric(count=count, denominator=denominator)


def _attempt_metrics(attempts: Sequence[CaseAttempt]) -> AttemptMetrics:
    total = len(attempts)
    comparable = sum(item.comparison_status is ComparisonStatus.COMPARABLE for item in attempts)
    correct = sum(item.semantic_correct is True for item in attempts)
    failures: Counter[str] = Counter()
    for attempt in attempts:
        if attempt.failure_code:
            stage = attempt.failure_stage.value if attempt.failure_stage else "RUNNER"
            failures[f"{stage}:{attempt.failure_code}"] += 1
    return AttemptMetrics(
        scheduled_attempts=total,
        completed_attempts=total,
        structural_ir_exact=_metric(sum(item.structural_ir_exact is True for item in attempts), total),
        structural_ir_evaluable=_metric(sum(item.structural_ir_exact is not None for item in attempts), total),
        semantic_correct=_metric(correct, total),
        semantic_correct_among_comparable=_metric(correct, comparable),
        execution_success=_metric(sum(item.execution_success for item in attempts), total),
        not_comparable=_metric(sum(item.comparison_status in {
            ComparisonStatus.NOT_COMPARABLE_PROJECTION,
            ComparisonStatus.COMPARISON_ERROR,
        } for item in attempts), total),
        failure_by_stage_and_code=dict(sorted(failures.items())),
    )


def _case_consistency(case_id: str, attempts: Sequence[CaseAttempt]) -> CaseConsistency:
    # The schedule check guarantees one attempt per configured variant.
    if len(attempts) < 3:
        return CaseConsistency(
            case_id=case_id, ir_consistent=None, result_consistent=None,
            cross_schema_consistent=None, reason_code="INSUFFICIENT_VARIANTS",
        )
    ir_hashes = [item.ir_fingerprint for item in attempts]
    ir_consistent = len(set(ir_hashes)) == 1 if all(ir_hashes) else None
    if not all(item.comparison_status is ComparisonStatus.COMPARABLE for item in attempts):
        if any(item.comparison_status is ComparisonStatus.NOT_EXECUTED for item in attempts):
            reason = "NOT_EXECUTED"
        elif any(item.comparison_status is ComparisonStatus.COMPARISON_ERROR for item in attempts):
            reason = "COMPARISON_ERROR"
        else:
            reason = "NOT_COMPARABLE_PROJECTION"
        return CaseConsistency(
            case_id=case_id, ir_consistent=ir_consistent, result_consistent=None,
            cross_schema_consistent=None, reason_code=reason,
        )
    result_consistent = len({item.result_fingerprint for item in attempts}) == 1
    if ir_consistent is None:
        combined = None
        reason = "IR_NOT_AVAILABLE"
    else:
        combined = ir_consistent and result_consistent
        reason = "CONSISTENT" if combined else "CROSS_SCHEMA_INCONSISTENCY"
    return CaseConsistency(
        case_id=case_id, ir_consistent=ir_consistent,
        result_consistent=result_consistent, cross_schema_consistent=combined,
        reason_code=reason,
    )


def _coverage_tags(case) -> tuple[str, ...]:
    cardinality = "zero" if case.result_cardinality == 0 else "one" if case.result_cardinality == 1 else "multiple"
    tags = {
        f"join_steps:{case.normalized_join_steps}",
        f"cardinality:{cardinality}",
        f"filter_count:{case.filter_count}",
        f"intent_family:{case.intent_family}",
    }
    tags.update(f"field:{field}" for field in [*case.selected_fields, *case.filter_fields])
    tags.update(f"operator:{operator}" for operator in case.operators)
    if case.paraphrase_family:
        tags.add(f"paraphrase_family:{case.paraphrase_family}")
    return tuple(sorted(tags))


def build_run_report(
    config: EvaluationRunConfig,
    gold: EvaluationSet,
    fixture_metadata: Sequence[FixtureRunMetadata],
    attempts: Sequence[CaseAttempt],
    coverage: CoverageReport,
) -> EvaluationRunReport:
    """Require every scheduled attempt and aggregate without success-only bias."""

    expected = [(case.case_id, variant) for case in gold.cases for variant in config.variants]
    actual = [(item.case_id, item.variant) for item in attempts]
    if actual != expected:
        raise ValueError("run attempts must match every case×variant exactly in scheduled order")
    if [item.variant for item in fixture_metadata] != config.variants:
        raise ValueError("fixture metadata must match selected variant order")
    if coverage.gold_set_fingerprint != evaluation_set_fingerprint(gold):
        raise ValueError("coverage and gold set fingerprints differ")
    if coverage.benchmark_id != gold.benchmark_id or coverage.benchmark_version != gold.version:
        raise ValueError("coverage and gold benchmark identity differ")
    if (
        coverage.seed_fingerprint != gold.seed_fingerprint
        or coverage.ontology_fingerprint != gold.ontology_fingerprint
        or coverage.query_ir_fingerprint != gold.query_ir_fingerprint
    ):
        raise ValueError("coverage and gold contract fingerprints differ")
    if [item.case_id for item in coverage.cases] != [case.case_id for case in gold.cases]:
        raise ValueError("coverage and gold case IDs differ")
    if any(item.request_id != _expected_request_id(config, gold, item) for item in attempts):
        raise ValueError("request IDs must be deterministic for case and variant")

    case_attempts = {
        case.case_id: [item for item in attempts if item.case_id == case.case_id]
        for case in gold.cases
    }
    consistency = [_case_consistency(case.case_id, case_attempts[case.case_id]) for case in gold.cases]
    by_variant = {
        variant: _attempt_metrics([item for item in attempts if item.variant == variant])
        for variant in config.variants
    }
    by_tag: defaultdict[str, list[CaseAttempt]] = defaultdict(list)
    for case in coverage.cases:
        for tag in _coverage_tags(case):
            by_tag[tag].extend(case_attempts[case.case_id])
    pending = any(case.review_status is ReviewStatus.NEEDS_HUMAN_REVIEW for case in gold.cases)
    warnings = []
    if config.oracle_smoke:
        warnings.append("ORACLE_SMOKE_NOT_MODEL_ACCURACY")
    if pending:
        warnings.append("GOLD_NEEDS_HUMAN_REVIEW")
    consistency_denominator = len(consistency) if len(config.variants) >= 3 else 0
    return EvaluationRunReport(
        config=config,
        benchmark_id=gold.benchmark_id,
        benchmark_version=gold.version,
        gold_set_fingerprint=coverage.gold_set_fingerprint,
        seed_fingerprint=gold.seed_fingerprint,
        ontology_fingerprint=gold.ontology_fingerprint,
        query_ir_fingerprint=gold.query_ir_fingerprint,
        fixture_metadata=list(fixture_metadata),
        gold_human_review_pending=pending,
        warnings=warnings,
        attempts=list(attempts),
        case_consistency=consistency,
        totals=_attempt_metrics(attempts),
        by_variant=by_variant,
        by_coverage_tag={tag: _attempt_metrics(by_tag[tag]) for tag in sorted(by_tag)},
        cross_schema_consistent=_metric(sum(item.cross_schema_consistent is True for item in consistency), consistency_denominator),
        ir_consistent=_metric(sum(item.ir_consistent is True for item in consistency), consistency_denominator),
        result_consistent=_metric(sum(item.result_consistent is True for item in consistency), consistency_denominator),
    )


def _expected_request_id(config: EvaluationRunConfig, gold: EvaluationSet, attempt: CaseAttempt) -> str:
    """Stable, ASCII-only ID; runner and report use the same explicit convention."""

    return deterministic_request_id(config, gold, attempt.case_id, attempt.variant)


def deterministic_request_id(
    config: EvaluationRunConfig, gold: EvaluationSet, case_id: str, variant: str
) -> str:
    """Bound ID length even if the benchmark label is unusually long."""

    benchmark_ref = hashlib.sha256(
        f"{gold.benchmark_id}:{gold.version}".encode("utf-8")
    ).hexdigest()[:16]
    return f"{config.model_run_id}:{benchmark_ref}:{case_id}:{variant}"


def render_run_summary(report: EvaluationRunReport) -> str:
    """Short human-readable status; never presents oracle results as model scores."""

    totals = report.totals
    lines = [
        f"Evaluation run: {report.config.model_run_id} ({report.config.model_label})",
        f"Benchmark: {report.benchmark_id} v{report.benchmark_version}",
        f"Variants: {', '.join(report.config.variants)}",
    ]
    if report.config.oracle_smoke:
        lines.append("WARNING: ORACLE SMOKE ONLY - gold answers supplied; not model performance.")
    if report.gold_human_review_pending:
        lines.append("WARNING: Gold questions and answers still need independent human review.")
    lines.extend([
        f"Attempts: {totals.completed_attempts}/{totals.scheduled_attempts}",
        f"Harness execution: {totals.execution_success.count}/{totals.execution_success.denominator}",
        f"Structural IR exact: {totals.structural_ir_exact.count}/{totals.structural_ir_exact.denominator} "
        f"({totals.structural_ir_evaluable.count} evaluable)",
        f"Semantic gold match: {totals.semantic_correct.count}/{totals.semantic_correct.denominator} "
        f"({totals.semantic_correct_among_comparable.denominator} comparable)",
        f"Projection/comparison not comparable: {totals.not_comparable.count}/{totals.not_comparable.denominator}",
        f"Cross-schema consistent: {report.cross_schema_consistent.count}/{report.cross_schema_consistent.denominator} cases",
    ])
    return "\n".join(lines) + "\n"
