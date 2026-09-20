"""Synthetic tests for the explicit clarification gate (Phase 6-03).

Every test uses small synthetic ontology/mapping fixtures only; no
models, executors, databases, network, recorded failures, or evaluation
scores. The gate never calls a model or executor, so call-count
assertions below observe that no such collaborator exists on the path.
Each test runs a single request (denominator 1) unless the name states
otherwise.
"""

from __future__ import annotations

import json
import warnings

import pytest

from backend.adaptive.harness import (
    CLARIFICATION_CONTRACT_VERSION,
    DataType,
    FieldMapping,
    HarnessRequest,
    MappingProfile,
    MappingStatus,
    Ontology,
    OntologyField,
    Operator,
    database_id_fingerprint,
    request_id_fingerprint,
)
from backend.adaptive.harness.clarification import (
    AmbiguityOption,
    AmbiguitySignal,
    ClarificationChoice,
    ClarificationError,
    ClarificationErrorCode,
    ClarificationResult,
    ClarificationStatus,
    PendingClarification,
    ontology_fingerprint,
    option_id_fingerprint,
    prepare as prepare_clarification,
    resolve as resolve_clarification,
)
from backend.adaptive.harness.recorded import (
    mapping_fingerprint,
    question_fingerprint,
)


QUESTION = "SYNTH_Q_CLARIFY_7f3a"
OTHER_QUESTION = "SYNTH_Q_CLARIFY_9c1e"
PHYSICAL_TABLE = "courses_synth_q7f3a"
PHYSICAL_COLUMN = "subject_name_synth_q7f3a"
MALICIOUS = "MALSTR_Q7f3a_<script>'\"; DROP"
LOCAL_PATH = "C:\\synth_q7f3a\\private\\notes.txt"
PATH_LIKE_REQUEST_ID = "..\\..\\synth_q7f3a\\secret\\request-id"
SECRET_LIKE_REQUEST_ID = "AKIAIOSFODNN7_SYNTH_Q7F3A_SECRET"
PATH_LIKE_DATABASE_ID = "..\\synth_q7f3a\\secret-db\\..\\private"
SECRET_LIKE_DATABASE_ID = "synth_q7f3a_db_secret_token_9f2c"


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
    values = {"request_id": "req-synth-001", "database_id": "synth-db", "question": QUESTION}
    values.update(overrides)
    return HarnessRequest(**values)


def _signal() -> AmbiguitySignal:
    return AmbiguitySignal(
        options=[
            AmbiguityOption(option_id="opt-course", semantic_ref="Course.name"),
            AmbiguityOption(option_id="opt-instructor", semantic_ref="Instructor.name"),
        ]
    )


def _pending(**overrides) -> PendingClarification:
    request = overrides.pop("request", _request())
    ontology = overrides.pop("ontology", _ontology())
    mapping = overrides.pop("mapping", _mapping())
    signal = overrides.pop("signal", _signal())
    pending = prepare_clarification(request, ontology, mapping, signal)
    assert pending is not None
    return pending


def _resolve(request, ontology, mapping, pending, signal, choice):
    return resolve_clarification(request, ontology, mapping, pending, signal, choice)


def test_no_signal_creates_no_pending_and_blocks_nothing():
    assert prepare_clarification(_request(), _ontology(), _mapping(), None) is None


def test_signal_with_two_options_creates_pending_and_confirms_one():
    pending = prepare_clarification(_request(), _ontology(), _mapping(), _signal())
    assert pending is not None
    assert pending.clarification_version == CLARIFICATION_CONTRACT_VERSION
    assert pending.option_ids == sorted(
        [option_id_fingerprint("opt-course"), option_id_fingerprint("opt-instructor")]
    )
    assert pending.request_id_sha256 == request_id_fingerprint("req-synth-001")
    assert pending.database_id_sha256 == database_id_fingerprint("synth-db")
    result = _resolve(
        _request(),
        _ontology(),
        _mapping(),
        pending,
        _signal(),
        ClarificationChoice(selection=["opt-course"]),
    )
    assert result.status is ClarificationStatus.CONFIRMED
    assert result.selected_option_id == option_id_fingerprint("opt-course")
    assert result.request_id_sha256 == request_id_fingerprint("req-synth-001")
    assert result.database_id_sha256 == database_id_fingerprint("synth-db")


