"""Phase 8-13: chat value hints grounded in the database's own values."""

from __future__ import annotations

import inspect
import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.adaptive.api import chat_query, value_hints
from backend.adaptive.api.chat_query import CHAT_QUERY_PATH, register_chat_query_routes
from backend.adaptive.api.value_hints import (
    MAX_HINTS,
    ChatValueIndexes,
    ValueIndex,
    domain_nouns,
    hint_fields,
    repair_output,
)
from backend.adaptive.harness import ollama as harness_ollama
from backend.adaptive.harness import postgres_values
from backend.adaptive.harness.contracts import FieldMapping, MappingProfile, MappingStatus
from backend.adaptive.harness.postgres_values import OVER_LIMIT, PostgresDistinctValueReader
from backend.adaptive.catalog.postgres import PostgresConnectionConfig
from backend.adaptive.mapping.values import ValueDictionary, ValueEntry
from backend.adaptive.ontology import DomainOntology, load_default_ontology
from tests.adaptive.test_chat_query_endpoint import RecordingExecutor, ir
from tests.adaptive.test_legacy_view_partial_mapping import DATABASE_ID, make_context

ONTOLOGY = load_default_ontology()
NOUNS = domain_nouns(ONTOLOGY)
# Synthetic values in the shape of a real catalogue (not the legacy DB).
DEPARTMENTS = (
    "인문대학 국어국문학과",
    "공과대학 전자공학과",
    "공과대학 기계공학부",
    "자연과학대학 물리학과",
    "대학 공통교양",
    "생명과학대학 생명시스템과학과",
)
CATEGORIES = ("전공(필수)", "전공(선택)", "교양(필수)", "교양(선택)", "일반선택")
ABBREVIATIONS = {
    "Department.name": ValueDictionary(
        entries=(ValueEntry(stored="공과대학 전자공학과", label="공과대학 전자공학과", aliases=("전자과",)),),
        reviewer="test",
        reason="synthetic abbreviations",
    ),
    "Course.category": ValueDictionary(
        entries=(ValueEntry(stored="전공(필수)", label="전공(필수)", aliases=("전필",)),),
        reviewer="test",
        reason="synthetic abbreviations",
    ),
}


def make_index(dictionaries: dict[str, ValueDictionary] | None = None) -> ValueIndex:
    return ValueIndex(
        {"Department.name": DEPARTMENTS, "Course.category": CATEGORIES},
        ABBREVIATIONS if dictionaries is None else dictionaries,
        nouns=NOUNS,
    )


def payloads(question: str, index: ValueIndex | None = None) -> list[dict[str, str]]:
    return [hint.payload() for hint in (index or make_index()).find(question)]


# --- question matching --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        # a word piece of a stored value stays a piece (containment finds all holders)
        ("국어국문학과 과목", [("국어국문학과", "Department.name", "국어국문학과")]),
        ("공통교양의 수업", [("공통교양", "Department.name", "공통교양")]),
        # a piece shared by several values
        ("공과대학 과목", [("공과대학", "Department.name", "공과대학")]),
        ("전공 과목", [("전공", "Course.category", "전공")]),
        # a whole stored value names itself
        ("공과대학 전자공학과 수업", [("공과대학 전자공학과", "Department.name", "공과대학 전자공학과")]),
        # aliases come from the database's own dictionaries
        ("전자과 전필 과목", [
            ("전자과", "Department.name", "공과대학 전자공학과"),
            ("전필", "Course.category", "전공(필수)"),
        ]),
        # particles and a value glued to a domain noun
        ("전자과에서 여는 전공과목", [
            ("전자과", "Department.name", "공과대학 전자공학과"),
            ("전공", "Course.category", "전공"),
        ]),
    ],
)
def test_question_words_are_matched_to_stored_values(question: str, expected: list[tuple[str, str, str]]) -> None:
    assert payloads(question) == [{"text": t, "field": f, "value": v} for t, f, v in expected]


