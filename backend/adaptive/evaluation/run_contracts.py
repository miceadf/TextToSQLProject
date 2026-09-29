"""Versioned, privacy-limited contracts for synthetic evaluation runs.

These records deliberately contain hashes and status codes, not model responses,
SQL, result rows, wall-clock measurements, or filesystem paths.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..harness.contracts import HarnessStage


RUN_REPORT_SCHEMA_VERSION = "fixture-evaluation-run-v1"
RUNNER_VERSION = "fixture-runner-v1"
FixtureVariant = Literal["flat", "normalized", "legacy", "coded", "hybrid"]
Fingerprint = str


class EvaluationRunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_run_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
    model_label: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
    variants: list[FixtureVariant] = Field(min_length=1, max_length=5)
    oracle_smoke: bool

    @model_validator(mode="after")
    def unique_variants(self) -> "EvaluationRunConfig":
        if len(self.variants) != len(set(self.variants)):
            raise ValueError("evaluation variants must be unique")
        return self


class ComparisonStatus(str, Enum):
    COMPARABLE = "COMPARABLE"
    NOT_COMPARABLE_PROJECTION = "NOT_COMPARABLE_PROJECTION"
    COMPARISON_ERROR = "COMPARISON_ERROR"
    NOT_EXECUTED = "NOT_EXECUTED"


class CaseAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^P2-[0-9]{3}$")
    variant: FixtureVariant
    request_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,199}$")
    structural_ir_exact: bool | None
    ir_fingerprint: Fingerprint | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    execution_success: bool
    comparison_status: ComparisonStatus
    semantic_correct: bool | None
    result_fingerprint: Fingerprint | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    failure_stage: HarnessStage | None = None
    failure_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$", max_length=80)

    @model_validator(mode="after")
    def coherent_outcome(self) -> "CaseAttempt":
        if self.structural_ir_exact is not None and self.ir_fingerprint is None:
            raise ValueError("structural comparison requires an IR fingerprint")
        if self.execution_success:
            if self.comparison_status is ComparisonStatus.NOT_EXECUTED:
                raise ValueError("successful execution cannot be NOT_EXECUTED")
        else:
            if self.comparison_status is not ComparisonStatus.NOT_EXECUTED:
                raise ValueError("failed execution cannot be compared")
            if self.result_fingerprint is not None or self.semantic_correct is not None:
                raise ValueError("failed execution cannot claim a semantic result")
            if self.failure_code is None:
                raise ValueError("failed execution requires a failure code")
        if self.comparison_status is ComparisonStatus.COMPARABLE:
            if self.semantic_correct is None:
                raise ValueError("comparable execution requires a semantic verdict")
            if self.result_fingerprint is None:
                raise ValueError("comparable execution requires a result fingerprint")
        elif self.semantic_correct is not None:
            raise ValueError("non-comparable execution cannot claim semantic correctness")
        elif self.result_fingerprint is not None:
            raise ValueError("non-comparable execution cannot claim a result fingerprint")
        if self.comparison_status is ComparisonStatus.NOT_COMPARABLE_PROJECTION:
            if self.failure_code != "NOT_COMPARABLE_PROJECTION":
                raise ValueError("projection mismatch requires its explicit reason code")
        if self.comparison_status is ComparisonStatus.COMPARISON_ERROR and self.failure_code is None:
            raise ValueError("comparison error requires a failure code")
        if self.comparison_status is ComparisonStatus.COMPARABLE and self.semantic_correct is False:
            if self.failure_code != "SEMANTIC_MISMATCH":
                raise ValueError("wrong semantic result requires SEMANTIC_MISMATCH")
        if self.failure_stage is not None and self.failure_code is None:
            raise ValueError("failure stage requires a failure code")
        return self


class FixtureRunMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant: FixtureVariant
    catalog_fingerprint: Fingerprint = Field(pattern=r"^[0-9a-f]{64}$")
    mapping_fingerprint: Fingerprint = Field(pattern=r"^[0-9a-f]{64}$")
    mapping_version: int = Field(ge=1)


class Metric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=0)
    denominator: int = Field(ge=0)

    @model_validator(mode="after")
    def count_not_greater_than_denominator(self) -> "Metric":
        if self.count > self.denominator:
            raise ValueError("metric count exceeds denominator")
        return self


class AttemptMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheduled_attempts: int = Field(ge=0)
    completed_attempts: int = Field(ge=0)
    structural_ir_exact: Metric
    structural_ir_evaluable: Metric
    semantic_correct: Metric
    semantic_correct_among_comparable: Metric
    execution_success: Metric
    not_comparable: Metric
    failure_by_stage_and_code: dict[str, int]


class CaseConsistency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^P2-[0-9]{3}$")
    ir_consistent: bool | None
    result_consistent: bool | None
    cross_schema_consistent: bool | None
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")


class EvaluationRunReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["fixture-evaluation-run-v1"] = RUN_REPORT_SCHEMA_VERSION
    runner_version: Literal["fixture-runner-v1"] = RUNNER_VERSION
    config: EvaluationRunConfig
    benchmark_id: str
    benchmark_version: int = Field(ge=1)
    gold_set_fingerprint: Fingerprint = Field(pattern=r"^[0-9a-f]{64}$")
    seed_fingerprint: Fingerprint = Field(pattern=r"^[0-9a-f]{64}$")
    ontology_fingerprint: Fingerprint = Field(pattern=r"^[0-9a-f]{64}$")
    query_ir_fingerprint: Fingerprint = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_metadata: list[FixtureRunMetadata]
    gold_human_review_pending: bool
    warnings: list[str]
    attempts: list[CaseAttempt]
    case_consistency: list[CaseConsistency]
    totals: AttemptMetrics
    by_variant: dict[str, AttemptMetrics]
    by_coverage_tag: dict[str, AttemptMetrics]
    cross_schema_consistent: Metric
    ir_consistent: Metric
    result_consistent: Metric
