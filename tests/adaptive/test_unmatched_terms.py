"""Phase 9-21, offline fake-output coverage; development questions, not gold."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.adaptive.api.chat_query import answer_chat_query
from backend.adaptive.api.unmatched_terms import GENERAL_WORDS, find_unmatched_terms
from backend.adaptive.api.value_hints import ValueIndex, domain_nouns
from backend.adaptive.mapping.extensions import ExtensionField
from backend.adaptive.mapping.values import ValueDictionary, ValueEntry
from backend.adaptive.ontology import load_default_ontology
from backend.adaptive.harness.contracts import QueryIR
from tests.adaptive.test_chat_query_endpoint import FakeModel, RecordingExecutor, ir
from tests.adaptive.test_legacy_view_partial_mapping import DATABASE_ID, make_context

ONTOLOGY = load_default_ontology()


class HintModel(FakeModel):
    def generate_ir(self, request, ontology, mapping, **kwargs):
        return super().generate_ir(request, ontology, mapping)


def condition(field, value, operator="contains"):
    return {"field": field, "operator": operator, "value": value}


def notice(question, filters=(), **kwargs):
    query = QueryIR.model_validate(ir(["Course.name"], list(filters)))
    return find_unmatched_terms(question, ONTOLOGY, query.filters, **kwargs)


def answer(question, filters=(), *, indexes=None, output=None, rows=None):
    executor = RecordingExecutor(rows=rows)
    context = make_context(executor=executor)
    model = HintModel(output if output is not None else ir(["Course.name"], list(filters)))
    result = answer_chat_query(database_id=DATABASE_ID, question=question,
                               pg_contexts={DATABASE_ID: context}, model=model, value_indexes=indexes)
    return result, model, executor


def test_dropped_abbreviation_notices_without_changing_conditions_or_call_count():
    result, model, executor = answer("컴융 전공 보여줘", [condition("Course.category", "전공")])
    assert result["status"] == "SUCCEEDED" and result["row_count"] == 1
    assert result["unmatched_terms"] == ["컴융"]
    assert len(model.calls) == len(executor.queries) == 1
    assert executor.queries[0].parameters == ["%전공%", 200]


def test_dictionary_abbreviation_is_covered_with_department_condition():
    dictionary = ValueDictionary(entries=(ValueEntry(stored="컴퓨터융합학부", label="컴퓨터융합학부", aliases=("컴융",)),), reviewer="test", reason="fixture")
    index = ValueIndex({}, {"Department.name": dictionary}, nouns=domain_nouns(ONTOLOGY))
    indexes = SimpleNamespace(get=lambda context: index)
    result, model, executor = answer("컴융 전공 보여줘", [condition("Department.name", "컴퓨터융합학부"), condition("Course.category", "전공")], indexes=indexes)
    assert "unmatched_terms" not in result
    assert result["understood"]["filters"][0]["value"] == "컴퓨터융합학부"
    assert len(model.calls) == len(executor.queries) == 1
    # A found value hint itself counts as coverage, as specified, even if
    # the model omitted that condition. The notice does not invent filters.
    assert notice("컴융 과목", hints=index.find("컴융 과목")) == []


@pytest.mark.parametrize("question", ["오후 3시 이후", "3학점", "60명 이상", "월요일 화요일 수요일 목요일 금요일 토요일 일요일", "2학년 1학년이", "교수님 수업 있어?", "정원이 100명 넘는 강의"])
def test_language_time_and_numeric_scaffolding(question):
    assert notice(question) == []


@pytest.mark.parametrize("phrase", ["정리해줘", "보여줘", "알려주세요", "찾아주세요"])
def test_request_verbs_do_not_raise_notices(phrase):
    assert notice(f"교양 과목 {phrase}", [condition("Course.category", "교양")]) == []


@pytest.mark.parametrize("phrase", ["정리해줘", "정리해 줘", "추천해주세요", "검색해봐", "확인할래", "비교해", "나열", "소개해줘"])
def test_general_request_endings_keep_unmatched_abbreviation(phrase):
    assert notice(f"컴융 전공 {phrase}", [condition("Course.category", "전공")]) == ["컴융"]


def test_request_ending_does_not_hide_unknown_stem():
    assert notice("컴융해줘 전공", [condition("Course.category", "전공")]) == ["컴융해줘"]


def test_normalization_particles_values_extensions_and_limits():
    assert notice("컴융에서 컴융은 컴융을 보여줘") == ["컴융"]
    assert notice("DATABASES 과목", [condition("Course.name", "Databases")]) == []
    assert notice("수학과 강의", [condition("Department.name", "수학과")]) == []
    assert notice("전공핵심", [condition("Course.category", "전공(핵심)")]) == []
    assert notice("실습실에서", extensions={"Schedule.ext_room": ExtensionField(label="실습실", data_type="string")}) == []
    assert notice("가나다 라마바 사아자 차카타 파하가 나다라 마바사") == ["가나다", "라마바", "사아자", "차카타", "파하"]
    assert notice("x 가 123") == []
    assert "컴융" not in GENERAL_WORDS


def test_general_vocabulary_is_a_closed_language_list():
    assert GENERAL_WORDS == frozenset("과목 수업 강의 강좌 목록 전체 모든 정보 이상 이하 이후 이전 초과 미만 오전 오후 저녁 아침 밤 시간 시각 요일 월요일 화요일 수요일 목요일 금요일 토요일 일요일 중 중에 보여줘 알려줘 찾아줘 있어 뭐야 보여주세요 알려주세요 찾아주세요 하는 듣는 있는 없는 시작하는 끝나는 종료하는 넘는 열리는 개설된 교수님".split())


def test_field_entity_aliases_filter_operator_and_time_rule_words():
    from backend.adaptive.api.time_hints import find_time_conditions
    assert notice("학수번호 학부명 담당교수 개설과목") == []
    assert notice("contains", [condition("Course.name", "다른값")]) == []
    assert notice("오후 3시 반부터 수업", timed=find_time_conditions("오후 3시 반부터 수업")) == []


@pytest.mark.parametrize("question,output,status", [
    ("컴융 강의실 수업", ir(["Course.name"]), "UNSUPPORTED"),
    ("컴융 수업", {"bad": True}, "NOT_UNDERSTOOD"),
    ("컴융 2024년 수업", ir(["Course.name"]), "UNSUPPORTED"),
])
def test_refusals_do_not_add_notices(question, output, status):
    result, _, executor = answer(question, output=output)
    assert result["status"] == status and "unmatched_terms" not in result
    assert executor.queries == []


def test_empty_answer_keeps_both_kinds_of_notice():
    result, model, _ = answer("컴융 전공 보여줘", [condition("Course.category", "전공")], rows=[])
    assert result["unmatched_terms"] == ["컴융"] and result["unmatched"]
    assert len(model.calls) == 1


# Fixed gold-like conditions derived from the checked-in reference SQL;
# these are development questions, never an unseen-school evaluation.
F = condition
LEGACY_CASES = {
    "q01": ([F("Department.name", "컴퓨터융합학부"), F("Course.category", "전공")], "SUCCEEDED", []),
    "q02": ([F("Course.category", "교양"), F("Schedule.day", "금", "equals")], "SUCCEEDED", []),
    "q03": ([F("Course.credits", 3, "equals")], "SUCCEEDED", []),
    "q04": ([F("Schedule.day", "월", "equals"), F("Schedule.start_time", "12:00", "less_than")], "SUCCEEDED", []),
    "q05": ([F("Department.name", "수학과")], "SUCCEEDED", []),
    "q06": ([F("Course.category", "전공(핵심)"), F("Schedule.day", "화", "equals")], "SUCCEEDED", []),
    "q07": ([F("Course.name", "데이터베이스")], "SUCCEEDED", []),
    "q08": ([F("Course.name", "딥러닝")], "SUCCEEDED", []),
    "q09": ([F("Instructor.name", "김소연")], "SUCCEEDED", []),
    "q10": ([F("Instructor.name", "이규철")], "SUCCEEDED", []),
    "q11": ([F("CourseOffering.capacity", 100, "greater_than")], "SUCCEEDED", []),
    "q13": ([F("Schedule.start_time", "15:00", "equals")], "SUCCEEDED", []),
    "q14": ([F("Course.credits", 2, "equals"), F("Course.category", "교양")], "SUCCEEDED", []),
    "q15": ([F("Department.name", "경영학부"), F("Course.category", "전공(기초)")], "SUCCEEDED", []),
    "q16": ([F("Schedule.day", "목", "equals"), F("Schedule.start_time", "18:00", "greater_than_or_equal")], "SUCCEEDED", []),
    "q17": ([F("Department.name", "영어영문학과"), F("Course.credits", 3, "equals"), F("Course.category", "전공")], "SUCCEEDED", []),
    "q18": ([F("Course.category", "일반선택")], "SUCCEEDED", []),
    "q19": ([F("Schedule.day", "토", "equals")], "SUCCEEDED", []),
    "q20": ([F("Course.name", "미적분학")], "SUCCEEDED", []),
    "q21": ([F("Course.name", "컴퓨터프로그래밍")], "SUCCEEDED", []),
    "q22": ([F("Department.name", "물리학과"), F("Schedule.day", "월", "equals")], "SUCCEEDED", []),
    "q31": ([F("Department.name", "간호학과"), F("Course.category", "전공"), F("Schedule.day", "수", "equals"), F("Schedule.start_time", "12:00", "greater_than_or_equal")], "SUCCEEDED", []),
    "q12": ([F("Department.name", "컴퓨터융합학부")], "SUCCEEDED", ["아직", "자리"]),
    "q23": ([F("Department.name", "컴퓨터융합학부"), F("Course.target_grade", 3, "equals"), F("Course.category", "전공")], "SUCCEEDED", []),
    "q24": ([F("Course.target_grade", 1, "equals"), F("Course.category", "교양")], "SUCCEEDED", []),
    "q25": ([], "UNSUPPORTED", []),
    "q26": ([F("Department.name", "컴퓨터융합학부")], "UNSUPPORTED", []),
    "q28": ([], "UNSUPPORTED", []),
    "q29": ([F("CourseOffering.semester", 1, "equals"), F("Course.category", "전공")], "UNSUPPORTED", []),
    "q30": ([F("CourseOffering.semester", 2, "equals"), F("Course.category", "교양")], "UNSUPPORTED", []),
}
CASES = json.loads((Path(__file__).parents[2] / "backend/adaptive/evaluation/data/legacy-chat-compare-v1.json").read_text(encoding="utf-8"))["cases"]


def test_legacy_notice_table_covers_exactly_thirty_questions():
    assert len(CASES) == 30 and set(LEGACY_CASES) == {case["case_id"] for case in CASES}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case_id"])
def test_legacy_development_question_notice_table(case):
    filters, status, terms = LEGACY_CASES[case["case_id"]]
    result, model, _ = answer(case["question"], filters)
    assert result["status"] == status
    assert result.get("unmatched_terms", []) == terms
    assert len(model.calls) <= 1
