"""Deterministic aggregate report; private text stays in the record artifact."""

from __future__ import annotations

import hashlib
import json

from .pseudonym_contracts import (
    PseudonymReasonCode,
    PseudonymStatus,
    PseudonymSummaryReport,
    PseudonymizationProfile,
    PseudonymizedRecord,
)
from .pseudonym_transform import PseudonymizationRun


def pseudonymized_jsonl_bytes(records: list[PseudonymizedRecord]) -> bytes:
    """Match the bundle's canonical JSONL encoding for the output fingerprint."""

    return b"".join(
        (
            json.dumps(
                item.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        for item in sorted(records, key=lambda record: record.record_id)
    )


def build_pseudonym_summary(
    run: PseudonymizationRun,
    *,
    dataset_id: str,
    dataset_fingerprint: str,
    input_artifact_sha256: str,
    profile: PseudonymizationProfile,
    key_id: str,
    invalid_input_count: int = 0,
) -> PseudonymSummaryReport:
    """Report only aggregate counts and fingerprints, never record text."""

    if invalid_input_count < 0:
        raise ValueError("invalid input count must be non-negative")
    counts = {
        status.value: run.status_counts.get(status.value, 0)
        for status in PseudonymStatus
    }
    counts[PseudonymStatus.UNSUPPORTED.value] += invalid_input_count
    return PseudonymSummaryReport(
        dataset_id=dataset_id,
        dataset_fingerprint=dataset_fingerprint,
        input_artifact_sha256=input_artifact_sha256,
        output_artifact_sha256=hashlib.sha256(
            pseudonymized_jsonl_bytes(run.records)
        ).hexdigest(),
        profile_id=profile.profile_id,
        profile_version=profile.version,
        ontology_version=profile.ontology_version,
        key_id=key_id,
        input_record_count=run.input_count + invalid_input_count,
        output_record_count=len(run.records),
        review_record_count=counts[PseudonymStatus.REVIEW_REQUIRED.value],
        unsupported_record_count=counts[PseudonymStatus.UNSUPPORTED.value],
        status_counts=counts,
        reason_code_counts={
            key: value for key, value in sorted({
                **run.reason_counts,
                PseudonymReasonCode.PSEUDONYMIZATION_ERROR.value:
                    run.reason_counts.get(PseudonymReasonCode.PSEUDONYMIZATION_ERROR.value, 0)
                    + invalid_input_count,
            }.items()) if value
        },
        field_unique_value_counts={
            key: run.field_unique_counts[key] for key in sorted(run.field_unique_counts)
        },
        field_length_bucket_counts={
            key: dict(sorted(value.items()))
            for key, value in sorted(run.field_length_bucket_counts.items())
        },
        known_value_leak_count=run.leak_count,
    )