def test_near_miss_spelling_is_a_fuzzy_hint() -> None:
    [hint] = make_index().find("생명시스탬과학과 과목")
    assert hint.fuzzy and hint.payload() == {
        "text": "생명시스탬과학과", "field": "Department.name", "value": "생명시스템과학과",
    }
    # one wrong letter in a short word is too uncertain to correct
    assert make_index().find("국어국믄학과 과목") == []


@pytest.mark.parametrize(
    "question",
    [
        "전자회로 과목 보여줘",  # a course name, not a value piece
        "필수전자 과목",  # a value word inside a longer word is not split off
        "과목 보여줘",  # domain nouns alone
        "월요일 오후 수업",
    ],
)
def test_no_hint_without_a_real_match(question: str) -> None:
    assert payloads(question) == []


def test_without_the_dictionary_only_generic_matching_is_left() -> None:
    index = make_index({})
    assert payloads("전자과 전필 과목", index) == []
    assert payloads("전자공학과 과목", index) == [
        {"text": "전자공학과", "field": "Department.name", "value": "전자공학과"}
    ]


def test_hints_are_bounded_and_filtered() -> None:
    question = " ".join(value.split()[-1] for value in DEPARTMENTS) + " " + " ".join(CATEGORIES)
    assert len(make_index().find(question)) == MAX_HINTS
    assert make_index().find("전공 과목", allowed_fields={"Department.name"}) == []


def test_the_generic_modules_hold_no_database_values() -> None:
    for module in (value_hints, postgres_values, chat_query, harness_ollama):
        source = inspect.getsource(module)
        for text in ("컴융", "학과공통", "컴퓨터융합", "공과대학", "전핵", "교소"):
            assert text not in source, (module.__name__, text)


# --- output repair ---------------------------------------------------------------------------


def test_invented_fields_are_repaired_from_ontology_hints() -> None:
    output = ir(
        ["Course.name", "Course.professor"],
        [
            {"field": "Course.department", "operator": "equals", "value": "전자공학과"},
            {"field": "Course.dept_name", "operator": "equals", "value": "x"},
            {"field": "Course.room", "operator": "equals", "value": "y"},
        ],
    )
    repaired = repair_output(output, ONTOLOGY)
    assert repaired["select"] == ["Course.name", "Instructor.name"]
    assert [item["field"] for item in repaired["filters"]] == [
        "Department.name", "Department.name", "Course.room",
    ]


def test_an_attribute_with_two_meanings_is_not_repaired() -> None:
    data = ONTOLOGY.model_dump()
    course_code = data["entities"][0]["fields"][0]
    course_code["physical_name_hints"].append("department")
    ontology = DomainOntology.model_validate(data)
    output = ir(["Course.name"], [{"field": "Course.department", "operator": "equals", "value": "x"}])
    assert repair_output(output, ontology)["filters"][0]["field"] == "Course.department"


def test_values_move_only_to_the_one_hinted_field_that_holds_them() -> None:
    index = make_index()
    hints = index.find("전자공학과 전공 과목")
    wrong = ir(["Course.name"], [
        {"field": "Course.category", "operator": "equals", "value": "전자공학과"},
        {"field": "Course.category", "operator": "equals", "value": "전공"},
    ])
    repaired = repair_output(wrong, ONTOLOGY, index=index, hints=hints)
    assert [item["field"] for item in repaired["filters"]] == ["Department.name", "Course.category"]
    # no hint: nothing moves (and no condition is ever added)
    assert repair_output(wrong, ONTOLOGY, index=index, hints=[])["filters"] == wrong["filters"]
    # a value the field holds stays
    own = ir(["Course.name"], [{"field": "Course.category", "operator": "equals", "value": "선택"}])
    assert repair_output(own, ONTOLOGY, index=index, hints=hints)["filters"] == own["filters"]


@pytest.mark.parametrize("identity", ["CourseOffering.section", "Course.code", "Department.code"])
def test_a_hinted_name_in_an_identity_field_moves(identity: str) -> None:
    # Phase 9-08: a general model put department names into the section.
    index = make_index()
    hints = index.find("전자공학과 전공 과목")
    wrong = ir(["Course.name"], [{"field": identity, "operator": "contains", "value": "전자공학과"}])
    repaired = repair_output(wrong, ONTOLOGY, index=index, hints=hints)
    assert repaired["filters"][0]["field"] == "Department.name"


