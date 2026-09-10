"""Read-only inspection tools for legacy Text-to-SQL datasets."""

from .contracts import (
    DatasetIssue,
    DatasetIssueCode,
    DatasetLoadResult,
    DatasetRecord,
)
from .loader import DatasetLoadFatalError, load_jsonl_dataset
from .report import DatasetQualityReport, build_quality_report

__all__ = [
    "DatasetIssue",
    "DatasetIssueCode",
    "DatasetLoadFatalError",
    "DatasetLoadResult",
    "DatasetQualityReport",
    "DatasetRecord",
    "build_quality_report",
    "load_jsonl_dataset",
]
