"""Confirmed QueryIR safe-execution adapter (synthetic only).

`clarification-execution-adapter-v1` closes one explicit call: re-confirm
through the existing Phase 6-09 `run_constrained_generation()` path, then
forward the verified snapshot through the existing `ExecutionHarness`
request/context validation, QueryIR revalidation, compile, SQL policy,
read-only execute, and result validation path with no bypass. Existing
`ExecutionHarness`, `QueryModel`, compiler, policy, and executor contracts
are reused unchanged.

Trust boundary: the caller supplies only the original detection context
(request, runtime ontology, Published mapping, detector rule set, bearer
pending, user choice) plus a constraint-aware provider and a read-only
executor. The caller cannot inject a `QueryIR`, `ConstrainedGenerationResult`,
constraint, `QueryModel`, `HarnessResult`, prebuilt SQL, or prebuilt result.
Every public Pydantic input is revalidated from plain JSON data
(`model_dump(mode="json", warnings=False)` then `model_validate`) and deep
snapshotted before any provider call. Missing provider/executor/compiler/
policy callables fail closed up front. Cancel, no/multi match, and
pending/choice/rule-set/context drift fail before any provider, compiler,
policy, or executor call. Existing ambiguity/clarification/constraint fixed
errors propagate unwrapped.

One-shot model: the adapter builds one internal `QueryModel` holding exactly
the Phase 6-09 verified `QueryIR`. It returns a defensive copy exactly once
and only when the harness-passed request/ontology/mapping equal the adapter
snapshot by value. A second call or context drift raises a fixed
`HarnessFailure` (no raw content) at `IR_GENERATED`, which the adapter
converts to a fixed internal error.

Harness reuse: a plain `ExecutionHarness` is created with the injected
read-only executor (the adapter never calls `execute()` directly), the
default `SingleTableSQLiteCompiler`, and the default
`ConservativeSqlPolicy`. Protocol-compatible compiler/policy doubles may be
injected for synthetic tests. Compile-stage and later harness failures keep
their existing `HarnessResult` code/stage. Because the QueryIR was already
generated and validated, a harness failure at request/context/IR stages, a
missing query_ir, or a query_ir different from the generation is a fixed
adapter internal error. No repair, regeneration, re-confirmation loop,
success estimation, or critique/queue registration happens here.

Raw-content boundary: the returned `HarnessResult` keeps its existing
contract and may therefore contain the raw request ID, compiled SQL, and
columns/rows (or a fixed error message). The adapter does not claim to hide
them. Adapter errors, logs, and stdout never reflect the question, raw
identifiers, option/rule/trigger text, physical mapping, SQL, rows, paths,
secrets, or provider/OS exception text.

Side effects and non-goals: no file, network, Ollama, PostgreSQL, queue, or
snapshot access. Test SQLite execution happens only through the explicitly
injected executor inside the harness. No UI, session, expiry, repair, or
re-execution loop. Result storage, display, and access control are a
follow-up API/UI boundary.

Synthetic use only: pure coordination over the inputs below.
"""

from __future__ import annotations

import copy
from enum import Enum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, model_validator

from .ambiguity_detector import DetectorRuleSet
from .clarification import (
    ClarificationChoice,
    PendingClarification,
    request_id_fingerprint,
)
from .clarification_query_constraint import (
    ConstrainedGenerationResult,
    ConstraintAwareProvider,
    run_constrained_generation,
)
from .compiler import SingleTableSQLiteCompiler
from .contracts import (
    HarnessErrorCode,
    HarnessRequest,
    HarnessResult,
    HarnessStage,
    HarnessStatus,
    MappingProfile,
    Ontology,
    QueryIR,
)
from .engine import ExecutionHarness
from .errors import HarnessFailure
from .policy import ConservativeSqlPolicy


CLARIFICATION_EXECUTION_ADAPTER_VERSION = "clarification-execution-adapter-v1"

_EARLY_STAGES = frozenset(
    {
        HarnessStage.REQUEST_VALIDATED,
        HarnessStage.CONTEXT_READY,
        HarnessStage.IR_GENERATED,
        HarnessStage.IR_VALIDATED,
    }
)


