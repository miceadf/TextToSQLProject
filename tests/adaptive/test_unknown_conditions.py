"""Phase 8-20: conditions a database cannot answer."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.adaptive.api import chat_query
from backend.adaptive.api.chat_query import CHAT_QUERY_PATH, register_chat_query_routes
from backend.adaptive.api.unknown_conditions import find_mentions, mentioned_fields, outside_concepts, uncovered
from backend.adaptive.harness.compiler import CompiledQuery
from backend.adaptive.mapping.extensions import ExtensionField
from backend.adaptive.ontology import load_default_ontology
from backend.adaptive.ontology.outside_concepts import (
    OutsideConcepts,
    default_outside_concepts,
    default_outside_concepts_path,
    load_outside_concepts,
    ontology_overlaps,
)
from tests.adaptive.test_chat_query_endpoint import RecordingExecutor, ir
from tests.adaptive.test_legacy_view_partial_mapping import DATABASE_ID, make_context

ONTOLOGY = load_default_ontology()
CONCEPTS = default_outside_concepts()
CLASSROOM = {"Schedule.ext_classroom": ExtensionField(label="강의실", data_type="string")}


def named(question: str, *, extensions: dict[str, Any] | None = None, values: list[str] = (), hints: list[str] = ()) -> list[str]:
    return [
        concept.key
        for concept in outside_concepts(
            question, CONCEPTS, extensions=extensions or {}, filter_values=values, hint_texts=hints
        )
    ]


# --- the concept list ------------------------------------------------------------------------


def test_the_shipped_list_loads_and_stays_clear_of_the_common_ontology() -> None:
    concepts = load_outside_concepts(default_outside_concepts_path())
    assert ontology_overlaps(concepts, ONTOLOGY) == []
    assert {concept.key for concept in concepts.concepts} >= {"classroom", "class_mode", "seats_left"}


@pytest.mark.parametrize(
    "data",
    [
        {"version": 1, "concepts": [{"key": "a", "label": "가", "aliases": ["가"]}]},  # one-letter alias
        {"version": 1, "concepts": [
            {"key": "a", "label": "가나", "aliases": ["다라"]},
            {"key": "b", "label": "마바", "aliases": ["다 라"]},  # the same word for two concepts
        ]},
        {"version": 1, "concepts": [{"key": "a", "label": "가나", "aliases": ["다라"], "patterns": ["("]}]},
        {"version": 2, "concepts": [{"key": "a", "label": "가나", "aliases": ["다라"]}]},
        {"version": 1, "concepts": [{"key": "A", "label": "가나", "aliases": ["다라"]}]},
    ],
)
def test_invalid_lists_are_refused(data: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        OutsideConcepts.model_validate(data)


def test_a_word_inside_a_common_name_is_an_overlap() -> None:
    concepts = OutsideConcepts.model_validate(
        {"version": 1, "concepts": [{"key": "a", "label": "제한", "aliases": ["학년"]}]}
    )
    # "제한인원" (capacity) and "대상 학년" would both read as the outside concept
    assert ontology_overlaps(concepts, ONTOLOGY) == ["제한", "학년"]


# --- finding concepts in a question ----------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("교111 강의실 수업 보여줘", ["classroom"]),
        ("강의실이 교111인 과목", ["classroom"]),
        ("IT관 303호 수업 보여줘", ["classroom"]),
        ("305 호에서 하는 수업", ["classroom"]),
        ("실시간 수업 보여줘", ["class_mode"]),
        ("온라인 강의 보여줘", ["class_mode"]),
        ("비대면 컴퓨터 과목", ["class_mode"]),
        ("절대평가 과목", ["grading"]),
        ("영어강의 과목 보여줘", ["lecture_language"]),
        ("여석 있는 과목", ["seats_left"]),
        ("강의실 정보랑 수업 방식 알려줘", ["classroom", "class_mode"]),
    ],
)
def test_named_concepts_are_found(question: str, expected: list[str]) -> None:
    assert named(question) == expected


@pytest.mark.parametrize(
    "question",
    [
        "컴퓨터 전공 과목 보여줘",
        "월요일 오전 수업 보여줘",
        "건물에너지 과목 보여줘",  # course names from the legacy catalogue
        "원격탐사 과목 보여줘",
        "온라인 영상 플랫폼 과목 보여줘",
        "수강 정원 30명 이상 과목",  # common fields
        "2학기 3학년 과목",
        "1호선 근처",
        "2024학년도 과목",
    ],
)
def test_ordinary_questions_name_no_concept(question: str) -> None:
    assert named(question) == []


def test_a_word_inside_a_longer_value_is_not_the_concept() -> None:
    # "교실" inside a course name the model searched for
    assert named("열린교실탐구 과목", values=["열린교실탐구"]) == []
    assert named("열린교실탐구 과목", hints=["열린교실탐구"]) == []
    # the model stuffing the concept word itself into a field excuses nothing
    assert named("강의실 과목", values=["강의실"]) == ["classroom"]
    # a value from another word does not excuse this one
    assert named("교실 수업", values=["열린교실탐구"]) == ["classroom"]


def test_a_room_number_is_excused_only_by_the_databases_own_values() -> None:
    assert named("IT관303호 수업", values=["IT관303호"]) == ["classroom"]
    assert named("IT관303호 수업", hints=["IT관303호"]) == []


def test_an_extension_field_covers_its_concept() -> None:
    assert named("교111 강의실 수업", extensions=CLASSROOM) == []
    assert named("IT관 303호 수업", extensions=CLASSROOM) == []
    by_key = {"Schedule.ext_classroom": ExtensionField(label="수업 장소", data_type="string")}
    assert named("강의실 수업", extensions=by_key) == []
    by_label = {"Schedule.ext_room_no": ExtensionField(label="강의실 번호", data_type="string")}
    assert named("강의실 수업", extensions=by_label) == []
    # an unrelated extension covers nothing
    mode = {"CourseOffering.ext_mode": ExtensionField(label="수업방식", data_type="string")}
    assert named("강의실 실시간 수업", extensions=mode) == ["classroom"]
    assert [concept.key for concept in uncovered(CONCEPTS, mode)].count("class_mode") == 0


def test_mentions_are_located_without_whitespace() -> None:
    [mention] = find_mentions("과목 강의실이 어디", CONCEPTS)
    assert (mention.concept.key, mention.key, mention.start, mention.end, mention.pattern) == (
        "classroom", "강의실", 2, 5, False,
    )


def test_room_numbers_ignore_whitespace_like_aliases() -> None:
    """Luna 8-20 review: the pattern ran only on the spaced text."""
    assert named("3 0 5 호에서 하는 수업") == ["classroom"]
    assert named("2024 305호 수업") == ["classroom"]  # joined digits still match on the spaced text


def test_a_value_spanning_words_excuses_the_alias_it_holds() -> None:
    """Luna 8-20 review: "온라인 강의 플랫폼" is a course title, not a class mode."""
    question = "온라인 강의 플랫폼 과목 보여줘"
    assert named(question) == ["class_mode"]
    assert named(question, values=["온라인 강의 플랫폼"]) == []
    # the value must sit where the alias is
    assert named("온라인 강의 듣고 싶어, 플랫폼 과목", values=["온라인 강의 플랫폼"]) == ["class_mode"]


# --- the chat answer -------------------------------------------------------------------------


class Model:
    def __init__(self, output: Any) -> None:
        self.output = output
        self.calls = 0

    def generate_ir(self, request: Any, ontology: Any, mapping: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return self.output


def ask(
    output: Any, question: str, *, executor: Any = None, show_sql: bool = False
) -> tuple[dict[str, Any], Any, Model]:
    executor = executor or RecordingExecutor()
    model = Model(output)
    app = FastAPI()
    register_chat_query_routes(
        app, pg_contexts={DATABASE_ID: make_context(executor=executor)}, model=model, show_sql=show_sql
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        body = client.post(CHAT_QUERY_PATH, json={"database_id": DATABASE_ID, "query": question}).json()
    return body, executor, model


def test_a_stuffed_concept_is_unsupported_and_never_runs() -> None:
    stuffed = ir(["Course.name"], [{"field": "CourseOffering.section", "operator": "equals", "value": "교111"}])
    body, executor, model = ask(stuffed, "교111 강의실 수업 보여줘")
    assert body["status"] == "UNSUPPORTED"
    assert body["unsupported"] == [{"concept": "classroom", "label": "강의실"}]
    assert executor.queries == [] and model.calls == 1


def test_an_invalid_output_still_names_the_concept() -> None:
    body, executor, _model = ask({"select": "nonsense"}, "실시간 수업 보여줘")
    assert body["status"] == "UNSUPPORTED"
    assert body["unsupported"] == [{"concept": "class_mode", "label": "수업 방식"}]
    assert executor.queries == []


def test_a_dropped_concept_is_unsupported_too() -> None:
    # the model ignored "여석" and would have listed every course
    body, executor, _model = ask(ir(["Course.name"], []), "여석 있는 과목")
    assert body["unsupported"] == [{"concept": "seats_left", "label": "여석"}] and executor.queries == []


def test_common_unsupported_fields_and_concepts_come_together() -> None:
    output = ir(["Course.name"], [{"field": "CourseOffering.semester", "operator": "equals", "value": "2"}])
    body, _executor, _model = ask(output, "2학기 강의실 수업")
    assert body["status"] == "UNSUPPORTED"
    assert body["unsupported"] == [
        {"field": "CourseOffering.semester", "label": "학기"},
        {"concept": "classroom", "label": "강의실"},
    ]
    # a common field the question never named is the model's addition: not blamed
    body, _executor, _model = ask(output, "교111 강의실 수업")
    assert body["unsupported"] == [{"concept": "classroom", "label": "강의실"}]


def test_fields_are_named_by_label_or_alias() -> None:
    fields = ["CourseOffering.semester", "CourseOffering.year", "CourseOffering.capacity"]
    assert mentioned_fields("2학기 개설 과목", ONTOLOGY, fields) == ["CourseOffering.semester"]
    assert mentioned_fields("2026 학년도 정원", ONTOLOGY, fields) == ["CourseOffering.year", "CourseOffering.capacity"]
    assert mentioned_fields("교111 수업", ONTOLOGY, [*fields, "Unknown.field"]) == []


def test_a_concept_word_inside_a_searched_course_name_runs_normally() -> None:
    output = ir(["Course.name"], [{"field": "Course.name", "operator": "contains", "value": "열린교실탐구"}])
    body, executor, _model = ask(output, "열린교실탐구 과목 보여줘")
    assert body["status"] == "SUCCEEDED" and len(executor.queries) == 1


def test_a_failing_concept_check_leaves_the_answer_path_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> Any:
        raise RuntimeError("list unavailable")

    monkeypatch.setattr(chat_query, "default_outside_concepts", broken)
    output = ir(["Course.name"], [{"field": "Department.name", "operator": "equals", "value": "물리학과"}])
    body, executor, _model = ask(output, "강의실 물리학과 과목")
    assert body["status"] == "SUCCEEDED" and len(executor.queries) == 1


# --- empty-result diagnosis -----------------------------------------------------------------


class ProbeExecutor:
    """The full query finds nothing; a lone condition finds a row unless its value is missing."""

    def __init__(self, missing: set[str], fail_probes: bool = False) -> None:
        self.missing = missing
        self.fail_probes = fail_probes
        self.queries: list[CompiledQuery] = []

    def execute(self, database_id: str, compiled: CompiledQuery) -> Any:
        self.queries.append(compiled)
        columns = list(compiled.expected_columns)
        if len(self.queries) == 1:
            return columns, []
        if self.fail_probes:
            raise RuntimeError("probe failed")
        values = {str(value).strip("%") for value in compiled.parameters}
        if values & self.missing:
            return columns, []
        return columns, [{column: "x" for column in columns}]


def test_an_empty_result_names_the_condition_that_matches_nothing() -> None:
    output = ir(["Course.name"], [
        {"field": "Course.code", "operator": "equals", "value": "교111"},
        {"field": "Course.category", "operator": "equals", "value": "전공"},
    ])
    executor = ProbeExecutor({"교111"})
    body, _executor, _model = ask(output, "교111에서 하는 전공 수업", executor=executor, show_sql=True)
    assert body["status"] == "SUCCEEDED" and body["row_count"] == 0
    assert body["unmatched"] == [{"field": "Course.code", "label": "과목 코드", "value": "교111"}]
    # the answer, then one probe per condition, each alone with one row
    assert len(executor.queries) == 3
    assert all(query.parameters[-1] == 1 or "LIMIT 1" in query.sql for query in executor.queries[1:])
    # the SQL shown is the answer's own query (both conditions), not a probe
    assert "교111" in body["sql"] and "전공" in body["sql"]


def test_a_lone_condition_is_its_own_probe() -> None:
    output = ir(["Course.name"], [{"field": "Department.name", "operator": "equals", "value": "IT대학"}])
    executor = ProbeExecutor({"IT대학"})
    body, _executor, _model = ask(output, "IT대학 과목", executor=executor)
    assert body["unmatched"] == [{"field": "Department.name", "label": "학과명", "value": "IT대학"}]
    assert len(executor.queries) == 1


def test_conditions_that_match_alone_are_not_blamed() -> None:
    output = ir(["Course.name"], [
        {"field": "Department.name", "operator": "equals", "value": "물리학과"},
        {"field": "Course.category", "operator": "equals", "value": "전공"},
    ])
    body, executor, _model = ask(output, "물리학과 전공", executor=ProbeExecutor(set()))
    assert body["row_count"] == 0 and "unmatched" not in body and len(executor.queries) == 3


def test_a_number_the_database_lacks_is_named_too() -> None:
    """Luna 8-20 review: the guideline (written for the value-list check) excluded numbers;
    a probe answers them exactly, so the guideline was amended."""
    output = ir(["Course.name"], [{"field": "Course.target_grade", "operator": "equals", "value": 7}])
    body, _executor, _model = ask(output, "7학년 과목", executor=ProbeExecutor(set()))
    assert body["unmatched"] == [{"field": "Course.target_grade", "label": "대상 학년", "value": 7}]


def test_ranges_and_failed_probes_give_no_diagnosis() -> None:
    output = ir(["Course.name"], [{"field": "Course.credits", "operator": "greater_than", "value": 9}])
    body, executor, _model = ask(output, "9학점 넘는 과목", executor=ProbeExecutor(set()))
    assert body["row_count"] == 0 and "unmatched" not in body and len(executor.queries) == 1
    output = ir(["Course.name"], [
        {"field": "Department.name", "operator": "equals", "value": "물리학과"},
        {"field": "Course.category", "operator": "equals", "value": "전공"},
    ])
    body, executor, _model = ask(output, "물리학과 전공", executor=ProbeExecutor({"물리학과"}, fail_probes=True))
    assert body["status"] == "SUCCEEDED" and body["row_count"] == 0 and "unmatched" not in body


def test_rows_mean_no_probe() -> None:
    output = ir(["Course.name"], [
        {"field": "Course.code", "operator": "equals", "value": "교111"},
        {"field": "Course.category", "operator": "equals", "value": "전공"},
    ])
    body, executor, _model = ask(output, "교111 전공 수업")
    assert body["row_count"] == 1 and "unmatched" not in body and len(executor.queries) == 1


# --- value hints after a republish ----------------------------------------------------------


def test_a_republished_mapping_rebuilds_the_value_index() -> None:
    """Found in the 8-20 live check: hints kept the pre-extension index after a republish."""
    from backend.adaptive.api import value_hints
    from backend.adaptive.api.postgres_runtime import PostgresRuntimeProfile
    from tests.adaptive import test_legacy_view_partial_mapping as lv

    class Reader:
        def __init__(self) -> None:
            self.reads = 0

        def read(self, database_id: str, table: str, column: str, limit: int) -> Any:
            self.reads += 1
            return ["공과대학 전자공학과"]

    def context(version: int) -> Any:
        base = lv.make_profile()
        return lv.make_context(
            PostgresRuntimeProfile(
                database_id=base.database_id,
                label=base.label,
                connection_config=base.connection_config,
                published_mapping=lv.make_published().model_copy(update={"version": version}),
                field_transforms=base.field_transforms,
                unsupported_fields=base.unsupported_fields,
            )
        )

    reader = Reader()
    indexes = value_hints.ChatValueIndexes(reader, ONTOLOGY)
    first = indexes.get(context(1))
    reads = reader.reads
    assert first is not None and indexes.get(context(1)) is first and reader.reads == reads
    second = indexes.get(context(2))
    assert second is not None and second is not first and reader.reads == 2 * reads
    assert len(indexes._cache) == 1  # the old version is dropped