def test_cancel_is_separate_state_without_default_choice():
    pending = _pending()
    result = _resolve(
        _request(), _ontology(), _mapping(), pending, _signal(), ClarificationChoice(cancelled=True)
    )
    assert result.status is ClarificationStatus.CANCELLED
    assert result.selected_option_id is None


def test_question_change_rejects_previous_pending():
    pending = _pending()
    with pytest.raises(ClarificationError) as exc_info:
        _resolve(
            _request(question=OTHER_QUESTION),
            _ontology(),
            _mapping(),
            pending,
            _signal(),
            ClarificationChoice(selection=["opt-course"]),
        )
    assert exc_info.value.code == ClarificationErrorCode.CONTEXT_MISMATCH.value


def test_request_id_change_rejects_previous_pending():
    pending = _pending()
    with pytest.raises(ClarificationError) as exc_info:
        _resolve(
            _request(request_id="req-synth-002"),
            _ontology(),
            _mapping(),
            pending,
            _signal(),
            ClarificationChoice(selection=["opt-course"]),
        )
    assert exc_info.value.code == ClarificationErrorCode.CONTEXT_MISMATCH.value


def test_ontology_change_rejects_previous_pending():
    pending = _pending()
    ontology = _ontology()
    ontology.fields["Course.extra"] = OntologyField(
        name="Course.extra",
        data_type=DataType.STRING,
        allowed_operators={Operator.EQUALS},
    )
    with pytest.raises(ClarificationError) as exc_info:
        _resolve(
            _request(),
            ontology,
            _mapping(),
            pending,
            _signal(),
            ClarificationChoice(selection=["opt-course"]),
        )
    assert exc_info.value.code == ClarificationErrorCode.CONTEXT_MISMATCH.value


def test_mapping_change_rejects_previous_pending():
    pending = _pending()
    mapping = _mapping(version=2)
    with pytest.raises(ClarificationError) as exc_info:
        _resolve(
            _request(),
            _ontology(),
            mapping,
            pending,
            _signal(),
            ClarificationChoice(selection=["opt-course"]),
        )
    assert exc_info.value.code == ClarificationErrorCode.CONTEXT_MISMATCH.value


def test_identical_inputs_share_fingerprints_and_set_order_is_free():
    first = _pending()
    ontology = Ontology(
        entities={"Instructor", "Course"},
        fields={
            "Instructor.name": OntologyField(
                name="Instructor.name",
                data_type=DataType.STRING,
                allowed_operators={Operator.EQUALS},
            ),
            "Course.credits": OntologyField(
                name="Course.credits",
                data_type=DataType.NUMBER,
                allowed_operators={Operator.EQUALS},
            ),
            "Course.name": OntologyField(
                name="Course.name",
                data_type=DataType.STRING,
                allowed_operators={Operator.CONTAINS, Operator.EQUALS},
            ),
        },
    )
    reordered_signal = AmbiguitySignal(
        options=[
            AmbiguityOption(option_id="opt-instructor", semantic_ref="Instructor.name"),
            AmbiguityOption(option_id="opt-course", semantic_ref="Course.name"),
        ]
    )
    second = prepare_clarification(_request(), ontology, _mapping(), reordered_signal)
    assert second is not None
    assert second.question_sha256 == first.question_sha256 == question_fingerprint(QUESTION)
    assert second.ontology_fingerprint == first.ontology_fingerprint
    assert second.mapping_sha256 == first.mapping_sha256
    assert second.option_ids == first.option_ids
    assert ontology_fingerprint(ontology) == ontology_fingerprint(_ontology())


def test_added_pending_option_is_rejected_against_trusted_signal():
    pending = _pending()
    tampered = pending.model_copy(
        update={
            "options": [
                *pending.model_dump(mode="json")["options"],
                {"option_id": "opt-extra", "semantic_ref": "Course.credits"},
            ]
        }
    )
    with pytest.raises(ClarificationError) as exc_info:
        _resolve(
            _request(),
            _ontology(),
            _mapping(),
            tampered,
            _signal(),
            ClarificationChoice(selection=["opt-extra"]),
        )
    assert exc_info.value.code == ClarificationErrorCode.CONTEXT_MISMATCH.value


