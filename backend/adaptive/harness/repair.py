"""Bounded single-retry coordinator for validation-failed QueryIR.

The coordinator reuses `ExecutionHarness` without changing its contract.
At most one repair candidate is accepted, and only for a fixed set of
(code, stage) pairs. Both attempts run on identical deep-copied snapshots
of the request, runtime ontology, and published mapping, so mutation by the
initial QueryModel or by the repair provider cannot change the other
attempt's context or the caller's objects. Each model additionally receives
separate deep copies from the context its own harness run validates,
compiles, and executes against, because `ExecutionHarness` otherwise shares
one object between `generate_ir` and later validation. The repair provider receives
only a request copy, ontology copy, mapping copy, and the fixed failure
code; gold IR, expected rows, SQL, database rows, raw model output, exception
text, and traces are never passed. A candidate must pass the full
harness path (validator, compiler, policy, read-only executor) again;
there is no direct SQL edit or execution path.

Synthetic use only: no live providers, CLIs, prompts, or score reporting.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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


class DetailedRepairOutcome(BaseModel):
    """Final harness result preserved alongside its repair summary.

    `final_result` is the `HarnessResult` from the initial execution
    (terminal path) or from the single repair-candidate execution
    (repair path), defensively copied. It is `None` only when the
    repair provider raised and the summary ends with
    `REPAIR_PROVIDER_ERROR`: no final harness execution exists there,
    and this contract expresses that absence explicitly instead of
    inventing a fake error code or harness result. Cancellation still
    propagates and yields no outcome.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: RepairSummary
    final_result: HarnessResult | None = None

    @model_validator(mode="before")
    @classmethod
    def _revalidate_nested(cls, data: Any) -> Any:
        # Pydantic may skip nested validation for already-built model
        # instances (`model_copy(update=...)`, `model_construct`), so
        # revalidate every nested instance from plain JSON data first.
        # Serializer warnings can echo forged input, hence suppressed.
        if isinstance(data, BaseModel):
            try:
                data = data.model_dump(mode="json", warnings=False)
            except Exception:
                raise ValueError("invalid repair outcome") from None
            if not isinstance(data, dict):
                raise ValueError("invalid repair outcome")
        if isinstance(data, dict):
            summary = data.get("summary")
            if isinstance(summary, BaseModel):
                try:
                    data = dict(data)
                    data["summary"] = summary.model_dump(
                        mode="json", warnings=False
                    )
                except Exception:
                    raise ValueError("invalid repair outcome") from None
            result = data.get("final_result")
            if isinstance(result, BaseModel):
                try:
                    data = dict(data)
                    data["final_result"] = result.model_dump(
                        mode="json", warnings=False
                    )
                except Exception:
                    raise ValueError("invalid repair outcome") from None
        return data

    @model_validator(mode="after")
    def _validate_result_agreement(self) -> DetailedRepairOutcome:
        summary = self.summary
        result = self.final_result
        if summary.final_code == REPAIR_PROVIDER_ERROR:
            if result is not None:
                raise ValueError("provider-error outcome must not carry a result")
            if summary.final_status is not HarnessStatus.FAILED:
                raise ValueError("provider-error outcome must be failed")
            if summary.final_stage is not None or summary.succeeded:
                raise ValueError("provider-error outcome must not succeed")
            return self
        if result is None:
            raise ValueError("non-provider-error outcome must carry a result")
        if summary.final_status is not result.status:
            raise ValueError("summary and result status disagree")
        if summary.succeeded != (result.status is HarnessStatus.SUCCEEDED):
            raise ValueError("summary and result success disagree")
        if result.status is HarnessStatus.SUCCEEDED:
            if summary.final_code is not None:
                raise ValueError("success outcome must not carry a code")
            if summary.final_stage is not HarnessStage.RESULT_VALIDATED:
                raise ValueError("success outcome must end validated")
            if result.error is not None:
                raise ValueError("success result must not carry an error")
            return self
        if summary.final_status is not HarnessStatus.FAILED or summary.succeeded:
            raise ValueError("failure outcome must be failed")
        if result.error is None:
            if summary.final_code is not None or summary.final_stage is not None:
                raise ValueError("summary and result failure disagree")
            return self
        if summary.final_code != result.error.code.value:
            raise ValueError("summary and result code disagree")
        if summary.final_stage is not result.error.stage:
            raise ValueError("summary and result stage disagree")
        return self


