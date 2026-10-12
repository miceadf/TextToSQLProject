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

from ..mapping.ai_proposal import (  # noqa: F401  (re-exported for callers and tests)
    NONE,
    OllamaJsonModel,
    ProposalError,
    build_column_prompt,
    schema_conforms,
)
from ..mapping.ai_proposal import notes_block as _notes_block
from ..mapping.transforms import FieldTransform, column_type_supports, transform_supports_data_type
from .variant_benchmark import (
    REPORT_DIR,
    FieldAnswer,
    VariantMappingAnswer,
    score_fields,
    with_section_anchor,
)
from .variant_specs import CANONICAL_FIELDS, SUITES, Note, Variant, sample_values

# v1.1 (after review): the slash example no longer repeats a benchmark
# stored value, model output is checked against its schema, failed calls
# mark the proposal invalid, and dictionaries pass the runtime model.
PROMPT_VERSION = "ai-mapping-v1.1"
# Phase 8-10: v2 checks transforms against the stored examples, asks for
# value dictionaries in a separate step, and can read administrator notes.
PIPELINES = ("v1.1", "v2")
KNOWN_VERSIONS = frozenset({PROMPT_VERSION, "ai-mapping-v2", "ai-mapping-v2+notes"})
WEEKDAYS = frozenset("월화수목금토일")
_SLASH_VALUE = re.compile(r"^\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)+$")


def version_of(pipeline: str, notes: bool) -> str:
    if pipeline not in PIPELINES or (notes and pipeline == "v1.1"):
        raise ValueError("unknown pipeline")
    return PROMPT_VERSION if pipeline == "v1.1" else "ai-mapping-v2" + ("+notes" if notes else "")
DEFAULT_MODEL = "qwen2.5-coder:7b-instruct"
DEFAULT_BASE_URL = "http://127.0.0.1:11434"

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


# --- inputs ---------------------------------------------------------------------------


def _ontology_fields() -> dict[str, Any]:
    from .ontology_version import recorded_ontology

    return recorded_ontology().active_fields


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


def _note_payload(ref: str, note: Note) -> dict[str, Any]:
    payload: dict[str, Any] = {"column": ref, "note": note.text}
    if note.codes:
        payload["codes"] = dict(note.codes)
    return payload


def column_prompt(
    spec: Variant,
    ontology: dict[str, Any],
    *,
    version: str = PROMPT_VERSION,
    notes: dict[str, Note] | None = None,
) -> tuple[str, dict[str, Any]]:
    return build_column_prompt(
        column_inputs(spec),
        ontology,
        CANONICAL_FIELDS,
        version=version,
        notes=[_note_payload(ref, note) for ref, note in (notes or {}).items()],
    )


