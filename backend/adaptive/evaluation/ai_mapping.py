"""LLM mapping proposals for the schema-variant benchmark (Phase 8-09).

A local model proposes, a deterministic validator filters, and the result
is an ordinary ``VariantMappingAnswer`` scored exactly like the rules:

1. one call per variant chooses a column (or none) for every canonical
   field; a JSON schema restricts the answer to real columns;
2. one call per mapped field chooses a closed-list value transform and,
   for fields with a canonical value list, a value dictionary;
3. the validator drops anything the runtime would reject (the same type
   rules) and no-op dictionaries;
4. the row anchor is the table holding the offering section (a fixed
   rule, not the model's choice).

Only synthetic catalogs and synthetic stored values are sent, to a
loopback Ollama endpoint. Nothing is published.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..mapping.transforms import FieldTransform, column_type_supports, transform_supports_data_type
from .variant_benchmark import (
    REPORT_DIR,
    FieldAnswer,
    VariantMappingAnswer,
    score_fields,
    with_section_anchor,
)
from .variant_specs import CANONICAL_FIELDS, VARIANTS, Variant, sample_values, spec_catalog

# v1.1 (after review): the slash example no longer repeats a benchmark
# stored value, model output is checked against its schema, failed calls
# mark the proposal invalid, and dictionaries pass the runtime model.
PROMPT_VERSION = "ai-mapping-v1.1"
DEFAULT_MODEL = "qwen2.5-coder:7b-instruct"
DEFAULT_BASE_URL = "http://127.0.0.1:11434"
NONE = "none"

# The system's canonical spellings for enumerated fields (``canonical-values-v1``).
# Kept with the proposer so the ontology (and fingerprints pinned to it) is unchanged.
CANONICAL_VALUES: dict[str, tuple[str, ...]] = {
    "Course.category": ("전공", "교양", "일반선택"),
    "Schedule.day": ("월", "화", "수", "목", "금", "토", "일"),
    "CourseOffering.semester": ("1", "2"),
}
# Values that identify rows cannot carry a dictionary (runtime rule).
IDENTITY_FIELDS = frozenset({"Course.code", "CourseOffering.section", "Department.code"})
_TEXT_TYPE = re.compile(r"^(?:character varying|varchar|character|char|bpchar|text)(?:\(\d{1,5}\))?$")

TRANSFORM_HELP = {
    FieldTransform.SLASH_FIRST_NUMERIC: (
        "the stored text holds several numbers separated by '/', and the field value is the "
        "first one (for example '4/2/2' means 4)"
    ),
    FieldTransform.TIME_TO_HHMM: "the column has a time type and the value is shown as HH:MM",
    FieldTransform.KOREAN_WEEKDAY: (
        "the stored value is already a one-character Korean weekday (월, 화, 수, 목, 금, 토, 일)"
    ),
}

ModelCall = Callable[[str, dict[str, Any]], Any]


class ProposalError(Exception):
    """Fixed-code failure of one model call."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class OllamaJsonModel:
    """One loopback ``/api/generate`` call with a JSON schema; temperature 0."""

    def __init__(
        self,
        model_tag: str = DEFAULT_MODEL,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: int = 120,
        transport: Callable[[str, dict[str, Any], float], Any] | None = None,
    ) -> None:
        from ..api.ollama_startup import build_loopback_transport, parse_ollama_startup_config

        config = parse_ollama_startup_config(model_tag, base_url, str(timeout_seconds))
        self._model = config.model_tag
        self._timeout = config.timeout_seconds
        self._url = config.base_url + "/api/generate"
        self._transport = transport or build_loopback_transport(config)

    def __call__(self, prompt: str, schema: dict[str, Any]) -> Any:
        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "format": schema,
            "options": {"temperature": 0},
        }
        try:
            response = self._transport(self._url, payload, self._timeout)
        except Exception:
            raise ProposalError("MODEL_UNAVAILABLE") from None
        text = response.get("response") if isinstance(response, dict) else None
        if not isinstance(text, str):
            raise ProposalError("MODEL_INVALID_RESPONSE")
        from ..harness.ollama import _finite_float, _reject_duplicate_keys, _reject_non_finite

        try:
            return json.loads(
                text,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite,
                parse_float=_finite_float,
            )
        except Exception:
            raise ProposalError("MODEL_INVALID_JSON") from None


