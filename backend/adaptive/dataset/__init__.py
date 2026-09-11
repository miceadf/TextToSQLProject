"""Read-only inspection tools for legacy Text-to-SQL datasets."""

from .contracts import (
    DatasetIssue,
    DatasetIssueCode,
    DatasetLoadResult,
    DatasetRecord,
)
from .loader import DatasetLoadFatalError, load_jsonl_dataset
from .conversion_contracts import (
    ConversionReasonCode,
    ConversionStatus,
    DatasetSchemaProfile,
    QueryIrConversionReport,
)
from .conversion_report import build_query_ir_conversion_report
from .converter import convert_record
from .report import DatasetQualityReport, build_quality_report
from .source_profile import load_dataset_schema_profile
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
    "DatasetSchemaProfile",
    "ConversionReasonCode",
    "ConversionStatus",
    "IrCompatibilityStatus",
    "IrReasonCode",
    "SqlAnalysisIssue",
    "SqlAnalysisIssueCode",
    "SqlAnalysisReport",
    "SqlFeatures",
    "QueryIrConversionReport",
    "build_quality_report",
    "build_query_ir_conversion_report",
    "build_sql_analysis_report",
    "load_jsonl_dataset",
    "load_dataset_schema_profile",
    "convert_record",
]
