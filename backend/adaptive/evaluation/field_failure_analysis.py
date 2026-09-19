"""Aggregate private UNKNOWN_FIELD failures without returning field names."""

from __future__ import annotations

from collections import Counter

from ..harness.recorded import ModelRecording, RecordedSuccess
from ..harness.recording_orchestrator import CaptureTarget
from .run_contracts import EvaluationRunReport


CATEGORIES = (
    "OUTSIDE_ONTOLOGY", "ENTITY_PREFIX_MISMATCH", "PHYSICAL_COLUMN_FORM",
    "CASING_NOTATION", "OTHER", "UNCLASSIFIABLE",
)


def _normalized(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def _category(name: str, target: CaptureTarget) -> str:
    allowed = set(target.ontology.fields)
    physical = {
        form
        for field in target.mapping.fields.values()
        for form in (field.column, f"{field.table}.{field.column}")
    }
    if name in physical:
        return "PHYSICAL_COLUMN_FORM"
    if any(name.split(".")[-1] == item.split(".")[-1] for item in allowed):
        return "ENTITY_PREFIX_MISMATCH"
    if _normalized(name) in {_normalized(item) for item in allowed}:
        return "CASING_NOTATION"
    if "." in name and all(part for part in name.split(".")):
        return "OUTSIDE_ONTOLOGY"
    return "OTHER"


def classify_unknown_field_attempts(
    report: EvaluationRunReport,
    recording: ModelRecording,
    targets: list[CaptureTarget],
) -> dict[str, int]:
    """One category per failed attempt; duplicates count extra repeated references."""
    records = {record.request_id: record for record in recording.records}
    contexts = {target.request.request_id: target for target in targets}
    counts: Counter[str] = Counter({key: 0 for key in CATEGORIES})
    duplicates = 0
    mixed = 0
    for attempt in report.attempts:
        if attempt.failure_code != "UNKNOWN_FIELD":
            continue
        record = records.get(attempt.request_id)
        target = contexts.get(attempt.request_id)
        if not isinstance(record, RecordedSuccess) or target is None or not isinstance(record.response, dict):
            counts["UNCLASSIFIABLE"] += 1
            continue
        response = record.response
        select = response.get("select")
        filters = response.get("filters")
        if not isinstance(select, list) or not isinstance(filters, list):
            counts["UNCLASSIFIABLE"] += 1
            continue
        names = [*select, *(item.get("field") for item in filters if isinstance(item, dict))]
        unknown = [name for name in names if isinstance(name, str) and name not in target.ontology.fields]
        if not unknown:
            counts["UNCLASSIFIABLE"] += 1
            continue
        duplicates += len(unknown) - len(set(unknown))
        categories = {_category(name, target) for name in unknown}
        if len(categories) > 1:
            mixed += 1
        # The validator reports the first unknown select/filter reference.
        counts[_category(unknown[0], target)] += 1
    counts["DUPLICATE_REFERENCES"] = duplicates
    counts["MIXED_CATEGORY_ATTEMPTS"] = mixed
    counts["TOTAL_UNKNOWN_FIELD_ATTEMPTS"] = sum(counts[key] for key in CATEGORIES)
    return dict(counts)