class ClarificationExecutionAdapterErrorCode(str, Enum):
    INVALID_INPUT = "EXECUTION_ADAPTER_INVALID_INPUT"
    INTERNAL_ERROR = "EXECUTION_ADAPTER_INTERNAL_ERROR"


class ClarificationExecutionAdapterError(Exception):
    """Fail-closed adapter error with a fixed code only; never reflects input."""

    def __init__(self, code: ClarificationExecutionAdapterErrorCode) -> None:
        self.code = code.value
        super().__init__(self.code)


class ReadOnlyExecutor(Protocol):
    def execute(self, database_id: str, query: Any) -> Any:
        """Execute compiler output against a read-only database."""
        ...


class AdapterCompiler(Protocol):
    def compile(self, query_ir: QueryIR, mapping: MappingProfile) -> Any:
        """Compile a validated query for the execution harness."""
        ...


class AdapterPolicy(Protocol):
    def validate(self, query: Any, **kwargs: Any) -> None:
        """Validate compiler output with optional semantic context."""
        ...


class ClarificationExecutionResult(BaseModel):
    """Versioned frozen outcome: generation plus harness result only."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    adapter_version: Literal["clarification-execution-adapter-v1"] = (
        CLARIFICATION_EXECUTION_ADAPTER_VERSION
    )
    generation: ConstrainedGenerationResult
    harness_result: HarnessResult

    @model_validator(mode="after")
    def validate_nested_result(self) -> "ClarificationExecutionResult":
        result = self.harness_result
        if result.query_ir is None or result.query_ir != self.generation.query_ir:
            raise ValueError("harness result does not match constrained generation")
        if request_id_fingerprint(result.request_id) != (
            self.generation.constraint.request_id_sha256
        ):
            raise ValueError("harness result request does not match constraint")
        if result.status is HarnessStatus.FAILED:
            if (
                result.error is None
                or result.error.stage is None
                or result.error.stage in _EARLY_STAGES
            ):
                raise ValueError("harness result has an invalid failure boundary")
        elif result.error is not None:
            raise ValueError("successful harness result cannot contain an error")
        return self


class _OneShotQueryModel:
    """Internal one-shot holder for the verified clarified QueryIR."""

    def __init__(
        self,
        request: HarnessRequest,
        ontology: Ontology,
        mapping: MappingProfile,
        query_ir: QueryIR,
    ) -> None:
        self._request = copy.deepcopy(request)
        self._ontology = copy.deepcopy(ontology)
        self._mapping = copy.deepcopy(mapping)
        self._query_ir = copy.deepcopy(query_ir)
        self._used = False

    def generate_ir(
        self,
        request: HarnessRequest,
        ontology: Ontology,
        mapping: MappingProfile,
    ) -> Any:
        if self._used:
            raise HarnessFailure(
                HarnessErrorCode.MODEL_INVALID_OUTPUT,
                "clarified one-shot model is unavailable",
                stage=HarnessStage.IR_GENERATED,
            )
        if request != self._request or ontology != self._ontology or mapping != self._mapping:
            raise HarnessFailure(
                HarnessErrorCode.MODEL_INVALID_OUTPUT,
                "clarified one-shot model context mismatch",
                stage=HarnessStage.IR_GENERATED,
            )
        self._used = True
        return copy.deepcopy(self._query_ir)


def _revalidate(model_cls: type[Any], value: Any) -> Any:
    if isinstance(value, model_cls):
        payload = value.model_dump(mode="json", warnings=False)
    elif isinstance(value, dict):
        payload = value
    else:
        raise ValueError("unsupported boundary input")
    return model_cls.model_validate(payload)


def _require_callable(holder: Any, name: str) -> None:
    target = getattr(holder, name, None)
    if not callable(target):
        raise ClarificationExecutionAdapterError(
            ClarificationExecutionAdapterErrorCode.INVALID_INPUT
        )


def run_clarified_execution(
    request_data: HarnessRequest | dict[str, Any],
    ontology: Ontology,
    mapping: MappingProfile,
    rules_data: DetectorRuleSet | dict[str, Any],
    pending: PendingClarification | dict[str, Any],
    choice: ClarificationChoice | dict[str, Any],
    provider: ConstraintAwareProvider,
    executor: ReadOnlyExecutor,
    *,
    compiler: AdapterCompiler | None = None,
    sql_policy: AdapterPolicy | None = None,
) -> ClarificationExecutionResult:
    """Confirm, generate one constrained QueryIR, and execute it once.

    Existing detector/gate/constraint errors propagate unchanged; every
    other adapter failure uses the two fixed adapter codes without
    reflecting input or provider text.
    """
    _require_callable(provider, "generate_ir")
    _require_callable(executor, "execute")
    resolved_compiler: Any = (
        compiler if compiler is not None else SingleTableSQLiteCompiler()
    )
    resolved_policy: Any = (
        sql_policy if sql_policy is not None else ConservativeSqlPolicy()
    )
    _require_callable(resolved_compiler, "compile")
    _require_callable(resolved_policy, "validate")

    try:
        snapshot_request = _revalidate(HarnessRequest, request_data)
        snapshot_ontology = _revalidate(Ontology, ontology)
        snapshot_mapping = _revalidate(MappingProfile, mapping)
        snapshot_rules = _revalidate(DetectorRuleSet, rules_data)
        snapshot_pending = _revalidate(PendingClarification, pending)
        snapshot_choice = _revalidate(ClarificationChoice, choice)
    except Exception:
        raise ClarificationExecutionAdapterError(
            ClarificationExecutionAdapterErrorCode.INVALID_INPUT
        ) from None

    snapshot_request = copy.deepcopy(snapshot_request)
    snapshot_ontology = copy.deepcopy(snapshot_ontology)
    snapshot_mapping = copy.deepcopy(snapshot_mapping)

    generation = run_constrained_generation(
        copy.deepcopy(snapshot_request),
        copy.deepcopy(snapshot_ontology),
        copy.deepcopy(snapshot_mapping),
        copy.deepcopy(snapshot_rules),
        copy.deepcopy(snapshot_pending),
        copy.deepcopy(snapshot_choice),
        provider,
    )
    try:
        frozen_generation = ConstrainedGenerationResult.model_validate(
            generation.model_dump(mode="json", warnings=False)
        )
    except Exception:
        raise ClarificationExecutionAdapterError(
            ClarificationExecutionAdapterErrorCode.INTERNAL_ERROR
        ) from None

    one_shot = _OneShotQueryModel(
        snapshot_request,
        snapshot_ontology,
        snapshot_mapping,
        frozen_generation.query_ir,
    )
    harness = ExecutionHarness(
        model=one_shot,  # type: ignore[arg-type]
        executor=executor,  # type: ignore[arg-type]
        compiler=resolved_compiler,
        sql_policy=resolved_policy,
    )
    try:
        harness_result = harness.run(
            copy.deepcopy(snapshot_request),
            copy.deepcopy(snapshot_ontology),
            copy.deepcopy(snapshot_mapping),
        )
    except Exception:
        raise ClarificationExecutionAdapterError(
            ClarificationExecutionAdapterErrorCode.INTERNAL_ERROR
        ) from None

    try:
        frozen_harness = HarnessResult.model_validate(
            harness_result.model_dump(mode="json", warnings=False)
        )
    except Exception:
        raise ClarificationExecutionAdapterError(
            ClarificationExecutionAdapterErrorCode.INTERNAL_ERROR
        ) from None

    try:
        return ClarificationExecutionResult.model_validate(
            {
                "adapter_version": CLARIFICATION_EXECUTION_ADAPTER_VERSION,
                "generation": frozen_generation.model_dump(
                    mode="json", warnings=False
                ),
                "harness_result": frozen_harness.model_dump(
                    mode="json", warnings=False
                ),
            }
        )
    except Exception:
        raise ClarificationExecutionAdapterError(
            ClarificationExecutionAdapterErrorCode.INTERNAL_ERROR
        ) from None
