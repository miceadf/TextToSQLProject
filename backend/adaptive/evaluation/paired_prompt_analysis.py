"""Aggregate two private recorded runs by identical case and fixture variant."""

from __future__ import annotations

from typing import Any

from ..harness.recorded import RecordedFailure
from ..harness.recording_bundle_contracts import LoadedRecordingBundle
from .run_contracts import CaseAttempt, EvaluationRunReport


def _indexed(report: EvaluationRunReport) -> dict[tuple[str, str], CaseAttempt]:
    indexed = {(attempt.case_id, attempt.variant): attempt for attempt in report.attempts}
    if len(indexed) != len(report.attempts):
        raise ValueError("DUPLICATE_PAIRED_ATTEMPT")
    return indexed


def _provenance_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _require_bundle(bundle: Any) -> Any:
    manifest = getattr(bundle, "manifest", None)
    bundled = getattr(bundle, "recording", None)
    if manifest is None or bundled is None:
        raise ValueError("PAIRED_PROVENANCE_MISSING")
    provenance = getattr(manifest, "provenance", None)
    if provenance is None:
        raise ValueError("PAIRED_PROVENANCE_MISSING")
    for field in (
        "provider_kind", "provider_id", "provider_version", "model_id",
        "generation_config_sha256",
    ):
        if getattr(provenance, field, None) is None:
            raise ValueError("PAIRED_PROVENANCE_MISSING")
    if getattr(manifest, "model_run_id", None) is None:
        raise ValueError("PAIRED_PROVENANCE_MISSING")
    if getattr(bundled, "model_run_id", None) is None:
        raise ValueError("PAIRED_PROVENANCE_MISSING")
    return bundle


def _validate_self_consistency(
    report: EvaluationRunReport,
    bundle: Any,
) -> Any:
    """Bind one side's report to its own bundle manifest and recording.

    v1 and v2 intentionally use different model_run_id values, so callers
    must not compare run IDs across sides. Only within-side equality of
    report config, manifest, and bundle recording is checked. The recording
    under comparison is always the bundle's own recording, so a caller
    cannot substitute a separate recording with matching request IDs but
    different fingerprints or payloads.
    """
    _require_bundle(bundle)
    manifest = bundle.manifest
    recording = bundle.recording
    report_run_id = report.config.model_run_id
    if (
        report_run_id != manifest.model_run_id
        or report_run_id != recording.model_run_id
    ):
        raise ValueError("PAIRED_MODEL_RUN_ID_MISMATCH")
    record_ids = [record.request_id for record in recording.records]
    if len(record_ids) != len(set(record_ids)):
        raise ValueError("PAIRED_RECORDING_DUPLICATE_REQUEST_ID")
    return recording


def _validate_provenance_comparability(v1_bundle: Any, v2_bundle: Any) -> None:
    """Check that two bundles describe a comparable model execution.

    The exact model tag comes only from the strict-loaded bundle manifest
    provenance (never from report model_label). Prompt template hashes are
    intentionally not compared because v1/v2 use different templates.
    Provenance stays DECLARED_UNVERIFIED: it binds the caller's declaration
    reproducibly but never proves model weights independently.
    """
    first = v1_bundle.manifest.provenance
    second = v2_bundle.manifest.provenance
    if (
        _provenance_value(first.provider_kind) != _provenance_value(second.provider_kind)
        or first.provider_id != second.provider_id
        or first.provider_version != second.provider_version
    ):
        raise ValueError("PAIRED_PROVIDER_MISMATCH")
    if first.model_id != second.model_id:
        raise ValueError("PAIRED_MODEL_TAG_MISMATCH")
    first_digest = getattr(first, "model_artifact_sha256", None)
    second_digest = getattr(second, "model_artifact_sha256", None)
    if first_digest is None or second_digest is None:
        raise ValueError("PAIRED_MODEL_DIGEST_UNVERIFIED")
    if first_digest != second_digest:
        raise ValueError("PAIRED_MODEL_DIGEST_MISMATCH")
    if first.generation_config_sha256 != second.generation_config_sha256:
        raise ValueError("PAIRED_GENERATION_CONFIG_MISMATCH")


def _validate_request_id_coverage(
    report: EvaluationRunReport,
    recording: Any,
) -> dict[str, Any]:
    """Require exact attempt↔recording request-ID correspondence per side.

    Duplicates, missing IDs, and extra IDs are all rejected with fixed
    codes. No success aggregation is returned when coverage is unproven.
    """
    attempt_ids = [attempt.request_id for attempt in report.attempts]
    if len(attempt_ids) != len(set(attempt_ids)):
        raise ValueError("PAIRED_RECORDING_DUPLICATE_REQUEST_ID")
    record_ids = [record.request_id for record in recording.records]
    if len(record_ids) != len(set(record_ids)):
        raise ValueError("PAIRED_RECORDING_DUPLICATE_REQUEST_ID")
    if set(attempt_ids) != set(record_ids):
        raise ValueError("PAIRED_REQUEST_ID_MISMATCH")
    return {record.request_id: record for record in recording.records}


