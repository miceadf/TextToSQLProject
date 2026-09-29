"""Paired comparison of the QueryIR arm and the direct-SQL arm (``arm-comparison-v1``).

Both reports must describe exactly the same benchmark, gold set, seed,
variants, fixture Catalogs, and case×variant attempts; otherwise the
comparison is refused. Only paired counts are produced: no significance test
and no winner statement, since gold review (L1) and result review (L3) are
still pending.
"""

from __future__ import annotations

from collections.abc import Iterable
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..fixtures.contracts import FIXTURE_SUITES
from .direct_sql import DirectSqlRunReport
from .run_contracts import EvaluationRunReport
from .run_report import deterministic_request_id


ARM_COMPARISON_VERSION = "arm-comparison-v1"


class ArmComparisonError(ValueError):
    """Reports are not a valid pair; fixed message only."""

    def __init__(self) -> None:
        super().__init__("ARM_COMPARISON_MISMATCH")


class PairCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    denominator: int = Field(ge=0)
    ir_correct: int = Field(ge=0)
    sql_correct: int = Field(ge=0)
    both_correct: int = Field(ge=0)
    ir_only: int = Field(ge=0)
    sql_only: int = Field(ge=0)
    neither: int = Field(ge=0)


class ArmComparisonReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["arm-comparison-v1"] = ARM_COMPARISON_VERSION
    benchmark_id: str
    benchmark_version: int = Field(ge=1)
    gold_set_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    variants: list[str]
    ir_model_run_id: str
    ir_model_label: str
    sql_model_run_id: str
    sql_model_label: str
    same_model_label: bool
    sql_prompt_version: str
    sql_prompt_template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    oracle_smoke: bool
    gold_human_review_pending: bool
    warnings: list[str]
    totals: PairCounts
    by_variant: dict[str, PairCounts]


def _count(pairs: Iterable[tuple[bool, bool]]) -> PairCounts:
    items = list(pairs)
    return PairCounts(
        denominator=len(items),
        ir_correct=sum(ir for ir, _ in items),
        sql_correct=sum(sql for _, sql in items),
        both_correct=sum(ir and sql for ir, sql in items),
        ir_only=sum(ir and not sql for ir, sql in items),
        sql_only=sum(sql and not ir for ir, sql in items),
        neither=sum(not ir and not sql for ir, sql in items),
    )


