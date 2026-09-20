from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.adaptive.evaluation.paired_prompt_analysis import paired_prompt_summary

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


def _records(prefix: str, **overrides):
    defaults = dict(
        question_sha256=QUESTION_FP,
        mapping_sha256=MAPPING_FP,
        database_id=DATABASE_ID,
    )
    defaults.update(overrides)
    return SimpleNamespace(
        model_run_id=f"{prefix}-run",
        records=[SimpleNamespace(
            request_id=f"{prefix}-{i}",
            question_sha256=defaults["question_sha256"],
            mapping_sha256=defaults["mapping_sha256"],
            database_id=defaults["database_id"],
        ) for i in range(120)],
    )


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


def _bundle(prefix: str, recording, provenance=None):
    provenance = provenance if provenance is not None else _provenance(
        prompt_template_sha256=("f" if prefix == "v1" else "9") * 64,
    )
    return SimpleNamespace(
        manifest=SimpleNamespace(
            model_run_id=f"{prefix}-run",
            provenance=provenance,
        ),
        recording=SimpleNamespace(
            model_run_id=f"{prefix}-run",
            records=list(recording.records),
        ),
    )


def _pair(exact_v1=None, exact_v2=None, unknown_v1=None, unknown_v2=None):
    left = _report("v1", exact_v1 or set(), unknown_v1 or set())
    right = _report("v2", exact_v2 or set(), unknown_v2 or set())
    v1_recording = _records("v1")
    v2_recording = _records("v2")
    v1_bundle = _bundle("v1", v1_recording)
    v2_bundle = _bundle("v2", v2_recording)
    return left, right, v1_recording, v2_recording, v1_bundle, v2_bundle


def test_paired_summary_matches_case_and_variant_with_fixed_denominator() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair(
        {0, 1}, {1, 2}, {2, 3}, {3},
    )
    summary = paired_prompt_summary(
        left, right, v1_recording, v2_recording,
        v1_bundle=v1_bundle, v2_bundle=v2_bundle,
    )
    assert summary["denominator"] == 120
    assert summary["structural_exact"] == {"v1": 2, "v2": 2, "gained": 1, "lost": 1}
    assert summary["unknown_field"] == {"v1": 2, "v2": 1, "gained": 0, "lost": 1}
    assert summary["v1_missing_records"] == summary["v2_missing_records"] == 0
    assert summary["v1_provider_failures"] == summary["v2_provider_failures"] == 0


def test_paired_summary_ignores_prompt_template_and_label_differences() -> None:
    # v1/v2 prompt template hashes differ and report labels differ, but the
    # manifest model tag is identical, so comparison must still succeed.
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    assert v1_bundle.manifest.provenance.prompt_template_sha256 != (
        v2_bundle.manifest.provenance.prompt_template_sha256
    )
    assert left.config.model_label != right.config.model_label
    summary = paired_prompt_summary(
        left, right, v1_recording, v2_recording,
        v1_bundle=v1_bundle, v2_bundle=v2_bundle,
    )
    assert summary["denominator"] == 120


def test_paired_summary_requires_bundles() -> None:
    left, right, v1_recording, v2_recording, _v1_bundle, _v2_bundle = _pair()
    with pytest.raises(ValueError, match="PAIRED_PROVENANCE_MISSING"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=None, v2_bundle=None,
        )


def test_paired_summary_rejects_mismatched_case_id() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair(
        {0, 1}, {1, 2}, {2, 3}, {3},
    )
    right.attempts[0].case_id = "P2-999"
    with pytest.raises(ValueError, match="PAIRED_TARGET_MISMATCH"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )


def test_paired_summary_rejects_mismatched_benchmark_id() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    right.benchmark_id = "other-benchmark"
    with pytest.raises(ValueError, match="PAIRED_BENCHMARK_ID_MISMATCH"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )


def test_paired_summary_rejects_mismatched_benchmark_version() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    right.benchmark_version = 2
    with pytest.raises(ValueError, match="PAIRED_BENCHMARK_VERSION_MISMATCH"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )


def test_paired_summary_rejects_mismatched_seed_fingerprint() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    right.seed_fingerprint = "e"
    with pytest.raises(ValueError, match="PAIRED_SEED_FINGERPRINT_MISMATCH"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )


def test_paired_summary_rejects_mismatched_gold_set_fingerprint() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    right.gold_set_fingerprint = "x"
    with pytest.raises(ValueError, match="PAIRED_GOLD_SET_FINGERPRINT_MISMATCH"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )


def test_paired_summary_rejects_mismatched_ontology_fingerprint() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    right.ontology_fingerprint = "y"
    with pytest.raises(ValueError, match="PAIRED_ONTOLOGY_FINGERPRINT_MISMATCH"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )


def test_paired_summary_rejects_mismatched_query_ir_fingerprint() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    right.query_ir_fingerprint = "z"
    with pytest.raises(ValueError, match="PAIRED_QUERY_IR_FINGERPRINT_MISMATCH"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )


def test_paired_summary_rejects_mismatched_variants() -> None:
    left = _report("v1", set(), set())
    right = _report(
        "v2", set(), set(),
        config=SimpleNamespace(
            model_run_id="v2-run", model_label="v2-label",
            variants=["flat", "normalized"],
        ),
    )
    v1_recording = _records("v1")
    v2_recording = _records("v2")
    with pytest.raises(ValueError, match="PAIRED_VARIANT_MISMATCH"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=_bundle("v1", v1_recording),
            v2_bundle=_bundle("v2", v2_recording),
        )


