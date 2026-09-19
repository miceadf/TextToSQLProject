from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.adaptive.evaluation.paired_prompt_analysis import paired_prompt_summary


def _report(prefix: str, exact: set[int], unknown: set[int]):
    attempts = [SimpleNamespace(
        case_id=f"P2-{i // 3 + 1:03d}",
        variant=("flat", "normalized", "legacy")[i % 3],
        request_id=f"{prefix}-{i}",
        structural_ir_exact=i in exact,
        execution_success=i not in unknown,
        semantic_correct=i in exact,
        failure_code="UNKNOWN_FIELD" if i in unknown else None,
    ) for i in range(120)]
    return SimpleNamespace(
        attempts=attempts, gold_set_fingerprint="a",
        ontology_fingerprint="b", query_ir_fingerprint="c",
    )


def test_paired_summary_matches_case_and_variant_with_fixed_denominator() -> None:
    left = _report("v1", {0, 1}, {2, 3})
    right = _report("v2", {1, 2}, {3})
    v1_records = SimpleNamespace(records=[SimpleNamespace(request_id=f"v1-{i}") for i in range(120)])
    v2_records = SimpleNamespace(records=[SimpleNamespace(request_id=f"v2-{i}") for i in range(120)])
    summary = paired_prompt_summary(left, right, v1_records, v2_records)
    assert summary["denominator"] == 120
    assert summary["structural_exact"] == {"v1": 2, "v2": 2, "gained": 1, "lost": 1}
    assert summary["unknown_field"] == {"v1": 2, "v2": 1, "gained": 0, "lost": 1}
    assert summary["v1_missing_records"] == summary["v2_missing_records"] == 0
    right.attempts[0].case_id = "P2-999"
    with pytest.raises(ValueError, match="PAIRED_TARGET_MISMATCH"):
        paired_prompt_summary(left, right, v1_records, v2_records)
