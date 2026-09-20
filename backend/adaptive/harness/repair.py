"""Bounded single-retry coordinator for validation-failed QueryIR.

The coordinator reuses `ExecutionHarness` without changing its contract.
At most one repair candidate is accepted, and only for a fixed set of
(code, stage) pairs. The repair provider receives the original request,
runtime ontology, published mapping, and the fixed failure code only;
gold IR, expected rows, SQL, database rows, raw model output, exception
text, and traces are never passed. A candidate must pass the full
harness path (validator, compiler, policy, read-only executor) again;
there is no direct SQL edit or execution path.

Synthetic use only: no live providers, CLIs, prompts, or score reporting.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .compiler import SingleTableSQLiteCompiler
from .contracts import (
    HarnessErrorCode,
    HarnessRequest,
    HarnessResult,
    HarnessStage,
    HarnessStatus,
    MappingProfile,
    MappingStatus,
    Ontology,
)
from .engine import ExecutionHarness
from .errors import HarnessFailure
from .executor import SQLiteReadOnlyExecutor
from .model import QueryModel
from .policy import ConservativeSqlPolicy, SqlPolicy


REPAIR_PROVIDER_ERROR = "REPAIR_PROVIDER_ERROR"
MAX_REPAIR_ATTEMPTS = 1

_RETRYABLE: frozenset[tuple[HarnessErrorCode, HarnessStage]] = frozenset({
    (HarnessErrorCode.MODEL_INVALID_OUTPUT, HarnessStage.IR_GENERATED),
    (HarnessErrorCode.UNKNOWN_ENTITY, HarnessStage.IR_VALIDATED),
    (HarnessErrorCode.UNKNOWN_FIELD, HarnessStage.IR_VALIDATED),
    (HarnessErrorCode.INVALID_OPERATOR, HarnessStage.IR_VALIDATED),
    (HarnessErrorCode.INVALID_VALUE_TYPE, HarnessStage.IR_VALIDATED),
})


def is_repairable(code: HarnessErrorCode, stage: HarnessStage | None) -> bool:
    """Code and stage must match together; either alone never retries."""
    return (code, stage) in _RETRYABLE


class RepairProvider(Protocol):
    def propose_repair(
        self,
        request: HarnessRequest,
        ontology: Ontology,
        mapping: MappingProfile,
        *,
        failure_code: HarnessErrorCode,
        failure_stage: HarnessStage,
    ) -> Any:
        """Return one QueryIR-validatable candidate; called at most once."""


class RepairSummary(BaseModel):
    """Fixed codes and counts only; no SQL, rows, messages, or paths."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    initial_status: HarnessStatus = Field(strict=False)
    initial_stage: HarnessStage | None = Field(default=None, strict=False)
    initial_code: str | None = None
    repair_attempted: bool
    repair_attempts: int = Field(ge=0, le=MAX_REPAIR_ATTEMPTS)
    final_status: HarnessStatus = Field(strict=False)
    final_stage: HarnessStage | None = Field(default=None, strict=False)
    final_code: str | None = None
    succeeded: bool


class _SingleCandidateModel:
    """Yield one repair candidate; a second call is a terminal harness failure."""

    def __init__(self, candidate: Any) -> None:
        self._candidate = candidate
        self.calls = 0

    def generate_ir(
        self,
        request: HarnessRequest,
        ontology: Ontology,
        mapping: MappingProfile,
    ) -> Any:
        del request, ontology, mapping
        self.calls += 1
        if self.calls > 1:
            raise HarnessFailure(
                HarnessErrorCode.EXECUTION_ERROR,
                "repair candidate already consumed",
                stage=HarnessStage.IR_GENERATED,
            )
        return self._candidate


def _outcome_of(result: HarnessResult) -> tuple[HarnessStatus, HarnessStage | None, str | None]:
    if result.status is HarnessStatus.SUCCEEDED:
        return result.status, HarnessStage.RESULT_VALIDATED, None
    if result.error is None:
        return result.status, None, None
    return result.status, result.error.stage, result.error.code.value


class BoundedRepairCoordinator:
    """Run once, then at most one full-path repair for validation failures."""

    def __init__(
        self,
        executor: SQLiteReadOnlyExecutor,
        *,
        compiler: SingleTableSQLiteCompiler | Any | None = None,
        sql_policy: SqlPolicy | None = None,
    ) -> None:
        self._executor = executor
        self._compiler = compiler or SingleTableSQLiteCompiler()
        self._sql_policy = sql_policy or ConservativeSqlPolicy()

    def _harness(self, model: QueryModel) -> ExecutionHarness:
        return ExecutionHarness(
            model,
            self._executor,
            compiler=self._compiler,
            sql_policy=self._sql_policy,
        )

    @staticmethod
    def _terminal(
        initial: HarnessResult, *, repair_attempts: int = 0,
        final_code: str | None = None, final_stage: HarnessStage | None = None,
    ) -> RepairSummary:
        initial_status, initial_stage, initial_code = _outcome_of(initial)
        if final_code is None:
            final_status, final_stage, final_code = initial_status, initial_stage, initial_code
        else:
            final_status = HarnessStatus.FAILED
        return RepairSummary(
            initial_status=initial_status,
            initial_stage=initial_stage,
            initial_code=initial_code,
            repair_attempted=repair_attempts > 0,
            repair_attempts=repair_attempts,
            final_status=final_status,
            final_stage=final_stage,
            final_code=final_code,
            succeeded=False,
        )

    def run(
        self,
        request_data: HarnessRequest | dict[str, Any],
        ontology: Ontology,
        mapping: MappingProfile,
        *,
        model: QueryModel,
        repair_provider: RepairProvider,
    ) -> RepairSummary:
        initial = self._harness(model).run(request_data, ontology, mapping)
        if initial.status is HarnessStatus.SUCCEEDED:
            status, stage, code = _outcome_of(initial)
            return RepairSummary(
                initial_status=status,
                initial_stage=stage,
                initial_code=code,
                repair_attempted=False,
                repair_attempts=0,
                final_status=status,
                final_stage=stage,
                final_code=code,
                succeeded=True,
            )
        if initial.error is None:
            return self._terminal(initial)
        code, stage = initial.error.code, initial.error.stage
        if (
            stage is None
            or mapping.status is not MappingStatus.PUBLISHED
            or not is_repairable(code, stage)
        ):
            return self._terminal(initial)
        try:
            validated = HarnessRequest.model_validate(request_data)
        except ValidationError:
            return self._terminal(initial)
        try:
            candidate = repair_provider.propose_repair(
                validated.model_copy(deep=True),
                ontology,
                mapping,
                failure_code=code,
                failure_stage=stage,
            )
        except Exception:
            # Cancellation (BaseException) propagates; anything else ends
            # the repair here with a fixed code and no further retry.
            return self._terminal(
                initial,
                repair_attempts=1,
                final_code=REPAIR_PROVIDER_ERROR,
                final_stage=None,
            )
        final = self._harness(_SingleCandidateModel(candidate)).run(
            validated, ontology, mapping
        )
        final_status, final_stage, final_code = _outcome_of(final)
        initial_status, initial_stage, initial_code = _outcome_of(initial)
        return RepairSummary(
            initial_status=initial_status,
            initial_stage=initial_stage,
            initial_code=initial_code,
            repair_attempted=True,
            repair_attempts=1,
            final_status=final_status,
            final_stage=final_stage,
            final_code=final_code,
            succeeded=final_status is HarnessStatus.SUCCEEDED,
        )