def value_prompt(
    name: str,
    field: Any,
    column: dict[str, Any],
    *,
    version: str = PROMPT_VERSION,
    note: Note | None = None,
) -> tuple[str, dict[str, Any]]:
    vocabulary = CANONICAL_VALUES.get(name)
    # From v2 on, dictionaries have their own step (``dictionary_prompt``).
    offer_dictionary = (
        version == PROMPT_VERSION
        and vocabulary is not None
        and name not in IDENTITY_FIELDS
        and _TEXT_TYPE.fullmatch(column["type"]) is not None
        and bool(column["values"])
    )
    lines = [
        f"Prompt version: {version}/values",
        "A database column has been mapped to a canonical field.",
        f"Field: {name} ({field.label}, {field.data_type.value}): {field.description}",
        f"Column: {column['ref']}, declared type {column['type']}",
        f"Stored values (examples): {', '.join(column['values'][:10])}",
        "How must the stored value be read to give the field value?",
        "- none: the stored value already is the field value",
        *(f"- {transform.value}: {help_text}" for transform, help_text in TRANSFORM_HELP.items()),
        *_notes_block([_note_payload(column["ref"], note)] if note else []),
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


def needs_dictionary(name: str, column: dict[str, Any]) -> bool:
    """v2 asks for a dictionary only when some stored example is not canonical."""
    vocabulary = CANONICAL_VALUES.get(name)
    return (
        vocabulary is not None
        and name not in IDENTITY_FIELDS
        and _TEXT_TYPE.fullmatch(column["type"]) is not None
        and bool(column["values"])
        and any(value not in vocabulary for value in column["values"])
    )


def dictionary_prompt(
    name: str, field: Any, column: dict[str, Any], *, version: str, note: Note | None = None
) -> tuple[str, dict[str, Any]]:
    vocabulary = list(CANONICAL_VALUES[name])
    lines = [
        f"Prompt version: {version}/dictionary",
        "A database column has been mapped to a canonical field whose values come from a fixed list.",
        f"Field: {name} ({field.label}, {field.data_type.value}): {field.description}",
        f"Column: {column['ref']}, declared type {column['type']}",
        f"Canonical values: {', '.join(vocabulary)}",
        f"Stored values: {', '.join(column['values'])}",
        *_notes_block([_note_payload(column["ref"], note)] if note else []),
        "For every stored value, give the canonical value it means. Return only the JSON object "
        "whose keys are the stored values.",
    ]
    schema = {
        "type": "object",
        "properties": {value: {"type": "string", "enum": vocabulary} for value in column["values"]},
        "required": list(column["values"]),
        "additionalProperties": False,
    }
    return "\n".join(lines), schema


def direct_dictionary(name: str, column: dict[str, Any], note: Note | None) -> dict[str, str] | None:
    """An administrator code table whose meanings are exactly canonical values."""
    from ..mapping.values import lookup_key

    if note is None or not note.codes or name not in CANONICAL_VALUES:
        return None
    canonical = {lookup_key(value): value for value in CANONICAL_VALUES[name]}
    codes = [code for code, _meaning in note.codes]
    entries = {code: canonical.get(lookup_key(meaning)) for code, meaning in note.codes}
    # Only a complete, duplicate-free table naming every stored example.
    if len(codes) != len(set(codes)) or set(codes) != set(column["values"]) or None in entries.values():
        return None
    return entries  # type: ignore[return-value]


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


def values_fit(transform: FieldTransform, values: list[str]) -> bool:
    """v2: the stored examples must look like what the transform reads."""
    if not values:
        return False  # nothing to check the transform against
    if transform is FieldTransform.KOREAN_WEEKDAY:
        return all(value in WEEKDAYS for value in values)
    if transform is FieldTransform.SLASH_FIRST_NUMERIC:
        return all(_SLASH_VALUE.fullmatch(value) for value in values)
    if transform is FieldTransform.HHMM_TEXT_TO_HHMM:
        return all(re.fullmatch(r"(?:[01]\d|2[0-3])[0-5]\d", value) for value in values)
    if transform is FieldTransform.PACKED_WEEKDAY:
        from ..registration.postgres_registrations import packed_weekdays_fit

        return packed_weekdays_fit(values)
    return True


def validate_dictionary(
    name: str, column: dict[str, Any], entries: dict[str, str]
) -> tuple[dict[str, str] | None, list[str]]:
    vocabulary = set(CANONICAL_VALUES.get(name, ()))
    if (
        name in IDENTITY_FIELDS
        or _TEXT_TYPE.fullmatch(column["type"]) is None
        or not set(entries) <= set(column["values"])
        or not set(entries.values()) <= vocabulary
    ):
        return None, ["DICTIONARY_INVALID"]
    if len(set(entries.values())) != len(entries):
        return None, ["DICTIONARY_NOT_ONE_TO_ONE"]
    if all(stored == canonical for stored, canonical in entries.items()):
        return None, ["DICTIONARY_NO_OP"]
    if not _runtime_accepts(entries):
        return None, ["DICTIONARY_REJECTED_BY_RUNTIME"]
    return dict(entries), []


def validate_value_output(
    name: str, field: Any, column: dict[str, Any], output: Any, *, check_values: bool = False
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
                if check_values and not values_fit(candidate, column["values"]):
                    dropped.append("TRANSFORM_VALUE_MISMATCH")
                else:
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


def propose(
    spec: Variant,
    model: ModelCall,
    *,
    pipeline: str = "v1.1",
    notes: bool = False,
) -> tuple[VariantMappingAnswer, dict[str, Any]]:
    """AI answer for one variant plus a trace (raw outputs, drops, timing)."""
    version = version_of(pipeline, notes)
    v2 = pipeline == "v2"
    ontology = _ontology_fields()
    columns = {c["ref"]: c for c in column_inputs(spec)}
    notes_by_ref = {f"{note.table}.{note.column}": note for note in spec.notes} if notes else {}
    trace: dict[str, Any] = {
        "version": version, "calls": 0, "seconds": 0.0, "dropped": {}, "raw": {}, "failures": {},
        "notes": len(notes_by_ref), "direct_dictionaries": [],
    }

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

    prompt, schema = column_prompt(spec, ontology, version=version, notes=notes_by_ref)
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
        note = notes_by_ref.get(ref)
        prompt, schema = value_prompt(name, ontology[name], column, version=version, note=note)
        output = call(name, prompt, schema)
        if output is None:
            fields[name] = FieldAnswer(table=column["table"], column=column["column"])
            continue
        transform, dictionary, dropped = validate_value_output(
            name, ontology[name], column, output, check_values=v2
        )
        if v2 and transform is None and needs_dictionary(name, column):
            entries = direct_dictionary(name, column, note)
            if entries is not None:
                trace["direct_dictionaries"].append(name)
            else:
                prompt, schema = dictionary_prompt(
                    name, ontology[name], column, version=version, note=note
                )
                entries = call(f"{name}#dictionary", prompt, schema)
            if entries is not None:
                dictionary, more = validate_dictionary(name, column, entries)
                dropped += more
        if dropped:
            trace["dropped"].setdefault(name, []).extend(dropped)
        fields[name] = FieldAnswer(
            table=column["table"], column=column["column"], transform=transform, dictionary=dictionary
        )
    answer = with_section_anchor(VariantMappingAnswer(variant=spec.name, fields=fields))
    return answer, trace


# --- CLI ----------------------------------------------------------------------------------


def propose_all(
    model: ModelCall, *, suite: str = "dev", pipeline: str = "v1.1", notes: bool = False
) -> dict[str, Any]:
    variants: dict[str, Any] = {}
    for spec in SUITES[suite]:
        answer, trace = propose(spec, model, pipeline=pipeline, notes=notes)
        variants[spec.name] = {
            "answer": answer.model_dump(mode="json"),
            "fields": score_fields(spec, answer),
            "trace": trace,
        }
    return {
        "version": version_of(pipeline, notes),
        "suite": suite,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "variants": variants,
    }


def load_answers(path: str | Path, suite: str = "dev") -> dict[str, VariantMappingAnswer]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("version") not in KNOWN_VERSIONS:
        raise ValueError("unknown proposal version")
    # Files written before suites existed hold the development suite.
    if payload.get("suite", "dev") != suite:
        raise ValueError("proposals belong to another suite")
    answers = {
        name: VariantMappingAnswer.model_validate(record["answer"])
        for name, record in payload["variants"].items()
    }
    if set(answers) != {spec.name for spec in SUITES[suite]}:
        raise ValueError("proposals must cover every variant")
    if any(answer.variant != name for name, answer in answers.items()):
        raise ValueError("an answer is filed under another variant")
    version = payload["version"]
    if any(
        record.get("trace", {}).get("version", version) != version
        for record in payload["variants"].values()
    ):
        raise ValueError("a trace was produced by another pipeline version")
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
    parser.add_argument("--suite", choices=sorted(SUITES), default="dev")
    parser.add_argument("--pipeline", choices=PIPELINES, default="v1.1")
    parser.add_argument("--notes", action="store_true", help="Give the model administrator notes (v2).")
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
    try:
        version_of(args.pipeline, args.notes)
    except ValueError:
        print(json.dumps({"status": "FAILED", "code": "INVALID_PIPELINE"}), flush=True)
        return 2
    run = propose_all(model, suite=args.suite, pipeline=args.pipeline, notes=args.notes)
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
