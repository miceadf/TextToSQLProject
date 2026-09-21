"""Synthetic tests for versioned ambiguity detection (Phase 6-08).

Small synthetic ontology/mapping/rule fixtures only; no models, executors,
databases, network, QueryIR, harness runs, or UI sessions. The detector never
calls a model or executor.
"""

from __future__ import annotations

import hashlib
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
    resolve_detected_clarification,
)
from backend.adaptive.harness.clarification import (
    AmbiguitySignal,
    ClarificationChoice,
    ClarificationError,
    ClarificationErrorCode,
    ClarificationStatus,
    PendingClarification,
)


QUESTION_BASE = "SYNTH_Q_AMB8_a1c4"
PHYSICAL_TABLE = "courses_synth_a1c4"
PHYSICAL_COLUMN = "subject_name_synth_a1c4"
MALICIOUS = "MALSTR_A1c4_<script>'\"; DROP"
LOCAL_PATH = "C:\\synth_a1c4\\private\\notes.txt"
PATH_LIKE_REQUEST_ID = "..\\..\\synth_a1c4\\secret\\request-id"
SECRET_LIKE_REQUEST_ID = "AKIAIOSFODNN7_SYNTH_A1C4_SECRET"
PATH_LIKE_DATABASE_ID = "..\\synth_a1c4\\secret-db\\..\\private"
SECRET_LIKE_DATABASE_ID = "synth_a1c4_db_secret_token_4e9d"


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
        "request_id": "req-synth-amb8",
        "database_id": "synth-db",
        "question": f"{QUESTION_BASE} show me the 성적표 please",
    }
    values.update(overrides)
    return HarnessRequest(**values)


def _rules(**overrides) -> dict:
    rules = overrides.pop("rules", None)
    if rules is None:
        rules = [
            {
                "rule_id": "rule-grades",
                "triggers": ["성적표"],
                "options": [
                    {"option_key": "opt-course", "semantic_ref": "Course.name"},
                    {"option_key": "opt-instructor", "semantic_ref": "Instructor.name"},
                ],
            }
        ]
    return {"version": AMBIGUITY_DETECTOR_VERSION, **overrides, "rules": rules}


def _two_rule_set() -> dict:
    return _rules(
        rules=[
            {
                "rule_id": "rule-a",
                "triggers": ["성적표"],
                "options": [
                    {"option_key": "opt-a1", "semantic_ref": "Course.name"},
                    {"option_key": "opt-a2", "semantic_ref": "Instructor.name"},
                ],
            },
            {
                "rule_id": "rule-b",
                "triggers": ["출석부"],
                "options": [
                    {"option_key": "opt-b1", "semantic_ref": "Course.name"},
                    {"option_key": "opt-b2", "semantic_ref": "Course.credits"},
                ],
            },
        ]
    )


def _first_choice(rules: dict | None = None) -> ClarificationChoice:
    """Build a valid choice from the detector's opaque derived option IDs."""
    signal = detect_ambiguity(_request(), _ontology(), _mapping(), rules or _rules())
    assert signal is not None
    return ClarificationChoice(selection=[signal.options[0].option_id])


def test_no_match_returns_none_without_side_effects():
    request = _request(question=f"{QUESTION_BASE} plain timetable query")
    assert detect_ambiguity(request, _ontology(), _mapping(), _rules()) is None
    assert prepare_detected_clarification(request, _ontology(), _mapping(), _rules()) is None


def test_single_match_creates_pending_and_confirms_one():
    request = _request()
    signal = detect_ambiguity(request, _ontology(), _mapping(), _rules())
    assert isinstance(signal, AmbiguitySignal)
    for option in signal.options:
        assert len(option.option_id) == 64
    pending = prepare_detected_clarification(request, _ontology(), _mapping(), _rules())
    assert pending is not None
    assert len(pending.option_ids) == 2
    # Choice carries the opaque derived option ID, never the raw option key.
    confirmed = resolve_detected_clarification(
        request,
        _ontology(),
        _mapping(),
        _rules(),
        pending,
        ClarificationChoice(selection=[signal.options[0].option_id]),
    )
    assert confirmed.status is ClarificationStatus.CONFIRMED


def test_explicit_cancel_is_separate_state():
    pending = prepare_detected_clarification(_request(), _ontology(), _mapping(), _rules())
    assert pending is not None
    result = resolve_detected_clarification(
        _request(), _ontology(), _mapping(), _rules(), pending, ClarificationChoice(cancelled=True)
    )
    assert result.status is ClarificationStatus.CANCELLED
    assert result.selected_option_id is None