def test_deleted_pending_option_is_rejected_against_trusted_signal():
    pending = _pending()
    raw = pending.model_dump(mode="json")
    cut = pending.model_copy(update={"options": [raw["options"][0], raw["options"][0]]})
    with pytest.raises(ClarificationError) as exc_info:
        _resolve(
            _request(),
            _ontology(),
            _mapping(),
            cut,
            _signal(),
            ClarificationChoice(selection=["opt-course"]),
        )
    assert exc_info.value.code == ClarificationErrorCode.CONTEXT_MISMATCH.value


def test_altered_pending_option_id_is_rejected_against_trusted_signal():
    pending = _pending()
    raw = pending.model_dump(mode="json")
    altered_options = [dict(raw["options"][0]), dict(raw["options"][1])]
    altered_options[0]["option_id"] = "opt-changed"
    altered = pending.model_copy(update={"options": altered_options})
    with pytest.raises(ClarificationError) as exc_info:
        _resolve(
            _request(),
            _ontology(),
            _mapping(),
            altered,
            _signal(),
            ClarificationChoice(selection=["opt-changed"]),
        )
    assert exc_info.value.code == ClarificationErrorCode.CONTEXT_MISMATCH.value


def test_mismatched_semantic_ref_is_rejected_against_trusted_signal():
    pending = _pending()
    raw = pending.model_dump(mode="json")
    altered_options = [dict(raw["options"][0]), dict(raw["options"][1])]
    altered_options[1]["semantic_ref"] = "Course.credits"
    altered = pending.model_copy(update={"options": altered_options})
    with pytest.raises(ClarificationError) as exc_info:
        _resolve(
            _request(),
            _ontology(),
            _mapping(),
            altered,
            _signal(),
            ClarificationChoice(selection=["opt-instructor"]),
        )
    assert exc_info.value.code == ClarificationErrorCode.CONTEXT_MISMATCH.value


def test_resolve_without_matching_trusted_signal_is_rejected():
    pending = _pending()
    other_signal = AmbiguitySignal(
        options=[
            AmbiguityOption(option_id="opt-course", semantic_ref="Course.name"),
            AmbiguityOption(option_id="opt-credits", semantic_ref="Course.credits"),
        ]
    )
    with pytest.raises(ClarificationError) as exc_info:
        _resolve(
            _request(),
            _ontology(),
            _mapping(),
            pending,
            other_signal,
            ClarificationChoice(selection=["opt-course"]),
        )
    assert exc_info.value.code == ClarificationErrorCode.CONTEXT_MISMATCH.value


@pytest.mark.parametrize("count", [0, 1])
def test_zero_or_single_candidate_is_rejected(count: int):
    options = [
        AmbiguityOption(option_id="opt-course", semantic_ref="Course.name"),
        AmbiguityOption(option_id="opt-instructor", semantic_ref="Instructor.name"),
    ][:count]
    with pytest.raises(ClarificationError) as exc_info:
        prepare_clarification(
            _request(), _ontology(), _mapping(), {"options": [o.model_dump() for o in options]}
        )
    assert exc_info.value.code == ClarificationErrorCode.INVALID_SIGNAL.value


def test_duplicate_option_ids_are_rejected():
    with pytest.raises(ClarificationError) as exc_info:
        prepare_clarification(
            _request(),
            _ontology(),
            _mapping(),
            {
                "options": [
                    {"option_id": "opt-dup", "semantic_ref": "Course.name"},
                    {"option_id": "opt-dup", "semantic_ref": "Instructor.name"},
                ]
            },
        )
    assert exc_info.value.code == ClarificationErrorCode.INVALID_SIGNAL.value


def test_unknown_semantic_field_is_rejected():
    with pytest.raises(ClarificationError) as exc_info:
        prepare_clarification(
            _request(),
            _ontology(),
            _mapping(),
            {
                "options": [
                    {"option_id": "opt-a", "semantic_ref": "Course.name"},
                    {"option_id": "opt-b", "semantic_ref": "Course.unknown_field"},
                ]
            },
        )
    assert exc_info.value.code == ClarificationErrorCode.INVALID_SIGNAL.value


def test_unknown_semantic_entity_is_rejected():
    with pytest.raises(ClarificationError) as exc_info:
        prepare_clarification(
            _request(),
            _ontology(),
            _mapping(),
            {
                "options": [
                    {"option_id": "opt-a", "semantic_ref": "Course.name"},
                    {"option_id": "opt-b", "semantic_ref": "Building"},
                ]
            },
        )
    assert exc_info.value.code == ClarificationErrorCode.INVALID_SIGNAL.value


