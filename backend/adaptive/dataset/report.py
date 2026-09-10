from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .contracts import DatasetIssue, DatasetLoadResult, DatasetRecord


REPORT_SCHEMA_VERSION = "dataset-quality-report-v1"
TOOL_VERSION = "dataset-quality-inspector-v1"


class DatasetQualitySummary(BaseModel):
    total_records: int = Field(ge=0)
    valid_records: int = Field(ge=0)
    invalid_records: int = Field(ge=0)
    unique_instruction_count: int = Field(ge=0)
    unique_question_count: int = Field(ge=0)
    unique_sql_count: int = Field(ge=0)
    repeated_question_group_count: int = Field(ge=0)
    exact_duplicate_group_count: int = Field(ge=0)
    question_sql_duplicate_group_count: int = Field(ge=0)
    question_conflict_group_count: int = Field(ge=0)


class NormalizationChange(BaseModel):
    line_number: int = Field(ge=1)
    field: Literal["question", "sql"]


class ExactDuplicateGroup(BaseModel):
    group_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    line_numbers: list[int]
    occurrence_count: int = Field(ge=2)
    instruction_preview: str | None = None
    question_preview: str | None = None
    sql_preview: str | None = None


class QuestionSqlDuplicateGroup(BaseModel):
    group_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    question_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    sql_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    line_numbers: list[int]
    occurrence_count: int = Field(ge=2)
    question_preview: str | None = None
    sql_preview: str | None = None


class ConflictSqlVariant(BaseModel):
    sql_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    line_numbers: list[int]
    sql_preview: str | None = None


class QuestionConflictGroup(BaseModel):
    group_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    question_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    line_numbers: list[int]
    sql_variants: list[ConflictSqlVariant]
    question_preview: str | None = None


class DatasetQualityReport(BaseModel):
    schema_version: Literal["dataset-quality-report-v1"] = REPORT_SCHEMA_VERSION
    tool_version: Literal["dataset-quality-inspector-v1"] = TOOL_VERSION
    dataset_id: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    summary: DatasetQualitySummary
    issues: list[DatasetIssue]
    normalization_changes: list[NormalizationChange]
    exact_duplicate_groups: list[ExactDuplicateGroup]
    question_sql_duplicate_groups: list[QuestionSqlDuplicateGroup]
    question_conflict_groups: list[QuestionConflictGroup]


def normalize_key(value: str) -> str:
    """Apply the deliberately narrow, lossless-for-storage identity boundary."""

    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n")).strip()