# --- inputs ---------------------------------------------------------------------------


def _ontology_fields() -> dict[str, Any]:
    from ..ontology.loader import load_default_ontology

    return {field.name: field for entity in load_default_ontology().entities for field in entity.fields}


def _shown(value: Any, pg_type: str) -> str:
    """Stored values as PostgreSQL renders them in text."""
    text = str(value)
    if pg_type.startswith("time") and re.fullmatch(r"\d{2}:\d{2}", text):
        return text + ":00"
    return text


def column_inputs(spec: Variant) -> list[dict[str, Any]]:
    samples = sample_values(spec)
    return [
        {
            "ref": f"{table.name}.{col.name}",
            "table": table.name,
            "column": col.name,
            "type": col.pg_type,
            "values": [_shown(value, col.pg_type) for value in samples[(table.name, col.name)]],
        }
        for table in spec.tables
        for col in table.columns
    ]


def _field_line(name: str, field: Any) -> str:
    return f"- {name} ({field.label}, {field.data_type.value}): {field.description}"


def column_prompt(spec: Variant, ontology: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    columns = column_inputs(spec)
    lines = [
        f"Prompt version: {PROMPT_VERSION}/columns",
        "You map the columns of a course-registration database onto canonical fields.",
        "Canonical fields:",
        *(_field_line(name, ontology[name]) for name in CANONICAL_FIELDS),
        "Columns (table.column : declared type : example stored values):",
        *(f"- {c['ref']} : {c['type']} : {', '.join(c['values'][:5])}" for c in columns),
        "For every canonical field, choose the one column that stores that field's value, "
        "or \"none\" if no column stores it. A column serves at most one field. Judge by the "
        "example values as well as the names. Return only the JSON object.",
    ]
    choices = [c["ref"] for c in columns] + [NONE]
    schema = {
        "type": "object",
        "properties": {name: {"type": "string", "enum": choices} for name in CANONICAL_FIELDS},
        "required": list(CANONICAL_FIELDS),
        "additionalProperties": False,
    }
    return "\n".join(lines), schema


def value_prompt(
    name: str, field: Any, column: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    vocabulary = CANONICAL_VALUES.get(name)
    offer_dictionary = (
        vocabulary is not None
        and name not in IDENTITY_FIELDS
        and _TEXT_TYPE.fullmatch(column["type"]) is not None
        and bool(column["values"])
    )
    lines = [
        f"Prompt version: {PROMPT_VERSION}/values",
        "A database column has been mapped to a canonical field.",
        f"Field: {name} ({field.label}, {field.data_type.value}): {field.description}",
        f"Column: {column['ref']}, declared type {column['type']}",
        f"Stored values (examples): {', '.join(column['values'][:10])}",
        "How must the stored value be read to give the field value?",
        "- none: the stored value already is the field value",
        *(f"- {transform.value}: {help_text}" for transform, help_text in TRANSFORM_HELP.items()),
    ]
    properties: dict[str, Any] = {
        "transform": {"type": "string", "enum": [NONE, *(t.value for t in TRANSFORM_HELP)]},
    }
    if offer_dictionary:
        lines += [
            f"The field's canonical values are: {', '.join(vocabulary)}.",
            "If the stored values are codes or other spellings of canonical values, pair every "
            "stored value with its canonical value in \"dictionary\". If the stored values already "
            "are canonical values, return an empty dictionary.",
        ]
        properties["dictionary"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "stored": {"type": "string", "enum": list(column["values"])},
                    "canonical": {"type": "string", "enum": list(vocabulary)},
                },
                "required": ["stored", "canonical"],
                "additionalProperties": False,
            },
        }
    lines.append("Return only the JSON object.")
    schema = {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
    return "\n".join(lines), schema


# --- validation and assembly ----------------------------------------------------------------


def _runtime_accepts(entries: dict[str, str]) -> bool:
    """The runtime's own dictionary model (spelling collisions across entries)."""
    from ..mapping.values import ValueDictionary, ValueEntry

    try:
        ValueDictionary(
            entries=tuple(ValueEntry(stored=s, label=c) for s, c in sorted(entries.items())),
            unknown="pass",
            reviewer="ai-mapping",
            reason="proposal check",
        )
    except Exception:
        return False
    return True


def schema_conforms(output: Any, schema: dict[str, Any]) -> bool:
    """The subset of JSON schema this module emits: objects, strings, enums, arrays."""
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(output, dict):
            return False
        properties = schema.get("properties", {})
        if set(schema.get("required", [])) - set(output):
            return False
        if schema.get("additionalProperties") is False and set(output) - set(properties):
            return False
        return all(schema_conforms(output[key], properties[key]) for key in output if key in properties)
    if kind == "array":
        return isinstance(output, list) and all(schema_conforms(item, schema["items"]) for item in output)
    if kind == "string":
        return isinstance(output, str) and ("enum" not in schema or output in schema["enum"])
    return False


def validate_value_output(
    name: str, field: Any, column: dict[str, Any], output: Any
) -> tuple[FieldTransform | None, dict[str, str] | None, list[str]]:
    """Keep only what the runtime accepts; return (transform, dictionary, dropped)."""
    dropped: list[str] = []
    if not isinstance(output, dict):
        return None, None, ["INVALID_OUTPUT"]
    transform: FieldTransform | None = None
    raw_transform = output.get("transform")
    if raw_transform not in (None, NONE):
        try:
            candidate = FieldTransform(raw_transform)
        except ValueError:
            dropped.append("UNKNOWN_TRANSFORM")
        else:
            if transform_supports_data_type(candidate, field.data_type) and column_type_supports(
                candidate, column["type"]
            ):
                transform = candidate
            else:
                dropped.append("TRANSFORM_TYPE_MISMATCH")
    dictionary: dict[str, str] | None = None
    pairs = output.get("dictionary")
    if isinstance(pairs, list) and pairs:
        vocabulary = set(CANONICAL_VALUES.get(name, ()))
        stored_values = set(column["values"])
        entries: dict[str, str] = {}
        valid = name not in IDENTITY_FIELDS and _TEXT_TYPE.fullmatch(column["type"]) is not None
        for pair in pairs:
            stored = pair.get("stored") if isinstance(pair, dict) else None
            canonical = pair.get("canonical") if isinstance(pair, dict) else None
            if stored not in stored_values or canonical not in vocabulary or stored in entries:
                valid = False
                break
            entries[stored] = canonical
        if not valid:
            dropped.append("DICTIONARY_INVALID")
        elif len(set(entries.values())) != len(entries):
            dropped.append("DICTIONARY_NOT_ONE_TO_ONE")
        elif all(stored == canonical for stored, canonical in entries.items()):
            dropped.append("DICTIONARY_NO_OP")
        elif transform is not None:
            dropped.append("DICTIONARY_WITH_TRANSFORM")
        elif not _runtime_accepts(entries):
            dropped.append("DICTIONARY_REJECTED_BY_RUNTIME")
        else:
            dictionary = entries
    return transform, dictionary, dropped


def propose(spec: Variant, model: ModelCall) -> tuple[VariantMappingAnswer, dict[str, Any]]:
    """AI answer for one variant plus a trace (raw outputs, drops, timing)."""
    ontology = _ontology_fields()
    columns = {c["ref"]: c for c in column_inputs(spec)}
    trace: dict[str, Any] = {"calls": 0, "seconds": 0.0, "dropped": {}, "raw": {}, "failures": {}}

    def call(key: str, prompt: str, schema: dict[str, Any]) -> Any:
        """Model output that conforms to the schema, or ``None`` (failure recorded)."""
        started = time.perf_counter()
        trace["calls"] += 1
        try:
            output = model(prompt, schema)
        except ProposalError as exc:
            trace["failures"][key] = exc.code
            output = None
        except Exception:
            trace["failures"][key] = "MODEL_ERROR"
            output = None
        trace["seconds"] = round(trace["seconds"] + time.perf_counter() - started, 2)
        trace["raw"][key] = output
        if output is not None and not schema_conforms(output, schema):
            trace["failures"][key] = "MODEL_SCHEMA_VIOLATION"
            return None
        return output

    prompt, schema = column_prompt(spec, ontology)
    chosen = call("columns", prompt, schema)
    locations: dict[str, str | None] = {}
    used: set[str] = set()
    for name in CANONICAL_FIELDS:
        ref = chosen.get(name) if isinstance(chosen, dict) else None
        if ref not in columns:
            locations[name] = None
            continue
        if ref in used:
            trace["dropped"].setdefault(name, []).append("COLUMN_ALREADY_USED")
            locations[name] = None
            continue
        used.add(ref)
        locations[name] = ref

    fields: dict[str, FieldAnswer] = {}
    for name in CANONICAL_FIELDS:
        ref = locations[name]
        if ref is None:
            fields[name] = FieldAnswer()
            continue
        column = columns[ref]
        prompt, schema = value_prompt(name, ontology[name], column)
        output = call(name, prompt, schema)
        if output is None:
            fields[name] = FieldAnswer(table=column["table"], column=column["column"])
            continue
        transform, dictionary, dropped = validate_value_output(name, ontology[name], column, output)
        if dropped:
            trace["dropped"].setdefault(name, []).extend(dropped)
        fields[name] = FieldAnswer(
            table=column["table"], column=column["column"], transform=transform, dictionary=dictionary
        )
    answer = with_section_anchor(VariantMappingAnswer(variant=spec.name, fields=fields))
    return answer, trace


# --- CLI ----------------------------------------------------------------------------------


def propose_all(model: ModelCall) -> dict[str, Any]:
    variants: dict[str, Any] = {}
    for spec in VARIANTS:
        answer, trace = propose(spec, model)
        variants[spec.name] = {
            "answer": answer.model_dump(mode="json"),
            "fields": score_fields(spec, answer),
            "trace": trace,
        }
    return {
        "version": PROMPT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "variants": variants,
    }


def load_answers(path: str | Path) -> dict[str, VariantMappingAnswer]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("version") != PROMPT_VERSION:
        raise ValueError("unknown proposal version")
    answers = {
        name: VariantMappingAnswer.model_validate(record["answer"])
        for name, record in payload["variants"].items()
    }
    if set(answers) != {spec.name for spec in VARIANTS}:
        raise ValueError("proposals must cover every variant")
    # A failed or malformed model call makes the proposal incomplete, not a
    # low-scoring answer: such runs are repeated, never scored.
    if any(record.get("trace", {}).get("failures") for record in payload["variants"].values()):
        raise ValueError("proposals contain failed model calls")
    return answers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backend.adaptive.evaluation.ai_mapping", allow_abbrev=False)
    parser.add_argument("--propose", action="store_true", required=True, help="Call the local model.")
    parser.add_argument("--label", required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--repository-root", default=str(Path.cwd()))
    args = parser.parse_args(argv)
    if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", args.label) is None:
        print(json.dumps({"status": "FAILED", "code": "INVALID_LABEL"}), flush=True)
        return 2
    path = Path(args.repository_root) / REPORT_DIR / f"{args.label}-proposals.json"
    if path.exists():
        print(json.dumps({"status": "FAILED", "code": "LABEL_EXISTS"}), flush=True)
        return 2
    try:
        model = OllamaJsonModel(args.model)
    except Exception:
        print(json.dumps({"status": "FAILED", "code": "MODEL_CONFIG_INVALID"}), flush=True)
        return 2
    run = propose_all(model)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(run, ensure_ascii=False, indent=2))
    failed = {name: record["trace"]["failures"] for name, record in run["variants"].items()
              if record["trace"]["failures"]}
    if failed:
        print(json.dumps({"status": "INCOMPLETE", "failures": failed}), flush=True)
    for name, record in run["variants"].items():
        fields = record["fields"]
        print(json.dumps({
            "variant": name, "field": fields["field"], "column": fields["column"],
            "transform": fields["transform"], "dictionary": fields["dictionary"],
            "anchor": fields["anchor"], "calls": record["trace"]["calls"],
            "seconds": record["trace"]["seconds"],
        }, ensure_ascii=False), flush=True)
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