def test_unpublished_mapping_is_rejected():
    with pytest.raises(ClarificationError) as exc_info:
        prepare_clarification(
            _request(), _ontology(), _mapping(status=MappingStatus.DRAFT), _signal()
        )
    assert exc_info.value.code == ClarificationErrorCode.INVALID_SIGNAL.value


def test_physical_name_injection_is_rejected():
    with pytest.raises(ClarificationError) as exc_info:
        prepare_clarification(
            _request(),
            _ontology(),
            _mapping(),
            {
                "options": [
                    {"option_id": "opt-a", "semantic_ref": "Course.name"},
                    {"option_id": "opt-b", "semantic_ref": PHYSICAL_TABLE},
                ]
            },
        )
    assert exc_info.value.code == ClarificationErrorCode.INVALID_SIGNAL.value


def test_unknown_option_choice_is_rejected():
    pending = _pending()
    with pytest.raises(ClarificationError) as exc_info:
        _resolve(
            _request(),
            _ontology(),
            _mapping(),
            pending,
            _signal(),
            ClarificationChoice(selection=["opt-elsewhere"]),
        )
    assert exc_info.value.code == ClarificationErrorCode.INVALID_CHOICE.value


def test_multiple_and_empty_and_free_text_answers_are_rejected():
    pending = _pending()
    for selection in ([], ["opt-course", "opt-instructor"], ["free text answer"]):
        with pytest.raises(ClarificationError) as exc_info:
            _resolve(
                _request(),
                _ontology(),
                _mapping(),
                pending,
                _signal(),
                ClarificationChoice(selection=selection),
            )
        assert exc_info.value.code == ClarificationErrorCode.INVALID_CHOICE.value


def test_cancel_with_selection_is_rejected():
    pending = _pending()
    with pytest.raises(ClarificationError) as exc_info:
        _resolve(
            _request(),
            _ontology(),
            _mapping(),
            pending,
            _signal(),
            ClarificationChoice(selection=["opt-course"], cancelled=True),
        )
    assert exc_info.value.code == ClarificationErrorCode.INVALID_CHOICE.value


def test_invalid_request_and_model_review_do_not_create_a_signal():
    assert prepare_clarification(_request(), _ontology(), _mapping(), None) is None
    with pytest.raises(ClarificationError) as exc_info:
        prepare_clarification(
            {"request_id": "req-synth-001", "database_id": "synth-db", "question": "   "},
            _ontology(),
            _mapping(),
            _signal(),
        )
    assert exc_info.value.code == ClarificationErrorCode.INVALID_SIGNAL.value


def test_no_auto_signal_from_empty_or_failed_execution_inputs():
    # The gate only acts on an explicit caller signal. Absent a signal,
    # empty results, invalid requests, or model failures create nothing.
    assert prepare_clarification(_request(), _ontology(), _mapping(), None) is None
    pending = prepare_clarification(_request(), _ontology(), _mapping(), _signal())
    assert pending is not None
    # A pending gate alone never executes: resolving requires the same
    # explicit choice, and a missing choice is not a confirmation.
    with pytest.raises(ClarificationError):
        _resolve(
            _request(),
            _ontology(),
            _mapping(),
            pending,
            _signal(),
            ClarificationChoice(selection=[]),
        )


