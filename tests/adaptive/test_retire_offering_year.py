"""Phase 9-16: historical year records remain valid, current runtime retires year."""
import pytest
from types import SimpleNamespace

from backend.adaptive.api.chat_query import _runtime_ontology, answer_chat_query
from backend.adaptive.api.postgres_runtime import PostgresRuntimeProfile, build_validated_contexts
from backend.adaptive.harness.ollama import _prompt, queryir_schema
from backend.adaptive.mapping import MappingReviewService, RuleBasedSchemaAligner
from backend.adaptive.mapping.ai_proposal import propose_upload_mapping
from backend.adaptive.ontology import load_default_ontology
from backend.adaptive.evaluation.loader import load_evaluation_set
from tests.adaptive.test_chat_query_endpoint import FakeModel, ir
from tests.adaptive.test_postgres_runtime_registry import (
    DATABASE_ID, FakeAdapter, FakeExecutor, make_catalog, make_profile,
    FIELD_MAP,
)

YEAR = "CourseOffering.year"


@pytest.mark.parametrize("action", ["approve", "remap", "ignore"])
def test_historical_v2_draft_rejects_retired_review_mutations(action):
    from backend.adaptive.mapping.errors import MappingWorkflowError, MappingWorkflowErrorCode
    from backend.adaptive.mapping.contracts import MappingDraft, ReviewDecision
    domain = load_default_ontology()
    proposal = RuleBasedSchemaAligner().align(make_catalog(), domain.at_version(2))
    service = MappingReviewService()
    draft = service.create_draft(proposal)
    # Historical loading remains supported and mutations are immutable.
    draft = MappingDraft.model_validate_json(draft.model_dump_json())
    assert YEAR in draft.fields and proposal.candidates[YEAR]
    before = draft.model_dump_json()
    def change(field):
        if action == "approve":
            return service.approve(draft, proposal, field, reviewer="test")
        if action == "remap":
            candidate = proposal.candidates[field][0]
            return service.remap(draft, field, table=candidate.table, column=candidate.column,
                                 reviewer="test", reason="fixture")
        return service.ignore(draft, field, reviewer="test", reason="fixture")
    with pytest.raises(MappingWorkflowError) as caught:
        change(YEAR)
    assert caught.value.code == MappingWorkflowErrorCode.UNKNOWN_FIELD
    assert draft.model_dump_json() == before
    changed = change("Course.name")
    assert changed.fields["Course.name"].decision is not ReviewDecision.PENDING


def test_retirement_versions_and_historical_gold():
    domain = load_default_ontology()
    assert domain.version == 3
    for version in (1, 2, 3):
        assert domain.accepts_version(version)
        assert (YEAR in domain.fields_at(version)) == (version < 3)
        assert set(domain.at_version(version).fields) == set(domain.fields_at(version))
    assert domain.fields[YEAR].required
    assert domain.fields[YEAR].retired_in_version == 3
    assert "CourseOffering.semester" in domain.active_fields
    for filename in ("fixture_v1_cases.yaml", "fixture_v1_independent_cases.yaml"):
        loaded = load_evaluation_set("backend/adaptive/evaluation/data/" + filename)
        assert loaded.ontology_version == 1


def context_v2():
    base = make_profile(ontology_version=2)
    profile = PostgresRuntimeProfile(
        database_id=base.database_id, label=base.label, connection_config=base.connection_config,
        published_mapping=base.published_mapping, unsupported_fields=("Course.target_grade",),
    )
    executor = FakeExecutor()
    contexts = build_validated_contexts(
        [profile], adapters_by_id={DATABASE_ID: FakeAdapter(make_catalog())},
        executors_by_id={DATABASE_ID: executor}, expected_ontology_version=3,
    )
    return profile, contexts, executor


@pytest.mark.parametrize("question", ["2024년에 개설된 강의 알려줘", "올해 강의", "개설 연도 알려줘"])
def test_year_question_rejects_even_when_v2_mapping_maps_year(question):
    profile, contexts, executor = context_v2()
    before = profile.published_mapping.model_dump(mode="json")
    model = FakeModel(ir(["Course.name"]))
    result = answer_chat_query(database_id=DATABASE_ID, question=question, pg_contexts=contexts, model=model)
    assert result["status"] == "UNSUPPORTED"
    assert result["unsupported"] == [{"field": YEAR, "label": "개설 연도"}]
    assert executor.calls == 0
    assert profile.published_mapping.model_dump(mode="json") == before
    assert YEAR in contexts[DATABASE_ID].unsupported_fields()