def test_rule_option_order_change_keeps_ids_and_pending():
    first = detect_ambiguity(_request(), _ontology(), _mapping(), _rules())
    reordered = _rules(
        rules=[
            {
                "rule_id": "rule-grades",
                "triggers": ["성적표"],
                "options": [
                    {"option_key": "opt-instructor", "semantic_ref": "Instructor.name"},
                    {"option_key": "opt-course", "semantic_ref": "Course.name"},
                ],
            }
        ]
    )
    second = detect_ambiguity(_request(), _ontology(), _mapping(), reordered)
    assert first is not None and second is not None
    assert sorted(o.option_id for o in first.options) == sorted(o.option_id for o in second.options)
    pending_first = prepare_detected_clarification(_request(), _ontology(), _mapping(), _rules())
    pending_second = prepare_detected_clarification(_request(), _ontology(), _mapping(), reordered)
    assert pending_first is not None and pending_second is not None
    assert pending_first.option_ids == pending_second.option_ids


def test_rule_and_trigger_order_change_keeps_ids():
    request = _request(question=f"{QUESTION_BASE} 성적표")
    rules = _two_rule_set()
    reordered = _rules(
        rules=[
            {
                "rule_id": "rule-b",
                "triggers": ["시간표", "출석부"],
                "options": [
                    {"option_key": "opt-b2", "semantic_ref": "Course.credits"},
                    {"option_key": "opt-b1", "semantic_ref": "Course.name"},
                ],
            },
            {
                "rule_id": "rule-a",
                "triggers": ["성적표"],
                "options": [
                    {"option_key": "opt-a2", "semantic_ref": "Instructor.name"},
                    {"option_key": "opt-a1", "semantic_ref": "Course.name"},
                ],
            },
        ]
    )
    rules["rules"][1]["triggers"].append("시간표")
    first = detect_ambiguity(request, _ontology(), _mapping(), rules)
    second = detect_ambiguity(request, _ontology(), _mapping(), reordered)
    assert first is not None and second is not None
    assert first == second


def test_different_meaning_gives_different_ids():
    first = detect_ambiguity(_request(), _ontology(), _mapping(), _rules())
    altered = _rules(
        rules=[
            {
                "rule_id": "rule-grades",
                "triggers": ["성적표"],
                "options": [
                    {"option_key": "opt-course", "semantic_ref": "Course.name"},
                    {"option_key": "opt-instructor", "semantic_ref": "Course.credits"},
                ],
            }
        ]
    )
    second = detect_ambiguity(_request(), _ontology(), _mapping(), altered)
    assert first is not None and second is not None
    assert {o.option_id for o in first.options} != {o.option_id for o in second.options}


def test_multi_match_conflicts_without_pending():
    request = _request(question=f"{QUESTION_BASE} 성적표 출석부 together")
    with pytest.raises(AmbiguityDetectorError) as exc_info:
        detect_ambiguity(request, _ontology(), _mapping(), _two_rule_set())
    assert exc_info.value.code == AmbiguityDetectorErrorCode.CONFLICT.value
    with pytest.raises(AmbiguityDetectorError):
        prepare_detected_clarification(request, _ontology(), _mapping(), _two_rule_set())


def test_normalization_and_literal_policy():
    # NFKC/case/whitespace variants of the trigger still match literally.
    request = _request(question=f"{QUESTION_BASE}   성적표  please")
    assert detect_ambiguity(request, _ontology(), _mapping(), _rules()) is not None
    # A question without the literal trigger does not match (no semantic inference).
    request = _request(question=f"{QUESTION_BASE} grades please")
    assert detect_ambiguity(request, _ontology(), _mapping(), _rules()) is None


@pytest.mark.parametrize("trigger", ["x", " ", "   ", "\x00\x01", "a"])
def test_short_blank_control_triggers_rejected(trigger: str):
    rules = _rules(
        rules=[
            {
                "rule_id": "rule-grades",
                "triggers": [trigger],
                "options": [
                    {"option_key": "opt-a", "semantic_ref": "Course.name"},
                    {"option_key": "opt-b", "semantic_ref": "Instructor.name"},
                ],
            }
        ]
    )
    with pytest.raises(AmbiguityDetectorError) as exc_info:
        detect_ambiguity(_request(), _ontology(), _mapping(), rules)
    assert exc_info.value.code == AmbiguityDetectorErrorCode.INVALID_RULESET.value