def test_path_like_and_secret_like_ids_are_never_stored_raw(caplog):
    request = _request(
        request_id=PATH_LIKE_REQUEST_ID,
        database_id=PATH_LIKE_DATABASE_ID,
        question=f"{QUESTION} {MALICIOUS} {LOCAL_PATH}",
    )
    mapping = _mapping(database_id=PATH_LIKE_DATABASE_ID)
    pending = prepare_clarification(request, _ontology(), mapping, _signal())
    assert pending is not None
    assert pending.request_id_sha256 == request_id_fingerprint(PATH_LIKE_REQUEST_ID)
    assert pending.database_id_sha256 == database_id_fingerprint(PATH_LIKE_DATABASE_ID)
    blob = pending.model_dump_json() + json.dumps(pending.model_dump(mode="json"), sort_keys=True)
    result = _resolve(
        request,
        _ontology(),
        mapping,
        pending,
        _signal(),
        ClarificationChoice(selection=["opt-course"]),
    )
    assert isinstance(result, ClarificationResult)
    result_blob = result.model_dump_json()
    secrets = (
        QUESTION,
        PHYSICAL_TABLE,
        PHYSICAL_COLUMN,
        MALICIOUS,
        LOCAL_PATH,
        PATH_LIKE_REQUEST_ID,
        PATH_LIKE_DATABASE_ID,
        SECRET_LIKE_REQUEST_ID,
        SECRET_LIKE_DATABASE_ID,
    )
    for secret in secrets:
        assert secret not in blob
        assert secret not in result_blob

    other = _request(request_id=SECRET_LIKE_REQUEST_ID, database_id=SECRET_LIKE_DATABASE_ID)
    with pytest.raises(ClarificationError) as exc_info:
        _resolve(
            other,
            _ontology(),
            _mapping(database_id=SECRET_LIKE_DATABASE_ID),
            pending,
            _signal(),
            ClarificationChoice(selection=[MALICIOUS]),
        )
    assert exc_info.value.code == ClarificationErrorCode.CONTEXT_MISMATCH.value
    assert str(exc_info.value) == ClarificationErrorCode.CONTEXT_MISMATCH.value
    for secret in secrets:
        assert secret not in str(exc_info.value)
    for record in caplog.records:
        message = record.getMessage()
        for secret in secrets:
            assert secret not in message


def test_caller_option_id_is_opaque_in_pending_and_result():
    raw_id = "SYNTHETICSECRET123"
    signal = {
        "options": [
            {"option_id": raw_id, "semantic_ref": "Course.name"},
            {"option_id": "other", "semantic_ref": "Instructor.name"},
        ]
    }
    pending = prepare_clarification(_request(), _ontology(), _mapping(), signal)
    assert pending is not None
    result = _resolve(
        _request(), _ontology(), _mapping(), pending, signal,
        ClarificationChoice(selection=[raw_id]),
    )
    assert raw_id not in pending.model_dump_json()
    assert raw_id not in result.model_dump_json()
    assert result.selected_option_id == option_id_fingerprint(raw_id)


def test_v1_pending_is_rejected_after_option_id_contract_change():
    old = _pending().model_copy(update={"clarification_version": "clarification-gate-v1"})
    with pytest.raises(ClarificationError) as exc_info:
        _resolve(
            _request(), _ontology(), _mapping(), old, _signal(),
            ClarificationChoice(selection=["opt-course"]),
        )
    assert exc_info.value.code == ClarificationErrorCode.CONTEXT_MISMATCH.value


def test_pending_result_errors_expose_no_raw_content(caplog):
    secrets = (QUESTION, PHYSICAL_TABLE, PHYSICAL_COLUMN, MALICIOUS, LOCAL_PATH)
    request = _request(question=f"{QUESTION} {MALICIOUS} {LOCAL_PATH}")
    pending = prepare_clarification(request, _ontology(), _mapping(), _signal())
    assert pending is not None
    blob = pending.model_dump_json() + json.dumps(pending.model_dump(mode="json"), sort_keys=True)
    for secret in secrets:
        assert secret not in blob

    result = _resolve(
        request,
        _ontology(),
        _mapping(),
        pending,
        _signal(),
        ClarificationChoice(selection=["opt-course"]),
    )
    assert isinstance(result, ClarificationResult)
    result_blob = result.model_dump_json()
    for secret in secrets:
        assert secret not in result_blob

    with pytest.raises(ClarificationError) as exc_info:
        _resolve(
            request,
            _ontology(),
            _mapping(),
            pending,
            _signal(),
            ClarificationChoice(selection=[MALICIOUS]),
        )
    assert exc_info.value.code == ClarificationErrorCode.INVALID_CHOICE.value
    assert str(exc_info.value) == ClarificationErrorCode.INVALID_CHOICE.value
    for secret in secrets:
        assert secret not in str(exc_info.value)
    for record in caplog.records:
        message = record.getMessage()
        for secret in secrets:
            assert secret not in message


