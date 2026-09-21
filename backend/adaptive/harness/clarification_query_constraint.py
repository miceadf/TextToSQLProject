"""Confirmed-clarification semantic constraint for follow-up QueryIR generation.

`clarification-query-constraint-v1` closes one synthetic flow: re-detect and
re-confirm through the existing `ambiguity-detector-v1` /
`clarification-gate-v2` path, promote the confirmed ontology entity/field
`semantic_ref` to a frozen constraint, call a constraint-aware provider
exactly once, then verify the returned QueryIR structurally and semantically
before checking constraint compliance. Existing `QueryModel`,
`ExecutionHarness`, repair loops, UI, DB, Ollama, and legacy paths are
untouched.

Trust boundary: the pending object stays untrusted bearer data. The caller
cannot inject a `ClarificationResult`, `AmbiguitySignal`, raw `semantic_ref`,
or a prebuilt constraint. Every public input is revalidated from plain JSON
data (`model_dump(mode="json", warnings=False)` then `model_validate`), so
forged `model_copy(update=...)` / `model_construct` instances fail closed.
`resolve_detected_clarification` regenerates the signal from the same
request/ontology/mapping/rule set, and only the option whose opaque selected
fingerprint matches exactly is promoted. Cancel, no/multi match, pending,
choice, context, or rule-set drift, and forged inputs fail before any model
call. Existing ambiguity/clarification fixed errors propagate unwrapped.

Compliance rule (small and explicit, not a full answer verdict): an entity
`semantic_ref` requires `QueryIR.entity` to equal it exactly; a field
`semantic_ref` requires it to appear at least once in `QueryIR.select` or
`QueryIR.filters[].field`. Anything else is a fixed constraint violation and
is never forwarded to execution, compilation, or repair.

Bearer, rule-based detection limits: literal-trigger rules are not NLU, the
pending value is not a single-use token, and there is no execution, UI,
session, or expiry handling here. The provider receives only defensive deep
copies, is called exactly once, and its mutations cannot reach the caller or
the post-call validation context. No file, DB, network, executor, compiler,
repair, queue, or snapshot is touched.

Synthetic use only: pure functions over the inputs below.
"""

from __future__ import annotations

import copy
from enum import Enum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .ambiguity_detector import (
    AmbiguityDetectorError,
    AmbiguityDetectorErrorCode,
    DetectorRuleSet,
    detect_ambiguity,
    resolve_detected_clarification,
)
from .clarification import (
    ClarificationChoice,
    ClarificationStatus,
    PendingClarification,
    database_id_fingerprint,
    ontology_fingerprint,
    option_id_fingerprint,
    request_id_fingerprint,
)
from .contracts import HarnessRequest, MappingProfile, Ontology, QueryIR
from .errors import HarnessFailure
from .recorded import mapping_fingerprint
from .validator import validate_query_ir


CLARIFICATION_QUERY_CONSTRAINT_VERSION = "clarification-query-constraint-v1"

_SEMANTIC_REF_PATTERN = r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)?$"
_HEX64_PATTERN = r"^[0-9a-f]{64}$"


class ClarificationQueryConstraintErrorCode(str, Enum):
    INVALID_INPUT = "CONSTRAINT_INVALID_INPUT"
    CANCELLED = "CONSTRAINT_CANCELLED"
    PROVIDER_ERROR = "CONSTRAINT_PROVIDER_ERROR"
    INVALID_QUERY_IR = "CONSTRAINT_INVALID_QUERY_IR"
    SEMANTIC_INVALID = "CONSTRAINT_SEMANTIC_INVALID"
    CONSTRAINT_VIOLATION = "CONSTRAINT_VIOLATION"


class ClarificationQueryConstraintError(Exception):
    """Fail-closed error with a fixed code only; never reflects input."""

    def __init__(self, code: ClarificationQueryConstraintErrorCode) -> None:
        self.code = code.value
        super().__init__(self.code)


class ClarificationQueryConstraint(BaseModel):
    """Frozen semantic constraint: fingerprints plus one selected ref only."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    constraint_version: Literal["clarification-query-constraint-v1"] = (
        CLARIFICATION_QUERY_CONSTRAINT_VERSION
    )
    request_id_sha256: str = Field(pattern=_HEX64_PATTERN)
    database_id_sha256: str = Field(pattern=_HEX64_PATTERN)
    ontology_fingerprint: str = Field(pattern=_HEX64_PATTERN)
    mapping_sha256: str = Field(pattern=_HEX64_PATTERN)
    selected_semantic_ref: str = Field(
        min_length=1, max_length=200, pattern=_SEMANTIC_REF_PATTERN
    )


class ConstrainedGenerationResult(BaseModel):
    """Versioned success value: validated constraint plus QueryIR only."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    result_version: Literal["clarification-query-constraint-v1"] = (
        CLARIFICATION_QUERY_CONSTRAINT_VERSION
    )
    constraint: ClarificationQueryConstraint
    query_ir: QueryIR