def test_duplicate_rule_ids_rejected():
    rules = _rules(
        rules=[
            {
                "rule_id": "rule-dup",
                "triggers": ["성적표"],
                "options": [
                    {"option_key": "opt-a1", "semantic_ref": "Course.name"},
                    {"option_key": "opt-a2", "semantic_ref": "Instructor.name"},
                ],
            },
            {
                "rule_id": "RULE-dup",
                "triggers": ["출석부"],
                "options": [
                    {"option_key": "opt-b1", "semantic_ref": "Course.name"},
                    {"option_key": "opt-b2", "semantic_ref": "Course.credits"},
                ],
            },
        ]
    )
    with pytest.raises(AmbiguityDetectorError) as exc_info:
        detect_ambiguity(_request(), _ontology(), _mapping(), rules)
    assert exc_info.value.code == AmbiguityDetectorErrorCode.INVALID_RULESET.value


def test_normalized_duplicate_trigger_rejected():
    rules = _rules(
        rules=[
            {
                "rule_id": "rule-a",
                "triggers": ["성적표"],
                "options": [
                    {"option_key": "opt-a1", "semantic_ref": "Course.name"},
                    {"option_key": "opt-a2", "semantic_ref": "Instructor.name"},
                ],
            },
            {
                "rule_id": "rule-b",
                "triggers": [" 성적표 "],
                "options": [
                    {"option_key": "opt-b1", "semantic_ref": "Course.name"},
                    {"option_key": "opt-b2", "semantic_ref": "Course.credits"},
                ],
            },
        ]
    )
    with pytest.raises(AmbiguityDetectorError) as exc_info:
        detect_ambiguity(_request(), _ontology(), _mapping(), rules)
    assert exc_info.value.code == AmbiguityDetectorErrorCode.INVALID_RULESET.value


def test_duplicate_option_key_and_semantic_ref_rejected():
    for options in (
        [
            {"option_key": "opt-dup", "semantic_ref": "Course.name"},
            {"option_key": "OPT-dup", "semantic_ref": "Instructor.name"},
        ],
        [
            {"option_key": "opt-a", "semantic_ref": "Course.name"},
            {"option_key": "opt-b", "semantic_ref": "Course.name"},
        ],
    ):
        rules = _rules(rules=[{"rule_id": "rule-x", "triggers": ["성적표"], "options": options}])
        with pytest.raises(AmbiguityDetectorError) as exc_info:
            detect_ambiguity(_request(), _ontology(), _mapping(), rules)
        assert exc_info.value.code == AmbiguityDetectorErrorCode.INVALID_RULESET.value


@pytest.mark.parametrize("semantic_ref", ["Course.unknown_field", "Building", "courses_synth_a1c4"])
def test_unknown_entity_field_or_physical_ref_rejected(semantic_ref: str):
    rules = _rules(
        rules=[
            {
                "rule_id": "rule-x",
                "triggers": ["성적표"],
                "options": [
                    {"option_key": "opt-a", "semantic_ref": "Course.name"},
                    {"option_key": "opt-b", "semantic_ref": semantic_ref},
                ],
            }
        ]
    )
    with pytest.raises(AmbiguityDetectorError) as exc_info:
        detect_ambiguity(_request(), _ontology(), _mapping(), rules)
    assert exc_info.value.code == AmbiguityDetectorErrorCode.INVALID_RULESET.value


def test_unpublished_mapping_and_database_mismatch_rejected():
    with pytest.raises(AmbiguityDetectorError) as exc_info:
        detect_ambiguity(_request(), _ontology(), _mapping(status=MappingStatus.DRAFT), _rules())
    assert exc_info.value.code == AmbiguityDetectorErrorCode.INVALID_RULESET.value
    with pytest.raises(AmbiguityDetectorError) as exc_info:
        detect_ambiguity(_request(), _ontology(), _mapping(database_id="other-db"), _rules())
    assert exc_info.value.code == AmbiguityDetectorErrorCode.INVALID_RULESET.value