def compare_arms(ir: EvaluationRunReport, sql: DirectSqlRunReport) -> ArmComparisonReport:
    """Pair attempts by (case_id, variant) after strict identity checks."""
    ir_variants = [str(getattr(item, "value", item)) for item in ir.config.variants]
    sql_variants = [str(getattr(item, "value", item)) for item in sql.config.variants]
    if (
        ir.benchmark_id != sql.benchmark_id
        or ir.benchmark_version != sql.benchmark_version
        or ir.gold_set_fingerprint != sql.gold_set_fingerprint
        or ir.seed_fingerprint != sql.seed_fingerprint
        or ir_variants != sql_variants
        or bool(ir.config.oracle_smoke) != bool(sql.config.oracle_smoke)
    ):
        raise ArmComparisonError()
    ir_catalogs = {str(getattr(m.variant, "value", m.variant)): m.catalog_fingerprint for m in ir.fixture_metadata}
    sql_catalogs = {str(getattr(m.variant, "value", m.variant)): m.catalog_fingerprint for m in sql.fixture_metadata}
    if ir_catalogs != sql_catalogs or set(ir_catalogs) != set(ir_variants):
        raise ArmComparisonError()

    ir_by_key: dict[tuple[str, str], bool] = {}
    for attempt in ir.attempts:
        key = (attempt.case_id, str(getattr(attempt.variant, "value", attempt.variant)))
        if key in ir_by_key:
            raise ArmComparisonError()
        ir_by_key[key] = attempt.semantic_correct is True
    sql_by_key: dict[tuple[str, str], bool] = {}
    for attempt in sql.attempts:
        key = (attempt.case_id, str(getattr(attempt.variant, "value", attempt.variant)))
        if key in sql_by_key:
            raise ArmComparisonError()
        sql_by_key[key] = attempt.correct is True
    if not ir_by_key or set(ir_by_key) != set(sql_by_key):
        raise ArmComparisonError()
    # Full schedule only: every case appears with every variant, and each
    # report's own denominator and deterministic request IDs agree.
    case_ids = {case_id for case_id, _ in ir_by_key}
    if len(ir_by_key) != len(case_ids) * len(ir_variants):
        raise ArmComparisonError()
    if (
        ir.totals.scheduled_attempts != len(ir_by_key)
        or sql.totals.scheduled_attempts != len(sql_by_key)
        or any(ir.by_variant[v].scheduled_attempts != len(case_ids) for v in ir_variants)
        or any(sql.by_variant[v].scheduled_attempts != len(case_ids) for v in ir_variants)
    ):
        raise ArmComparisonError()
    benchmark = SimpleNamespace(benchmark_id=ir.benchmark_id, version=ir.benchmark_version)
    for report in (ir, sql):
        for attempt in report.attempts:
            variant = str(getattr(attempt.variant, "value", attempt.variant))
            expected_id = deterministic_request_id(report.config, benchmark, attempt.case_id, variant)
            if attempt.request_id != expected_id:
                raise ArmComparisonError()

    keys = sorted(ir_by_key)
    oracle = bool(ir.config.oracle_smoke or sql.config.oracle_smoke)
    pending = bool(ir.gold_human_review_pending or sql.gold_human_review_pending)
    warnings = []
    if oracle:
        warnings.append("ORACLE_SMOKE_NOT_MODEL_ACCURACY")
    if pending:
        warnings.append("GOLD_NEEDS_HUMAN_REVIEW")
    warnings.append("PAIRED_COUNTS_ONLY_NO_SIGNIFICANCE_CLAIM")
    return ArmComparisonReport(
        benchmark_id=ir.benchmark_id,
        benchmark_version=ir.benchmark_version,
        gold_set_fingerprint=ir.gold_set_fingerprint,
        seed_fingerprint=ir.seed_fingerprint,
        variants=ir_variants,
        ir_model_run_id=ir.config.model_run_id,
        ir_model_label=ir.config.model_label,
        sql_model_run_id=sql.config.model_run_id,
        sql_model_label=sql.config.model_label,
        same_model_label=ir.config.model_label == sql.config.model_label,
        sql_prompt_version=sql.prompt_version,
        sql_prompt_template_sha256=sql.prompt_template_sha256,
        oracle_smoke=oracle,
        gold_human_review_pending=pending,
        warnings=warnings,
        totals=_count((ir_by_key[key], sql_by_key[key]) for key in keys),
        by_variant={
            variant: _count(
                (ir_by_key[key], sql_by_key[key]) for key in keys if key[1] == variant
            )
            for variant in ir_variants
        },
    )


SCHEMA_CONSISTENCY_VERSION = "schema-consistency-v1"


class ArmConsistency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_denominator: int = Field(ge=0)
    by_variant_correct: dict[str, int]
    spread: int = Field(ge=0)
    all_variants_correct: int = Field(ge=0)


class SchemaConsistencyReport(BaseModel):
    """Per-arm accuracy stability across fixture variants; counts only."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["schema-consistency-v1"] = SCHEMA_CONSISTENCY_VERSION
    variants: list[str]
    oracle_smoke: bool
    gold_human_review_pending: bool
    ir: ArmConsistency
    sql: ArmConsistency


def _arm_consistency(correct: dict[tuple[str, str], bool], variants: list[str]) -> ArmConsistency:
    case_ids = sorted({case_id for case_id, _ in correct})
    by_variant = {
        variant: sum(correct[(case_id, variant)] for case_id in case_ids) for variant in variants
    }
    return ArmConsistency(
        case_denominator=len(case_ids),
        by_variant_correct=by_variant,
        spread=max(by_variant.values()) - min(by_variant.values()),
        all_variants_correct=sum(
            all(correct[(case_id, variant)] for variant in variants) for case_id in case_ids
        ),
    )


def schema_consistency(ir: EvaluationRunReport, sql: DirectSqlRunReport) -> SchemaConsistencyReport:
    """Summarize each arm's stability across variants after the same pairing checks."""
    comparison = compare_arms(ir, sql)
    variants = comparison.variants
    # Consistency is only meaningful over a whole fixed suite, never a subset.
    if variants not in [[item.value for item in suite] for suite in FIXTURE_SUITES.values()]:
        raise ArmComparisonError()
    ir_correct = {
        (a.case_id, str(getattr(a.variant, "value", a.variant))): a.semantic_correct is True
        for a in ir.attempts
    }
    sql_correct = {
        (a.case_id, str(getattr(a.variant, "value", a.variant))): a.correct is True
        for a in sql.attempts
    }
    return SchemaConsistencyReport(
        variants=variants,
        oracle_smoke=comparison.oracle_smoke,
        gold_human_review_pending=comparison.gold_human_review_pending,
        ir=_arm_consistency(ir_correct, variants),
        sql=_arm_consistency(sql_correct, variants),
    )
