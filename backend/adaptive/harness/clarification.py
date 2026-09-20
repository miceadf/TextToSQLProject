"""Explicit caller-provided ambiguity signal and user-confirmation boundary.

A small synthetic contract: when a caller already holds an explicit
ambiguity signal (two or more candidate readings), `prepare` binds that
signal to the exact request/ontology/mapping context as a pending
clarification. `resolve` accepts exactly one presented option ID as
`CONFIRMED`, or a separate `CANCELLED` state. Nothing here detects
ambiguity (no model, no string heuristics), rewrites the question,
generates or repairs QueryIR, runs the harness, or touches UI, admin
queues, storage, DB, network, Ollama, or legacy paths.

Trust boundary: the pending object is untrusted bearer data. `resolve`
therefore requires the original caller-owned `AmbiguitySignal` again
and accepts a confirmation only when every sorted option ID plus its
semantic_ref matches the pending object exactly. This proves
consistency with the trusted caller signal, not cryptographic
authenticity: it does not authenticate who created the pending object
and does not replace signatures or a server-side session store.

Privacy: pending objects, results, errors, and this module's output
never carry the raw question, raw request or database identifiers
(which may be arbitrary path or secret text), physical table/column
names, SQL, rows, paths, or secrets. Binding uses domain-separated
SHA-256 over UTF-8 bytes (canonical JSON for structured values) and
stores hashes only.

Revalidation: Pydantic `model_validate` on an already constructed model
instance may skip validation after `model_copy(update=...)` or
`model_construct` forgery, so every public boundary revalidates by
serializing instances to plain JSON data first and validating the data.
Forged instances fail closed with the fixed code of their boundary.

Stateless limit: pending objects are bearer values, not single-use
tokens. This contract does not enforce one-time consumption, expiry, or
revocation; a presented pending value can be resolved more than once by
any holder. Callers that need single-use semantics must enforce it
outside this module.

Synthetic use only: pure functions over the inputs below. No file, DB,
or network access. Existing `CritiqueResult` and the harness error
taxonomy are unchanged; `INVALID_REQUEST` and other failures are never
auto-promoted into a clarification signal here.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .contracts import HarnessRequest, MappingProfile, MappingStatus, Ontology
from .recorded import mapping_fingerprint, question_fingerprint


CLARIFICATION_CONTRACT_VERSION = "clarification-gate-v2"

_OPTION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
_SEMANTIC_REF_PATTERN = r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)?$"
_HEX64_PATTERN = r"^[0-9a-f]{64}$"

_REQUEST_ID_DOMAIN = "clarification-gate-v2:request-id:"
_DATABASE_ID_DOMAIN = "clarification-gate-v2:database-id:"
_OPTION_ID_DOMAIN = "clarification-gate-v2:option-id:"


class ClarificationErrorCode(str, Enum):
    INVALID_SIGNAL = "CLARIFICATION_INVALID_SIGNAL"
    CONTEXT_MISMATCH = "CLARIFICATION_CONTEXT_MISMATCH"
    INVALID_CHOICE = "CLARIFICATION_INVALID_CHOICE"


class ClarificationError(Exception):
    """Fail-closed error with a fixed code only; never reflects input."""

    def __init__(self, code: ClarificationErrorCode) -> None:
        self.code = code.value
        super().__init__(self.code)


class ClarificationStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


class AmbiguityOption(BaseModel):
    """One candidate reading: stable ID plus a semantic identifier.

    `semantic_ref` names an ontology entity (e.g. ``Course``) or an
    ontology field (e.g. ``Course.name``). Physical table/column names,
    SQL, result rows, display strings, and free-text answers have no
    field here and are rejected by the membership check in `prepare`.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    option_id: str = Field(pattern=_OPTION_ID_PATTERN)
    semantic_ref: str = Field(min_length=1, max_length=200, pattern=_SEMANTIC_REF_PATTERN)


