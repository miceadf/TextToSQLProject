"""Ollama constraint-aware bridge (synthetic only).

Connects ``OllamaQueryModel`` to the adaptive clarification
``ConstraintAwareProvider`` boundary. The caller supplies only the original
request, runtime ontology, Published mapping, and frozen constraint. Every
public input is revalidated from plain JSON data with strict types and no
extra fields, so forged ``model_copy(update=...)`` / ``model_construct``
instances fail closed. The selected semantic ref is restricted to one field
or one entity with its mapped fields, then the wrapped model is called
exactly once with defensive deep copies. Model output passes through
untouched so ``run_constrained_generation`` still owns QueryIR structure,
semantic, and constraint-compliance validation.

No file, DB, network, compiler, policy, executor, router, repair, queue,
or snapshot access. No SQL handling, no dialect knowledge, no logging.
Failures use one fixed code only and never reflect input, transport,
model, or parser text.
"""

from __future__ import annotations

import copy
from enum import Enum
from typing import Any

from .clarification import (
    database_id_fingerprint,
    ontology_fingerprint,
    request_id_fingerprint,
)
from .clarification_query_constraint import CLARIFICATION_QUERY_CONSTRAINT_VERSION
from .contracts import HarnessRequest, MappingProfile, MappingStatus, Ontology
from .recorded import mapping_fingerprint


OLLAMA_CONSTRAINT_PROVIDER_VERSION = "ollama-constraint-provider-v1"

_FIXED_REPR = "OllamaConstraintProvider(version='ollama-constraint-provider-v1')"


class OllamaConstraintProviderErrorCode(str, Enum):
    PROVIDER_ERROR = "OLLAMA_CONSTRAINT_PROVIDER_ERROR"


class OllamaConstraintProviderError(Exception):
    """Fail-closed bridge error with a fixed code only."""

    def __init__(
        self,
        code: OllamaConstraintProviderErrorCode = OllamaConstraintProviderErrorCode.PROVIDER_ERROR,
    ) -> None:
        self.code = code.value
        super().__init__(self.code)


def _fail() -> OllamaConstraintProviderError:
    return OllamaConstraintProviderError(
        OllamaConstraintProviderErrorCode.PROVIDER_ERROR
    )


def _strict_revalidate(model_cls: type[Any], value: Any) -> Any:
    payload: Any = None
    failed = False
    try:
        if isinstance(value, model_cls):
            payload = value.model_dump(mode="json", warnings=False)
        elif isinstance(value, dict):
            payload = value
        else:
            failed = True
    except Exception:
        failed = True
    if failed:
        raise _fail()
    encoded = ""
    decoded: Any = None
    json_ok = False
    try:
        import json as _json

        encoded = _json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        decoded = _json.loads(encoded)
        json_ok = True
    except Exception:
        json_ok = False
    if not json_ok:
        raise _fail()
    if not isinstance(decoded, dict):
        raise _fail()
    allowed: set[str] = set()
    fields_ok = False
    try:
        allowed = set(model_cls.model_fields.keys())
        fields_ok = True
    except Exception:
        fields_ok = False
    if not fields_ok:
        raise _fail()
    for key in decoded.keys():
        if key not in allowed:
            raise _fail()
    if model_cls is Ontology:
        nested = decoded.get("fields")
        if not isinstance(nested, dict):
            raise _fail()
        expected = {"name", "data_type", "allowed_operators"}
        # Phase 8-19: an optional label (None, or text for an extension field).
        if any(
            not isinstance(item, dict)
            or set(item) not in (expected, expected | {"label"})
            or not (item.get("label") is None or isinstance(item.get("label"), str))
            for item in nested.values()
        ):
            raise _fail()
    if model_cls is MappingProfile:
        nested = decoded.get("fields")
        if not isinstance(nested, dict):
            raise _fail()
        expected = {"table", "column"}
        if any(
            not isinstance(item, dict) or set(item) != expected
            for item in nested.values()
        ):
            raise _fail()
    result: Any = None
    ok = False
    try:
        result = model_cls.model_validate_json(encoded, strict=True)
        ok = True
    except Exception:
        ok = False
    if not ok:
        raise _fail()
    return result


def _build_strict(model_cls: type[Any], payload: dict[str, Any]) -> Any:
    encoded = ""
    encode_ok = False
    try:
        import json as _json

        encoded = _json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        encode_ok = True
    except Exception:
        encode_ok = False
    if not encode_ok:
        raise _fail()
    result: Any = None
    ok = False
    try:
        result = model_cls.model_validate_json(encoded, strict=True)
        ok = True
    except Exception:
        ok = False
    if not ok:
        raise _fail()
    return result