def test_identity_values_without_an_equal_hint_stay() -> None:
    index = make_index()
    hints = index.find("전자공학과 전공 과목")
    for value in ("01", "1101-7019", "전자", "전자공학과 01분반"):
        # A code, a part of a hinted name, or a longer text never moves.
        own = ir(["Course.name"], [{"field": "CourseOffering.section", "operator": "equals", "value": value}])
        assert repair_output(own, ONTOLOGY, index=index, hints=hints)["filters"] == own["filters"]
    wrong = ir(["Course.name"], [{"field": "CourseOffering.section", "operator": "contains", "value": "전자공학과"}])
    assert repair_output(wrong, ONTOLOGY, index=index, hints=[])["filters"] == wrong["filters"]
    assert repair_output(wrong, ONTOLOGY, index=None, hints=hints)["filters"] == wrong["filters"]


def test_repair_leaves_malformed_output_alone() -> None:
    for output in ("SELECT 1", None, {"filters": "x"}, {"select": "Course.department"}):
        assert repair_output(output, ONTOLOGY) == output


# --- prompt ----------------------------------------------------------------------------------


def _mapping() -> MappingProfile:
    return MappingProfile(
        database_id="db", version=1, status=MappingStatus.PUBLISHED,
        fields={name: FieldMapping(table="t", column=name.split(".")[1]) for name in ("Course.name", "Department.name")},
    )


def test_prompt_is_unchanged_without_hints_and_carries_them_otherwise() -> None:
    ontology, _labels = chat_query._runtime_ontology()
    base = harness_ollama._prompt("질문", ontology, _mapping(), "query-ir-v1")
    assert harness_ollama._prompt("질문", ontology, _mapping(), "query-ir-v1", None) == base
    assert harness_ollama._prompt("질문", ontology, _mapping(), "query-ir-v1", []) == base
    hint = {"text": "전자과", "field": "Department.name", "value": "공과대학 전자공학과"}
    prompt = harness_ollama._prompt("질문", ontology, _mapping(), "query-ir-v1", [hint])
    head, _, question = base.partition("\nQuestion: ")
    assert prompt == head + "\n" + harness_ollama.VALUE_HINTS_LABEL + json.dumps([hint], ensure_ascii=False) + "\nQuestion: " + question


@pytest.mark.parametrize(
    "hints",
    [
        [{"text": "a", "field": "Instructor.name", "value": "b"}],  # not an available field
        [{"text": "a", "field": "Department.name"}],
        [{"text": "a", "field": "Department.name", "value": "b", "extra": "c"}],
        [{"text": "", "field": "Department.name", "value": "b"}],
        [{"text": "a", "field": "Department.name", "value": "b"}] * 9,
        "not a list",
    ],
)
def test_invalid_hints_are_refused(hints: Any) -> None:
    ontology, _labels = chat_query._runtime_ontology()
    with pytest.raises(ValueError):
        harness_ollama._prompt("질문", ontology, _mapping(), "query-ir-v1", hints)


# --- chat path -------------------------------------------------------------------------------