def build_quality_report(
    load_result: DatasetLoadResult,
    dataset_id: str,
    include_previews: bool = False,
    preview_group_limit: int = 5,
    preview_char_limit: int = 160,
    preview_variant_limit: int = 3,
) -> DatasetQualityReport:
    """Build a deterministic, privacy-preserving report from a loader result."""

    if not dataset_id:
        raise ValueError("dataset_id must not be empty")
    if preview_group_limit < 0:
        raise ValueError("preview_group_limit must be non-negative")
    if preview_char_limit < 1:
        raise ValueError("preview_char_limit must be positive")
    if preview_variant_limit < 0:
        raise ValueError("preview_variant_limit must be non-negative")

    records = sorted(load_result.records, key=lambda record: record.source_line)
    exact: dict[tuple[str, str, str], list[DatasetRecord]] = defaultdict(list)
    question_sql: dict[tuple[str, str], list[DatasetRecord]] = defaultdict(list)
    questions: dict[str, dict[str, list[DatasetRecord]]] = defaultdict(
        lambda: defaultdict(list)
    )
    changes: list[NormalizationChange] = []

    for record in records:
        question_key = normalize_key(record.question)
        sql_key = normalize_key(record.sql)
        exact[(record.instruction, record.question, record.sql)].append(record)
        question_sql[(question_key, sql_key)].append(record)
        questions[question_key][sql_key].append(record)

        if question_key != record.question:
            changes.append(
                NormalizationChange(line_number=record.source_line, field="question")
            )
        if sql_key != record.sql:
            changes.append(
                NormalizationChange(line_number=record.source_line, field="sql")
            )

    preview_limit = preview_group_limit if include_previews else 0
    exact_group_total = sum(len(grouped) >= 2 for grouped in exact.values())
    duplicate_group_total = sum(len(grouped) >= 2 for grouped in question_sql.values())
    preview_budget = max(0, preview_limit - exact_group_total - duplicate_group_total)

    conflict_groups: list[QuestionConflictGroup] = []
    for question_key, sql_groups in questions.items():
        if len(sql_groups) < 2:
            continue
        ordered_sql = sorted(sql_groups)
        group_records = [record for sql_key in ordered_sql for record in sql_groups[sql_key]]
        show_preview = preview_budget > 0
        if show_preview:
            preview_budget -= 1
        conflict_groups.append(
            QuestionConflictGroup(
                group_id=_digest("question-conflict-group", question_key, *ordered_sql),
                question_hash=_digest("normalized-question", question_key),
                line_numbers=sorted(record.source_line for record in group_records),
                sql_variants=[
                    ConflictSqlVariant(
                        sql_hash=_digest("normalized-sql", sql_key),
                        line_numbers=sorted(record.source_line for record in sql_groups[sql_key]),
                        sql_preview=(
                            _preview(sql_key, preview_char_limit)
                            if show_preview and variant_index < preview_variant_limit
                            else None
                        ),
                    )
                    for variant_index, sql_key in enumerate(ordered_sql)
                ],
                question_preview=_preview(question_key, preview_char_limit)
                if show_preview
                else None,
            )
        )
    conflict_groups.sort(key=lambda group: (group.group_id, group.line_numbers))

    preview_budget = max(0, preview_limit - exact_group_total)
    duplicate_groups: list[QuestionSqlDuplicateGroup] = []
    for (question_key, sql_key), grouped_records in question_sql.items():
        if len(grouped_records) < 2:
            continue
        show_preview = preview_budget > 0
        if show_preview:
            preview_budget -= 1
        lines = sorted(record.source_line for record in grouped_records)
        duplicate_groups.append(
            QuestionSqlDuplicateGroup(
                group_id=_digest("question-sql-duplicate-group", question_key, sql_key),
                question_hash=_digest("normalized-question", question_key),
                sql_hash=_digest("normalized-sql", sql_key),
                line_numbers=lines,
                occurrence_count=len(lines),
                question_preview=_preview(question_key, preview_char_limit)
                if show_preview
                else None,
                sql_preview=_preview(sql_key, preview_char_limit) if show_preview else None,
            )
        )
    duplicate_groups.sort(key=lambda group: (group.group_id, group.line_numbers))

    preview_budget = preview_limit
    exact_groups: list[ExactDuplicateGroup] = []
    for raw_record, grouped_records in exact.items():
        if len(grouped_records) < 2:
            continue
        show_preview = preview_budget > 0
        if show_preview:
            preview_budget -= 1
        lines = sorted(record.source_line for record in grouped_records)
        exact_groups.append(
            ExactDuplicateGroup(
                group_id=_digest("exact-record-duplicate-group", *raw_record),
                record_hash=_digest("raw-record", *raw_record),
                line_numbers=lines,
                occurrence_count=len(lines),
                instruction_preview=_preview(raw_record[0], preview_char_limit)
                if show_preview
                else None,
                question_preview=_preview(raw_record[1], preview_char_limit)
                if show_preview
                else None,
                sql_preview=_preview(raw_record[2], preview_char_limit)
                if show_preview
                else None,
            )
        )
    exact_groups.sort(key=lambda group: (group.group_id, group.line_numbers))

    normalized_questions = {normalize_key(record.question) for record in records}
    normalized_sql = {normalize_key(record.sql) for record in records}
    issues = sorted(load_result.issues, key=_issue_sort_key)
    invalid_records = max(0, load_result.total_lines - len(records))

    return DatasetQualityReport(
        dataset_id=dataset_id,
        source_name=Path(load_result.source_name).name,
        content_sha256=load_result.content_sha256,
        summary=DatasetQualitySummary(
            total_records=load_result.total_lines,
            valid_records=len(records),
            invalid_records=invalid_records,
            unique_instruction_count=len({record.instruction for record in records}),
            unique_question_count=len(normalized_questions),
            unique_sql_count=len(normalized_sql),
            repeated_question_group_count=sum(
                sum(len(group) for group in sql_groups.values()) >= 2
                for sql_groups in questions.values()
            ),
            exact_duplicate_group_count=len(exact_groups),
            question_sql_duplicate_group_count=len(duplicate_groups),
            question_conflict_group_count=len(conflict_groups),
        ),
        issues=issues,
        normalization_changes=sorted(
            changes,
            key=lambda change: (change.line_number, change.field),
        ),
        exact_duplicate_groups=exact_groups,
        question_sql_duplicate_groups=duplicate_groups,
        question_conflict_groups=conflict_groups,
    )


def _digest(domain: str, *values: str) -> str:
    payload = json.dumps(
        [domain, *values],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _preview(value: str, limit: int) -> str:
    return value[:limit]


def _issue_sort_key(issue: DatasetIssue) -> tuple[int, str, str, str]:
    code = issue.code.value if hasattr(issue.code, "value") else str(issue.code)
    return (issue.line_number, code, issue.field or "", issue.message)