def test_resolve_rejects_context_and_rule_drift():
    pending = prepare_detected_clarification(_request(), _ontology(), _mapping(), _rules())
    assert pending is not None
    valid_choice = _first_choice()
    # Question drift: match disappears.
    with pytest.raises(AmbiguityDetectorError) as exc_info:
        resolve_detected_clarification(
            _request(question=f"{QUESTION_BASE} plain timetable"),
            _ontology(),
            _mapping(),
            _rules(),
            pending,
            valid_choice,
        )
    assert exc_info.value.code == AmbiguityDetectorErrorCode.CONTEXT_MISMATCH.value
    # Trigger drift: rule set no longer matches.
    drifted = _rules(
        rules=[
            {
                "rule_id": "rule-grades",
                "triggers": ["출석부"],
                "options": [
                    {"option_key": "opt-course", "semantic_ref": "Course.name"},
                    {"option_key": "opt-instructor", "semantic_ref": "Instructor.name"},
                ],
            }
        ]
    )
    with pytest.raises(AmbiguityDetectorError):
        resolve_detected_clarification(
            _request(), _ontology(), _mapping(), drifted, pending, valid_choice,
        )
    # A changed trigger that still matches is also ruleset drift.
    still_matching_drift = _rules(
        rules=[
            {
                "rule_id": "rule-grades",
                "triggers": ["성적표", "성적"],
                "options": [
                    {"option_key": "opt-course", "semantic_ref": "Course.name"},
                    {"option_key": "opt-instructor", "semantic_ref": "Instructor.name"},
                ],
            }
        ]
    )
    with pytest.raises(ClarificationError) as exc_info:
        resolve_detected_clarification(
            _request(), _ontology(), _mapping(), still_matching_drift, pending, valid_choice,
        )
    assert exc_info.value.code == ClarificationErrorCode.CONTEXT_MISMATCH.value
    # Even an unmatched rule changes the versioned ruleset binding.
    with pytest.raises(ClarificationError) as exc_info:
        resolve_detected_clarification(
            _request(), _ontology(), _mapping(), _two_rule_set(), pending, valid_choice,
        )
    assert exc_info.value.code == ClarificationErrorCode.CONTEXT_MISMATCH.value
    # Option drift: old pending cannot succeed under new options.
    option_drifted = _rules(
        rules=[
            {
                "rule_id": "rule-grades",
                "triggers": ["성적표"],
                "options": [
                    {"option_key": "opt-course", "semantic_ref": "Course.name"},
                    {"option_key": "opt-changed", "semantic_ref": "Course.credits"},
                ],
            }
        ]
    )
    with pytest.raises(ClarificationError) as exc_info:
        resolve_detected_clarification(
            _request(), _ontology(), _mapping(), option_drifted, pending, valid_choice,
        )
    assert exc_info.value.code == ClarificationErrorCode.CONTEXT_MISMATCH.value
    # Ontology/mapping drift.
    ontology = _ontology()
    ontology.fields["Course.extra"] = OntologyField(
        name="Course.extra", data_type=DataType.STRING, allowed_operators={Operator.EQUALS}
    )
    with pytest.raises(ClarificationError):
        resolve_detected_clarification(
            _request(), ontology, _mapping(), _rules(), pending, valid_choice,
        )


def test_resolve_rejects_new_multi_match():
    pending = prepare_detected_clarification(_request(), _ontology(), _mapping(), _rules())
    assert pending is not None
    both = _request(question=f"{QUESTION_BASE} 성적표 query")
    assert prepare_detected_clarification(both, _ontology(), _mapping(), _rules()) is not None
    widened = _rules(
        rules=[
            {
                "rule_id": "rule-grades",
                "triggers": ["성적표"],
                "options": [
                    {"option_key": "opt-a1", "semantic_ref": "Course.name"},
                    {"option_key": "opt-a2", "semantic_ref": "Instructor.name"},
                ],
            },
            {
                "rule_id": "rule-b",
                "triggers": ["query"],
                "options": [
                    {"option_key": "opt-b1", "semantic_ref": "Course.name"},
                    {"option_key": "opt-b2", "semantic_ref": "Course.credits"},
                ],
            },
        ]
    )
    with pytest.raises(AmbiguityDetectorError) as exc_info:
        resolve_detected_clarification(
            both, _ontology(), _mapping(), widened, pending, _first_choice(),
        )
    assert exc_info.value.code == AmbiguityDetectorErrorCode.CONFLICT.value