@pytest.mark.parametrize("output", [ir(["Course.name", YEAR]), ir(["Course.name"], [{"field": YEAR, "operator": "equals", "value": 2024}])])
def test_model_cannot_reintroduce_year(output):
    _profile, contexts, executor = context_v2()
    model = FakeModel(output)
    result = answer_chat_query(database_id=DATABASE_ID, question="강의 알려줘", pg_contexts=contexts, model=model)
    assert result["status"] == "NOT_UNDERSTOOD"
    assert result["failure_code"] == "UNKNOWN_FIELD"
    assert executor.calls == 0
    ontology = model.calls[0][1]
    assert YEAR not in ontology.fields
    assert YEAR not in str(queryir_schema(ontology, contexts[DATABASE_ID].harness_mapping()))
    assert YEAR not in _prompt("강의 알려줘", ontology, contexts[DATABASE_ID].harness_mapping(), "query-ir-v3")


def test_new_proposal_and_review_have_no_year():
    domain = load_default_ontology()
    catalog = make_catalog()
    proposal = RuleBasedSchemaAligner().align(catalog, domain)
    draft = MappingReviewService().create_draft(proposal)
    assert proposal.ontology_version == 3
    assert YEAR not in proposal.candidates
    assert YEAR not in draft.fields
    assert YEAR not in _runtime_ontology()[0].fields


def test_fake_ai_mapping_prompt_and_schema_exclude_year():
    domain = load_default_ontology()
    catalog = make_catalog()
    seen = []
    def model(prompt, schema):
        seen.append((prompt, schema))
        return {}  # no usable answer; the rule proposal stands
    rules = RuleBasedSchemaAligner().align(catalog, domain)
    propose_upload_mapping(catalog, domain, rules, model=model)
    assert seen and all(YEAR not in str(part) for part in seen[0])


def test_retired_required_field_does_not_block_historical_draft_publish():
    domain = load_default_ontology()
    catalog = make_catalog()
    service = MappingReviewService()
    draft = service.create_draft(RuleBasedSchemaAligner().align(catalog, domain.at_version(2)))
    for name, (table, column) in FIELD_MAP.items():
        if name != YEAR:
            draft = service.remap(draft, name, table=table, column=column, reviewer="test", reason="test")
    draft = service.ignore(draft, "Course.target_grade", reviewer="test", reason="not stored")
    # Year remains PENDING and required in its historical definition.
    assert service.validate_for_publish(draft, catalog, domain) == []
    published = service.publish(draft, catalog, domain, version=1, published_by="test")
    assert YEAR not in published.fields


def test_legacy_q28_is_still_an_unsupported_field_refusal():
    from backend.adaptive.evaluation.legacy_chat_compare import Scope, load_question_set, score_answer
    from tests.adaptive.test_legacy_view_partial_mapping import DATABASE_ID as legacy_id, make_context
    case = next(case for case in load_question_set().cases if case.case_id == "q28")
    assert case.scope is Scope.UNSUPPORTED_FIELD
    result = answer_chat_query(
        database_id=legacy_id, question=case.question, pg_contexts={legacy_id: make_context()},
        model=FakeModel(ir(["Course.name"])),
    )
    assert result["status"] == "UNSUPPORTED"
    assert score_answer(case.scope, answered=False, keys=None, reference=None, refused=True)["outcome"] == "REFUSED"


def test_postgres_republish_ignores_pending_retired_year():
    from backend.adaptive.registration.postgres_registrations import plan_publish
    domain = load_default_ontology()
    catalog = make_catalog().model_copy(deep=True)
    for name, (table, column) in FIELD_MAP.items():
        field = domain.fields[name]
        declared = {"number": "numeric", "integer": "integer", "time": "time"}.get(field.data_type.value, "text")
        next(c for t in catalog.tables if t.name == table for c in t.columns if c.name == column).declared_type = declared
    service = MappingReviewService()
    draft = service.create_draft(RuleBasedSchemaAligner().align(catalog, domain.at_version(2)))
    for name, (table, column) in FIELD_MAP.items():
        if name != YEAR:
            draft = service.remap(draft, name, table=table, column=column, reviewer="test", reason="test")
    draft = service.ignore(draft, "Course.target_grade", reviewer="test", reason="not stored")
    plan, blockers = plan_publish(
        SimpleNamespace(catalog=catalog), draft, version=2, published_by="test", read_values=lambda *_: [],
    )
    assert blockers == [] and plan is not None
    assert YEAR not in plan.mapping.fields
    assert YEAR in plan.unsupported  # historical v2 coverage remains valid