def _validate_paired_fingerprints(
    left: dict[tuple[str, str], CaseAttempt],
    right: dict[tuple[str, str], CaseAttempt],
    v1_records: dict[str, Any],
    v2_records: dict[str, Any],
) -> None:
    """Require identical question/mapping/database context per case×variant.

    Pairing across reports uses case×variant keys, never raw request-ID
    strings. Only fingerprints are compared; original questions, mappings,
    responses, and paths are never returned or logged here.
    """
    for key in sorted(left):
        first = v1_records[left[key].request_id]
        second = v2_records[right[key].request_id]
        if first.question_sha256 != second.question_sha256:
            raise ValueError("PAIRED_QUESTION_FINGERPRINT_MISMATCH")
        if first.mapping_sha256 != second.mapping_sha256:
            raise ValueError("PAIRED_MAPPING_FINGERPRINT_MISMATCH")
        if first.database_id != second.database_id:
            raise ValueError("PAIRED_DATABASE_CONTEXT_MISMATCH")


def _validate_paired_contracts(
    v1: EvaluationRunReport,
    v2: EvaluationRunReport,
) -> None:
    """Verify that two reports share every comparison contract dimension.

    Checks benchmark identity, version, gold/ontology/QueryIR fingerprints,
    fixture metadata fingerprints, variant sets, and attempt key sets.
    Does not check model_run_id because paired comparison intentionally
    uses different run IDs for v1 and v2.
    """
    if v1.benchmark_id != v2.benchmark_id:
        raise ValueError("PAIRED_BENCHMARK_ID_MISMATCH")
    if v1.benchmark_version != v2.benchmark_version:
        raise ValueError("PAIRED_BENCHMARK_VERSION_MISMATCH")
    if v1.gold_set_fingerprint != v2.gold_set_fingerprint:
        raise ValueError("PAIRED_GOLD_SET_FINGERPRINT_MISMATCH")
    if v1.ontology_fingerprint != v2.ontology_fingerprint:
        raise ValueError("PAIRED_ONTOLOGY_FINGERPRINT_MISMATCH")
    if v1.query_ir_fingerprint != v2.query_ir_fingerprint:
        raise ValueError("PAIRED_QUERY_IR_FINGERPRINT_MISMATCH")
    if v1.seed_fingerprint != v2.seed_fingerprint:
        raise ValueError("PAIRED_SEED_FINGERPRINT_MISMATCH")
    if v1.config.variants != v2.config.variants:
        raise ValueError("PAIRED_VARIANT_MISMATCH")
    if len(v1.fixture_metadata) != len(v2.fixture_metadata):
        raise ValueError("PAIRED_FIXTURE_METADATA_LENGTH_MISMATCH")
    for fm1, fm2 in zip(v1.fixture_metadata, v2.fixture_metadata):
        if fm1.variant != fm2.variant:
            raise ValueError("PAIRED_FIXTURE_VARIANT_ORDER_MISMATCH")
        if fm1.catalog_fingerprint != fm2.catalog_fingerprint:
            raise ValueError("PAIRED_FIXTURE_CATALOG_FINGERPRINT_MISMATCH")
        if fm1.mapping_fingerprint != fm2.mapping_fingerprint:
            raise ValueError("PAIRED_FIXTURE_MAPPING_FINGERPRINT_MISMATCH")
        if fm1.mapping_version != fm2.mapping_version:
            raise ValueError("PAIRED_FIXTURE_MAPPING_VERSION_MISMATCH")


def paired_prompt_summary(
    v1: EvaluationRunReport,
    v2: EvaluationRunReport,
    *,
    v1_bundle: LoadedRecordingBundle | Any,
    v2_bundle: LoadedRecordingBundle | Any,
) -> dict[str, object]:
    """Return counts only; no request IDs, questions, responses or paths.

    The recordings under comparison always come from the bundles'
    own ``recording`` entries; there is no separate recording argument,
    so a caller cannot substitute fingerprints or payloads while reusing
    a valid manifest. Both bundles must be the validated results of
    ``load_recording_bundle()``: accepting the ``LoadedRecordingBundle``
    type alone does not perform strict loading, and this function never
    re-verifies bundle hashes itself. Without manifest provenance there
    is no proven model tag, digest, or generation config, so no success
    aggregation is returned (PAIRED_PROVENANCE_MISSING). The report
    itself is not cryptographically bound to the bundle manifest;
    binding is limited to matching model_run_id values, matching
    request-ID sets, and matching per-case fingerprints. Raw response
    payloads are not compared; outcome comparison comes from the
    reports. Anything beyond that is a documented limitation, not a
    verified claim.
    """
    if v1_bundle is None or v2_bundle is None:
        raise ValueError("PAIRED_PROVENANCE_MISSING")
    _validate_paired_contracts(v1, v2)
    v1_recording = _validate_self_consistency(v1, v1_bundle)
    v2_recording = _validate_self_consistency(v2, v2_bundle)
    _validate_provenance_comparability(v1_bundle, v2_bundle)
    left, right = _indexed(v1), _indexed(v2)
    if left.keys() != right.keys() or not left:
        raise ValueError("PAIRED_TARGET_MISMATCH")
    if len(left) != 120:
        raise ValueError("PAIRED_DENOMINATOR_MISMATCH")
    record_sets = (
        _validate_request_id_coverage(v1, v1_recording),
        _validate_request_id_coverage(v2, v2_recording),
    )
    _validate_paired_fingerprints(left, right, record_sets[0], record_sets[1])
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