class ConstraintAwareProvider(Protocol):
    def generate_ir(
        self,
        request: HarnessRequest,
        ontology: Ontology,
        mapping: MappingProfile,
        constraint: ClarificationQueryConstraint,
    ) -> Any:
        """Return data that can be validated as QueryIR."""


def _revalidate(model_cls: type[Any], value: Any) -> Any:
    if isinstance(value, model_cls):
        payload = value.model_dump(mode="json", warnings=False)
    elif isinstance(value, dict):
        payload = value
    else:
        raise ValueError("unsupported boundary input")
    return model_cls.model_validate(payload)


def _revalidate_query_ir_output(value: Any) -> QueryIR:
    if isinstance(value, QueryIR):
        payload = value.model_dump(mode="json", warnings=False)
    elif isinstance(value, dict):
        payload = value
    else:
        raise ValueError("unsupported provider output")
    return QueryIR.model_validate(payload)


def _reject_reflected_context(
    query_ir: QueryIR,
    request: HarnessRequest,
    ontology: Ontology,
    mapping: MappingProfile,
    rules: DetectorRuleSet,
    pending: PendingClarification,
    choice: ClarificationChoice,
) -> None:
    """Reject known raw boundary strings reflected into provider output."""
    semantic_names = {*ontology.entities, *ontology.fields}
    forbidden = {
        request.request_id,
        request.database_id,
        request.question,
        *(field.table for field in mapping.fields.values() if field.table not in semantic_names),
        *(field.column for field in mapping.fields.values() if field.column not in semantic_names),
        *(rule.rule_id for rule in rules.rules),
        *(trigger for rule in rules.rules for trigger in rule.triggers),
        *(option.option_key for rule in rules.rules for option in rule.options),
        *(option.option_id for option in pending.options),
        *choice.selection,
    }
    payload = query_ir.model_dump(mode="json", warnings=False)

    def walk(value: Any) -> bool:
        if isinstance(value, str):
            return value in forbidden
        if isinstance(value, list):
            return any(walk(item) for item in value)
        if isinstance(value, dict):
            return any(walk(item) for item in value.values())
        return False

    if walk(payload):
        raise ClarificationQueryConstraintError(
            ClarificationQueryConstraintErrorCode.INVALID_QUERY_IR
        )


