"""Minimal Ollama QueryModel adapter with an injectable HTTP boundary."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import socket
from collections.abc import Callable
from concurrent.futures import CancelledError
from dataclasses import dataclass, field
from urllib import error, parse, request

from .contracts import DataType, HarnessRequest, MappingProfile, MappingStatus, Ontology
from .ollama_request_format import (
    ALPACA_NO_SYSTEM_SHA256,
    REQUEST_FORMAT_MODEL_TEMPLATE,
    REQUEST_FORMAT_NO_SYSTEM,
    REQUEST_FORMATS,
    apply_request_format,
)
from .recorded import RecordedFailureCode
from .recording_bundle_contracts import (
    ProviderKind,
    RecordingProvenance,
    SAFE_LABEL_PATTERN,
    SHA256_PATTERN,
)
from .recording_orchestrator import RecordingProviderError


# Phase 9-08 (ADR-098): a general model is held to the QueryIR shape by the
# request's JSON schema (Ollama structured outputs). The fine-tuned model
# learned the shape and keeps its exact pre-9-08 request ("none").
OUTPUT_FORMAT_NONE = "none"
OUTPUT_FORMAT_QUERYIR_SCHEMA = "queryir-schema-v1"
OUTPUT_FORMATS = (OUTPUT_FORMAT_NONE, OUTPUT_FORMAT_QUERYIR_SCHEMA)
# Bounds a generation that repeats conditions until the timeout (seen without
# a bound, Phase 9-08). Far above what a question states (three or four), so
# no stated condition is cut in practice; a longer question is a known limit.
MAX_SCHEMA_FILTERS = 12
# JSON value type per data type, as ``validator._validate_value_type`` checks it.
_JSON_TYPES = {
    DataType.STRING: "string",
    DataType.DATE: "string",
    DataType.TIME: "string",
    DataType.INTEGER: "integer",
    DataType.NUMBER: "number",
    DataType.BOOLEAN: "boolean",
}

PROMPT_VERSION = "query-ir-v1"
GENERATION_OPTIONS = {"temperature": 0}
PROMPT_TEMPLATE = (
    "Prompt version: {prompt_version}\n"
    "Return only one JSON value matching QueryIR: "
    '{"entity":"Entity","select":["Entity.field"],'
    '"filters":[{"field":"Entity.field","operator":"equals","value":"text"}],'
    '"limit":50}. Use only available semantic fields and allowed operators. '
    "Do not return SQL, Markdown, or an explanation.\n"
    "Context: {context}\n"
    "Question: {question}"
)
PROMPT_TEMPLATE_V2 = (
    "Prompt version: {prompt_version}\n"
    "Return exactly one JSON QueryIR object with entity, select, filters, and limit. "
    "For every select entry and every filter.field, copy a complete string exactly "
    "from Context.available_fields. Do not invent a field, change its entity prefix, "
    "use a physical column, or change spelling or case. Use only Context.entities "
    "and the operators listed for each field in Context.fields. "
    "Return JSON only; no SQL, Markdown, or explanation.\n"
    "Context: {context}\n"
    "Question: {question}"
)
# Phase 9-08 (ADR-098): v2 plus what the fine-tuned model learned from its
# data and a general model must be told: state no condition the question
# does not, and follow the value hints.
PROMPT_TEMPLATE_V3 = PROMPT_TEMPLATE_V2.replace(
    "Return JSON only;",
    "Add a filter only for a condition the question states; do not add any other condition. "
    "When Value hints are given, use each hint's field and value for its text. "
    "Return JSON only;",
)
PROMPT_VERSIONS = ("query-ir-v1", "query-ir-v2", "query-ir-v3")
OllamaTransport = Callable[[str, dict[str, object], float], object]


def _versioned_template(prompt_version: str | None = None) -> str:
    version = PROMPT_VERSION if prompt_version is None else prompt_version
    if version not in PROMPT_VERSIONS:
        raise ValueError("INVALID_PROMPT_VERSION")
    template = {
        "query-ir-v1": PROMPT_TEMPLATE,
        "query-ir-v2": PROMPT_TEMPLATE_V2,
        "query-ir-v3": PROMPT_TEMPLATE_V3,
    }[version]
    return template.replace("{prompt_version}", version)


@dataclass(frozen=True)
class OllamaGeneration:
    """In-memory format observation; never persist or print value by default."""

    value: object = field(repr=False)
    json_parsed: bool


def _default_transport(url: str, payload: dict[str, object], timeout: float) -> object:
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    http_request = request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with request.urlopen(http_request, timeout=timeout) as response:
        return json.loads(
            response.read().decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
            parse_float=_finite_float,
        )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _reject_non_finite(_: str) -> None:
    raise ValueError("non-finite number")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite number")
    return parsed


VALUE_HINTS_LABEL = "Value hints (text in the question and the field/value it refers to): "
# Phase 8-25: a time hint also carries the operator; this label is used only then.
CONDITION_HINTS_LABEL = (
    "Value hints (text in the question and the field/value it refers to; "
    "a hint with an operator is the exact condition that text means): "
)
MAX_VALUE_HINTS = 8
_HINT_KEYS = frozenset({"text", "field", "value"})
_CONDITION_HINT_KEYS = _HINT_KEYS | {"operator"}
_HINT_OPERATORS = frozenset(
    {"equals", "greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal"}
)


def _valid_hint(hint: object, available: list[str], operators: dict[str, set[str]] | None) -> bool:
    if not isinstance(hint, dict) or set(hint) not in (_HINT_KEYS, _CONDITION_HINT_KEYS):
        return False
    if not all(isinstance(hint[key], str) and 0 < len(hint[key]) <= 200 for key in hint):
        return False
    if hint["field"] not in available:
        return False
    if "operator" not in hint:
        return True
    allowed = _HINT_OPERATORS if operators is None else _HINT_OPERATORS & operators.get(hint["field"], set())
    return hint["operator"] in allowed


def _hint_line(
    value_hints: object, available: list[str], operators: dict[str, set[str]] | None = None
) -> str:
    """Phase 8-13: optional grounded values, one line between Context and Question."""
    if value_hints is None:
        return ""
    if not isinstance(value_hints, list) or len(value_hints) > MAX_VALUE_HINTS:
        raise ValueError("INVALID_VALUE_HINTS")
    if not all(_valid_hint(hint, available, operators) for hint in value_hints):
        raise ValueError("INVALID_VALUE_HINTS")
    if not value_hints:
        return ""
    label = CONDITION_HINTS_LABEL if any("operator" in hint for hint in value_hints) else VALUE_HINTS_LABEL
    return "\n" + label + json.dumps(value_hints, ensure_ascii=False)


def _prompt(
    question: str,
    ontology: Ontology,
    mapping: MappingProfile,
    prompt_version: str,
    value_hints: object = None,
) -> str:
    # Never serialize MappingProfile: it contains physical table and column names.
    semantic_fields = sorted(set(ontology.fields).intersection(mapping.fields))
    context = {
        "entities": sorted(ontology.entities),
        "fields": [
            {
                "name": name,
                "type": ontology.fields[name].data_type.value,
                "operators": sorted(item.value for item in ontology.fields[name].allowed_operators),
                **({"label": ontology.fields[name].label} if ontology.fields[name].label else {}),
            }
            for name in sorted(ontology.fields)
        ],
        "available_fields": semantic_fields,
    }
    encoded_context = json.dumps(
        context, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    hint_line = _hint_line(
        value_hints,
        semantic_fields,
        {name: {item.value for item in ontology.fields[name].allowed_operators} for name in semantic_fields},
    )
    prefix, _, remainder = _versioned_template(prompt_version).partition("{context}")
    between, _, suffix = remainder.partition("{question}")
    return prefix + encoded_context + hint_line + between + question + suffix


def queryir_schema(ontology: Ontology, mapping: MappingProfile) -> dict[str, object] | None:
    """The QueryIR JSON schema for one request: shape only, validation still runs.

    Fields are every field the prompt lists, not only the mapped ones, so a
    question about a field this database lacks still names it and is
    refused as unsupported. ``None`` when the database maps no field.
    """
    if not set(ontology.fields).intersection(mapping.fields) or not ontology.entities:
        return None
    fields = sorted(ontology.fields)
    # One filter shape per value type, the validator's own type rule, so a
    # number is never written as text.
    groups: dict[str, list[str]] = {}
    for name in fields:
        groups.setdefault(_JSON_TYPES[ontology.fields[name].data_type], []).append(name)
    filter_shapes = [
        {
            "type": "object",
            "properties": {
                "field": {"type": "string", "enum": names},
                "operator": {
                    "type": "string",
                    "enum": sorted(
                        {item.value for name in names for item in ontology.fields[name].allowed_operators}
                    ),
                },
                "value": {"type": json_type},
            },
            "required": ["field", "operator", "value"],
        }
        for json_type, names in sorted(groups.items())
    ]
    return {
        "type": "object",
        "properties": {
            "entity": {"type": "string", "enum": sorted(ontology.entities)},
            "select": {
                "type": "array",
                "items": {"type": "string", "enum": fields},
                "minItems": 1,
                "maxItems": len(fields),
                "uniqueItems": True,
            },
            "filters": {
                "type": "array",
                "items": filter_shapes[0] if len(filter_shapes) == 1 else {"anyOf": filter_shapes},
                "maxItems": MAX_SCHEMA_FILTERS,
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "required": ["entity", "select", "filters", "limit"],
    }


class OllamaQueryModel:
    """One /api/generate call per request; no retries or model substitution.

    The default urllib transport has no active cancellation API. An injected
    transport can signal a real cancellation with CancelledError.
    """

    def __init__(
        self,
        base_url: str,
        model_tag: str,
        timeout: float,
        *,
        transport: OllamaTransport = _default_transport,
        prompt_version: str | None = None,
        request_format: str | None = None,
        output_format: str | None = None,
    ) -> None:
        try:
            parsed = parse.urlsplit(base_url)
        except (TypeError, ValueError):
            raise ValueError("INVALID_OLLAMA_BASE_URL") from None
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("INVALID_OLLAMA_BASE_URL")
        if not isinstance(model_tag, str) or not model_tag.strip():
            raise ValueError("INVALID_OLLAMA_MODEL_TAG")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout < float("inf"):
            raise ValueError("INVALID_OLLAMA_TIMEOUT")
        if not callable(transport):
            raise ValueError("INVALID_OLLAMA_TRANSPORT")
        fmt = REQUEST_FORMAT_MODEL_TEMPLATE if request_format is None else request_format
        if type(fmt) is not str or fmt not in REQUEST_FORMATS:
            raise ValueError("INVALID_OLLAMA_REQUEST_FORMAT")
        output = OUTPUT_FORMAT_NONE if output_format is None else output_format
        if type(output) is not str or output not in OUTPUT_FORMATS:
            raise ValueError("INVALID_OLLAMA_OUTPUT_FORMAT")
        version = PROMPT_VERSION if prompt_version is None else prompt_version
        _versioned_template(version)
        self._url = base_url.rstrip("/") + "/api/generate"
        self._model_tag = model_tag
        self._timeout = float(timeout)
        # The single application point of the request format (ADR-060).
        self._transport = apply_request_format(transport, fmt)
        self.prompt_version = version
        self.request_format = fmt
        self.output_format = output

    def declared_provenance(
        self, provider_version: str, model_digest: str | None = None
    ) -> RecordingProvenance:
        """Declare reproducible metadata; this does not attest model identity."""
        if (
            not isinstance(provider_version, str)
            or re.fullmatch(SAFE_LABEL_PATTERN, provider_version) is None
            or re.fullmatch(SAFE_LABEL_PATTERN, self._model_tag) is None
            or (model_digest is not None and (
                not isinstance(model_digest, str)
                or re.fullmatch(SHA256_PATTERN, model_digest) is None
            ))
        ):
            raise ValueError("INVALID_OLLAMA_PROVENANCE")
        generation_config: dict[str, object] = {
            "stream": False,
            "options": dict(GENERATION_OPTIONS),
            "timeout_seconds": self._timeout,
        }
        # model-template keeps the historical hash; other formats are declared.
        if self.request_format == REQUEST_FORMAT_NO_SYSTEM:
            generation_config["request_format"] = self.request_format
            generation_config["raw"] = True
            generation_config["request_template_sha256"] = ALPACA_NO_SYSTEM_SHA256
        # Declared only when used, so earlier hashes stay the same.
        if self.output_format != OUTPUT_FORMAT_NONE:
            generation_config["output_format"] = self.output_format
        config_bytes = json.dumps(
            generation_config,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return RecordingProvenance(
            provider_kind=ProviderKind.OLLAMA,
            provider_id="ollama-api-generate",
            provider_version=provider_version,
            model_id=self._model_tag,
            model_artifact_sha256=model_digest,
            prompt_template_sha256=hashlib.sha256(
                _versioned_template(self.prompt_version).encode("utf-8")
            ).hexdigest(),
            generation_config_sha256=hashlib.sha256(config_bytes).hexdigest(),
            collector_version="ollama-capture-v1",
        )

    def generate_ir(
        self,
        request: HarnessRequest,
        ontology: Ontology,
        mapping: MappingProfile,
        *,
        value_hints: object = None,
    ) -> object:
        return self.generate_observed(request, ontology, mapping, value_hints=value_hints).value

    def generate_observed(
        self,
        request: HarnessRequest,
        ontology: Ontology,
        mapping: MappingProfile,
        *,
        value_hints: object = None,
    ) -> OllamaGeneration:
        """Invoke once and expose parse status for private, in-memory diagnostics."""
        if mapping.status is not MappingStatus.PUBLISHED or mapping.database_id != request.database_id:
            raise RecordingProviderError(RecordedFailureCode.PROVIDER_ERROR)
        payload: dict[str, object] = {
            "model": self._model_tag,
            "prompt": _prompt(
                request.question, ontology, mapping, self.prompt_version, value_hints
            ),
            "stream": False,
            "options": dict(GENERATION_OPTIONS),
        }
        if self.output_format == OUTPUT_FORMAT_QUERYIR_SCHEMA:
            schema = queryir_schema(ontology, mapping)
            if schema is not None:
                payload["format"] = schema
        failure_code: RecordedFailureCode | None = None
        envelope: object = None
        try:
            envelope = self._transport(self._url, payload, self._timeout)
        except (CancelledError, asyncio.CancelledError):
            failure_code = RecordedFailureCode.CANCELLED
        except (TimeoutError, socket.timeout):
            failure_code = RecordedFailureCode.TIMEOUT
        except error.URLError as exc:
            failure_code = (
                RecordedFailureCode.TIMEOUT
                if isinstance(exc.reason, (TimeoutError, socket.timeout))
                else RecordedFailureCode.PROVIDER_ERROR
            )
        except Exception:
            failure_code = RecordedFailureCode.PROVIDER_ERROR
        if failure_code is not None:
            raise RecordingProviderError(failure_code)

        if (
            not isinstance(envelope, dict)
            or "error" in envelope
            or not isinstance(envelope.get("response"), str)
        ):
            raise RecordingProviderError(RecordedFailureCode.PROVIDER_ERROR)
        output = envelope["response"]
        if not output.strip():
            raise RecordingProviderError(RecordedFailureCode.EMPTY_RESPONSE)
        try:
            parsed_output = json.loads(
                output,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite,
                parse_float=_finite_float,
            )
        except json.JSONDecodeError:
            return OllamaGeneration(value=output, json_parsed=False)
        except ValueError:
            pass
        else:
            return OllamaGeneration(value=parsed_output, json_parsed=True)
        raise RecordingProviderError(RecordedFailureCode.PROVIDER_ERROR)
