"""Phase 9-24: offline versions and stale AI keys never change product v3."""

import pytest

from backend.adaptive.evaluation.contracts import EvaluationSet
from backend.adaptive.evaluation.ontology_version import recorded_ontology
from backend.adaptive.evaluation.reference import ontology_fingerprint
from backend.adaptive.evaluation.validator import validate_evaluation_set
from backend.adaptive.fixtures.seed import DEFAULT_SEED
from backend.adaptive.mapping.ai_proposal import propose_upload_mapping
from backend.adaptive.ontology import load_default_ontology
from tests.adaptive.test_evaluation_gold import _gold_document
from tests.adaptive.test_upload_ai_proposal import CORRECT, FakeModel, _inputs, _renamed_database


YEAR = "CourseOffering.year"


def test_unversioned_record_defaults_to_v2_without_mutating_current_ontology():
    current = load_default_ontology()
    before = current.model_dump()
    replay = recorded_ontology(current)
    assert replay.version == 2 and YEAR in replay.active_fields
    assert current.version == 3 and YEAR not in current.active_fields
    assert current.model_dump() == before


@pytest.mark.parametrize("version", [1, 2, 3])
def test_explicit_version_selects_that_versions_fields(version):
    replay = recorded_ontology(version=version)
    assert replay.version == version
    assert (YEAR in replay.active_fields) == (version < 3)
    assert ("Course.target_grade" in replay.active_fields) == (version >= 2)


@pytest.mark.parametrize("version", [0, 4, True, "2", None])
def test_invalid_declared_version_is_not_replaced_by_v2(version):
    with pytest.raises(ValueError):
        recorded_ontology(version=version)


def test_unversioned_evaluation_metadata_is_validated_against_v2_fingerprint():
    raw = _gold_document()
    raw.pop("ontology_version")
    raw["ontology_fingerprint"] = ontology_fingerprint(recorded_ontology())
    gold = EvaluationSet.model_validate(raw)
    assert gold.ontology_version == 2
    validate_evaluation_set(gold, DEFAULT_SEED, load_default_ontology(), require_full=False)
    stale = gold.model_copy(update={"ontology_fingerprint": ontology_fingerprint(load_default_ontology())})
    with pytest.raises(ValueError, match="ontology fingerprint drift"):
        validate_evaluation_set(stale, DEFAULT_SEED, load_default_ontology(), require_full=False)


@pytest.mark.parametrize("retired_choice", ["lect.no-such-column", {"obsolete": "shape"}, None])
def test_stale_ai_year_is_discarded_without_mutating_output(tmp_path, retired_choice):
    catalog, ontology, rules = _inputs(_renamed_database(tmp_path))
    answer = {**CORRECT, YEAR: retired_choice, "Course.target_grade": "none"}
    model = FakeModel(answer)
    before = dict(answer)
    proposal = propose_upload_mapping(catalog, ontology, rules, model=model)
    assert proposal is not None
    assert YEAR not in proposal.candidates and YEAR not in model.schemas[0]["required"]
    assert proposal.candidates["Course.code"][0].column == "subj_cd"
    assert answer == before


@pytest.mark.parametrize("fault", ["unknown", "missing", "invalid_column"])
def test_discarding_retired_ai_keys_keeps_active_schema_checks(tmp_path, fault):
    catalog, ontology, rules = _inputs(_renamed_database(tmp_path))
    answer = {**CORRECT, "Course.target_grade": "none"}
    if fault == "unknown":
        answer["Course.unknown"] = "none"
    elif fault == "missing":
        answer.pop("Course.name")
    else:
        answer["Course.name"] = "lect.no-such-column"
    assert propose_upload_mapping(catalog, ontology, rules, model=FakeModel(answer)) is None


def test_historical_school_mapping_check_uses_v2_with_fake_adapter(tmp_path, monkeypatch):
    from backend.adaptive.catalog import postgres
    from backend.adaptive.evaluation.school_mapping_check import _propose
    from backend.adaptive.harness import postgres_values
    from backend.adaptive.registration import postgres_registrations

    catalog, _ontology, _rules = _inputs(_renamed_database(tmp_path))
    seen = []
    monkeypatch.setattr(postgres.PostgresAdapter, "inspect", lambda *_args: catalog)
    monkeypatch.setattr(postgres_values, "PostgresDistinctValueReader", lambda *_args: object())
    monkeypatch.setattr(postgres_registrations, "_sampled_catalog", lambda *_args: catalog)

    def fake_model(_prompt, schema):
        seen.append(schema)
        return {**CORRECT, "Course.target_grade": "none"}

    proposal, _catalog = _propose("synthetic_school", "postgresql://unused/unused", fake_model)
    assert proposal.ontology_version == 2 and YEAR in seen[0]["required"]
    assert proposal.candidates[YEAR][0].column == "yr"