class OllamaConstraintProvider:
    """Server-owned bridge wrapping exactly one ``OllamaQueryModel``."""

    def __init__(self, model: Any) -> None:
        target: Any = None
        failed = False
        try:
            from .ollama import OllamaQueryModel as _OllamaQueryModel

            target = _OllamaQueryModel
        except Exception:
            failed = True
        if failed or target is None:
            raise _fail()
        if not isinstance(model, target):
            raise _fail()
        if not callable(getattr(model, "generate_ir", None)):
            raise _fail()
        self._model = model

    def __repr__(self) -> str:
        return _FIXED_REPR

    def __str__(self) -> str:
        return _FIXED_REPR

    def generate_ir(
        self,
        request: Any,
        ontology: Any,
        mapping: Any,
        constraint: Any,
    ) -> Any:
        constraint_cls: Any = None
        try:
            from .clarification_query_constraint import (
                ClarificationQueryConstraint as _Constraint,
            )

            constraint_cls = _Constraint
        except Exception:
            constraint_cls = None
        if constraint_cls is None:
            raise _fail()
        validated_request = _strict_revalidate(HarnessRequest, request)
        validated_ontology = _strict_revalidate(Ontology, ontology)
        validated_mapping = _strict_revalidate(MappingProfile, mapping)
        validated_constraint = _strict_revalidate(constraint_cls, constraint)

        if validated_mapping.status is not MappingStatus.PUBLISHED:
            raise _fail()
        if validated_mapping.database_id != validated_request.database_id:
            raise _fail()
        if (
            validated_constraint.constraint_version
            != CLARIFICATION_QUERY_CONSTRAINT_VERSION
        ):
            raise _fail()
        if (
            validated_constraint.request_id_sha256
            != request_id_fingerprint(validated_request.request_id)
        ):
            raise _fail()
        if (
            validated_constraint.database_id_sha256
            != database_id_fingerprint(validated_request.database_id)
        ):
            raise _fail()
        if (
            validated_constraint.ontology_fingerprint
            != ontology_fingerprint(validated_ontology)
        ):
            raise _fail()
        if (
            validated_constraint.mapping_sha256
            != mapping_fingerprint(validated_mapping)
        ):
            raise _fail()

        ref = validated_constraint.selected_semantic_ref
        if not isinstance(ref, str) or not ref:
            raise _fail()
        restricted_ontology: Any = None
        restricted_mapping: Any = None
        if "." in ref:
            if ref not in validated_ontology.fields:
                raise _fail()
            if ref not in validated_mapping.fields:
                raise _fail()
            entity = ref.split(".", 1)[0]
            if entity not in validated_ontology.entities:
                raise _fail()
            restricted_ontology = _build_strict(
                Ontology,
                {
                    "entities": [entity],
                    "fields": {
                        ref: validated_ontology.fields[ref].model_dump(
                            mode="json", warnings=False
                        )
                    },
                },
            )
            restricted_mapping = _build_strict(
                MappingProfile,
                {
                    "database_id": validated_mapping.database_id,
                    "version": validated_mapping.version,
                    "status": MappingStatus.PUBLISHED.value,
                    "fields": {
                        ref: validated_mapping.fields[ref].model_dump(
                            mode="json", warnings=False
                        )
                    },
                },
            )
        else:
            if ref not in validated_ontology.entities:
                raise _fail()
            prefixed = sorted(
                name
                for name in validated_mapping.fields
                if name.startswith(ref + ".")
            )
            if not prefixed:
                raise _fail()
            for name in prefixed:
                if name not in validated_ontology.fields:
                    raise _fail()
            restricted_ontology = _build_strict(
                Ontology,
                {
                    "entities": [ref],
                    "fields": {
                        name: validated_ontology.fields[name].model_dump(
                            mode="json", warnings=False
                        )
                        for name in prefixed
                    },
                },
            )
            restricted_mapping = _build_strict(
                MappingProfile,
                {
                    "database_id": validated_mapping.database_id,
                    "version": validated_mapping.version,
                    "status": MappingStatus.PUBLISHED.value,
                    "fields": {
                        name: validated_mapping.fields[name].model_dump(
                            mode="json", warnings=False
                        )
                        for name in prefixed
                    },
                },
            )

        request_copy: Any = None
        ontology_copy: Any = None
        mapping_copy: Any = None
        copy_ok = False
        try:
            request_copy = copy.deepcopy(validated_request)
            ontology_copy = copy.deepcopy(restricted_ontology)
            mapping_copy = copy.deepcopy(restricted_mapping)
            copy_ok = True
        except Exception:
            copy_ok = False
        if not copy_ok:
            raise _fail()
        output: Any = None
        called = False
        try:
            output = self._model.generate_ir(
                request_copy, ontology_copy, mapping_copy
            )
            called = True
        except OllamaConstraintProviderError:
            raise
        except Exception:
            called = False
        if not called:
            raise _fail()
        return output
