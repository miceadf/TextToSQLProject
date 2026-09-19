from __future__ import annotations

from types import SimpleNamespace

from backend.adaptive.evaluation.field_failure_analysis import classify_unknown_field_attempts
from backend.adaptive.harness import (
    CaptureTarget, DataType, FieldMapping, HarnessRequest, MappingProfile,
    MappingStatus, Ontology, OntologyField, Operator, RecordedSuccess,
)


def test_private_failure_categories_and_duplicate_accounting() -> None:
    ontology = Ontology(
        entities={"Course"},
        fields={"Course.name": OntologyField(
            name="Course.name", data_type=DataType.STRING,
            allowed_operators={Operator.EQUALS},
        )},
    )
    mapping = MappingProfile(
        database_id="db", version=1, status=MappingStatus.PUBLISHED,
        fields={"Course.name": FieldMapping(table="PRIVATE_TABLE", column="PRIVATE_COLUMN")},
    )
    values = [
        ["Course.unknown"], ["Wrong.name"], ["PRIVATE_COLUMN"],
        ["course.NAME"], ["no_dot"], ["Course.unknown", "Course.unknown"],
        None,
    ]
    targets = [CaptureTarget(
        request=HarnessRequest(request_id=f"r{i}", database_id="db", question="PRIVATE_QUESTION"),
        ontology=ontology, mapping=mapping,
    ) for i in range(len(values))]
    records = [RecordedSuccess(
        status="SUCCEEDED", request_id=f"r{i}", database_id="db",
        question_sha256="a" * 64, mapping_sha256="b" * 64,
        response={"select": names, "filters": []} if names is not None else {},
    ) for i, names in enumerate(values)]
    report = SimpleNamespace(attempts=[SimpleNamespace(
        request_id=f"r{i}", failure_code="UNKNOWN_FIELD",
    ) for i in range(len(values))])
    counts = classify_unknown_field_attempts(report, SimpleNamespace(records=records), targets)
    assert counts == {
        "OUTSIDE_ONTOLOGY": 2, "ENTITY_PREFIX_MISMATCH": 1,
        "PHYSICAL_COLUMN_FORM": 1, "CASING_NOTATION": 1, "OTHER": 1,
        "UNCLASSIFIABLE": 1, "DUPLICATE_REFERENCES": 1,
        "MIXED_CATEGORY_ATTEMPTS": 0, "TOTAL_UNKNOWN_FIELD_ATTEMPTS": 7,
    }
    assert "PRIVATE_" not in str(counts)
