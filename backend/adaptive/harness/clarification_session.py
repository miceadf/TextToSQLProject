"""Bearer clarification session boundary (synthetic only).

`clarification-session-v1` connects Phase 6-08/10
(`prepare_detected_clarification` -> pending -> choice ->
`run_clarified_execution`) through one framework-neutral in-memory
coordinator. No HTTP/FastAPI, React UI, durable storage, Ollama,
PostgreSQL, or CNU material is touched.

Memory limit: an active session record keeps the raw question and its
request/ontology/mapping/rule context in process memory so a later
`submit` can re-confirm without caller-supplied context. `submit` and
expiry remove the active record, but immediate secure erasure of Python
memory is not claimed. Out of scope: persistence, cross-process
sharing, restart recovery, distributed locks, authentication /
authorization, rate limits, HTTP cookies / CSRF, and UI labels.

At-most-once dispatch: the first syntactically valid `submit` claims
the active record and transitions it to a terminal consumed tombstone
under a lock before any provider/compiler/policy/executor call, so at
most one concurrent submission reaches downstream. Downstream failures
never restore the session or retry. Process-crash exactly-once is not
claimed.

Privacy: only domain-separated SHA-256 of the raw bearer token is
stored. Tokens, questions, raw request/database identifiers, physical
mapping, SQL, rows, paths, secrets, and downstream exception text never
appear in errors, stdout, or logs. This module writes no stdout/logs.
"""

from __future__ import annotations

import copy
import hashlib
import math
import re
import secrets
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .ambiguity_detector import (
    DetectorRuleSet,
    detect_ambiguity,
    prepare_detected_clarification,
)
from .ambiguity_detector import resolve_detected_clarification as _resolve_detected
from .clarification import (
    ClarificationChoice,
    ClarificationResult,
    ClarificationStatus,
    AmbiguityOption,
    PendingClarification,
    database_id_fingerprint,
    ontology_fingerprint,
    option_id_fingerprint,
    request_id_fingerprint,
)
from .clarification_execution_adapter import (
    ClarificationExecutionResult,
    run_clarified_execution,
)
from .contracts import HarnessRequest, MappingProfile, Ontology
from .recorded import mapping_fingerprint


CLARIFICATION_SESSION_VERSION = "clarification-session-v1"

_TOKEN_DOMAIN = "clarification-session-v1:token:"
_TOKEN_PATTERN = r"^[A-Za-z0-9_-]{43,256}$"
_HEX64_PATTERN = r"^[0-9a-f]{64}$"

_MIN_TTL_SECONDS = 1.0
_MAX_TTL_SECONDS = 86400.0


class ClarificationSessionErrorCode(str, Enum):
    INVALID_INPUT = "CLARIFICATION_SESSION_INVALID_INPUT"
    NOT_FOUND = "CLARIFICATION_SESSION_NOT_FOUND"
    EXPIRED = "CLARIFICATION_SESSION_EXPIRED"
    ALREADY_CONSUMED = "CLARIFICATION_SESSION_ALREADY_CONSUMED"
    INTERNAL_ERROR = "CLARIFICATION_SESSION_INTERNAL_ERROR"


class ClarificationSessionError(Exception):
    """Fail-closed session error with a fixed code only."""

    def __init__(self, code: ClarificationSessionErrorCode) -> None:
        self.code = code.value
        super().__init__(self.code)


