from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.adaptive.evaluation.paired_prompt_analysis import paired_prompt_summary
from backend.adaptive.evaluation.run_contracts import (
    AttemptMetrics,
    CaseAttempt,
    CaseConsistency,
    ComparisonStatus,
    EvaluationRunConfig,
    EvaluationRunReport,
    FixtureRunMetadata,
    Metric,
)
from backend.adaptive.harness import RecordedFailure, RecordedSuccess
from backend.adaptive.harness.recorded import ModelRecording
from backend.adaptive.harness.recording_bundle_contracts import (
    LoadedRecordingBundle,
    ProviderKind,
    RecordingArtifactMetadata,
    RecordingBundleManifest,
    RecordingProvenance,
)

QUESTION_FP = "a" * 64
MAPPING_FP = "b" * 64
DATABASE_ID = "synthetic-db"
DIGEST = "d" * 64
GENERATION_CONFIG = "e" * 64


def _report(prefix: str, exact: set[int], unknown: set[int], **overrides):
    attempts = [SimpleNamespace(
        case_id=f"P2-{i // 3 + 1:03d}",
        variant=("flat", "normalized", "legacy")[i % 3],
        request_id=f"{prefix}-{i}",
        structural_ir_exact=i in exact,
        execution_success=i not in unknown,
        semantic_correct=i in exact,
        failure_code="UNKNOWN_FIELD" if i in unknown else None,
    ) for i in range(120)]
    defaults = dict(
        attempts=attempts,
        gold_set_fingerprint="a",
        ontology_fingerprint="b",
        query_ir_fingerprint="c",
        seed_fingerprint="d",
        benchmark_id="synthetic-fixture-benchmark-v1",
        benchmark_version=1,
        config=SimpleNamespace(
            model_run_id=f"{prefix}-run",
            model_label=f"{prefix}-label",
            variants=["flat", "normalized", "legacy"],
        ),
        fixture_metadata=[
            SimpleNamespace(
                variant="flat", catalog_fingerprint="cf1",
                mapping_fingerprint="mf1", mapping_version=1,
            ),
            SimpleNamespace(
                variant="normalized", catalog_fingerprint="cf2",
                mapping_fingerprint="mf2", mapping_version=1,
            ),
            SimpleNamespace(
                variant="legacy", catalog_fingerprint="cf3",
                mapping_fingerprint="mf3", mapping_version=1,
            ),
        ],
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _provenance(**overrides):
    defaults = dict(
        provider_kind="OLLAMA",
        provider_id="local-ollama",
        provider_version="0.34.2",
        model_id="text2sql_qwen:latest",
        model_artifact_sha256=DIGEST,
        prompt_template_sha256="f" * 64,
        generation_config_sha256=GENERATION_CONFIG,
        verification_status="DECLARED_UNVERIFIED",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _bundle(prefix: str, provenance=None, **overrides):
    """Bundle whose own recording is the single source of compared content."""
    defaults = dict(
        question_sha256=QUESTION_FP,
        mapping_sha256=MAPPING_FP,
        database_id=DATABASE_ID,
    )
    defaults.update(overrides)
    provenance = provenance if provenance is not None else _provenance(
        prompt_template_sha256=("f" if prefix == "v1" else "9") * 64,
    )
    records = [SimpleNamespace(
        request_id=f"{prefix}-{i}",
        question_sha256=defaults["question_sha256"],
        mapping_sha256=defaults["mapping_sha256"],
        database_id=defaults["database_id"],
    ) for i in range(120)]
    return SimpleNamespace(
        manifest=SimpleNamespace(
            model_run_id=f"{prefix}-run",
            provenance=provenance,
        ),
        recording=SimpleNamespace(
            model_run_id=f"{prefix}-run",
            records=records,
        ),
    )


def _pair(exact_v1=None, exact_v2=None, unknown_v1=None, unknown_v2=None):
    left = _report("v1", exact_v1 or set(), unknown_v1 or set())
    right = _report("v2", exact_v2 or set(), unknown_v2 or set())
    return left, right, _bundle("v1"), _bundle("v2")


def _summarize(left, right, v1_bundle, v2_bundle):
    return paired_prompt_summary(
        left, right, v1_bundle=v1_bundle, v2_bundle=v2_bundle,
    )


def test_paired_summary_matches_case_and_variant_with_fixed_denominator() -> None:
    left, right, v1_bundle, v2_bundle = _pair({0, 1}, {1, 2}, {2, 3}, {3})
    summary = _summarize(left, right, v1_bundle, v2_bundle)
    assert summary["denominator"] == 120
    assert summary["structural_exact"] == {"v1": 2, "v2": 2, "gained": 1, "lost": 1}
    assert summary["unknown_field"] == {"v1": 2, "v2": 1, "gained": 0, "lost": 1}
    assert summary["v1_missing_records"] == summary["v2_missing_records"] == 0
    assert summary["v1_provider_failures"] == summary["v2_provider_failures"] == 0


def test_paired_summary_ignores_prompt_template_and_label_differences() -> None:
    # v1/v2 prompt template hashes differ and report labels differ, but the
    # manifest model tag is identical, so comparison must still succeed.
    left, right, v1_bundle, v2_bundle = _pair()
    assert v1_bundle.manifest.provenance.prompt_template_sha256 != (
        v2_bundle.manifest.provenance.prompt_template_sha256
    )
    assert left.config.model_label != right.config.model_label
    summary = _summarize(left, right, v1_bundle, v2_bundle)
    assert summary["denominator"] == 120


def test_paired_summary_requires_bundles() -> None:
    left, right, _v1_bundle, _v2_bundle = _pair()
    with pytest.raises(ValueError, match="PAIRED_PROVENANCE_MISSING"):
        paired_prompt_summary(left, right, v1_bundle=None, v2_bundle=None)


def test_paired_summary_rejects_mismatched_case_id() -> None:
    left, right, v1_bundle, v2_bundle = _pair({0, 1}, {1, 2}, {2, 3}, {3})
    right.attempts[0].case_id = "P2-999"
    with pytest.raises(ValueError, match="PAIRED_TARGET_MISMATCH"):
        _summarize(left, right, v1_bundle, v2_bundle)


def test_paired_summary_rejects_mismatched_benchmark_id() -> None:
    left, right, v1_bundle, v2_bundle = _pair()
    right.benchmark_id = "other-benchmark"
    with pytest.raises(ValueError, match="PAIRED_BENCHMARK_ID_MISMATCH"):
        _summarize(left, right, v1_bundle, v2_bundle)


def test_paired_summary_rejects_mismatched_benchmark_version() -> None:
    left, right, v1_bundle, v2_bundle = _pair()
    right.benchmark_version = 2
    with pytest.raises(ValueError, match="PAIRED_BENCHMARK_VERSION_MISMATCH"):
        _summarize(left, right, v1_bundle, v2_bundle)


def test_paired_summary_rejects_mismatched_seed_fingerprint() -> None:
    left, right, v1_bundle, v2_bundle = _pair()
    right.seed_fingerprint = "e"
    with pytest.raises(ValueError, match="PAIRED_SEED_FINGERPRINT_MISMATCH"):
        _summarize(left, right, v1_bundle, v2_bundle)


def test_paired_summary_rejects_mismatched_gold_set_fingerprint() -> None:
    left, right, v1_bundle, v2_bundle = _pair()
    right.gold_set_fingerprint = "x"
    with pytest.raises(ValueError, match="PAIRED_GOLD_SET_FINGERPRINT_MISMATCH"):
        _summarize(left, right, v1_bundle, v2_bundle)


def test_paired_summary_rejects_mismatched_ontology_fingerprint() -> None:
    left, right, v1_bundle, v2_bundle = _pair()
    right.ontology_fingerprint = "y"
    with pytest.raises(ValueError, match="PAIRED_ONTOLOGY_FINGERPRINT_MISMATCH"):
        _summarize(left, right, v1_bundle, v2_bundle)


def test_paired_summary_rejects_mismatched_query_ir_fingerprint() -> None:
    left, right, v1_bundle, v2_bundle = _pair()
    right.query_ir_fingerprint = "z"
    with pytest.raises(ValueError, match="PAIRED_QUERY_IR_FINGERPRINT_MISMATCH"):
        _summarize(left, right, v1_bundle, v2_bundle)


def test_paired_summary_rejects_mismatched_variants() -> None:
    left = _report("v1", set(), set())
    right = _report(
        "v2", set(), set(),
        config=SimpleNamespace(
            model_run_id="v2-run", model_label="v2-label",
            variants=["flat", "normalized"],
        ),
    )
    with pytest.raises(ValueError, match="PAIRED_VARIANT_MISMATCH"):
        _summarize(left, right, _bundle("v1"), _bundle("v2"))


def test_paired_summary_rejects_mismatched_fixture_catalog_fingerprint() -> None:
    left, right, v1_bundle, v2_bundle = _pair()
    right.fixture_metadata[0] = SimpleNamespace(
        variant="flat", catalog_fingerprint="different",
        mapping_fingerprint="mf1", mapping_version=1,
    )
    with pytest.raises(ValueError, match="PAIRED_FIXTURE_CATALOG_FINGERPRINT_MISMATCH"):
        _summarize(left, right, v1_bundle, v2_bundle)


def test_paired_summary_rejects_mismatched_fixture_mapping_fingerprint() -> None:
    left, right, v1_bundle, v2_bundle = _pair()
    right.fixture_metadata[1] = SimpleNamespace(
        variant="normalized", catalog_fingerprint="cf2",
        mapping_fingerprint="different", mapping_version=1,
    )
    with pytest.raises(ValueError, match="PAIRED_FIXTURE_MAPPING_FINGERPRINT_MISMATCH"):
        _summarize(left, right, v1_bundle, v2_bundle)


def test_paired_summary_rejects_mismatched_fixture_mapping_version() -> None:
    left, right, v1_bundle, v2_bundle = _pair()
    right.fixture_metadata[2] = SimpleNamespace(
        variant="legacy", catalog_fingerprint="cf3",
        mapping_fingerprint="mf3", mapping_version=2,
    )
    with pytest.raises(ValueError, match="PAIRED_FIXTURE_MAPPING_VERSION_MISMATCH"):
        _summarize(left, right, v1_bundle, v2_bundle)


def test_paired_summary_rejects_mismatched_fixture_variant_order() -> None:
    left, right, v1_bundle, v2_bundle = _pair()
    right.fixture_metadata = list(reversed(right.fixture_metadata))
    with pytest.raises(ValueError, match="PAIRED_FIXTURE_VARIANT_ORDER_MISMATCH"):
        _summarize(left, right, v1_bundle, v2_bundle)


def test_paired_summary_rejects_model_tag_mismatch() -> None:
    left, right, v1_bundle, v2_bundle = _pair()
    v2_bundle.manifest.provenance.model_id = "other-model:latest"
    with pytest.raises(ValueError, match="PAIRED_MODEL_TAG_MISMATCH") as excinfo:
        _summarize(left, right, v1_bundle, v2_bundle)
    assert str(excinfo.value) == "PAIRED_MODEL_TAG_MISMATCH"


def test_paired_summary_rejects_provider_mismatch() -> None:
    left, right, v1_bundle, v2_bundle = _pair()
    v2_bundle.manifest.provenance.provider_version = "9.9.9"
    with pytest.raises(ValueError, match="PAIRED_PROVIDER_MISMATCH") as excinfo:
        _summarize(left, right, v1_bundle, v2_bundle)
    assert str(excinfo.value) == "PAIRED_PROVIDER_MISMATCH"


def test_paired_summary_rejects_generation_config_mismatch() -> None:
    left, right, v1_bundle, v2_bundle = _pair()
    v2_bundle.manifest.provenance.generation_config_sha256 = "0" * 64
    with pytest.raises(ValueError, match="PAIRED_GENERATION_CONFIG_MISMATCH"):
        _summarize(left, right, v1_bundle, v2_bundle)


def test_paired_summary_flags_missing_model_digest() -> None:
    left, right, v1_bundle, v2_bundle = _pair()
    v2_bundle.manifest.provenance.model_artifact_sha256 = None
    with pytest.raises(ValueError, match="PAIRED_MODEL_DIGEST_UNVERIFIED"):
        _summarize(left, right, v1_bundle, v2_bundle)


def test_paired_summary_rejects_model_digest_mismatch() -> None:
    left, right, v1_bundle, v2_bundle = _pair()
    v2_bundle.manifest.provenance.model_artifact_sha256 = "1" * 64
    with pytest.raises(ValueError, match="PAIRED_MODEL_DIGEST_MISMATCH"):
        _summarize(left, right, v1_bundle, v2_bundle)


def test_paired_summary_rejects_tampered_bundle_question_fingerprint() -> None:
    # Same request IDs, but the bundle's own entry carries a different
    # question fingerprint. Comparison must fail because bundle content
    # is the only compared recording.
    left, right, v1_bundle, v2_bundle = _pair()
    v2_bundle.recording.records[7].question_sha256 = "c" * 64
    with pytest.raises(ValueError, match="PAIRED_QUESTION_FINGERPRINT_MISMATCH") as excinfo:
        _summarize(left, right, v1_bundle, v2_bundle)
    assert str(excinfo.value) == "PAIRED_QUESTION_FINGERPRINT_MISMATCH"


def test_paired_summary_rejects_tampered_bundle_mapping_fingerprint() -> None:
    left, right, v1_bundle, v2_bundle = _pair()
    v2_bundle.recording.records[7].mapping_sha256 = "c" * 64
    with pytest.raises(ValueError, match="PAIRED_MAPPING_FINGERPRINT_MISMATCH"):
        _summarize(left, right, v1_bundle, v2_bundle)


def test_paired_summary_rejects_tampered_bundle_database_context() -> None:
    left, right, v1_bundle, v2_bundle = _pair()
    v2_bundle.recording.records[7].database_id = "other-db"
    with pytest.raises(ValueError, match="PAIRED_DATABASE_CONTEXT_MISMATCH"):
        _summarize(left, right, v1_bundle, v2_bundle)


def test_paired_summary_rejects_model_run_id_mismatch() -> None:
    left, right, v1_bundle, v2_bundle = _pair()
    v1_bundle.recording.model_run_id = "stale-run"
    with pytest.raises(ValueError, match="PAIRED_MODEL_RUN_ID_MISMATCH"):
        _summarize(left, right, v1_bundle, v2_bundle)


def test_paired_summary_rejects_manifest_run_id_mismatch() -> None:
    left, right, v1_bundle, v2_bundle = _pair()
    v1_bundle.manifest.model_run_id = "stale-run"
    with pytest.raises(ValueError, match="PAIRED_MODEL_RUN_ID_MISMATCH"):
        _summarize(left, right, v1_bundle, v2_bundle)


def test_paired_summary_rejects_missing_record() -> None:
    left, right, v1_bundle, v2_bundle = _pair()
    v1_bundle.recording.records.pop()
    with pytest.raises(ValueError, match="PAIRED_REQUEST_ID_MISMATCH"):
        _summarize(left, right, v1_bundle, v2_bundle)


def test_paired_summary_rejects_extra_record() -> None:
    left, right, v1_bundle, v2_bundle = _pair()
    v1_bundle.recording.records.append(SimpleNamespace(
        request_id="v1-extra", question_sha256=QUESTION_FP,
        mapping_sha256=MAPPING_FP, database_id=DATABASE_ID,
    ))
    with pytest.raises(ValueError, match="PAIRED_REQUEST_ID_MISMATCH"):
        _summarize(left, right, v1_bundle, v2_bundle)


def test_paired_summary_rejects_duplicate_recording_id() -> None:
    left, right, v1_bundle, v2_bundle = _pair()
    records = v1_bundle.recording.records
    records[1].request_id = records[0].request_id
    with pytest.raises(ValueError, match="PAIRED_RECORDING_DUPLICATE_REQUEST_ID"):
        _summarize(left, right, v1_bundle, v2_bundle)


# --- Real contract models (no duck-typed namespaces for bundles/recordings) ---

VARIANTS = ("flat", "normalized", "legacy")


def _real_attempt(index: int, prefix: str, unknown: set[int]) -> CaseAttempt:
    if index in unknown:
        return CaseAttempt(
            case_id=f"P2-{index // 3 + 1:03d}",
            variant=VARIANTS[index % 3],  # type: ignore[arg-type]
            request_id=f"{prefix}-{index}",
            structural_ir_exact=None,
            ir_fingerprint=None,
            execution_success=False,
            comparison_status=ComparisonStatus.NOT_EXECUTED,
            semantic_correct=None,
            result_fingerprint=None,
            failure_stage=None,
            failure_code="UNKNOWN_FIELD",
        )
    return CaseAttempt(
        case_id=f"P2-{index // 3 + 1:03d}",
        variant=VARIANTS[index % 3],  # type: ignore[arg-type]
        request_id=f"{prefix}-{index}",
        structural_ir_exact=True,
        ir_fingerprint="1" * 64,
        execution_success=True,
        comparison_status=ComparisonStatus.COMPARABLE,
        semantic_correct=True,
        result_fingerprint="2" * 64,
        failure_stage=None,
        failure_code=None,
    )


def _real_metrics(count: int) -> AttemptMetrics:
    metric = Metric(count=count, denominator=120)
    return AttemptMetrics(
        scheduled_attempts=120,
        completed_attempts=120,
        structural_ir_exact=metric,
        structural_ir_evaluable=metric,
        semantic_correct=metric,
        semantic_correct_among_comparable=metric,
        execution_success=metric,
        not_comparable=Metric(count=0, denominator=120),
        failure_by_stage_and_code={},
    )


def _real_report(prefix: str, unknown: set[int]) -> EvaluationRunReport:
    attempts = [_real_attempt(index, prefix, unknown) for index in range(120)]
    metrics = _real_metrics(120 - len(unknown))
    return EvaluationRunReport(
        config=EvaluationRunConfig(
            model_run_id=f"{prefix}-run",
            model_label=f"{prefix}-label",
            variants=list(VARIANTS),
            oracle_smoke=False,
        ),
        benchmark_id="synthetic-fixture-benchmark-v1",
        benchmark_version=1,
        gold_set_fingerprint="a" * 64,
        seed_fingerprint="b" * 64,
        ontology_fingerprint="c" * 64,
        query_ir_fingerprint="d" * 64,
        fixture_metadata=[
            FixtureRunMetadata(
                variant=variant,  # type: ignore[arg-type]
                catalog_fingerprint="e" * 64,
                mapping_fingerprint="f" * 64,
                mapping_version=1,
            )
            for variant in VARIANTS
        ],
        gold_human_review_pending=True,
        warnings=[],
        attempts=attempts,
        case_consistency=[
            CaseConsistency(
                case_id=f"P2-{number:03d}",
                ir_consistent=True,
                result_consistent=True,
                cross_schema_consistent=True,
                reason_code="CONSISTENT",
            )
            for number in range(1, 41)
        ],
        totals=metrics,
        by_variant={variant: metrics for variant in VARIANTS},
        by_coverage_tag={},
        cross_schema_consistent=Metric(count=120, denominator=120),
        ir_consistent=Metric(count=120, denominator=120),
        result_consistent=Metric(count=120, denominator=120),
    )


def _real_recording(prefix: str, failed: set[int] | None = None) -> ModelRecording:
    failed = failed or set()
    records = []
    for index in range(120):
        base = dict(
            request_id=f"{prefix}-{index}",
            database_id=DATABASE_ID,
            question_sha256=QUESTION_FP,
            mapping_sha256=MAPPING_FP,
        )
        if index in failed:
            records.append(RecordedFailure(
                status="FAILED", failure_code="TIMEOUT", **base,
            ))
        else:
            records.append(RecordedSuccess(
                status="SUCCEEDED",
                response={"select": ["Course.name"], "filters": []},
                **base,
            ))
    return ModelRecording(model_run_id=f"{prefix}-run", records=records)


def _real_bundle(prefix: str, recording: ModelRecording) -> LoadedRecordingBundle:
    provenance = RecordingProvenance(
        provider_kind=ProviderKind.OLLAMA,
        provider_id="local-ollama",
        provider_version="0.34.2",
        model_id="text2sql_qwen:latest",
        model_artifact_sha256=DIGEST,
        prompt_template_sha256=("f" if prefix == "v1" else "9") * 64,
        generation_config_sha256=GENERATION_CONFIG,
        collector_version="recording-storage-v1",
    )
    successes = sum(isinstance(item, RecordedSuccess) for item in recording.records)
    manifest = RecordingBundleManifest(
        generation_id="0" * 64,
        recording_schema_version="recorded-model-v1",
        model_run_id=recording.model_run_id,
        record_count=len(recording.records),
        success_count=successes,
        failure_count=len(recording.records) - successes,
        provenance=provenance,
        provenance_sha256="1" * 64,
        recording=RecordingArtifactMetadata(
            path="generations/" + "0" * 64 + "/recording.json",
            sha256="2" * 64,
            bytes=128,
        ),
    )
    return LoadedRecordingBundle(manifest=manifest, recording=recording)


def test_paired_summary_with_real_contract_models() -> None:
    left = _real_report("v1", {2, 3})
    right = _real_report("v2", {3})
    v1_bundle = _real_bundle("v1", _real_recording("v1"))
    v2_bundle = _real_bundle("v2", _real_recording("v2"))
    summary = paired_prompt_summary(
        left, right, v1_bundle=v1_bundle, v2_bundle=v2_bundle,
    )
    assert summary["denominator"] == 120
    assert summary["unknown_field"] == {"v1": 2, "v2": 1, "gained": 0, "lost": 1}
    assert summary["v1_provider_failures"] == summary["v2_provider_failures"] == 0
    assert summary["v1_missing_records"] == summary["v2_missing_records"] == 0


def test_paired_summary_with_real_models_counts_provider_failures() -> None:
    left = _real_report("v1", set())
    right = _real_report("v2", set())
    v1_bundle = _real_bundle("v1", _real_recording("v1", failed={5, 6}))
    v2_bundle = _real_bundle("v2", _real_recording("v2"))
    summary = paired_prompt_summary(
        left, right, v1_bundle=v1_bundle, v2_bundle=v2_bundle,
    )
    assert summary["v1_provider_failures"] == 2
    assert summary["v2_provider_failures"] == 0


def test_paired_summary_with_real_models_rejects_tampered_fingerprint() -> None:
    # Same request IDs, but one bundle entry carries a different question
    # fingerprint. Only bundle content is compared, so this must fail.
    left = _real_report("v1", set())
    right = _real_report("v2", set())
    v2_recording = _real_recording("v2")
    entries = list(v2_recording.records)
    entries[7] = entries[7].model_copy(update={"question_sha256": "c" * 64})
    tampered = ModelRecording(
        model_run_id=v2_recording.model_run_id, records=entries,
    )
    with pytest.raises(ValueError, match="PAIRED_QUESTION_FINGERPRINT_MISMATCH"):
        paired_prompt_summary(
            left, right,
            v1_bundle=_real_bundle("v1", _real_recording("v1")),
            v2_bundle=_real_bundle("v2", tampered),
        )


def test_paired_summary_with_real_models_rejects_run_id_mismatch() -> None:
    left = _real_report("v1", set())
    right = _real_report("v2", set())
    v1_bundle = _real_bundle("v1", _real_recording("v1"))
    stale_manifest = v1_bundle.manifest.model_copy(
        update={"model_run_id": "stale-run"},
    )
    stale = LoadedRecordingBundle(
        manifest=stale_manifest, recording=v1_bundle.recording,
    )
    with pytest.raises(ValueError, match="PAIRED_MODEL_RUN_ID_MISMATCH"):
        paired_prompt_summary(
            left, right,
            v1_bundle=stale,
            v2_bundle=_real_bundle("v2", _real_recording("v2")),
        )
