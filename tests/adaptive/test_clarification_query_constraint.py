"""Synthetic tests for clarification-query-constraint-v1 (Phase 6-09).

Small synthetic ontology/mapping/ruleset fixtures only; no DB, network,
executors, compilers, repair, queues, snapshots, or UI sessions. The
coordinator never calls them.
"""

from __future__ import annotations

import copy
import warnings

import pytest

from backend.adaptive.harness import (
    DataType,
    FieldMapping,
    HarnessRequest,
    MappingProfile,
    MappingStatus,
    Ontology,
    OntologyField,
    Operator,
)
from backend.adaptive.harness.ambiguity_detector import (
    AMBIGUITY_DETECTOR_VERSION,
    AmbiguityDetectorError,
    AmbiguityDetectorErrorCode,
    DetectorRuleSet,
    detect_ambiguity,
    prepare_detected_clarification,
)
from backend.adaptive.harness.clarification import (
    ClarificationChoice,
    ClarificationError,
    ClarificationErrorCode,
    PendingClarification,
)
from backend.adaptive.harness.clarification_query_constraint import (
    CLARIFICATION_QUERY_CONSTRAINT_VERSION,
    ClarificationQueryConstraint,
    ClarificationQueryConstraintError,
    ClarificationQueryConstraintErrorCode,
    ConstrainedGenerationResult,
    run_constrained_generation,
)


QUESTION_BASE = "SYNTH_Q_CQC9_b2d7"
PHYSICAL_TABLE = "courses_synth_b2d7"
PHYSICAL_COLUMN = "subject_name_synth_b2d7"
MALICIOUS = "MALSTR_B2d7_<script>'\"; DROP"
LOCAL_PATH = "C:\\synth_b2d7\\private\\notes.txt"
PATH_LIKE_REQUEST_ID = "..\\..\\synth_b2d7\\secret\\request-id"
SECRET_LIKE_REQUEST_ID = "AKIAIOSFODNN7_SYNTH_B2D7_SECRET"
PATH_LIKE_DATABASE_ID = "..\\synth_b2d7\\secret-db\\..\\private"
SECRET_LIKE_DATABASE_ID = "synth_b2d7_db_secret_token_71ac"


def _ontology() -> Ontology:
    return Ontology(
        entities={"Course", "Instructor"},
        fields={
            "Course.name": OntologyField(
                name="Course.name",
                data_type=DataType.STRING,
                allowed_operators={Operator.EQUALS, Operator.CONTAINS},
            ),
            "Course.credits": OntologyField(
                name="Course.credits",
                data_type=DataType.NUMBER,
                allowed_operators={Operator.EQUALS},
            ),
            "Instructor.name": OntologyField(
                name="Instructor.name",
                data_type=DataType.STRING,
                allowed_operators={Operator.EQUALS},
            ),
        },
    )


def _mapping(**overrides) -> MappingProfile:
    fields = overrides.pop("fields", None)
    if fields is None:
        fields = {
            "Course.name": FieldMapping(table=PHYSICAL_TABLE, column=PHYSICAL_COLUMN),
            "Course.credits": FieldMapping(table=PHYSICAL_TABLE, column="credit_hours_synth"),
            "Instructor.name": FieldMapping(table="instructors_synth", column="name_synth"),
        }
    return MappingProfile(
        database_id=overrides.pop("database_id", "synth-db"),
        version=overrides.pop("version", 1),
        status=overrides.pop("status", MappingStatus.PUBLISHED),
        fields=fields,
        **overrides,
    )


def _request(**overrides) -> HarnessRequest:
    values = {
        "request_id": "req-synth-cqc9",
        "database_id": "synth-db",
        "question": f"{QUESTION_BASE} 성적표 보여줘",
    }
    values.update(overrides)
    return HarnessRequest(**values)


def _entity_rules() -> dict:
    return {
        "version": AMBIGUITY_DETECTOR_VERSION,
        "rules": [
            {
                "rule_id": "rule-scope",
                "triggers": ["성적표"],
                "options": [
                    {"option_key": "opt-course", "semantic_ref": "Course"},
                    {"option_key": "opt-instructor", "semantic_ref": "Instructor"},
                ],
            }
        ],
    }