def test_paired_summary_rejects_mismatched_fixture_catalog_fingerprint() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    right.fixture_metadata[0] = SimpleNamespace(
        variant="flat", catalog_fingerprint="different",
        mapping_fingerprint="mf1", mapping_version=1,
    )
    with pytest.raises(ValueError, match="PAIRED_FIXTURE_CATALOG_FINGERPRINT_MISMATCH"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )


def test_paired_summary_rejects_mismatched_fixture_mapping_fingerprint() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    right.fixture_metadata[1] = SimpleNamespace(
        variant="normalized", catalog_fingerprint="cf2",
        mapping_fingerprint="different", mapping_version=1,
    )
    with pytest.raises(ValueError, match="PAIRED_FIXTURE_MAPPING_FINGERPRINT_MISMATCH"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )


def test_paired_summary_rejects_mismatched_fixture_mapping_version() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    right.fixture_metadata[2] = SimpleNamespace(
        variant="legacy", catalog_fingerprint="cf3",
        mapping_fingerprint="mf3", mapping_version=2,
    )
    with pytest.raises(ValueError, match="PAIRED_FIXTURE_MAPPING_VERSION_MISMATCH"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )


def test_paired_summary_rejects_mismatched_fixture_variant_order() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    right.fixture_metadata = list(reversed(right.fixture_metadata))
    with pytest.raises(ValueError, match="PAIRED_FIXTURE_VARIANT_ORDER_MISMATCH"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )


def test_paired_summary_rejects_model_tag_mismatch() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    v2_bundle.manifest.provenance.model_id = "other-model:latest"
    with pytest.raises(ValueError, match="PAIRED_MODEL_TAG_MISMATCH") as excinfo:
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )
    assert str(excinfo.value) == "PAIRED_MODEL_TAG_MISMATCH"


def test_paired_summary_rejects_provider_mismatch() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    v2_bundle.manifest.provenance.provider_version = "9.9.9"
    with pytest.raises(ValueError, match="PAIRED_PROVIDER_MISMATCH") as excinfo:
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )
    assert str(excinfo.value) == "PAIRED_PROVIDER_MISMATCH"


def test_paired_summary_rejects_generation_config_mismatch() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    v2_bundle.manifest.provenance.generation_config_sha256 = "0" * 64
    with pytest.raises(ValueError, match="PAIRED_GENERATION_CONFIG_MISMATCH"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )


def test_paired_summary_flags_missing_model_digest() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    v2_bundle.manifest.provenance.model_artifact_sha256 = None
    with pytest.raises(ValueError, match="PAIRED_MODEL_DIGEST_UNVERIFIED"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )


def test_paired_summary_rejects_model_digest_mismatch() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    v2_bundle.manifest.provenance.model_artifact_sha256 = "1" * 64
    with pytest.raises(ValueError, match="PAIRED_MODEL_DIGEST_MISMATCH"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )


def test_paired_summary_rejects_question_fingerprint_mismatch() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    v2_recording.records[7].question_sha256 = "c" * 64
    v2_bundle.recording.records[7].question_sha256 = "c" * 64
    with pytest.raises(ValueError, match="PAIRED_QUESTION_FINGERPRINT_MISMATCH") as excinfo:
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )
    assert str(excinfo.value) == "PAIRED_QUESTION_FINGERPRINT_MISMATCH"


def test_paired_summary_rejects_mapping_fingerprint_mismatch() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    v2_recording.records[7].mapping_sha256 = "c" * 64
    v2_bundle.recording.records[7].mapping_sha256 = "c" * 64
    with pytest.raises(ValueError, match="PAIRED_MAPPING_FINGERPRINT_MISMATCH"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )


def test_paired_summary_rejects_database_context_mismatch() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    v2_recording.records[7].database_id = "other-db"
    v2_bundle.recording.records[7].database_id = "other-db"
    with pytest.raises(ValueError, match="PAIRED_DATABASE_CONTEXT_MISMATCH"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )


def test_paired_summary_rejects_model_run_id_mismatch() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    v1_recording.model_run_id = "stale-run"
    with pytest.raises(ValueError, match="PAIRED_MODEL_RUN_ID_MISMATCH"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )


def test_paired_summary_rejects_manifest_run_id_mismatch() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    v1_bundle.manifest.model_run_id = "stale-run"
    with pytest.raises(ValueError, match="PAIRED_MODEL_RUN_ID_MISMATCH"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )


def test_paired_summary_rejects_missing_record() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    v1_recording.records.pop()
    v1_bundle.recording.records.pop()
    with pytest.raises(ValueError, match="PAIRED_REQUEST_ID_MISMATCH"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )


def test_paired_summary_rejects_extra_record() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    extra = SimpleNamespace(
        request_id="v1-extra", question_sha256=QUESTION_FP,
        mapping_sha256=MAPPING_FP, database_id=DATABASE_ID,
    )
    v1_recording.records.append(extra)
    v1_bundle.recording.records.append(extra)
    with pytest.raises(ValueError, match="PAIRED_REQUEST_ID_MISMATCH"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )


def test_paired_summary_rejects_duplicate_recording_id() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    v1_recording.records[1].request_id = v1_recording.records[0].request_id
    with pytest.raises(ValueError, match="PAIRED_RECORDING_DUPLICATE_REQUEST_ID"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )


def test_paired_summary_rejects_swapped_bundle_recording() -> None:
    left, right, v1_recording, v2_recording, v1_bundle, v2_bundle = _pair()
    v1_bundle.recording.records.pop()
    with pytest.raises(ValueError, match="PAIRED_RECORDING_BUNDLE_MISMATCH"):
        paired_prompt_summary(
            left, right, v1_recording, v2_recording,
            v1_bundle=v1_bundle, v2_bundle=v2_bundle,
        )