class HintModel:
    def __init__(self, output: Any) -> None:
        self.output = output
        self.calls: list[dict[str, Any]] = []

    def generate_ir(self, request: Any, ontology: Any, mapping: Any, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.output


class FakeReader:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.reads: list[tuple[str, str, str, int]] = []

    def read(self, database_id: str, table: str, column: str, limit: int) -> Any:
        self.reads.append((database_id, table, column, limit))
        if self.fail:
            raise RuntimeError("down")
        return {"dept_name": list(DEPARTMENTS), "category": list(CATEGORIES)}.get(column, OVER_LIMIT)


def chat(model: Any, question: str, reader: Any) -> tuple[dict[str, Any], RecordingExecutor]:
    executor = RecordingExecutor()
    app = FastAPI()
    register_chat_query_routes(
        app, pg_contexts={DATABASE_ID: make_context(executor=executor)}, model=model, value_reader=reader
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        body = client.post(CHAT_QUERY_PATH, json={"database_id": DATABASE_ID, "query": question}).json()
    return body, executor


def test_hints_reach_the_model_and_an_invented_field_is_repaired() -> None:
    model = HintModel(ir(["Course.name"], [
        {"field": "Course.department", "operator": "equals", "value": "전자공학과"},
        {"field": "Course.category", "operator": "equals", "value": "전공"},
    ]))
    reader = FakeReader()
    body, executor = chat(model, "전자공학과 전공 과목 보여줘", reader)
    assert model.calls == [{"value_hints": [
        {"text": "전자공학과", "field": "Department.name", "value": "전자공학과"},
        {"text": "전공", "field": "Course.category", "value": "전공"},
    ]}]
    assert body["status"] == "SUCCEEDED"
    assert [(f["field"], f["operator"], f["value"]) for f in body["understood"]["filters"]] == [
        ("Department.name", "contains", "전자공학과"),
        ("Course.category", "contains", "전공"),
    ]
    [compiled] = executor.queries
    assert compiled.parameters[:2] == ["%전자공학과%", "%전공%"]
    # Only text fields without transforms or row identity are read.
    assert {column for _db, _table, column, _limit in reader.reads} == {
        "subject_name", "dept_name", "professor", "category",
    }


def test_a_value_in_the_wrong_field_moves_to_the_hinted_field() -> None:
    model = HintModel(ir(["Course.name"], [{"field": "Course.category", "operator": "equals", "value": "국어국문학과"}]))
    body, _executor = chat(model, "국어국문학과 과목", FakeReader())
    assert body["understood"]["filters"][0]["field"] == "Department.name"


@pytest.mark.parametrize("reader", [None, FakeReader(fail=True)])
def test_without_values_the_chat_works_as_before(reader: Any) -> None:
    class PlainModel:  # the pre-8-13 model interface (no keyword arguments)
        def generate_ir(self, request: Any, ontology: Any, mapping: Any) -> Any:
            return ir(["Course.name"], [{"field": "Department.name", "operator": "equals", "value": "수학과"}])

    body, executor = chat(PlainModel(), "수학과 과목", reader)
    assert body["status"] == "SUCCEEDED" and len(executor.queries) == 1


def test_repaired_output_still_passes_validation() -> None:
    model = HintModel(ir(["Course.name"], [{"field": "Course.department", "operator": "greater_than", "value": 3}]))
    body, executor = chat(model, "과목", FakeReader())
    assert body["status"] == "NOT_UNDERSTOOD" and executor.queries == []


def test_indexes_are_built_once_per_database_and_fingerprint() -> None:
    reader = FakeReader()
    indexes = ChatValueIndexes(reader, ONTOLOGY)
    context = make_context()
    first = indexes.get(context)
    assert first is not None and indexes.get(context) is first
    assert len(reader.reads) == len(hint_fields(context, ONTOLOGY))


def test_a_failed_read_is_retried_later_and_aliases_stay() -> None:
    now = [0.0]
    reader = FakeReader(fail=True)
    indexes = ChatValueIndexes(reader, ONTOLOGY, clock=lambda: now[0])
    context = make_context()
    outage = indexes.get(context)
    assert outage is not None and not outage.has_values("Department.name")
    assert indexes.get(context) is outage  # not re-read on every question
    reader.fail = False
    now[0] += value_hints.RETRY_SECONDS + 1
    recovered = indexes.get(context)
    assert recovered is not outage and recovered.has_values("Department.name")
    now[0] += 10 * value_hints.RETRY_SECONDS
    assert indexes.get(context) is recovered  # complete: kept


def test_an_overlong_hint_is_dropped_not_fatal() -> None:
    long_value = "가" * 199 + "나"
    index = ValueIndex({"Department.name": (long_value,)}, {}, nouns=NOUNS)
    [fits] = index.find("가" * 199 + "다 과목")  # a 200-char near miss still fits the prompt
    assert fits.fuzzy and fits.value == long_value
    assert index.find("가" * 200 + "다 과목") == []  # 201 chars: dropped instead of failing the prompt


# --- distinct value reader -------------------------------------------------------------------


class FakeCursor:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.executed: list[Any] = []

    def execute(self, query: Any, params: Any) -> None:
        self.executed.append((query, params))

    def fetchall(self) -> list[Any]:
        return self.rows


class FakeConnection:
    def __init__(self, rows: list[Any]) -> None:
        self.cursor_obj = FakeCursor(rows)
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self.cursor_obj

    def set_client_encoding(self, _encoding: str) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def test_reader_is_bounded_and_fails_quietly() -> None:
    config = PostgresConnectionConfig("postgresql://u:p@127.0.0.1:5432/db")
    connection = FakeConnection([("b",), ("a",), (None,), (3,), ("x" * 300,)])
    reader = PostgresDistinctValueReader({"db": config}, connector=lambda _config: connection)
    assert reader.read("db", "department", "dept_name", 10) == ["a", "b"]
    assert connection.closed
    [(query, params)] = connection.cursor_obj.executed
    assert params == (11,)  # one more than the limit, to detect overflow
    # Phase 8-21: read as text, so integer period columns list their values too
    assert query == 'SELECT DISTINCT CAST("dept_name" AS text) FROM "public"."department" WHERE "dept_name" IS NOT NULL LIMIT %s'
    odd = FakeConnection([])
    PostgresDistinctValueReader({"db": config}, connector=lambda _config: odd).read("db", 'a"b', "c", 3)
    assert odd.cursor_obj.executed[0][0].startswith('SELECT DISTINCT CAST("c" AS text) FROM "public"."a""b"')
    too_many = PostgresDistinctValueReader(
        {"db": config}, connector=lambda _config: FakeConnection([(str(i),) for i in range(4)])
    )
    assert too_many.read("db", "t", "c", 3) is OVER_LIMIT
    assert reader.read("other", "t", "c", 3) is None
    assert reader.read("db", "t", "c", 0) is None

    def broken(_config: Any) -> Any:
        raise RuntimeError("postgresql://u:p@secret")

    assert PostgresDistinctValueReader({"db": config}, connector=broken).read("db", "t", "c", 3) is None


def test_select_wildcards_of_known_entities_are_dropped() -> None:
    output = ir(["Schedule.*"], [{"field": "Schedule.day", "operator": "equals", "value": "월"}])
    assert repair_output(output, ONTOLOGY, available_fields=["Course.code"])["select"] == ["Course.code"]
    mixed = ir(["Course.name", "Schedule.*", "Room.*"])
    assert repair_output(mixed, ONTOLOGY, available_fields=["Course.code"])["select"] == ["Course.name", "Room.*"]


def test_a_wildcard_select_is_answered_with_the_card() -> None:
    model = HintModel(ir(["Schedule.*"], [{"field": "Department.name", "operator": "equals", "value": "국어국문학과"}]))
    body, executor = chat(model, "국어국문학과 과목", FakeReader())
    assert body["status"] == "SUCCEEDED" and len(executor.queries) == 1


def test_reader_presence_check_is_exact_and_bounded() -> None:
    config = PostgresConnectionConfig("postgresql://u:p@127.0.0.1:5432/db")
    connection = FakeConnection([("MON",)])
    reader = PostgresDistinctValueReader({"db": config}, connector=lambda _config: connection)
    assert reader.present("db", "t", "day", ["MON", "XYZ"]) == {"MON"}
    [(query, params)] = connection.cursor_obj.executed
    assert query == 'SELECT DISTINCT CAST("day" AS text) FROM "public"."t" WHERE CAST("day" AS text) = ANY(%s)'
    assert params == (["MON", "XYZ"],)
    assert reader.present("db", "t", "day", []) is None
    assert reader.present("db", "t", "day", ["x"] * 65) is None
    assert reader.present("other", "t", "day", ["x"]) is None
