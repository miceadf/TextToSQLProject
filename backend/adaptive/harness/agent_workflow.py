"""Synthetic agent workflow: repair, critique, and review-queue registration.

One explicit coordinator call runs `validate -> execute -> bounded
repair -> critique -> failure queue registration` while preserving the
final `HarnessResult`. Existing `ExecutionHarness`, `QueryModel`,
`BoundedRepairCoordinator.run()`, critique taxonomy, review-queue
classification, and snapshot schema contracts are unchanged.

Contract (`agent-workflow-v1`):

- Inputs are an explicit `model_run_id`, request, runtime ontology,
  Published mapping, model, repair provider, and an injected
  `InMemoryReviewQueue`. There is no API that accepts a
  caller-supplied `CritiqueResult` or disposition; the workflow calls
  `critique_repair_summary()` internally, then `queue.enqueue()`.
- Success (`EXECUTED_UNVERIFIED`) and `REQUEST_INVALID` are never
  stored and return `NOT_ELIGIBLE` without touching the queue, per the
  existing queue contract. Only operational-failure dispositions are
  registered.
- Pre-execution validation checks workflow inputs and queue identity
  before any model, repair-provider, or DB call: bad `model_run_id`,
  queue-incompatible request IDs on otherwise valid requests, and bad
  queue/model/provider/ontology/mapping types fail closed with a fixed
  code, zero model/provider/executor calls, and zero queue entries.
  Queue ID rules are reused from the single public helper
  `validate_queue_identifier`; no copy of the rule lives here. An
  invalid `HarnessRequest` -- including a forged or mutated instance
  that fails ordinary validation -- keeps the existing
  harness/critique `REQUEST_INVALID` outcome and is not queued.
- Queue conflicts or unexpected internal contract mismatches fail
  closed with a fixed code and leave the stored entry unchanged.
- The result carries the contract version, final `RepairSummary`,
  `CritiqueResult`, queue registration status/entry, and the final
  `HarnessResult` when one exists (`None` only for
  `REPAIR_PROVIDER_ERROR`). Success rows, including empty success,
  are preserved without inferring accuracy, correctness, or user
  satisfaction. Raw exceptions, questions, SQL, rows, physical
  mappings, local paths, and secrets are never copied into new
  summaries, queue entries, errors, stdout, or module logs. The
  embedded `HarnessResult` keeps its existing SQL/rows contract as an
  explicit caller return value.
- Residual boundary: model/repair/DB execution happens before queue
  registration, so a post-execution queue `CONFLICT` or internal error
  leaves those read-only calls consumed while the queue entry is
  preserved. No snapshot write, overwrite, retry, clarification,
  QueryIR fix-up, or mapping/glossary/verified-query change is made
  here.

Synthetic use only: injected models/providers/queues and read-only
SQLite execution. No file, network, Ollama, PostgreSQL, CNU data, UI,
or operational persistence access.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .contracts import HarnessRequest, HarnessResult, MappingProfile, Ontology
from .critique import (
    CritiqueDisposition,
    CritiqueError,
    CritiqueResult,
    critique_repair_summary,
)
from .model import QueryModel
from .repair import (
    BoundedRepairCoordinator,
    DetailedRepairOutcome,
    RepairProvider,
    RepairSummary,
)
from .review_queue import (
    REVIEW_QUEUE_VERSION,
    InMemoryReviewQueue,
    ReviewQueueEntry,
    ReviewQueueError,
    ReviewQueueErrorCode,
    ReviewQueueStatus,
    validate_queue_identifier,
)

AGENT_WORKFLOW_VERSION = "agent-workflow-v1"

AGENT_WORKFLOW_INVALID_INPUT = "AGENT_WORKFLOW_INVALID_INPUT"
AGENT_WORKFLOW_CONFLICT = "AGENT_WORKFLOW_CONFLICT"
AGENT_WORKFLOW_INTERNAL_ERROR = "AGENT_WORKFLOW_INTERNAL_ERROR"


class AgentWorkflowErrorCode(str, Enum):
    INVALID_INPUT = AGENT_WORKFLOW_INVALID_INPUT
    CONFLICT = AGENT_WORKFLOW_CONFLICT
    INTERNAL_ERROR = AGENT_WORKFLOW_INTERNAL_ERROR


class AgentWorkflowError(Exception):
    """Fail-closed workflow error with a fixed code only."""

    def __init__(self, code: AgentWorkflowErrorCode) -> None:
        self.code = code.value
        super().__init__(self.code)


class AgentWorkflowResult(BaseModel):
    """One workflow execution; fixed metadata plus preserved result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_version: str = Field(pattern=r"^agent-workflow-v1$")
    repair_summary: RepairSummary
    critique_result: CritiqueResult
    queue_status: ReviewQueueStatus = Field(strict=False)
    queue_entry: ReviewQueueEntry | None = None
    final_result: HarnessResult | None = None

    @model_validator(mode="before")
    @classmethod
    def _revalidate_nested(cls, data: Any) -> Any:
        # Forged nested instances (`model_copy(update=...)`,
        # `model_construct`) may skip validation, so revalidate every
        # nested instance from plain JSON data first. Serializer
        # warnings can echo forged input, hence suppressed.
        if isinstance(data, BaseModel):
            try:
                data = data.model_dump(mode="json", warnings=False)
            except Exception:
                raise ValueError("invalid workflow result") from None
            if not isinstance(data, dict):
                raise ValueError("invalid workflow result")
        if isinstance(data, dict):
            normalized: dict[str, Any] | None = None
            for key in (
                "repair_summary",
                "critique_result",
                "queue_entry",
                "final_result",
            ):
                value = data.get(key)
                if isinstance(value, BaseModel):
                    if normalized is None:
                        normalized = dict(data)
                    try:
                        normalized[key] = value.model_dump(
                            mode="json", warnings=False
                        )
                    except Exception:
                        raise ValueError("invalid workflow result") from None
            if normalized is not None:
                return normalized
        return data

    @model_validator(mode="after")
    def _validate_consistency(self) -> AgentWorkflowResult:
        summary = self.repair_summary
        critique = self.critique_result
        try:
            expected = critique_repair_summary(summary)
        except (CritiqueError, ValueError, TypeError):
            raise ValueError("critique and summary disagree") from None
        if critique != expected:
            raise ValueError("critique and summary disagree")
        try:
            DetailedRepairOutcome(
                summary=summary, final_result=self.final_result
            )
        except ValueError:
            raise
        except Exception:
            raise ValueError("summary and result disagree") from None
        if self.queue_status is ReviewQueueStatus.NOT_ELIGIBLE:
            if self.queue_entry is not None:
                raise ValueError("ineligible result must not carry an entry")
            if critique.disposition not in (
                CritiqueDisposition.EXECUTED_UNVERIFIED,
                CritiqueDisposition.REQUEST_INVALID,
            ):
                raise ValueError("ineligible result must succeed or refuse")
            return self
        if critique.disposition in (
            CritiqueDisposition.EXECUTED_UNVERIFIED,
            CritiqueDisposition.REQUEST_INVALID,
        ):
            raise ValueError("non-operational result must not be registered")
        entry = self.queue_entry
        if entry is None:
            raise ValueError("eligible result must carry an entry")
        if entry.queue_version != REVIEW_QUEUE_VERSION:
            raise ValueError("queue entry version mismatch")
        if (
            entry.disposition != critique.disposition
            or entry.final_code != critique.final_code
            or entry.final_stage != critique.final_stage
            or entry.repair_attempted != critique.repair_attempted
            or entry.recovered_after_repair != critique.recovered_after_repair
        ):
            raise ValueError("queue entry and critique disagree")
        return self


