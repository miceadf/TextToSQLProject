from __future__ import annotations

from types import SimpleNamespace

from backend.adaptive.evaluation.field_failure_analysis import (
    FIXED_REFERENCE_CATEGORIES,
    audit_summary,
    classify_unknown_field_attempts,
)
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


def test_fixed_reference_categories_is_frozen_set() -> None:
    assert isinstance(FIXED_REFERENCE_CATEGORIES, frozenset)
    assert FIXED_REFERENCE_CATEGORIES == {
        "OUTSIDE_ONTOLOGY", "ENTITY_PREFIX_MISMATCH", "PHYSICAL_COLUMN_FORM",
        "CASING_NOTATION", "OTHER", "UNCLASSIFIABLE",
    }


def test_audit_summary_returns_no_original_names() -> None:
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
    values = [["Course.unknown"], ["Wrong.name"], ["PRIVATE_COLUMN"]]
    targets = [CaptureTarget(
        request=HarnessRequest(request_id=f"r{i}", database_id="db", question="q"),
        ontology=ontology, mapping=mapping,
    ) for i in range(len(values))]
    records = [RecordedSuccess(
        status="SUCCEEDED", request_id=f"r{i}", database_id="db",
        question_sha256="a" * 64, mapping_sha256="b" * 64,
        response={"select": names, "filters": []},
    ) for i, names in enumerate(values)]
    non_unknown = [SimpleNamespace(
        request_id=f"ok{i}", failure_code=None,
    ) for i in range(5)]
    unknown_attempts = [SimpleNamespace(
        request_id=f"r{i}", failure_code="UNKNOWN_FIELD",
    ) for i in range(len(values))]
    report = SimpleNamespace(attempts=unknown_attempts + non_unknown)
    summary = audit_summary(report, SimpleNamespace(records=records), targets)
    assert summary["total_attempts"] == 8
    assert summary["unknown_field_attempts"] == 3
    assert summary["total_unknown_field_attempts"] == 3
    assert summary["categories"] == sorted(FIXED_REFERENCE_CATEGORIES)
    serialized = str(summary)
    assert "PRIVATE_TABLE" not in serialized
    assert "PRIVATE_COLUMN" not in serialized
    assert "PRIVATE_" not in serialized


def test_audit_summary_empty_report() -> None:
    report = SimpleNamespace(attempts=[])
    recording = SimpleNamespace(records=[])
    summary = audit_summary(report, recording, [])
    assert summary["total_attempts"] == 0
    assert summary["unknown_field_attempts"] == 0
    assert summary["total_unknown_field_attempts"] == 0
    assert summary["duplicate_references"] == 0
    assert summary["mixed_category_attempts"] == 0


def test_audit_summary_counts_mixed_and_duplicates_consistently() -> None:
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
        ["Course.unknown", "Wrong.name"],
        ["Course.unknown", "Course.unknown"],
        ["no_dot"],
    ]
    targets = [CaptureTarget(
        request=HarnessRequest(request_id=f"r{i}", database_id="db", question="q"),
        ontology=ontology, mapping=mapping,
    ) for i in range(len(values))]
    records = [RecordedSuccess(
        status="SUCCEEDED", request_id=f"r{i}", database_id="db",
        question_sha256="a" * 64, mapping_sha256="b" * 64,
        response={"select": names, "filters": []},
    ) for i, names in enumerate(values)]
    report = SimpleNamespace(attempts=[SimpleNamespace(
        request_id=f"r{i}", failure_code="UNKNOWN_FIELD",
    ) for i in range(len(values))])
    summary = audit_summary(report, SimpleNamespace(records=records), targets)
    counts = summary["category_counts"]
    assert summary["total_unknown_field_attempts"] == sum(counts.values()) == 3
    assert summary["unknown_field_attempts"] == 3
    assert summary["mixed_category_attempts"] == 1
    assert summary["duplicate_references"] == 1
    assert set(counts) == set(FIXED_REFERENCE_CATEGORIES)
    serialized = str(summary)
    assert "PRIVATE_TABLE" not in serialized
    assert "PRIVATE_COLUMN" not in serialized
    assert "PRIVATE_" not in serialized