def test_gate_path_uses_no_model_executor_or_database_calls(capsys):
    class CountingModel:
        def __init__(self) -> None:
            self.calls = 0

        def generate_ir(self, request, ontology, mapping):
            self.calls += 1
            raise AssertionError("gate must not call a model")

    class CountingExecutor:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, database_id, compiled):
            self.calls += 1
            raise AssertionError("gate must not execute")

    model, executor = CountingModel(), CountingExecutor()
    pending = prepare_clarification(_request(), _ontology(), _mapping(), _signal())
    assert pending is not None
    _resolve(
        _request(),
        _ontology(),
        _mapping(),
        pending,
        _signal(),
        ClarificationChoice(selection=["opt-course"]),
    )
    assert model.calls == 0
    assert executor.calls == 0
    captured = capsys.readouterr()
    for secret in (QUESTION, PHYSICAL_TABLE, PHYSICAL_COLUMN, LOCAL_PATH):
        assert secret not in captured.out
        assert secret not in captured.err


def test_mapping_fingerprint_matches_recorded_contract():
    assert mapping_fingerprint(_mapping()) == mapping_fingerprint(_mapping())


def test_forged_signal_with_single_option_is_rejected():
    forged = _signal().model_copy(
        update={"options": [{"option_id": "opt-only", "semantic_ref": "Course.name"}]}
    )
    with pytest.raises(ClarificationError) as exc_info:
        prepare_clarification(_request(), _ontology(), _mapping(), forged)
    assert exc_info.value.code == ClarificationErrorCode.INVALID_SIGNAL.value


def test_constructed_signal_without_validation_is_rejected():
    forged = AmbiguitySignal.model_construct(options=[])
    with pytest.raises(ClarificationError) as exc_info:
        prepare_clarification(_request(), _ontology(), _mapping(), forged)
    assert exc_info.value.code == ClarificationErrorCode.INVALID_SIGNAL.value


def test_forged_pending_is_rejected_at_resolve():
    pending = _pending()
    forged = pending.model_copy(update={"question_sha256": "0" * 64})
    with pytest.raises(ClarificationError) as exc_info:
        _resolve(
            _request(),
            _ontology(),
            _mapping(),
            forged,
            _signal(),
            ClarificationChoice(selection=["opt-course"]),
        )
    assert exc_info.value.code == ClarificationErrorCode.CONTEXT_MISMATCH.value


def test_constructed_pending_without_validation_is_rejected():
    forged = PendingClarification.model_construct(
        clarification_version=CLARIFICATION_CONTRACT_VERSION,
        request_id_sha256="0" * 64,
        database_id_sha256="0" * 64,
        question_sha256="0" * 64,
        ontology_fingerprint="0" * 64,
        mapping_sha256="0" * 64,
        mapping_version=1,
        options=[],
    )
    with pytest.raises(ClarificationError) as exc_info:
        _resolve(
            _request(),
            _ontology(),
            _mapping(),
            forged,
            _signal(),
            ClarificationChoice(selection=["opt-course"]),
        )
    assert exc_info.value.code == ClarificationErrorCode.CONTEXT_MISMATCH.value


def test_forged_choice_with_bad_selection_is_rejected():
    pending = _pending()
    forged = ClarificationChoice.model_construct(selection=LOCAL_PATH, cancelled=False)
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        with pytest.raises(ClarificationError) as exc_info:
            _resolve(_request(), _ontology(), _mapping(), pending, _signal(), forged)
    assert exc_info.value.code == ClarificationErrorCode.INVALID_CHOICE.value
    assert not emitted


def test_forged_request_mapping_ontology_are_rejected():
    pending = _pending()
    forged_request = _request().model_copy(update={"question": "   "})
    with pytest.raises(ClarificationError) as exc_info:
        _resolve(
            forged_request,
            _ontology(),
            _mapping(),
            pending,
            _signal(),
            ClarificationChoice(selection=["opt-course"]),
        )
    assert exc_info.value.code == ClarificationErrorCode.CONTEXT_MISMATCH.value

    forged_mapping = _mapping().model_copy(update={"status": "DRAFT"})
    with pytest.raises(ClarificationError) as exc_info:
        _resolve(
            _request(),
            _ontology(),
            forged_mapping,
            pending,
            _signal(),
            ClarificationChoice(selection=["opt-course"]),
        )
    assert exc_info.value.code == ClarificationErrorCode.CONTEXT_MISMATCH.value

    forged_ontology = _ontology().model_copy(update={"entities": set()})
    with pytest.raises(ClarificationError) as exc_info:
        _resolve(
            _request(),
            forged_ontology,
            _mapping(),
            pending,
            _signal(),
            ClarificationChoice(selection=["opt-course"]),
        )
    assert exc_info.value.code == ClarificationErrorCode.CONTEXT_MISMATCH.value