def test_forged_inputs_rejected_without_warnings():
    pending = prepare_detected_clarification(_request(), _ontology(), _mapping(), _rules())
    assert pending is not None
    valid_choice = _first_choice()
    forged_request = _request().model_copy(update={"question": "   "})
    forged_mapping = _mapping().model_copy(update={"status": "DRAFT"})
    forged_ontology = _ontology().model_copy(update={"fields": {}})
    forged_rules = DetectorRuleSet.model_validate(_rules()).model_copy(update={"rules": []})
    forged_pending = pending.model_copy(update={"question_sha256": "0" * 64})
    forged_choice = ClarificationChoice.model_construct(selection=LOCAL_PATH, cancelled=False)
    constructed_rules = DetectorRuleSet.model_construct(version=AMBIGUITY_DETECTOR_VERSION, rules=[])
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        with pytest.raises(AmbiguityDetectorError):
            detect_ambiguity(forged_request, _ontology(), _mapping(), _rules())
        with pytest.raises(AmbiguityDetectorError):
            detect_ambiguity(_request(), forged_ontology, _mapping(), _rules())
        with pytest.raises(AmbiguityDetectorError):
            detect_ambiguity(_request(), _ontology(), forged_mapping, _rules())
        with pytest.raises(AmbiguityDetectorError):
            detect_ambiguity(_request(), _ontology(), _mapping(), forged_rules)
        with pytest.raises(AmbiguityDetectorError):
            detect_ambiguity(_request(), _ontology(), _mapping(), constructed_rules)
        with pytest.raises((AmbiguityDetectorError, ClarificationError)):
            resolve_detected_clarification(
                _request(), _ontology(), _mapping(), _rules(), forged_pending, valid_choice,
            )
        with pytest.raises((AmbiguityDetectorError, ClarificationError)):
            resolve_detected_clarification(
                _request(), _ontology(), _mapping(), _rules(), pending, forged_choice
            )
    assert not emitted


def test_raw_secrets_absent_from_pending_result_error_and_streams(caplog, capsys):
    raw_rule_id = f"..\\{SECRET_LIKE_REQUEST_ID}\\rule"
    raw_option_key = f"{SECRET_LIKE_DATABASE_ID}-opt"
    raw_trigger = "성적표"
    request = _request(
        request_id=PATH_LIKE_REQUEST_ID,
        database_id=PATH_LIKE_DATABASE_ID,
        question=f"{QUESTION_BASE} {raw_trigger} {MALICIOUS} {LOCAL_PATH}",
    )
    mapping = _mapping(database_id=PATH_LIKE_DATABASE_ID)
    rules = _rules(
        rules=[
            {
                "rule_id": "rule-ok",
                "triggers": [raw_trigger],
                "options": [
                    {"option_key": "opt-a", "semantic_ref": "Course.name"},
                    {"option_key": "opt-b", "semantic_ref": "Instructor.name"},
                ],
            }
        ]
    )
    pending = prepare_detected_clarification(request, _ontology(), mapping, rules)
    assert pending is not None
    blob = pending.model_dump_json()
    signal = detect_ambiguity(request, _ontology(), mapping, rules)
    assert signal is not None
    result = resolve_detected_clarification(
        request, _ontology(), mapping, rules, pending,
        ClarificationChoice(selection=[signal.options[0].option_id]),
    )
    result_blob = result.model_dump_json()
    with pytest.raises((AmbiguityDetectorError, ClarificationError)) as exc_info:
        resolve_detected_clarification(
            request, _ontology(), mapping, rules, pending,
            ClarificationChoice(selection=[MALICIOUS]),
        )
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
        raw_rule_id,
        raw_option_key,
    ):
        assert secret not in blob
        assert secret not in result_blob
        assert secret not in error_blob
        assert secret not in out
        assert secret not in err
        assert secret not in log_blob
    # Option IDs are opaque hashes.
    assert hashlib.sha256(b"probe").hexdigest()[:8] not in blob
    assert "opt-a" not in blob
    assert "rule-ok" not in blob


def test_no_model_executor_or_database_calls(capsys):
    class CountingModel:
        def __init__(self) -> None:
            self.calls = 0

        def generate_ir(self, request, ontology, mapping):
            self.calls += 1
            raise AssertionError("detector must not call a model")

    class CountingExecutor:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, database_id, compiled):
            self.calls += 1
            raise AssertionError("detector must not execute")

    model, executor = CountingModel(), CountingExecutor()
    pending = prepare_detected_clarification(_request(), _ontology(), _mapping(), _rules())
    assert pending is not None
    resolve_detected_clarification(
        _request(), _ontology(), _mapping(), _rules(), pending, _first_choice(),
    )
    assert model.calls == 0
    assert executor.calls == 0
    captured = capsys.readouterr()
    for secret in (QUESTION_BASE, PHYSICAL_TABLE, PHYSICAL_COLUMN, LOCAL_PATH):
        assert secret not in captured.out
        assert secret not in captured.err
