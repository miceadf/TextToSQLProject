"""Read-only inspection tools for legacy Text-to-SQL datasets."""

from .contracts import (
    DatasetIssue,
    DatasetIssueCode,
    DatasetLoadResult,
    DatasetRecord,
)
from .loader import DatasetLoadFatalError, load_jsonl_dataset
from .report import DatasetQualityReport, build_quality_report
from .sql_contracts import (
    IrCompatibilityStatus,
    IrReasonCode,
    SqlAnalysisIssue,
    SqlAnalysisIssueCode,
    SqlAnalysisReport,
    SqlFeatures,
)
from .sql_report import build_sql_analysis_report

__all__ = [
    "DatasetIssue",
    "DatasetIssueCode",
    "DatasetLoadFatalError",
    "DatasetLoadResult",
    "DatasetQualityReport",
    "DatasetRecord",
    "IrCompatibilityStatus",
    "IrReasonCode",
    "SqlAnalysisIssue",
    "SqlAnalysisIssueCode",
    "SqlAnalysisReport",
    "SqlFeatures",
    "build_quality_report",
    "build_sql_analysis_report",
    "load_jsonl_dataset",
]