def _field_rules() -> dict:
    return {
        "version": AMBIGUITY_DETECTOR_VERSION,
        "rules": [
            {
                "rule_id": "rule-field",
                "triggers": ["성적표"],
                "options": [
                    {"option_key": "opt-name", "semantic_ref": "Course.name"},
                    {"option_key": "opt-credits", "semantic_ref": "Course.credits"},
                ],
            }
        ],
    }


def _pending_and_choice(request, ontology, mapping, rules, want_ref: str):
    signal = detect_ambiguity(request, ontology, mapping, rules)
    assert signal is not None
    wanted = [o for o in signal.options if o.semantic_ref == want_ref]
    assert len(wanted) == 1
    pending = prepare_detected_clarification(request, ontology, mapping, rules)
    assert pending is not None
    choice = ClarificationChoice(selection=[wanted[0].option_id])
    return pending, choice


class CountingProvider:
    def __init__(self, payload) -> None:
        self.calls = 0
        self.payload = payload
        self.seen: list = []

    def generate_ir(self, request, ontology, mapping, constraint):
        self.calls += 1
        self.seen.append((request, ontology, mapping, constraint))
        if callable(self.payload):
            return self.payload(request, ontology, mapping, constraint)
        return copy.deepcopy(self.payload)


def test_entity_selection_promoted_and_compliant():
    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _entity_rules()
    pending, choice = _pending_and_choice(request, ontology, mapping, rules, "Course")
    provider = CountingProvider(
        {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 10}
    )
    result = run_constrained_generation(
        request, ontology, mapping, rules, pending, choice, provider
    )
    assert isinstance(result, ConstrainedGenerationResult)
    assert provider.calls == 1
    assert result.constraint.selected_semantic_ref == "Course"
    assert result.query_ir.entity == "Course"
    assert result.result_version == CLARIFICATION_QUERY_CONSTRAINT_VERSION


def test_field_selection_allows_select_usage():
    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _field_rules()
    pending, choice = _pending_and_choice(request, ontology, mapping, rules, "Course.name")
    provider = CountingProvider(
        {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 10}
    )
    result = run_constrained_generation(
        request, ontology, mapping, rules, pending, choice, provider
    )
    assert result.constraint.selected_semantic_ref == "Course.name"
    assert "Course.name" in result.query_ir.select


def test_field_selection_allows_filter_usage():
    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _field_rules()
    pending, choice = _pending_and_choice(request, ontology, mapping, rules, "Course.credits")
    provider = CountingProvider(
        {
            "entity": "Course",
            "select": ["Course.name"],
            "filters": [
                {"field": "Course.credits", "operator": "equals", "value": 3}
            ],
            "limit": 10,
        }
    )
    result = run_constrained_generation(
        request, ontology, mapping, rules, pending, choice, provider
    )
    assert result.constraint.selected_semantic_ref == "Course.credits"


def test_field_unused_is_violation():
    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _field_rules()
    pending, choice = _pending_and_choice(request, ontology, mapping, rules, "Course.credits")
    provider = CountingProvider(
        {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 10}
    )
    with pytest.raises(ClarificationQueryConstraintError) as exc_info:
        run_constrained_generation(request, ontology, mapping, rules, pending, choice, provider)
    assert exc_info.value.code == ClarificationQueryConstraintErrorCode.CONSTRAINT_VIOLATION.value
    assert provider.calls == 1


def test_other_entity_is_violation():
    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _entity_rules()
    pending, choice = _pending_and_choice(request, ontology, mapping, rules, "Course")
    provider = CountingProvider(
        {"entity": "Instructor", "select": ["Instructor.name"], "filters": [], "limit": 5}
    )
    with pytest.raises(ClarificationQueryConstraintError) as exc_info:
        run_constrained_generation(request, ontology, mapping, rules, pending, choice, provider)
    assert exc_info.value.code == ClarificationQueryConstraintErrorCode.CONSTRAINT_VIOLATION.value


