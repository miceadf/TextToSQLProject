"""Phase 8-18: the common target-grade field and additive ontology versions."""

from __future__ import annotations

from typing import Any

import pytest

from backend.adaptive.api import legacy_view_profile as lvp
from backend.adaptive.api.chat_query import answer_chat_query
from backend.adaptive.api.postgres_runtime import PostgresRuntimeError, build_validated_contexts
from backend.adaptive.api.database_registry import _REF_TO_DTO_KEY, CANONICAL_SELECT, CARD_FIELDS
from backend.adaptive.ontology import load_default_ontology
from backend.adaptive.ontology.contracts import DomainOntology
from backend.adaptive.api.postgres_runtime import PostgresRuntimeProfile
from backend.adaptive.catalog.postgres import PostgresConnectionConfig
from tests.adaptive.test_legacy_view_partial_mapping import (
    DATABASE_ID,
    SECRET_DSN,
    FakeAdapter,
    FakeExecutor,
    make_catalog,
    make_published,
)


def make_profile(*, ontology_version: int, fields: dict[str, str] | None = None) -> PostgresRuntimeProfile:
    published = make_published(fields).model_copy(update={"ontology_version": ontology_version})
    return PostgresRuntimeProfile(
        database_id=DATABASE_ID,
        label=lvp.LABEL,
        connection_config=PostgresConnectionConfig(SECRET_DSN, include_views=True),
        published_mapping=published,
        field_transforms=dict(lvp.FIELD_TRANSFORMS),
        unsupported_fields=lvp.UNSUPPORTED_FIELDS,
    )


def test_version_two_only_adds_the_target_grade() -> None:
    ontology = load_default_ontology()
    assert ontology.version == 3
    assert set(ontology.fields_at(2)) - set(ontology.fields_at(1)) == {"Course.target_grade"}
    assert set(ontology.fields_at(1)) == set(CANONICAL_SELECT) | {"CourseOffering.year"}
    field = ontology.fields["Course.target_grade"]
    assert field.label == "대상 학년" and field.data_type.value == "integer" and not field.required
    assert ontology.accepts_version(1) and ontology.accepts_version(2)
    for bad in (0, 4, True, "2", None):
        assert not ontology.accepts_version(bad)
    with pytest.raises(ValueError):
        ontology.fields_at(4)


def test_a_field_newer_than_the_ontology_is_rejected() -> None:
    data = load_default_ontology().model_dump(mode="json")
    data["version"] = 1
    with pytest.raises(ValueError):
        DomainOntology.model_validate(data)


def test_card_fields_follow_the_ontology_and_have_dto_keys() -> None:
    assert list(CARD_FIELDS) == list(load_default_ontology().active_fields)
    assert set(_REF_TO_DTO_KEY) == set(CARD_FIELDS) | {"CourseOffering.year"}
    assert _REF_TO_DTO_KEY["Course.target_grade"] == "target_grade"


# --- a mapping made under version 1 -----------------------------------------------------


def _v1_profile() -> Any:
    fields = {name: column for name, (column, _) in lvp.VIEW_FIELDS.items() if name != "Course.target_grade"}
    return make_profile(fields=fields, ontology_version=1)


def _context(profile: Any, executor: Any = None) -> Any:
    return build_validated_contexts(
        [profile],
        adapters_by_id={DATABASE_ID: FakeAdapter(make_catalog())},
        executors_by_id={DATABASE_ID: executor or FakeExecutor()},
        expected_ontology_version=2,
    )[DATABASE_ID]


def test_a_version_one_mapping_runs_and_the_new_field_is_unsupported() -> None:
    context = _context(_v1_profile())
    assert "Course.target_grade" in context.unsupported_fields()

    class Model:
        def generate_ir(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {
                "entity": "Course",
                "select": ["Course.name"],
                "filters": [{"field": "Course.target_grade", "operator": "equals", "value": 1}],
                "limit": 50,
            }

    answer = answer_chat_query(
        database_id=DATABASE_ID, question="1학년 과목", pg_contexts={DATABASE_ID: context}, model=Model()
    )
    assert answer["status"] == "UNSUPPORTED"
    assert answer["unsupported"] == [{"field": "Course.target_grade", "label": "대상 학년"}]


def test_a_mapping_newer_than_the_server_is_refused() -> None:
    with pytest.raises(PostgresRuntimeError):
        build_validated_contexts(
            [make_profile(ontology_version=2)],
            adapters_by_id={DATABASE_ID: FakeAdapter(make_catalog())},
            executors_by_id={DATABASE_ID: FakeExecutor()},
            expected_ontology_version=1,
        )


def test_a_version_one_mapping_may_not_declare_the_newer_field() -> None:
    # Under version 1 the field did not exist: mapping it is refused.
    with pytest.raises(PostgresRuntimeError):
        _context(make_profile(ontology_version=1))


def test_the_legacy_profiles_map_the_target_grade() -> None:
    assert lvp.VIEW_FIELDS["Course.target_grade"] == ("target_year", "integer")
    assert lvp.BASE_FIELDS["Course.target_grade"] == ("subject", "target_year", "integer")
    context = _context(make_profile(ontology_version=2))
    assert "Course.target_grade" not in context.unsupported_fields()


def test_a_version_one_dataset_profile_cannot_map_the_newer_field(tmp_path) -> None:
    import yaml

    from backend.adaptive.dataset.source_profile import load_dataset_schema_profile

    source = __import__("pathlib").Path("backend/adaptive/dataset/profiles/cnu-courses-v1.yaml")
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    assert data["ontology_version"] == 1
    ontology = load_default_ontology()
    assert load_dataset_schema_profile(source, ontology)  # version 1 stays valid
    data["mappings"].append({
        "table": "courses", "column": "target_year", "semantic_field": "Course.target_grade",
        "allowed_literal_types": ["integer"], "evidence": "check",
    })
    changed = tmp_path / "profile.yaml"
    changed.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError):
        load_dataset_schema_profile(changed, ontology)


def test_version_one_evaluation_fingerprints_are_unchanged() -> None:
    import yaml

    from backend.adaptive.evaluation.reference import ontology_fingerprint

    data = yaml.safe_load(open("backend/adaptive/evaluation/data/fixture_v1_cases.yaml", encoding="utf-8"))
    ontology = load_default_ontology()
    assert data["ontology_fingerprint"] == ontology_fingerprint(ontology.at_version(1))
    assert ontology_fingerprint(ontology) != data["ontology_fingerprint"]
