from __future__ import annotations

from collections import Counter
from pathlib import Path

from sqlglot import exp

from ..ontology.contracts import DomainOntology
from .contracts import DatasetIssue, DatasetLoadResult, DatasetRecord
from .conversion_contracts import (
    ConversionRecordSummary,
    ConversionReviewItem,
    ConversionStatus,
    ConversionSummary,
    DatasetSchemaProfile,
    DuplicateProvenance,
    PrivateConvertedRecord,
    QueryIrConversionReport,
)
from .converter import convert_record
from .report import DatasetQualityReport, build_quality_report
from .source_profile import profile_sha256
from .sql_contracts import IrCompatibilityStatus
from .sql_parser import parse_and_canonicalize


def build_query_ir_conversion_report(
    load_result: DatasetLoadResult,
    dataset_id: str,
    profile: DatasetSchemaProfile,
    ontology: DomainOntology,
    *,
    default_limit: int = 50,
    include_previews: bool = False,
    preview_record_limit: int = 5,
    preview_char_limit: int = 240,
) -> tuple[QueryIrConversionReport, list[PrivateConvertedRecord]]:
    """Convert every valid record and build public and private artifacts in memory."""

    if not dataset_id:
        raise ValueError("dataset_id must not be empty")
    if not 1 <= default_limit <= 200:
        raise ValueError("default_limit must be between 1 and 200")
    if preview_record_limit < 0:
        raise ValueError("preview_record_limit must be non-negative")
    if preview_char_limit < 1:
        raise ValueError("preview_char_limit must be positive")

    quality = build_quality_report(load_result, dataset_id=dataset_id)
    provenance_by_line = _duplicate_provenance(quality)
    records = []
    private_records = []
    preview_budget = preview_record_limit if include_previews else 0

    for source in sorted(load_result.records, key=lambda item: item.source_line):
        result = convert_record(
            source,
            profile,
            ontology,
            duplicate_provenance=provenance_by_line.get(source.source_line),
            default_limit=default_limit,
        )
        preview = None
        if preview_budget > 0:
            parsed, _ = parse_and_canonicalize(source, dialect=profile.dialect)
            if parsed is not None and not _has_quoted_identifier(parsed.expression):
                preview = parsed.shape_sql[:preview_char_limit]
                preview_budget -= 1
        records.append(
            ConversionRecordSummary(
                source_line=result.source_line,
                canonical_hash=result.canonical_hash,
                shape_hash=result.shape_hash,
                status=result.status,
                reason_codes=result.reason_codes,
                analyzer_status=result.analyzer_status,
                analyzer_reason_codes=result.analyzer_reason_codes,
                duplicate_provenance=result.duplicate_provenance,
                preview=preview,
            )
        )
        if result.query_ir is not None and result.intent_signature is not None:
            if result.canonical_hash is None or result.shape_hash is None:
                raise ValueError("converted result must include SQL fingerprints")
            private_records.append(
                PrivateConvertedRecord(
                    dataset_id=dataset_id,
                    dataset_content_sha256=load_result.content_sha256,
                    profile_id=profile.profile_id,
                    profile_version=profile.version,
                    ontology_version=ontology.version,
                    source_line=source.source_line,
                    canonical_hash=result.canonical_hash,
                    shape_hash=result.shape_hash,
                    duplicate_provenance=result.duplicate_provenance,
                    instruction=source.instruction,
                    question=source.question,
                    query_ir=result.query_ir,
                    conversion_status=result.status,
                    limit_provenance=result.intent_signature.limit_provenance,
                )
            )

    records.sort(key=lambda item: item.source_line)
    private_records.sort(key=lambda item: item.source_line)
    review_items = [
        ConversionReviewItem(
            source_line=item.source_line,
            canonical_hash=item.canonical_hash,
            shape_hash=item.shape_hash,
            reason_codes=item.reason_codes,
            duplicate_provenance=item.duplicate_provenance,
            preview=item.preview,
        )
        for item in records
        if item.status == ConversionStatus.REVIEW_REQUIRED
    ]

    report = QueryIrConversionReport(
        dataset_id=dataset_id,
        source_name=Path(load_result.source_name).name,
        content_sha256=load_result.content_sha256,
        profile_id=profile.profile_id,
        profile_version=profile.version,
        profile_sha256=profile_sha256(profile),
        ontology_version=ontology.version,
        dialect=profile.dialect,
        default_limit=default_limit,
        summary=_summarize(load_result, records),
        dataset_issues=sorted(load_result.issues, key=_issue_key),
        records=records,
        review_items=review_items,
    )
    return report, private_records


def _duplicate_provenance(
    quality: DatasetQualityReport,
) -> dict[int, DuplicateProvenance]:
    values: dict[int, dict[str, str]] = {}
    group_specs = (
        (quality.exact_duplicate_groups, "exact_duplicate_group_id"),
        (quality.question_sql_duplicate_groups, "question_sql_duplicate_group_id"),
        (quality.question_conflict_groups, "question_conflict_group_id"),
    )
    for groups, field in group_specs:
        for group in groups:
            for line in group.line_numbers:
                values.setdefault(line, {})[field] = group.group_id
    return {
        line: DuplicateProvenance.model_validate(fields)
        for line, fields in sorted(values.items())
    }


def _summarize(
    load_result: DatasetLoadResult,
    records: list[ConversionRecordSummary],
) -> ConversionSummary:
    statuses = Counter(item.status.value for item in records)
    reasons = Counter(reason.value for item in records for reason in item.reason_codes)
    analyzer = Counter(item.analyzer_status.value for item in records)
    return ConversionSummary(
        total_lines=load_result.total_lines,
        valid_dataset_records=len(load_result.records),
        invalid_dataset_records=max(0, load_result.total_lines - len(load_result.records)),
        status_counts={status.value: statuses[status.value] for status in ConversionStatus},
        reason_code_counts={key: reasons[key] for key in sorted(reasons)},
        analyzer_status_counts={
            status.value: analyzer[status.value]
            for status in IrCompatibilityStatus
        },
        exact_duplicate_record_count=sum(
            item.duplicate_provenance.exact_duplicate_group_id is not None
            for item in records
        ),
        question_sql_duplicate_record_count=sum(
            item.duplicate_provenance.question_sql_duplicate_group_id is not None
            for item in records
        ),
        question_conflict_record_count=sum(
            item.duplicate_provenance.question_conflict_group_id is not None
            for item in records
        ),
    )


def _issue_key(issue: DatasetIssue) -> tuple[int, str, str, str]:
    return (issue.line_number, issue.code.value, issue.field or "", issue.message)


def _has_quoted_identifier(expression: exp.Expression) -> bool:
    return any(
        isinstance(node, exp.Identifier) and bool(node.args.get("quoted"))
        for node in expression.walk()
    )
