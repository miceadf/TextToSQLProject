"""Aggregate two private recorded runs by identical case and fixture variant."""

from __future__ import annotations

from ..harness.recorded import ModelRecording, RecordedFailure
from .run_contracts import CaseAttempt, EvaluationRunReport


def _indexed(report: EvaluationRunReport) -> dict[tuple[str, str], CaseAttempt]:
    indexed = {(attempt.case_id, attempt.variant): attempt for attempt in report.attempts}
    if len(indexed) != len(report.attempts):
        raise ValueError("DUPLICATE_PAIRED_ATTEMPT")
    return indexed


def paired_prompt_summary(
    v1: EvaluationRunReport,
    v2: EvaluationRunReport,
    v1_recording: ModelRecording,
    v2_recording: ModelRecording,
) -> dict[str, object]:
    """Return counts only; no request IDs, questions, responses or paths."""
    left, right = _indexed(v1), _indexed(v2)
    if left.keys() != right.keys() or not left:
        raise ValueError("PAIRED_TARGET_MISMATCH")
    if len(left) != 120:
        raise ValueError("PAIRED_DENOMINATOR_MISMATCH")
    if (v1.gold_set_fingerprint != v2.gold_set_fingerprint
            or v1.ontology_fingerprint != v2.ontology_fingerprint
            or v1.query_ir_fingerprint != v2.query_ir_fingerprint):
        raise ValueError("PAIRED_CONTRACT_MISMATCH")
    record_sets = (
        {item.request_id: item for item in v1_recording.records},
        {item.request_id: item for item in v2_recording.records},
    )
    result: dict[str, object] = {"denominator": len(left)}
    for label, predicate in (
        ("structural_exact", lambda attempt: attempt.structural_ir_exact is True),
        ("execution_success", lambda attempt: attempt.execution_success),
        ("semantic_correct", lambda attempt: attempt.semantic_correct is True),
        ("unknown_field", lambda attempt: attempt.failure_code == "UNKNOWN_FIELD"),
    ):
        before = [predicate(left[key]) for key in sorted(left)]
        after = [predicate(right[key]) for key in sorted(left)]
        result[label] = {
            "v1": sum(before), "v2": sum(after),
            "gained": sum(not a and b for a, b in zip(before, after)),
            "lost": sum(a and not b for a, b in zip(before, after)),
        }
    for side, attempts, records in (("v1", left, record_sets[0]), ("v2", right, record_sets[1])):
        result[f"{side}_provider_failures"] = sum(
            isinstance(records.get(attempt.request_id), RecordedFailure)
            for attempt in attempts.values()
        )
        result[f"{side}_missing_records"] = sum(
            attempt.request_id not in records for attempt in attempts.values()
        )
    return result