def test_cancel_fails_closed_with_zero_provider_calls():
    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _field_rules()
    pending = prepare_detected_clarification(request, ontology, mapping, rules)
    assert pending is not None
    provider = CountingProvider(
        {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5}
    )
    with pytest.raises(ClarificationQueryConstraintError) as exc_info:
        run_constrained_generation(
            request, ontology, mapping, rules, pending,
            ClarificationChoice(cancelled=True), provider,
        )
    assert exc_info.value.code == ClarificationQueryConstraintErrorCode.CANCELLED.value
    assert provider.calls == 0


def test_no_match_and_multi_match_fail_closed():
    ontology, mapping = _ontology(), _mapping()
    plain = _request(question=f"{QUESTION_BASE} plain timetable")
    provider = CountingProvider(
        {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5}
    )
    with pytest.raises((AmbiguityDetectorError, ClarificationError)):
        run_constrained_generation(
            plain, ontology, mapping, _field_rules(), None,  # type: ignore[arg-type]
            ClarificationChoice(selection=["x" * 64]), provider,
        )
    assert provider.calls == 0
    both_rules = {
        "version": AMBIGUITY_DETECTOR_VERSION,
        "rules": [
            {
                "rule_id": "rule-a",
                "triggers": ["성적표"],
                "options": [
                    {"option_key": "opt-a1", "semantic_ref": "Course.name"},
                    {"option_key": "opt-a2", "semantic_ref": "Course.credits"},
                ],
            },
            {
                "rule_id": "rule-b",
                "triggers": ["보여줘"],
                "options": [
                    {"option_key": "opt-b1", "semantic_ref": "Course.name"},
                    {"option_key": "opt-b2", "semantic_ref": "Instructor.name"},
                ],
            },
        ],
    }
    single_rules = _field_rules()
    request = _request()
    pending_multi, choice_multi = _pending_and_choice(
        request, ontology, mapping, single_rules, "Course.name"
    )
    with pytest.raises(AmbiguityDetectorError) as exc_info:
        run_constrained_generation(
            request, ontology, mapping, both_rules,
            pending_multi, choice_multi, provider,
        )
    assert exc_info.value.code == AmbiguityDetectorErrorCode.CONFLICT.value
    assert provider.calls == 0


def test_context_and_ruleset_drift_fail_closed():
    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _field_rules()
    pending, choice = _pending_and_choice(request, ontology, mapping, rules, "Course.name")
    good_payload = {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5}
    drifted_rules = {
        "version": AMBIGUITY_DETECTOR_VERSION,
        "rules": [
            {
                "rule_id": "rule-field",
                "triggers": ["출석부"],
                "options": [
                    {"option_key": "opt-name", "semantic_ref": "Course.name"},
                    {"option_key": "opt-credits", "semantic_ref": "Course.credits"},
                ],
            }
        ],
    }
    for kwargs in (
        {"request_data": _request(question=f"{QUESTION_BASE} plain timetable")},
        {"rules_data": drifted_rules},
        {"pending": pending.model_copy(update={"mapping_version": 99})},
        {"choice": ClarificationChoice(selection=["0" * 64])},
        {"mapping": _mapping(version=2)},
    ):
        provider = CountingProvider(good_payload)
        call = {
            "request_data": request, "ontology": ontology, "mapping": mapping,
            "rules_data": rules, "pending": pending, "choice": choice,
            "provider": provider,
        }
        call.update(kwargs)
        with pytest.raises((AmbiguityDetectorError, ClarificationError, ClarificationQueryConstraintError)):
            run_constrained_generation(**call)
        assert provider.calls == 0