class ClarificationSessionStartStatus(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"


class ClarificationSessionSubmitStatus(str, Enum):
    CANCELLED = "CANCELLED"
    EXECUTED = "EXECUTED"


class ClarificationSessionStartResult(BaseModel):
    """Versioned frozen start outcome: token/pending only when PENDING."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    session_version: Literal["clarification-session-v1"] = CLARIFICATION_SESSION_VERSION
    status: ClarificationSessionStartStatus = Field(strict=False)
    request_id_sha256: str = Field(pattern=_HEX64_PATTERN)
    database_id_sha256: str = Field(pattern=_HEX64_PATTERN)
    ontology_fingerprint: str = Field(pattern=_HEX64_PATTERN)
    mapping_sha256: str = Field(pattern=_HEX64_PATTERN)
    mapping_version: int = Field(ge=1)
    session_token: str | None = None
    pending: PendingClarification | None = None
    options: list[AmbiguityOption] | None = None

    @model_validator(mode="before")
    @classmethod
    def _revalidate_nested(cls, data: Any) -> Any:
        if isinstance(data, dict):
            nested = dict(data)
            changed = False
            if isinstance(data.get("pending"), BaseModel):
                nested["pending"] = data["pending"].model_dump(
                    mode="json", warnings=False
                )
                changed = True
            options = data.get("options")
            if isinstance(options, list) and any(
                isinstance(option, BaseModel) for option in options
            ):
                nested["options"] = [
                    option.model_dump(mode="json", warnings=False)
                    if isinstance(option, BaseModel)
                    else option
                    for option in options
                ]
                changed = True
            if changed:
                return nested
        return data

    @model_validator(mode="after")
    def _check_start_invariants(self) -> "ClarificationSessionStartResult":
        if self.status is ClarificationSessionStartStatus.NOT_REQUIRED:
            if (
                self.session_token is not None
                or self.pending is not None
                or self.options is not None
            ):
                raise ValueError("not-required start must not carry session data")
        else:
            if self.session_token is None or self.pending is None or self.options is None:
                raise ValueError("pending start must carry token, pending, and options")
            if not re.fullmatch(_TOKEN_PATTERN, self.session_token):
                raise ValueError("pending start carries a malformed token")
            option_pairs = sorted(
                (option_id_fingerprint(option.option_id), option.semantic_ref)
                for option in self.options
            )
            pending_pairs = sorted(
                (option.option_id, option.semantic_ref) for option in self.pending.options
            )
            if option_pairs != pending_pairs:
                raise ValueError("selectable options do not match pending binding")
            if (
                self.pending.request_id_sha256 != self.request_id_sha256
                or self.pending.database_id_sha256 != self.database_id_sha256
                or self.pending.ontology_fingerprint != self.ontology_fingerprint
                or self.pending.mapping_sha256 != self.mapping_sha256
                or self.pending.mapping_version != self.mapping_version
            ):
                raise ValueError("pending binding does not match start request")
        return self


class ClarificationSessionSubmitResult(BaseModel):
    """Versioned frozen submit outcome: one nested result only."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    submission_version: Literal["clarification-session-v1"] = CLARIFICATION_SESSION_VERSION
    status: ClarificationSessionSubmitStatus = Field(strict=False)
    request_id_sha256: str = Field(pattern=_HEX64_PATTERN)
    database_id_sha256: str = Field(pattern=_HEX64_PATTERN)
    clarification_result: ClarificationResult | None = None
    execution: ClarificationExecutionResult | None = None

    @model_validator(mode="before")
    @classmethod
    def _revalidate_nested(cls, data: Any) -> Any:
        if isinstance(data, dict):
            nested = dict(data)
            changed = False
            for key in ("clarification_result", "execution"):
                value = nested.get(key)
                if isinstance(value, BaseModel):
                    nested[key] = value.model_dump(mode="json", warnings=False)
                    changed = True
            if changed:
                return nested
        return data

    @model_validator(mode="after")
    def _check_submit_invariants(self) -> "ClarificationSessionSubmitResult":
        if self.status is ClarificationSessionSubmitStatus.CANCELLED:
            if self.clarification_result is None or self.execution is not None:
                raise ValueError("cancelled submit must carry only a clarification result")
            nested = self.clarification_result
            if nested.status is not ClarificationStatus.CANCELLED:
                raise ValueError("cancelled submit must carry a cancelled result")
            if (
                nested.request_id_sha256 != self.request_id_sha256
                or nested.database_id_sha256 != self.database_id_sha256
            ):
                raise ValueError("cancelled result does not match session request")
        else:
            if self.execution is None or self.clarification_result is not None:
                raise ValueError("executed submit must carry only an execution result")
            constraint = self.execution.generation.constraint
            if (
                constraint.request_id_sha256 != self.request_id_sha256
                or constraint.database_id_sha256 != self.database_id_sha256
            ):
                raise ValueError("execution result does not match session request")
            if (
                request_id_fingerprint(self.execution.harness_result.request_id)
                != self.request_id_sha256
            ):
                raise ValueError("execution harness request does not match session")
        return self


@dataclass
class _ActiveRecord:
    request: HarnessRequest
    ontology: Ontology
    mapping: MappingProfile
    rules: DetectorRuleSet
    pending: PendingClarification
    deadline: float


def _revalidate(model_cls: type[Any], value: Any) -> Any:
    if isinstance(value, model_cls):
        payload = value.model_dump(mode="json", warnings=False)
    elif isinstance(value, dict):
        payload = value
    else:
        raise ValueError("unsupported boundary input")
    return model_cls.model_validate(payload)


def _token_hash(token: str) -> str:
    return hashlib.sha256((_TOKEN_DOMAIN + token).encode("utf-8")).hexdigest()


def _default_token() -> str:
    return secrets.token_urlsafe(32)


class ClarificationSessionCoordinator:
    """In-memory bearer session coordinator with TTL and one-shot submit."""

    def __init__(
        self,
        ttl_seconds: float = 600.0,
        *,
        clock: Any | None = None,
        token_factory: Any | None = None,
    ) -> None:
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)):
            raise ClarificationSessionError(ClarificationSessionErrorCode.INVALID_INPUT)
        ttl = float(ttl_seconds)
        if not math.isfinite(ttl) or ttl < _MIN_TTL_SECONDS or ttl > _MAX_TTL_SECONDS:
            raise ClarificationSessionError(ClarificationSessionErrorCode.INVALID_INPUT)
        resolved_clock = time.monotonic if clock is None else clock
        if not callable(resolved_clock):
            raise ClarificationSessionError(ClarificationSessionErrorCode.INVALID_INPUT)
        resolved_factory = _default_token if token_factory is None else token_factory
        if not callable(resolved_factory):
            raise ClarificationSessionError(ClarificationSessionErrorCode.INVALID_INPUT)
        self._ttl = ttl
        self._clock = resolved_clock
        self._token_factory = resolved_factory
        self._lock = threading.Lock()
        self._active: dict[str, _ActiveRecord] = {}
        self._consumed: set[str] = set()
        self._expired: set[str] = set()

    def start(
        self,
        request_data: HarnessRequest | dict[str, Any],
        ontology: Ontology,
        mapping: MappingProfile,
        rules_data: DetectorRuleSet | dict[str, Any],
    ) -> ClarificationSessionStartResult:
        try:
            validated_request = _revalidate(HarnessRequest, request_data)
            validated_ontology = _revalidate(Ontology, ontology)
            validated_mapping = _revalidate(MappingProfile, mapping)
            validated_rules = _revalidate(DetectorRuleSet, rules_data)
        except Exception:
            # Canonical detector/gate taxonomy owns invalid context/rulesets.
            prepare_detected_clarification(request_data, ontology, mapping, rules_data)
            raise ClarificationSessionError(
                ClarificationSessionErrorCode.INVALID_INPUT
            ) from None
        snapshot_request = copy.deepcopy(validated_request)
        snapshot_ontology = copy.deepcopy(validated_ontology)
        snapshot_mapping = copy.deepcopy(validated_mapping)
        snapshot_rules = copy.deepcopy(validated_rules)
        request_fp = request_id_fingerprint(snapshot_request.request_id)
        database_fp = database_id_fingerprint(snapshot_request.database_id)
        ontology_fp = ontology_fingerprint(snapshot_ontology)
        mapping_fp = mapping_fingerprint(snapshot_mapping)
        mapping_ver = snapshot_mapping.version

        pending = prepare_detected_clarification(
            copy.deepcopy(snapshot_request),
            copy.deepcopy(snapshot_ontology),
            copy.deepcopy(snapshot_mapping),
            copy.deepcopy(snapshot_rules),
        )
        signal = detect_ambiguity(
            copy.deepcopy(snapshot_request),
            copy.deepcopy(snapshot_ontology),
            copy.deepcopy(snapshot_mapping),
            copy.deepcopy(snapshot_rules),
        )
        if pending is None:
            if signal is not None:
                raise ClarificationSessionError(
                    ClarificationSessionErrorCode.INTERNAL_ERROR
                )
            try:
                return ClarificationSessionStartResult.model_validate(
                    {
                        "session_version": CLARIFICATION_SESSION_VERSION,
                        "status": ClarificationSessionStartStatus.NOT_REQUIRED,
                        "request_id_sha256": request_fp,
                        "database_id_sha256": database_fp,
                        "ontology_fingerprint": ontology_fp,
                        "mapping_sha256": mapping_fp,
                        "mapping_version": mapping_ver,
                        "session_token": None,
                        "pending": None,
                        "options": None,
                    }
                )
            except Exception:
                raise ClarificationSessionError(
                    ClarificationSessionErrorCode.INTERNAL_ERROR
                ) from None
        if signal is None:
            raise ClarificationSessionError(
                ClarificationSessionErrorCode.INTERNAL_ERROR
            )
        try:
            frozen_pending = PendingClarification.model_validate(
                pending.model_dump(mode="json", warnings=False)
            )
            frozen_options = [
                AmbiguityOption.model_validate(
                    option.model_dump(mode="json", warnings=False)
                )
                for option in signal.options
            ]
        except Exception:
            raise ClarificationSessionError(
                ClarificationSessionErrorCode.INTERNAL_ERROR
            ) from None
        try:
            token = self._token_factory()
        except Exception:
            raise ClarificationSessionError(
                ClarificationSessionErrorCode.INTERNAL_ERROR
            ) from None
        if not isinstance(token, str) or not re.fullmatch(_TOKEN_PATTERN, token):
            raise ClarificationSessionError(ClarificationSessionErrorCode.INTERNAL_ERROR)
        digest = _token_hash(token)
        try:
            now = float(self._clock())
        except Exception:
            raise ClarificationSessionError(
                ClarificationSessionErrorCode.INTERNAL_ERROR
            ) from None
        if not math.isfinite(now):
            raise ClarificationSessionError(ClarificationSessionErrorCode.INTERNAL_ERROR)
        deadline = now + self._ttl
        if not math.isfinite(deadline) or deadline <= now:
            raise ClarificationSessionError(ClarificationSessionErrorCode.INTERNAL_ERROR)
        with self._lock:
            for expired_digest, record in list(self._active.items()):
                if now >= record.deadline:
                    self._active.pop(expired_digest)
                    self._expired.add(expired_digest)
            if digest in self._active or digest in self._consumed or digest in self._expired:
                raise ClarificationSessionError(
                    ClarificationSessionErrorCode.INTERNAL_ERROR
                )
            self._active[digest] = _ActiveRecord(
                request=snapshot_request,
                ontology=snapshot_ontology,
                mapping=snapshot_mapping,
                rules=snapshot_rules,
                pending=frozen_pending,
                deadline=deadline,
            )
        try:
            return ClarificationSessionStartResult.model_validate(
                {
                    "session_version": CLARIFICATION_SESSION_VERSION,
                    "status": ClarificationSessionStartStatus.PENDING,
                    "request_id_sha256": request_fp,
                    "database_id_sha256": database_fp,
                    "ontology_fingerprint": ontology_fp,
                    "mapping_sha256": mapping_fp,
                    "mapping_version": mapping_ver,
                    "session_token": token,
                    "pending": frozen_pending.model_dump(mode="json", warnings=False),
                    "options": [
                        option.model_dump(mode="json", warnings=False)
                        for option in frozen_options
                    ],
                }
            )
        except Exception:
            with self._lock:
                self._active.pop(digest, None)
                self._consumed.add(digest)
            raise ClarificationSessionError(
                ClarificationSessionErrorCode.INTERNAL_ERROR
            ) from None

    def submit(
        self,
        session_token: str,
        choice: ClarificationChoice | dict[str, Any],
        provider: Any,
        executor: Any,
        *,
        compiler: Any | None = None,
        sql_policy: Any | None = None,
    ) -> ClarificationSessionSubmitResult:
        if not isinstance(session_token, str) or not re.fullmatch(
            _TOKEN_PATTERN, session_token
        ):
            raise ClarificationSessionError(ClarificationSessionErrorCode.INVALID_INPUT)
        try:
            validated_choice = _revalidate(ClarificationChoice, choice)
        except Exception:
            raise ClarificationSessionError(
                ClarificationSessionErrorCode.INVALID_INPUT
            ) from None
        digest = _token_hash(session_token)
        with self._lock:
            if digest in self._consumed:
                raise ClarificationSessionError(
                    ClarificationSessionErrorCode.ALREADY_CONSUMED
                )
            if digest in self._expired:
                raise ClarificationSessionError(ClarificationSessionErrorCode.EXPIRED)
            record = self._active.get(digest)
            if record is None:
                raise ClarificationSessionError(ClarificationSessionErrorCode.NOT_FOUND)
            claimed = self._active.pop(digest)
            self._consumed.add(digest)
            try:
                now = float(self._clock())
            except Exception:
                raise ClarificationSessionError(
                    ClarificationSessionErrorCode.INTERNAL_ERROR
                ) from None
            if not math.isfinite(now):
                raise ClarificationSessionError(
                    ClarificationSessionErrorCode.INTERNAL_ERROR
                ) from None
            for expired_digest, other_record in list(self._active.items()):
                if now >= other_record.deadline:
                    self._active.pop(expired_digest)
                    self._expired.add(expired_digest)
            if now >= claimed.deadline:
                self._consumed.remove(digest)
                self._expired.add(digest)
                raise ClarificationSessionError(ClarificationSessionErrorCode.EXPIRED)
            record = claimed
        request_fp = request_id_fingerprint(record.request.request_id)
        database_fp = database_id_fingerprint(record.request.database_id)
        if validated_choice.cancelled:
            # Cancel path never touches provider/compiler/policy/executor.
            cancelled = _resolve_detected(
                copy.deepcopy(record.request),
                copy.deepcopy(record.ontology),
                copy.deepcopy(record.mapping),
                copy.deepcopy(record.rules),
                copy.deepcopy(record.pending),
                copy.deepcopy(validated_choice),
            )
            try:
                frozen = ClarificationResult.model_validate(
                    cancelled.model_dump(mode="json", warnings=False)
                )
                return ClarificationSessionSubmitResult.model_validate(
                    {
                        "submission_version": CLARIFICATION_SESSION_VERSION,
                        "status": ClarificationSessionSubmitStatus.CANCELLED,
                        "request_id_sha256": request_fp,
                        "database_id_sha256": database_fp,
                        "clarification_result": frozen.model_dump(
                            mode="json", warnings=False
                        ),
                        "execution": None,
                    }
                )
            except Exception:
                raise ClarificationSessionError(
                    ClarificationSessionErrorCode.INTERNAL_ERROR
                ) from None
        execution = run_clarified_execution(
            copy.deepcopy(record.request),
            copy.deepcopy(record.ontology),
            copy.deepcopy(record.mapping),
            copy.deepcopy(record.rules),
            copy.deepcopy(record.pending),
            copy.deepcopy(validated_choice),
            provider,
            executor,
            **({"compiler": compiler} if compiler is not None else {}),
            **({"sql_policy": sql_policy} if sql_policy is not None else {}),
        )
        try:
            frozen_execution = ClarificationExecutionResult.model_validate(
                execution.model_dump(mode="json", warnings=False)
            )
            return ClarificationSessionSubmitResult.model_validate(
                {
                    "submission_version": CLARIFICATION_SESSION_VERSION,
                    "status": ClarificationSessionSubmitStatus.EXECUTED,
                    "request_id_sha256": request_fp,
                    "database_id_sha256": database_fp,
                    "clarification_result": None,
                    "execution": frozen_execution.model_dump(mode="json", warnings=False),
                }
            )
        except Exception:
            raise ClarificationSessionError(
                ClarificationSessionErrorCode.INTERNAL_ERROR
            ) from None