class AmbiguitySignal(BaseModel):
    """Caller-provided signal; never inferred from the question text."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    options: list[AmbiguityOption] = Field(min_length=2, max_length=16)

    @model_validator(mode="after")
    def options_are_distinct(self) -> "AmbiguitySignal":
        option_ids = [option.option_id for option in self.options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("ambiguity option IDs must be distinct")
        semantic_refs = [option.semantic_ref for option in self.options]
        if len(semantic_refs) != len(set(semantic_refs)):
            raise ValueError("ambiguity semantic refs must be distinct")
        return self


class PendingClarification(BaseModel):
    """Context-bound pending state; fingerprints and IDs only, no raw content.

    Raw request and database identifiers are never stored: only their
    domain-separated SHA-256 fingerprints are kept, because identifier
    strings may be arbitrary path or secret text.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    clarification_version: str = Field(pattern=r"^clarification-gate-v2$")
    request_id_sha256: str = Field(pattern=_HEX64_PATTERN)
    database_id_sha256: str = Field(pattern=_HEX64_PATTERN)
    question_sha256: str = Field(pattern=_HEX64_PATTERN)
    ontology_fingerprint: str = Field(pattern=_HEX64_PATTERN)
    mapping_sha256: str = Field(pattern=_HEX64_PATTERN)
    mapping_version: int = Field(ge=1)
    options: list[AmbiguityOption] = Field(min_length=2, max_length=16)

    @field_validator("options")
    @classmethod
    def options_sorted_and_distinct(
        cls, options: list[AmbiguityOption]
    ) -> list[AmbiguityOption]:
        option_ids = [option.option_id for option in options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("pending option IDs must be distinct")
        semantic_refs = [option.semantic_ref for option in options]
        if len(semantic_refs) != len(set(semantic_refs)):
            raise ValueError("pending semantic refs must be distinct")
        if option_ids != sorted(option_ids):
            raise ValueError("pending options must be sorted by option ID")
        return options

    @property
    def option_ids(self) -> list[str]:
        return [option.option_id for option in self.options]


class ClarificationChoice(BaseModel):
    """User answer: either one presented option ID or an explicit cancel.

    Free-text answers have no field here: `selection` holds option IDs
    only, and any string outside the pending set is rejected at resolve
    time. An empty selection without `cancelled` is not a confirmation.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    selection: list[str] = Field(default_factory=list, max_length=16)
    cancelled: bool = False


class ClarificationResult(BaseModel):
    """Deterministic outcome; fingerprints and status only, no raw content."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    clarification_version: str = Field(pattern=r"^clarification-gate-v2$")
    status: ClarificationStatus = Field(strict=False)
    request_id_sha256: str = Field(pattern=_HEX64_PATTERN)
    database_id_sha256: str = Field(pattern=_HEX64_PATTERN)
    selected_option_id: str | None = None

    @model_validator(mode="after")
    def selection_matches_status(self) -> "ClarificationResult":
        if self.status is ClarificationStatus.CONFIRMED and not self.selected_option_id:
            raise ValueError("confirmed results must carry the selected option ID")
        if self.status is ClarificationStatus.CANCELLED and self.selected_option_id is not None:
            raise ValueError("cancelled results must not carry an option ID")
        return self


def _domain_fingerprint(domain: str, value: str) -> str:
    return hashlib.sha256((domain + value).encode("utf-8")).hexdigest()


def request_id_fingerprint(request_id: str) -> str:
    """Domain-separated hash of a raw request identifier (never stored raw)."""
    return _domain_fingerprint(_REQUEST_ID_DOMAIN, request_id)


def database_id_fingerprint(database_id: str) -> str:
    """Domain-separated hash of a raw database identifier (never stored raw)."""
    return _domain_fingerprint(_DATABASE_ID_DOMAIN, database_id)


def option_id_fingerprint(option_id: str) -> str:
    """Opaque ID used in pending state and results; never store caller IDs raw."""
    return _domain_fingerprint(_OPTION_ID_DOMAIN, option_id)


def ontology_fingerprint(ontology: Ontology) -> str:
    """Deterministic hash of the runtime ontology; set/dict order free."""
    canonical = {
        "entities": sorted(ontology.entities),
        "fields": {
            name: {
                "allowed_operators": sorted(op.value for op in field.allowed_operators),
                "data_type": field.data_type.value,
                "name": field.name,
            }
            for name, field in sorted(ontology.fields.items())
        },
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _revalidate(model_cls: type[Any], value: Any) -> Any:
    """Revalidate boundary input even if it is a forged model instance.

    `model_validate` on an already constructed instance may skip
    validation after `model_copy(update=...)` or `model_construct`
    forgery, so instances are serialized to plain JSON data first and
    the data is validated. Anything unserializable or invalid raises the
    caller's fixed error; the raw input is never reflected.
    """
    if isinstance(value, model_cls):
        # Forged instances can carry arbitrary values. Pydantic's default
        # serializer warnings include those values, so suppress them here.
        payload = value.model_dump(mode="json", warnings=False)
    elif isinstance(value, dict):
        payload = value
    else:
        raise ValueError("unsupported boundary input")
    return model_cls.model_validate(payload)


def _is_known_semantic_ref(semantic_ref: str, ontology: Ontology) -> bool:
    if semantic_ref in ontology.entities:
        return True
    return semantic_ref in ontology.fields


def _safe_options(signal: AmbiguitySignal) -> list[AmbiguityOption]:
    return sorted(
        (
            AmbiguityOption(
                option_id=option_id_fingerprint(option.option_id),
                semantic_ref=option.semantic_ref,
            )
            for option in signal.options
        ),
        key=lambda option: option.option_id,
    )


def _sorted_option_pairs(pending: PendingClarification) -> list[tuple[str, str]]:
    return sorted(
        ((option.option_id, option.semantic_ref) for option in pending.options),
        key=lambda pair: pair[0],
    )


def prepare(
    request_data: HarnessRequest | dict[str, Any],
    ontology: Ontology,
    mapping: MappingProfile,
    signal: AmbiguitySignal | dict[str, Any] | None,
) -> PendingClarification | None:
    """Bind an explicit signal to the exact context, or return None.

    A missing signal (`None`) means no pending clarification is created
    and the call is never blocked. Any shape violation, unpublished
    mapping, database mismatch, or unknown semantic reference fails
    closed with `CLARIFICATION_INVALID_SIGNAL`. Raw identifiers are
    hashed before storage and never kept.
    """
    if signal is None:
        return None
    try:
        request = _revalidate(HarnessRequest, request_data)
        validated_ontology = _revalidate(Ontology, ontology)
        validated_mapping = _revalidate(MappingProfile, mapping)
        validated_signal = _revalidate(AmbiguitySignal, signal)
    except Exception:
        raise ClarificationError(ClarificationErrorCode.INVALID_SIGNAL) from None
    if validated_mapping.status is not MappingStatus.PUBLISHED:
        raise ClarificationError(ClarificationErrorCode.INVALID_SIGNAL)
    if validated_mapping.database_id != request.database_id:
        raise ClarificationError(ClarificationErrorCode.INVALID_SIGNAL)
    for option in validated_signal.options:
        if not _is_known_semantic_ref(option.semantic_ref, validated_ontology):
            raise ClarificationError(ClarificationErrorCode.INVALID_SIGNAL)
    ordered = _safe_options(validated_signal)
    try:
        return PendingClarification(
            clarification_version=CLARIFICATION_CONTRACT_VERSION,
            request_id_sha256=request_id_fingerprint(request.request_id),
            database_id_sha256=database_id_fingerprint(request.database_id),
            question_sha256=question_fingerprint(request.question),
            ontology_fingerprint=ontology_fingerprint(validated_ontology),
            mapping_sha256=mapping_fingerprint(validated_mapping),
            mapping_version=validated_mapping.version,
            options=ordered,
        )
    except (ValidationError, ValueError):
        raise ClarificationError(ClarificationErrorCode.INVALID_SIGNAL) from None


def resolve(
    request_data: HarnessRequest | dict[str, Any],
    ontology: Ontology,
    mapping: MappingProfile,
    pending: PendingClarification | dict[str, Any],
    signal: AmbiguitySignal | dict[str, Any],
    choice: ClarificationChoice | dict[str, Any],
) -> ClarificationResult:
    """Confirm exactly one presented option or record an explicit cancel.

    The caller-owned `signal` is required again: every sorted option ID
    plus its semantic_ref must match the untrusted pending object
    exactly, otherwise the pending value was altered, extended, or cut
    down and the call fails closed with
    `CLARIFICATION_CONTEXT_MISMATCH`. This checks consistency with the
    trusted caller signal, not cryptographic authenticity. The original
    request, ontology, and mapping are revalidated against the pending
    binding; any drift in identifiers, question, ontology, or mapping
    fails closed the same way. Unknown, empty, multiple, or free-text
    selections fail with `CLARIFICATION_INVALID_CHOICE`. Cancellation
    never picks a default.
    """
    try:
        request = _revalidate(HarnessRequest, request_data)
        validated_ontology = _revalidate(Ontology, ontology)
        validated_mapping = _revalidate(MappingProfile, mapping)
        validated_pending = _revalidate(PendingClarification, pending)
    except Exception:
        raise ClarificationError(ClarificationErrorCode.CONTEXT_MISMATCH) from None
    try:
        validated_signal = _revalidate(AmbiguitySignal, signal)
    except Exception:
        raise ClarificationError(ClarificationErrorCode.INVALID_SIGNAL) from None
    try:
        validated_choice = _revalidate(ClarificationChoice, choice)
    except Exception:
        raise ClarificationError(ClarificationErrorCode.INVALID_CHOICE) from None

    if validated_mapping.status is not MappingStatus.PUBLISHED:
        raise ClarificationError(ClarificationErrorCode.CONTEXT_MISMATCH)
    for option in validated_signal.options:
        if not _is_known_semantic_ref(option.semantic_ref, validated_ontology):
            raise ClarificationError(ClarificationErrorCode.INVALID_SIGNAL)
    expected_pairs = [(option.option_id, option.semantic_ref) for option in _safe_options(validated_signal)]
    if expected_pairs != _sorted_option_pairs(validated_pending):
        raise ClarificationError(ClarificationErrorCode.CONTEXT_MISMATCH)
    if (
        validated_pending.clarification_version != CLARIFICATION_CONTRACT_VERSION
        or validated_pending.request_id_sha256 != request_id_fingerprint(request.request_id)
        or validated_pending.database_id_sha256 != database_id_fingerprint(request.database_id)
        or validated_pending.question_sha256 != question_fingerprint(request.question)
        or validated_pending.ontology_fingerprint
        != ontology_fingerprint(validated_ontology)
        or validated_pending.mapping_sha256 != mapping_fingerprint(validated_mapping)
        or validated_pending.mapping_version != validated_mapping.version
    ):
        raise ClarificationError(ClarificationErrorCode.CONTEXT_MISMATCH)

    allowed = set(validated_pending.option_ids)
    try:
        if validated_choice.cancelled:
            if validated_choice.selection:
                raise ClarificationError(ClarificationErrorCode.INVALID_CHOICE)
            return ClarificationResult(
                clarification_version=CLARIFICATION_CONTRACT_VERSION,
                status=ClarificationStatus.CANCELLED,
                request_id_sha256=request_id_fingerprint(request.request_id),
                database_id_sha256=database_id_fingerprint(request.database_id),
                selected_option_id=None,
            )
        if len(validated_choice.selection) != 1:
            raise ClarificationError(ClarificationErrorCode.INVALID_CHOICE)
        raw_selected = validated_choice.selection[0]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", raw_selected):
            raise ClarificationError(ClarificationErrorCode.INVALID_CHOICE)
        selected = option_id_fingerprint(raw_selected)
        if selected not in allowed:
            raise ClarificationError(ClarificationErrorCode.INVALID_CHOICE)
        return ClarificationResult(
            clarification_version=CLARIFICATION_CONTRACT_VERSION,
            status=ClarificationStatus.CONFIRMED,
            request_id_sha256=request_id_fingerprint(request.request_id),
            database_id_sha256=database_id_fingerprint(request.database_id),
            selected_option_id=selected,
        )
    except ClarificationError:
        raise
    except (ValidationError, ValueError):
        raise ClarificationError(ClarificationErrorCode.INVALID_CHOICE) from None