def test_provider_called_once_with_defensive_copies_and_mutation_isolation():
    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _field_rules()
    pending, choice = _pending_and_choice(request, ontology, mapping, rules, "Course.name")
    before = (
        request.model_dump(mode="json"),
        ontology.model_dump(mode="json"),
        mapping.model_dump(mode="json"),
    )

    def mutating(request_c, ontology_c, mapping_c, constraint_c):
        object.__setattr__(request_c, "question", f"{QUESTION_BASE} mutated")
        object.__setattr__(mapping_c, "version", 999)
        return {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5}

    provider = CountingProvider(mutating)
    result = run_constrained_generation(request, ontology, mapping, rules, pending, choice, provider)
    assert provider.calls == 1
    assert result.query_ir.select == ["Course.name"]
    assert (request.model_dump(mode="json"), ontology.model_dump(mode="json"), mapping.model_dump(mode="json")) == before
    seen_request, _, seen_mapping, _ = provider.seen[0]
    assert seen_request is not request
    assert seen_mapping is not mapping


def test_provider_exception_becomes_fixed_error():
    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _field_rules()
    pending, choice = _pending_and_choice(request, ontology, mapping, rules, "Course.name")

    class Failing:
        def __init__(self) -> None:
            self.calls = 0

        def generate_ir(self, request, ontology, mapping, constraint):
            self.calls += 1
            raise RuntimeError(f"boom {MALICIOUS} {LOCAL_PATH}")

    provider = Failing()
    with pytest.raises(ClarificationQueryConstraintError) as exc_info:
        run_constrained_generation(request, ontology, mapping, rules, pending, choice, provider)
    assert exc_info.value.code == ClarificationQueryConstraintErrorCode.PROVIDER_ERROR.value
    assert MALICIOUS not in str(exc_info.value)
    assert LOCAL_PATH not in str(exc_info.value)
    assert provider.calls == 1


def test_invalid_query_ir_and_semantic_failure_are_fixed_errors():
    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _field_rules()
    pending, choice = _pending_and_choice(request, ontology, mapping, rules, "Course.name")

    class CountingCompiler:
        def __init__(self) -> None:
            self.calls = 0

    class CountingExecutor:
        def __init__(self) -> None:
            self.calls = 0

    compiler, executor = CountingCompiler(), CountingExecutor()
    bad_struct = CountingProvider({"entity": "Course", "select": [], "filters": []})
    with pytest.raises(ClarificationQueryConstraintError) as exc_info:
        run_constrained_generation(request, ontology, mapping, rules, pending, choice, bad_struct)
    assert exc_info.value.code == ClarificationQueryConstraintErrorCode.INVALID_QUERY_IR.value

    unknown_field = CountingProvider(
        {"entity": "Course", "select": ["Course.unknown_field"], "filters": [], "limit": 5}
    )
    with pytest.raises(ClarificationQueryConstraintError) as exc_info:
        run_constrained_generation(request, ontology, mapping, rules, pending, choice, unknown_field)
    assert exc_info.value.code == ClarificationQueryConstraintErrorCode.SEMANTIC_INVALID.value
    assert compiler.calls == 0
    assert executor.calls == 0


@pytest.mark.parametrize(
    "reflected",
    [
        "req-synth-cqc9",
        "synth-db",
        "rule-field",
        "성적표",
        "opt-name",
        PHYSICAL_TABLE,
        PHYSICAL_COLUMN,
    ],
)
def test_known_raw_context_reflection_is_rejected(reflected: str):
    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _field_rules()
    pending, choice = _pending_and_choice(request, ontology, mapping, rules, "Course.name")
    provider = CountingProvider(
        {
            "entity": "Course",
            "select": ["Course.name"],
            "filters": [
                {"field": "Course.name", "operator": "equals", "value": reflected}
            ],
            "limit": 5,
        }
    )
    with pytest.raises(ClarificationQueryConstraintError) as exc_info:
        run_constrained_generation(request, ontology, mapping, rules, pending, choice, provider)
    assert exc_info.value.code == ClarificationQueryConstraintErrorCode.INVALID_QUERY_IR.value


def test_physical_name_overlap_does_not_overblock_semantic_names():
    request, ontology, rules = _request(), _ontology(), _field_rules()
    mapping = _mapping(
        fields={
            "Course.name": FieldMapping(table="Course", column="name"),
            "Course.credits": FieldMapping(table="Course", column="credits"),
            "Instructor.name": FieldMapping(table="Instructor", column="name"),
        }
    )
    pending, choice = _pending_and_choice(request, ontology, mapping, rules, "Course.name")
    provider = CountingProvider(
        {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5}
    )
    result = run_constrained_generation(
        request, ontology, mapping, rules, pending, choice, provider
    )
    assert result.query_ir.select == ["Course.name"]


