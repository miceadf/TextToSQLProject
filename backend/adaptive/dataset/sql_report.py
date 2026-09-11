from __future__ import annotations

from collections import Counter
from pathlib import Path

import sqlglot
from sqlglot import Dialect

from .contracts import DatasetIssue, DatasetLoadResult
from .sql_classifier import classify_ir_compatibility
from .sql_contracts import (
    IrCompatibilityStatus,
    IrReasonCode,
    SqlAnalysisIssue,
    SqlAnalysisIssueCode,
    SqlAnalysisReport,
    SqlAnalysisSummary,
    SqlCompatibilityGroupSummary,
    SqlFeatures,
    SqlRecordAnalysis,
)
from .sql_features import extract_sql_features
from .sql_parser import parse_and_canonicalize


_COMPATIBILITY_NOTICE = (
    "Compatibility is a conservative candidate screening result for a future "
    "SQL-to-QueryIR converter, not proof that automatic conversion preserves intent."
)

_ISSUE_REASONS = {
    SqlAnalysisIssueCode.EMPTY_SQL: IrReasonCode.PARSE_FAILED,
    SqlAnalysisIssueCode.WHITESPACE_ONLY_SQL: IrReasonCode.PARSE_FAILED,
    SqlAnalysisIssueCode.PARSE_ERROR: IrReasonCode.PARSE_FAILED,
    SqlAnalysisIssueCode.MULTIPLE_STATEMENTS: IrReasonCode.MULTIPLE_STATEMENTS,
    SqlAnalysisIssueCode.NON_SELECT_STATEMENT: IrReasonCode.NON_SELECT_STATEMENT,
    SqlAnalysisIssueCode.CANONICALIZATION_ERROR: IrReasonCode.PARSE_FAILED,
    SqlAnalysisIssueCode.ROUND_TRIP_MISMATCH: IrReasonCode.ROUND_TRIP_MISMATCH,
    SqlAnalysisIssueCode.FEATURE_EXTRACTION_ERROR: IrReasonCode.PARSE_FAILED,
}

_FEATURE_NAMES = (
    "aggregate",
    "arithmetic",
    "cte",
    "distinct",
    "group_by",
    "having",
    "join",
    "limit",
    "offset",
    "order_by",
    "set_operation",
    "subquery",
    "wildcard",
    "window_function",
)


def build_sql_analysis_report(
    load_result: DatasetLoadResult,
    dataset_id: str,
    *,
    dialect: str = "postgres",
    include_previews: bool = False,
    preview_record_limit: int = 5,
    preview_char_limit: int = 240,
) -> SqlAnalysisReport:
    """Analyze valid dataset SQL without executing it or exposing raw text."""

    if not dataset_id:
        raise ValueError("dataset_id must not be empty")
    if not dialect:
        raise ValueError("dialect must not be empty")
    if preview_record_limit < 0:
        raise ValueError("preview_record_limit must be non-negative")
    if preview_char_limit < 1:
        raise ValueError("preview_char_limit must be positive")
    try:
        Dialect.get_or_raise(dialect)
    except Exception as exc:
        raise ValueError(f"unsupported SQL dialect: {dialect!r}") from exc

    records: list[SqlRecordAnalysis] = []
    issues: list[SqlAnalysisIssue] = []
    preview_budget = preview_record_limit if include_previews else 0

    for record in sorted(load_result.records, key=lambda item: item.source_line):
        canonicalized, issue = parse_and_canonicalize(record, dialect=dialect)
        if issue is not None:
            issues.append(issue)
            continue
        if canonicalized is None:  # pragma: no cover - defensive contract guard
            issues.append(
                SqlAnalysisIssue(
                    code=SqlAnalysisIssueCode.CANONICALIZATION_ERROR,
                    line_number=record.source_line,
                    message="SQL analysis produced no result",
                )
            )
            continue

        try:
            features = extract_sql_features(canonicalized.expression)
            compatibility = classify_ir_compatibility(
                canonicalized.expression,
                features,
            )
        except Exception:
            issues.append(
                SqlAnalysisIssue(
                    code=SqlAnalysisIssueCode.FEATURE_EXTRACTION_ERROR,
                    line_number=record.source_line,
                    message="SQL AST feature extraction failed",
                )
            )
            continue

        preview = None
        if preview_budget > 0:
            preview = canonicalized.shape_sql[:preview_char_limit]
            preview_budget -= 1
        records.append(
            SqlRecordAnalysis(
                source_line=record.source_line,
                canonical_hash=canonicalized.canonical_hash,
                shape_hash=canonicalized.shape_hash,
                features=features,
                compatibility=compatibility,
                preview=preview,
            )
        )

    records.sort(key=lambda item: item.source_line)
    issues.sort(key=lambda item: (item.line_number, item.code.value, item.message))
    dataset_issues = sorted(load_result.issues, key=_dataset_issue_key)
    summary = _summarize(load_result, records, issues)
    return SqlAnalysisReport(
        dataset_id=dataset_id,
        source_name=Path(load_result.source_name).name,
        content_sha256=load_result.content_sha256,
        dialect=dialect,
        sqlglot_version=sqlglot.__version__,
        compatibility_notice=_COMPATIBILITY_NOTICE,
        summary=summary,
        dataset_issues=dataset_issues,
        sql_issues=issues,
        records=records,
    )


