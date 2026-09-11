from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from .contracts import DatasetIssue


SQL_ANALYSIS_REPORT_SCHEMA_VERSION = "sql-analysis-report-v1"
SQL_ANALYZER_VERSION = "sql-ast-analyzer-v1"


class SqlAnalysisIssueCode(str, Enum):
    EMPTY_SQL = "EMPTY_SQL"
    WHITESPACE_ONLY_SQL = "WHITESPACE_ONLY_SQL"
    PARSE_ERROR = "PARSE_ERROR"
    MULTIPLE_STATEMENTS = "MULTIPLE_STATEMENTS"
    NON_SELECT_STATEMENT = "NON_SELECT_STATEMENT"
    CANONICALIZATION_ERROR = "CANONICALIZATION_ERROR"
    ROUND_TRIP_MISMATCH = "ROUND_TRIP_MISMATCH"
    FEATURE_EXTRACTION_ERROR = "FEATURE_EXTRACTION_ERROR"


class IrCompatibilityStatus(str, Enum):
    CURRENT_IR_CANDIDATE = "CURRENT_IR_CANDIDATE"
    IR_EXTENSION_REQUIRED = "IR_EXTENSION_REQUIRED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    UNSUPPORTED_OR_UNSAFE = "UNSUPPORTED_OR_UNSAFE"


class IrReasonCode(str, Enum):
    PARSE_FAILED = "PARSE_FAILED"
    MULTIPLE_STATEMENTS = "MULTIPLE_STATEMENTS"
    NON_SELECT_STATEMENT = "NON_SELECT_STATEMENT"
    ROUND_TRIP_MISMATCH = "ROUND_TRIP_MISMATCH"
    SET_OPERATION = "SET_OPERATION"
    CTE = "CTE"
    SUBQUERY = "SUBQUERY"
    WINDOW_FUNCTION = "WINDOW_FUNCTION"
    SELECT_INTO = "SELECT_INTO"
    LOCKING_CLAUSE = "LOCKING_CLAUSE"
    UNSUPPORTED_SELECT_CLAUSE = "UNSUPPORTED_SELECT_CLAUSE"
    JOIN = "JOIN"
    MULTIPLE_TABLES = "MULTIPLE_TABLES"
    AGGREGATE = "AGGREGATE"
    DISTINCT = "DISTINCT"
    GROUP_BY = "GROUP_BY"
    HAVING = "HAVING"
    ORDER_BY = "ORDER_BY"
    OFFSET = "OFFSET"
    ARITHMETIC_EXPRESSION = "ARITHMETIC_EXPRESSION"
    OR_PREDICATE = "OR_PREDICATE"
    NOT_PREDICATE = "NOT_PREDICATE"
    IN_PREDICATE = "IN_PREDICATE"
    BETWEEN_PREDICATE = "BETWEEN_PREDICATE"
    IS_NULL_PREDICATE = "IS_NULL_PREDICATE"
    UNSUPPORTED_COMPARISON = "UNSUPPORTED_COMPARISON"
    WILDCARD_PROJECTION = "WILDCARD_PROJECTION"
    EXPRESSION_PROJECTION = "EXPRESSION_PROJECTION"
    RESULT_ALIAS = "RESULT_ALIAS"
    FUNCTION_PROJECTION = "FUNCTION_PROJECTION"
    NON_LITERAL_FILTER_VALUE = "NON_LITERAL_FILTER_VALUE"
    LIKE_PREFIX_PATTERN = "LIKE_PREFIX_PATTERN"
    LIKE_SUFFIX_PATTERN = "LIKE_SUFFIX_PATTERN"
    LIKE_EXACT_PATTERN = "LIKE_EXACT_PATTERN"
    LIKE_COMPLEX_PATTERN = "LIKE_COMPLEX_PATTERN"
    LIMIT_NOT_INTEGER_LITERAL = "LIMIT_NOT_INTEGER_LITERAL"
    LIMIT_OUT_OF_RANGE = "LIMIT_OUT_OF_RANGE"
    PLACEHOLDER_VALUE = "PLACEHOLDER_VALUE"


