"""Seed-only semantic reference, independent of compiler and SQL execution."""

from __future__ import annotations

import hashlib
import json

from ..fixtures.contracts import FixtureSeed, LogicalOffering
from ..ontology.contracts import DomainOntology
from ..harness.contracts import QueryIR
from .contracts import OfferingKey, Scalar


FIELD_TO_ATTRIBUTE = {
    "Course.code": "course_code",
    "Course.name": "course_name",
    "Course.credits": "credits",
    "Course.category": "category",
    "Department.code": "department_code",
    "Department.name": "department_name",
    "Instructor.name": "instructor_name",
    "CourseOffering.year": "year",
    "CourseOffering.semester": "semester",
    "CourseOffering.section": "section",
    "CourseOffering.capacity": "capacity",
    "CourseOffering.enrolled": "enrolled",
    "Schedule.day": "day",
    "Schedule.start_time": "start_time",
    "Schedule.end_time": "end_time",
}


def seed_fingerprint(seed: FixtureSeed) -> str:
    payload = {"version": seed.version, "offerings": seed.canonical_rows()}
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def ontology_fingerprint(ontology: DomainOntology) -> str:
    payload = ontology.at_version(ontology.version).model_dump(mode="json")
    for entity in payload["entities"]:
        for field in entity["fields"]:
            field["allowed_operators"] = sorted(field["allowed_operators"])
            # Phase 8-18: bookkeeping only; leaving it out keeps version-1
            # fingerprints unchanged (compare with ``ontology.at_version``).
            field.pop("since_version", None)
            if field.get("retired_in_version") is None:
                field.pop("retired_in_version", None)
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def query_ir_fingerprint() -> str:
    """Pin the actual QueryIR JSON contract, not just a handwritten version."""

    canonical = json.dumps(
        QueryIR.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def offering_index(seed: FixtureSeed) -> dict[tuple[int, str, str, str], LogicalOffering]:
    return {offering.offering_key: offering for offering in seed.offerings}


def semantic_row(offering: LogicalOffering, fields: list[str]) -> dict[str, Scalar]:
    return {field: getattr(offering, FIELD_TO_ATTRIBUTE[field]) for field in fields}


def offering_key(offering: LogicalOffering) -> OfferingKey:
    return OfferingKey(
        year=offering.year,
        semester=offering.semester,
        course_code=offering.course_code,
        section=offering.section,
    )