def _summarize(
    load_result: DatasetLoadResult,
    records: list[SqlRecordAnalysis],
    issues: list[SqlAnalysisIssue],
) -> SqlAnalysisSummary:
    compatibility = Counter(record.compatibility.status.value for record in records)
    compatibility[IrCompatibilityStatus.UNSUPPORTED_OR_UNSAFE.value] += len(issues)
    reasons = Counter(
        reason.value
        for record in records
        for reason in record.compatibility.reason_codes
    )
    reasons.update(_ISSUE_REASONS[issue.code].value for issue in issues)

    feature_records: Counter[str] = Counter()
    aggregate_functions: Counter[str] = Counter()
    functions: Counter[str] = Counter()
    arithmetic: Counter[str] = Counter()
    comparisons: Counter[str] = Counter()
    booleans: Counter[str] = Counter()
    joins: Counter[str] = Counter()
    literals: Counter[str] = Counter()
    grouped_features = {
        status.value: Counter() for status in IrCompatibilityStatus
    }
    grouped_reasons = {
        status.value: Counter() for status in IrCompatibilityStatus
    }
    for record in records:
        feature = record.features
        flags = _feature_flags(feature)
        feature_records.update(name for name, present in flags.items() if present)
        status = record.compatibility.status.value
        grouped_features[status].update(
            name for name, present in flags.items() if present
        )
        grouped_reasons[status].update(
            reason.value for reason in record.compatibility.reason_codes
        )
        aggregate_functions.update(feature.aggregate_functions)
        functions.update(feature.functions)
        arithmetic.update(feature.arithmetic_operators)
        comparisons.update(feature.comparison_operators)
        booleans.update(feature.boolean_operators)
        joins.update(feature.join_types)
        literals.update(feature.literal_types)

    unsupported = IrCompatibilityStatus.UNSUPPORTED_OR_UNSAFE.value
    grouped_reasons[unsupported].update(
        _ISSUE_REASONS[issue.code].value for issue in issues
    )

    return SqlAnalysisSummary(
        total_lines=load_result.total_lines,
        valid_dataset_records=len(load_result.records),
        invalid_dataset_records=max(
            0,
            load_result.total_lines - len(load_result.records),
        ),
        analyzed_sql_records=len(records),
        sql_issue_records=len(issues),
        unique_canonical_sql_count=len({record.canonical_hash for record in records}),
        unique_shape_count=len({record.shape_hash for record in records}),
        compatibility_counts={
            status.value: compatibility[status.value]
            for status in IrCompatibilityStatus
        },
        feature_record_counts={
            name: feature_records[name]
            for name in _FEATURE_NAMES
        },
        aggregate_function_counts=_ordered_counter(aggregate_functions),
        function_counts=_ordered_counter(functions),
        arithmetic_operator_counts=_ordered_counter(arithmetic),
        comparison_operator_counts=_ordered_counter(comparisons),
        boolean_operator_counts=_ordered_counter(booleans),
        join_type_counts=_ordered_counter(joins),
        literal_type_counts=_ordered_counter(literals),
        reason_code_counts=_ordered_counter(reasons),
        compatibility_groups={
            status.value: SqlCompatibilityGroupSummary(
                record_count=compatibility[status.value],
                feature_record_counts={
                    name: grouped_features[status.value][name]
                    for name in _FEATURE_NAMES
                },
                reason_code_counts=_ordered_counter(grouped_reasons[status.value]),
            )
            for status in IrCompatibilityStatus
        },
    )


def _ordered_counter(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter) if counter[key]}


def _feature_flags(feature: SqlFeatures) -> dict[str, bool]:
    return {
        "aggregate": bool(feature.aggregate_functions),
        "arithmetic": bool(feature.arithmetic_operators),
        "cte": feature.cte_count > 0,
        "distinct": feature.distinct,
        "group_by": feature.group_by,
        "having": feature.having,
        "join": feature.join_count > 0,
        "limit": feature.limit,
        "offset": feature.offset,
        "order_by": feature.order_by,
        "set_operation": feature.set_operation_count > 0,
        "subquery": feature.subquery_count > 0,
        "wildcard": feature.wildcard_count > 0,
        "window_function": feature.window_function_count > 0,
    }


def _dataset_issue_key(issue: DatasetIssue) -> tuple[int, str, str, str]:
    return (issue.line_number, issue.code.value, issue.field or "", issue.message)