class SqlAnalysisIssue(BaseModel):
    code: SqlAnalysisIssueCode
    line_number: int = Field(ge=1)
    message: str


class SqlFeatures(BaseModel):
    statement_kind: str
    root_kind: str
    table_references: list[str] = Field(default_factory=list)
    column_references: list[str] = Field(default_factory=list)
    table_reference_count: int = Field(ge=0)
    column_reference_count: int = Field(ge=0)
    projection_count: int = Field(ge=0)
    wildcard_count: int = Field(ge=0)
    projection_alias_count: int = Field(ge=0)
    expression_projection_count: int = Field(ge=0)
    predicate_count: int = Field(ge=0)
    has_where: bool = False
    boolean_operators: dict[str, int] = Field(default_factory=dict)
    comparison_operators: dict[str, int] = Field(default_factory=dict)
    aggregate_functions: dict[str, int] = Field(default_factory=dict)
    functions: dict[str, int] = Field(default_factory=dict)
    arithmetic_operators: dict[str, int] = Field(default_factory=dict)
    join_types: dict[str, int] = Field(default_factory=dict)
    literal_types: dict[str, int] = Field(default_factory=dict)
    placeholder_count: int = Field(ge=0)
    distinct: bool = False
    group_by: bool = False
    having: bool = False
    order_by: bool = False
    limit: bool = False
    offset: bool = False
    join_count: int = Field(ge=0)
    subquery_count: int = Field(ge=0)
    cte_count: int = Field(ge=0)
    set_operation_count: int = Field(ge=0)
    window_function_count: int = Field(ge=0)


class SqlCompatibility(BaseModel):
    status: IrCompatibilityStatus
    reason_codes: list[IrReasonCode] = Field(default_factory=list)


class SqlRecordAnalysis(BaseModel):
    source_line: int = Field(ge=1)
    canonical_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    shape_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    features: SqlFeatures
    compatibility: SqlCompatibility
    preview: str | None = None


class SqlCompatibilityGroupSummary(BaseModel):
    record_count: int = Field(ge=0)
    feature_record_counts: dict[str, int] = Field(default_factory=dict)
    reason_code_counts: dict[str, int] = Field(default_factory=dict)


class SqlAnalysisSummary(BaseModel):
    total_lines: int = Field(ge=0)
    valid_dataset_records: int = Field(ge=0)
    invalid_dataset_records: int = Field(ge=0)
    analyzed_sql_records: int = Field(ge=0)
    sql_issue_records: int = Field(ge=0)
    unique_canonical_sql_count: int = Field(ge=0)
    unique_shape_count: int = Field(ge=0)
    compatibility_counts: dict[str, int] = Field(default_factory=dict)
    feature_record_counts: dict[str, int] = Field(default_factory=dict)
    aggregate_function_counts: dict[str, int] = Field(default_factory=dict)
    function_counts: dict[str, int] = Field(default_factory=dict)
    arithmetic_operator_counts: dict[str, int] = Field(default_factory=dict)
    comparison_operator_counts: dict[str, int] = Field(default_factory=dict)
    boolean_operator_counts: dict[str, int] = Field(default_factory=dict)
    join_type_counts: dict[str, int] = Field(default_factory=dict)
    literal_type_counts: dict[str, int] = Field(default_factory=dict)
    reason_code_counts: dict[str, int] = Field(default_factory=dict)
    compatibility_groups: dict[str, SqlCompatibilityGroupSummary] = Field(
        default_factory=dict
    )


class SqlAnalysisReport(BaseModel):
    schema_version: Literal["sql-analysis-report-v1"] = (
        SQL_ANALYSIS_REPORT_SCHEMA_VERSION
    )
    analyzer_version: Literal["sql-ast-analyzer-v1"] = SQL_ANALYZER_VERSION
    dataset_id: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dialect: str = Field(min_length=1)
    sqlglot_version: str = Field(min_length=1)
    compatibility_notice: str
    summary: SqlAnalysisSummary
    dataset_issues: list[DatasetIssue] = Field(default_factory=list)
    sql_issues: list[SqlAnalysisIssue] = Field(default_factory=list)
    records: list[SqlRecordAnalysis] = Field(default_factory=list)