def _invalid_input() -> AgentWorkflowError:
    return AgentWorkflowError(AgentWorkflowErrorCode.INVALID_INPUT)


def _internal_error() -> AgentWorkflowError:
    return AgentWorkflowError(AgentWorkflowErrorCode.INTERNAL_ERROR)


class AgentWorkflowCoordinator:
    """Run detailed repair, internal critique, and queue registration once."""

    def __init__(self, executor: Any, *, compiler: Any | None = None,
                 sql_policy: Any | None = None) -> None:
        self._repair = BoundedRepairCoordinator(
            executor, compiler=compiler, sql_policy=sql_policy
        )

    def run(
        self,
        request_data: HarnessRequest | dict[str, Any],
        ontology: Ontology,
        mapping: MappingProfile,
        *,
        model: QueryModel,
        repair_provider: RepairProvider,
        model_run_id: str,
        queue: InMemoryReviewQueue,
    ) -> AgentWorkflowResult:
        """Validate, execute with bounded repair, critique, and register."""
        # ---- Pre-execution validation: no model/provider/executor calls.
        try:
            raw_run_id = validate_queue_identifier(model_run_id)
        except ReviewQueueError:
            raise _invalid_input() from None
        except Exception:
            raise _internal_error() from None
        if not isinstance(queue, InMemoryReviewQueue):
            raise _invalid_input()
        if not isinstance(ontology, Ontology) or not isinstance(
            mapping, MappingProfile
        ):
            raise _invalid_input()
        if not callable(getattr(model, "generate_ir", None)):
            raise _invalid_input()
        if not callable(getattr(repair_provider, "propose_repair", None)):
            raise _invalid_input()

        # Derive the queue request ID without executing anything. A
        # forged or mutated HarnessRequest *instance* that fails ordinary
        # validation follows the existing INVALID_REQUEST path (never
        # queued, zero model/provider/executor calls). Instance input is
        # normalized to plain data first so Pydantic instance
        # short-circuit cannot bypass validation in the repair
        # coordinator below.
        validated_request: HarnessRequest | None = None
        request_for_repair: HarnessRequest | dict[str, Any]
        if isinstance(request_data, HarnessRequest):
            try:
                plain = request_data.model_dump(mode="json", warnings=False)
            except Exception:
                plain = {}
            if not isinstance(plain, dict):
                plain = {}
            try:
                validated_request = HarnessRequest.model_validate(plain)
            except (ValidationError, ValueError, TypeError):
                validated_request = None
            if validated_request is None:
                request_for_repair = plain
            else:
                try:
                    validate_queue_identifier(validated_request.request_id)
                except ReviewQueueError:
                    raise _invalid_input() from None
                except Exception:
                    raise _internal_error() from None
                request_for_repair = plain
        elif isinstance(request_data, dict):
            request_for_repair = request_data
            try:
                validated_request = HarnessRequest.model_validate(request_data)
            except (ValidationError, ValueError, TypeError):
                validated_request = None
            if validated_request is not None:
                try:
                    validate_queue_identifier(validated_request.request_id)
                except ReviewQueueError:
                    raise _invalid_input() from None
                except Exception:
                    raise _internal_error() from None
        else:
            raise _invalid_input()

        # ---- Detailed repair execution (single budget, snapshots inside).
        detailed = self._repair.run_detailed(
            request_for_repair,
            ontology,
            mapping,
            model=model,
            repair_provider=repair_provider,
        )
        try:
            summary = RepairSummary.model_validate(
                detailed.summary.model_dump(mode="json")
            )
        except Exception:
            raise _internal_error() from None
        if detailed.final_result is not None:
            try:
                final_result = HarnessResult.model_validate(
                    detailed.final_result.model_dump(mode="json")
                )
            except Exception:
                raise _internal_error() from None
        else:
            final_result = None

        # ---- Internal critique; caller cannot inject a CritiqueResult.
        try:
            critique = critique_repair_summary(summary)
        except CritiqueError:
            raise _internal_error() from None
        except Exception:
            raise _internal_error() from None

        # ---- Queue registration for operational failures only.
        if critique.disposition in (
            CritiqueDisposition.EXECUTED_UNVERIFIED,
            CritiqueDisposition.REQUEST_INVALID,
        ):
            try:
                return AgentWorkflowResult(
                    workflow_version=AGENT_WORKFLOW_VERSION,
                    repair_summary=summary.model_copy(deep=True),
                    critique_result=critique.model_copy(deep=True),
                    queue_status=ReviewQueueStatus.NOT_ELIGIBLE,
                    queue_entry=None,
                    final_result=(
                        final_result.model_copy(deep=True)
                        if final_result is not None
                        else None
                    ),
                )
            except Exception:
                raise _internal_error() from None

        if validated_request is None:
            # Eligible dispositions require a valid request ID; reaching
            # here means an internal contract mismatch, not a queue write.
            raise _internal_error()
        try:
            registration = queue.enqueue(
                summary,
                model_run_id=raw_run_id,
                request_id=validated_request.request_id,
            )
        except ReviewQueueError as exc:
            if exc.code == ReviewQueueErrorCode.CONFLICT.value:
                raise AgentWorkflowError(
                    AgentWorkflowErrorCode.CONFLICT
                ) from None
            raise _internal_error() from None
        except CritiqueError:
            raise _internal_error() from None
        except Exception:
            raise _internal_error() from None

        if registration.status not in (
            ReviewQueueStatus.ENQUEUED,
            ReviewQueueStatus.ALREADY_PRESENT,
        ):
            raise _internal_error()
        entry = registration.entry
        if entry is None:
            raise _internal_error()
        try:
            entry_copy = ReviewQueueEntry.model_validate(
                entry.model_dump(mode="json")
            )
        except Exception:
            raise _internal_error() from None
        try:
            return AgentWorkflowResult(
                workflow_version=AGENT_WORKFLOW_VERSION,
                repair_summary=summary.model_copy(deep=True),
                critique_result=critique.model_copy(deep=True),
                queue_status=registration.status,
                queue_entry=entry_copy,
                final_result=(
                    final_result.model_copy(deep=True)
                    if final_result is not None
                    else None
                ),
            )
        except Exception:
            raise _internal_error() from None


__all__ = [
    "AGENT_WORKFLOW_CONFLICT",
    "AGENT_WORKFLOW_INTERNAL_ERROR",
    "AGENT_WORKFLOW_INVALID_INPUT",
    "AGENT_WORKFLOW_VERSION",
    "AgentWorkflowCoordinator",
    "AgentWorkflowError",
    "AgentWorkflowErrorCode",
    "AgentWorkflowResult",
]