class _IsolatedModel:
    """Wrap a model so its mutations cannot reach harness validation context.

    `ExecutionHarness` passes the same request/ontology/mapping objects to
    `generate_ir` and to later validation/compile/execute steps, so a model
    that mutates its inputs would otherwise change what the harness
    validates against. This wrapper hands the wrapped model deep copies and
    keeps the harness-owned objects pristine, without changing either the
    `ExecutionHarness` or the `QueryModel` public contract.
    """

    def __init__(self, model: QueryModel) -> None:
        self._model = model

    def generate_ir(
        self,
        request: HarnessRequest,
        ontology: Ontology,
        mapping: MappingProfile,
    ) -> Any:
        return self._model.generate_ir(
            request.model_copy(deep=True),
            ontology.model_copy(deep=True),
            mapping.model_copy(deep=True),
        )


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

    def run_detailed(
        self,
        request_data: HarnessRequest | dict[str, Any],
        ontology: Ontology,
        mapping: MappingProfile,
        *,
        model: QueryModel,
        repair_provider: RepairProvider,
    ) -> DetailedRepairOutcome:
        """Run the bounded repair and preserve the final harness result.

        Same call budget as `run` (initial model once, provider at most
        once, second execution at most once); no request is re-executed
        to restore a result. `final_result` is `None` only for
        `REPAIR_PROVIDER_ERROR`, where no final harness execution
        exists. Cancellation (`BaseException`) propagates with no
        outcome.
        """
        # Snapshot caller context first; the harness hands only deep copies
        # to the initial model, the repair provider, and the second attempt.
        try:
            snapshot_request = HarnessRequest.model_validate(request_data)
        except ValidationError:
            snapshot_request = None
        ontology_snapshot = ontology.model_copy(deep=True)
        mapping_snapshot = mapping.model_copy(deep=True)
        initial = self._harness(_IsolatedModel(model)).run(
            snapshot_request.model_copy(deep=True) if snapshot_request is not None else request_data,
            ontology_snapshot.model_copy(deep=True),
            mapping_snapshot.model_copy(deep=True),
        )
        if initial.status is HarnessStatus.SUCCEEDED:
            status, stage, code = _outcome_of(initial)
            summary = RepairSummary(
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
            return DetailedRepairOutcome(
                summary=summary,
                final_result=initial.model_copy(deep=True),
            )
        if initial.error is None:
            return DetailedRepairOutcome(
                summary=self._terminal(initial),
                final_result=initial.model_copy(deep=True),
            )
        code, stage = initial.error.code, initial.error.stage
        if (
            stage is None
            or mapping_snapshot.status is not MappingStatus.PUBLISHED
            or not is_repairable(code, stage)
        ):
            return DetailedRepairOutcome(
                summary=self._terminal(initial),
                final_result=initial.model_copy(deep=True),
            )
        if snapshot_request is None:
            return DetailedRepairOutcome(
                summary=self._terminal(initial),
                final_result=initial.model_copy(deep=True),
            )
        validated = snapshot_request.model_copy(deep=True)
        try:
            candidate = repair_provider.propose_repair(
                validated.model_copy(deep=True),
                ontology_snapshot.model_copy(deep=True),
                mapping_snapshot.model_copy(deep=True),
                failure_code=code,
                failure_stage=stage,
            )
        except Exception:
            # Cancellation (BaseException) propagates; anything else ends
            # the repair here with a fixed code and no further retry.
            # No final harness execution exists, so the outcome carries
            # no harness result instead of a fabricated one.
            return DetailedRepairOutcome(
                summary=self._terminal(
                    initial,
                    repair_attempts=1,
                    final_code=REPAIR_PROVIDER_ERROR,
                    final_stage=None,
                ),
                final_result=None,
            )
        final = self._harness(_IsolatedModel(_SingleCandidateModel(candidate))).run(
            snapshot_request.model_copy(deep=True),
            ontology_snapshot.model_copy(deep=True),
            mapping_snapshot.model_copy(deep=True),
        )
        final_status, final_stage, final_code = _outcome_of(final)
        initial_status, initial_stage, initial_code = _outcome_of(initial)
        summary = RepairSummary(
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
        return DetailedRepairOutcome(
            summary=summary,
            final_result=final.model_copy(deep=True),
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
        """Legacy summary-only entry point; delegates without extra calls."""
        return self.run_detailed(
            request_data,
            ontology,
            mapping,
            model=model,
            repair_provider=repair_provider,
        ).summary.model_copy(deep=True)