def run_constrained_generation(
    request_data: HarnessRequest | dict[str, Any],
    ontology: Ontology,
    mapping: MappingProfile,
    rules_data: DetectorRuleSet | dict[str, Any],
    pending: PendingClarification | dict[str, Any],
    choice: ClarificationChoice | dict[str, Any],
    provider: ConstraintAwareProvider,
) -> ConstrainedGenerationResult:
    """Re-confirm a clarification, then generate one constraint-checked QueryIR.

    The caller supplies only the original detection context plus the bearer
    pending value, the user choice, and the provider. No
    `ClarificationResult`, `AmbiguitySignal`, raw `semantic_ref`, or
    prebuilt constraint is accepted. Existing detector/gate errors
    propagate unchanged; every other failure uses the fixed constraint
    taxonomy without reflecting input or provider text.
    """
    generate = getattr(provider, "generate_ir", None)
    if not callable(generate):
        raise ClarificationQueryConstraintError(
            ClarificationQueryConstraintErrorCode.INVALID_INPUT
        )

    confirmed = resolve_detected_clarification(
        request_data, ontology, mapping, rules_data, pending, choice
    )
    if confirmed.status is ClarificationStatus.CANCELLED:
        raise ClarificationQueryConstraintError(
            ClarificationQueryConstraintErrorCode.CANCELLED
        )
    if confirmed.status is not ClarificationStatus.CONFIRMED:
        raise ClarificationQueryConstraintError(
            ClarificationQueryConstraintErrorCode.INVALID_INPUT
        )
    selected_id = confirmed.selected_option_id
    if not selected_id:
        raise ClarificationQueryConstraintError(
            ClarificationQueryConstraintErrorCode.INVALID_INPUT
        )

    signal = detect_ambiguity(request_data, ontology, mapping, rules_data)
    if signal is None:
        raise AmbiguityDetectorError(AmbiguityDetectorErrorCode.CONTEXT_MISMATCH)
    # `detect_ambiguity` yields derived IDs while the confirmed result
    # carries the clarification-gate fingerprint of the chosen derived ID,
    # so compare through the same fingerprint domain.
    matches = [
        option
        for option in signal.options
        if option_id_fingerprint(option.option_id) == selected_id
    ]
    if len(matches) != 1:
        raise AmbiguityDetectorError(AmbiguityDetectorErrorCode.CONTEXT_MISMATCH)
    selected_ref = matches[0].semantic_ref

    try:
        validated_request = _revalidate(HarnessRequest, request_data)
        validated_ontology = _revalidate(Ontology, ontology)
        validated_mapping = _revalidate(MappingProfile, mapping)
        validated_rules = _revalidate(DetectorRuleSet, rules_data)
        validated_pending = _revalidate(PendingClarification, pending)
        validated_choice = _revalidate(ClarificationChoice, choice)
    except Exception:
        raise ClarificationQueryConstraintError(
            ClarificationQueryConstraintErrorCode.INVALID_INPUT
        ) from None
    if selected_ref not in validated_ontology.entities and (
        selected_ref not in validated_ontology.fields
    ):
        raise ClarificationQueryConstraintError(
            ClarificationQueryConstraintErrorCode.INVALID_INPUT
        )

    try:
        constraint = ClarificationQueryConstraint.model_validate(
            ClarificationQueryConstraint(
                constraint_version=CLARIFICATION_QUERY_CONSTRAINT_VERSION,
                request_id_sha256=request_id_fingerprint(validated_request.request_id),
                database_id_sha256=database_id_fingerprint(
                    validated_request.database_id
                ),
                ontology_fingerprint=ontology_fingerprint(validated_ontology),
                mapping_sha256=mapping_fingerprint(validated_mapping),
                selected_semantic_ref=selected_ref,
            ).model_dump(mode="json", warnings=False)
        )
    except Exception:
        raise ClarificationQueryConstraintError(
            ClarificationQueryConstraintErrorCode.INVALID_INPUT
        ) from None

    snapshot_request = copy.deepcopy(validated_request)
    snapshot_ontology = copy.deepcopy(validated_ontology)
    snapshot_mapping = copy.deepcopy(validated_mapping)
    snapshot_constraint = copy.deepcopy(constraint)

    try:
        raw_output = generate(
            copy.deepcopy(validated_request),
            copy.deepcopy(validated_ontology),
            copy.deepcopy(validated_mapping),
            copy.deepcopy(constraint),
        )
    except Exception:
        raise ClarificationQueryConstraintError(
            ClarificationQueryConstraintErrorCode.PROVIDER_ERROR
        ) from None

    try:
        query_ir = _revalidate_query_ir_output(raw_output)
    except Exception:
        raise ClarificationQueryConstraintError(
            ClarificationQueryConstraintErrorCode.INVALID_QUERY_IR
        ) from None

    _reject_reflected_context(
        query_ir,
        snapshot_request,
        snapshot_ontology,
        snapshot_mapping,
        validated_rules,
        validated_pending,
        validated_choice,
    )

    try:
        validate_query_ir(query_ir, snapshot_ontology, snapshot_mapping)
    except HarnessFailure:
        raise ClarificationQueryConstraintError(
            ClarificationQueryConstraintErrorCode.SEMANTIC_INVALID
        ) from None
    except Exception:
        raise ClarificationQueryConstraintError(
            ClarificationQueryConstraintErrorCode.SEMANTIC_INVALID
        ) from None

    if selected_ref in snapshot_ontology.entities:
        compliant = query_ir.entity == selected_ref
    else:
        used = [*query_ir.select, *(item.field for item in query_ir.filters)]
        compliant = selected_ref in used
    if not compliant:
        raise ClarificationQueryConstraintError(
            ClarificationQueryConstraintErrorCode.CONSTRAINT_VIOLATION
        )

    try:
        return ConstrainedGenerationResult.model_validate(
            {
                "result_version": CLARIFICATION_QUERY_CONSTRAINT_VERSION,
                "constraint": snapshot_constraint.model_dump(
                    mode="json", warnings=False
                ),
                "query_ir": query_ir.model_dump(mode="json", warnings=False),
            }
        )
    except Exception:
        raise ClarificationQueryConstraintError(
            ClarificationQueryConstraintErrorCode.INVALID_INPUT
        ) from None