def test_raw_content_absent_from_contract_result_error_and_streams(caplog, capsys):
    request = _request(
        request_id=PATH_LIKE_REQUEST_ID,
        database_id=PATH_LIKE_DATABASE_ID,
        question=f"{QUESTION_BASE} 성적표 {MALICIOUS} {LOCAL_PATH}",
    )
    mapping = _mapping(database_id=PATH_LIKE_DATABASE_ID)
    ontology = _ontology()
    rules = _field_rules()
    pending, choice = _pending_and_choice(request, ontology, mapping, rules, "Course.name")
    provider = CountingProvider(
        {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5}
    )
    result = run_constrained_generation(request, ontology, mapping, rules, pending, choice, provider)
    blob = result.model_dump_json() + result.constraint.model_dump_json()
    bad = CountingProvider(
        {"entity": "Instructor", "select": ["Instructor.name"], "filters": [], "limit": 5}
    )
    with pytest.raises(ClarificationQueryConstraintError) as exc_info:
        run_constrained_generation(request, ontology, mapping, rules, pending, choice, bad)
    error_blob = str(exc_info.value)
    out, err = capsys.readouterr()
    log_blob = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (
        PATH_LIKE_REQUEST_ID,
        PATH_LIKE_DATABASE_ID,
        SECRET_LIKE_REQUEST_ID,
        SECRET_LIKE_DATABASE_ID,
        PHYSICAL_TABLE,
        PHYSICAL_COLUMN,
        MALICIOUS,
        LOCAL_PATH,
        QUESTION_BASE,
    ):
        assert secret not in blob
        assert secret not in error_blob
        assert secret not in out
        assert secret not in err
        assert secret not in log_blob
    assert "opt-name" not in blob
    assert "rule-field" not in blob


def test_forged_inputs_rejected_without_warnings():
    request, ontology, mapping, rules = _request(), _ontology(), _mapping(), _field_rules()
    pending, choice = _pending_and_choice(request, ontology, mapping, rules, "Course.name")
    good = {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 5}
    forged_request = request.model_copy(update={"question": "   "})
    forged_mapping = mapping.model_copy(update={"status": "DRAFT"})
    forged_ontology = ontology.model_copy(update={"fields": {}})
    forged_rules = DetectorRuleSet.model_validate(rules).model_copy(update={"rules": []})
    forged_pending = pending.model_copy(update={"question_sha256": "0" * 64})
    forged_choice = ClarificationChoice.model_construct(selection=LOCAL_PATH, cancelled=False)
    constructed_rules = DetectorRuleSet.model_construct(version=AMBIGUITY_DETECTOR_VERSION, rules=[])
    forged_constraint_ir = ClarificationQueryConstraint.model_construct(
        constraint_version=CLARIFICATION_QUERY_CONSTRAINT_VERSION,
        request_id_sha256="0" * 64,
        database_id_sha256="0" * 64,
        ontology_fingerprint="0" * 64,
        mapping_sha256="0" * 64,
        selected_semantic_ref="Course.name",
    )
    assert forged_constraint_ir is not None
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        for kwargs in (
            {"request_data": forged_request},
            {"ontology": forged_ontology},
            {"mapping": forged_mapping},
            {"rules_data": forged_rules},
            {"rules_data": constructed_rules},
            {"pending": forged_pending},
            {"choice": forged_choice},
            {"provider": object()},
        ):
            provider = CountingProvider(good)
            call = {
                "request_data": request, "ontology": ontology, "mapping": mapping,
                "rules_data": rules, "pending": pending, "choice": choice,
                "provider": provider,
            }
            call.update(kwargs)
            with pytest.raises(
                (AmbiguityDetectorError, ClarificationError, ClarificationQueryConstraintError)
            ):
                run_constrained_generation(**call)
            assert provider.calls == 0
    assert not emitted
