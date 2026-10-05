"""Phase 8-21 A: coded columns searched through their labels ("교양" → 21, 22)."""

from __future__ import annotations

from typing import Any

import pytest

from backend.adaptive.api.chat_query import answer_chat_query
from backend.adaptive.api.value_hints import ValueIndex
from backend.adaptive.harness.errors import HarnessFailure
from backend.adaptive.mapping.values import ValueDictionary, ValueEntry, contains_label_values
from backend.adaptive.ontology.contracts import Operator
from tests.adaptive.test_legacy_view_partial_mapping import FakeExecutor
from tests.adaptive.test_value_dictionary import (
    DATABASE_ID,
    _Model,
    category,
    compile_only,
    make_context,
    make_profile,
    validate,
)

# Synthetic codes in the shape of a common-code table.
ISU = ValueDictionary(
    entries=tuple(
        ValueEntry(stored=code, label=label)
        for code, label in (("11", "전공필수"), ("12", "전공선택"), ("21", "교양필수"), ("22", "교양선택"), ("30", "일반선택"))
    ),
    reviewer="synthetic-test",
    reason="common-code table (synthetic)",
)


def coded_context(executor: Any = None) -> Any:
    return make_context(make_profile({"Course.category": ISU}), executor=executor)


# --- which values a word reaches --------------------------------------------------------------


def test_a_word_reaches_the_codes_whose_labels_hold_it() -> None:
    dictionaries = {"Course.category": ISU}
    assert contains_label_values("Course.category", Operator.CONTAINS, "교양", value_dictionaries=dictionaries) == ("21", "22")
    assert contains_label_values("Course.category", Operator.CONTAINS, "선택", value_dictionaries=dictionaries) == ("12", "22", "30")
    # a whole label binds exactly instead; other operators and fields are untouched
    assert contains_label_values("Course.category", Operator.CONTAINS, "교양필수", value_dictionaries=dictionaries) == ()
    assert contains_label_values("Course.category", Operator.EQUALS, "교양", value_dictionaries=dictionaries) == ()
    assert contains_label_values("Course.name", Operator.CONTAINS, "교양", value_dictionaries=dictionaries) == ()
    assert contains_label_values("Course.category", Operator.CONTAINS, "체육", value_dictionaries=dictionaries) == ()
    assert ISU.containing(3) == () and ISU.containing(" ") == ()


def test_names_stored_as_themselves_and_aliases_add_nothing() -> None:
    """Luna 8-21 review: "전공핵" reached 전공(핵심) through its alias 전공핵심 on the legacy
    profile, where LIKE had found nothing; legacy SQL must stay as it was."""
    from backend.adaptive.api import legacy_view_profile as lvp

    for word in ("전공핵", "전공", "교양", "컴융", "대학컴퓨터"):
        for field in lvp.VALUE_DICTIONARIES:
            assert contains_label_values(field, Operator.CONTAINS, word, value_dictionaries=lvp.VALUE_DICTIONARIES) == ()
    coded = ValueDictionary(
        entries=(ValueEntry(stored="11", label="전공필수", aliases=("전필수강",)),),
        reviewer="t",
        reason="t",
    )
    assert coded.containing("필수강") == () and coded.containing("필수") == ("11",)


# --- compiler and policy ----------------------------------------------------------------------


def test_compiler_and_policy_agree_on_the_label_list() -> None:
    context = coded_context()
    ir = category("contains", "교양")
    compiled = compile_only(context, ir)
    assert " LIKE %s OR " in compiled.sql and " IN (%s, %s))" in compiled.sql
    assert compiled.parameters == ["%교양%", "21", "22", 50]
    validate(context, ir, compiled)


@pytest.mark.parametrize(
    "tamper",
    [
        lambda sql, params: (sql, ["%교양%", "21", "30", 50]),  # another code
        lambda sql, params: (sql.replace(", %s))", "))"), ["%교양%", "21", 50]),  # one value short
        lambda sql, params: (sql.replace("IN (%s, %s)", "IN (%s, '22')"), ["%교양%", "21", 50]),  # literal
        lambda sql, params: (sql.replace(" OR ", " AND "), params),
        lambda sql, params: (sql.replace("(%s, %s)", "(SELECT %s)").replace(", %s))", "))"), params),
        lambda sql, params: (sql.split(" WHERE ")[0] + ' WHERE "t0"."category" LIKE %s LIMIT %s', ["%교양%", 50]),
    ],
)
def test_the_policy_refuses_any_other_shape(tamper: Any) -> None:
    context = coded_context()
    ir = category("contains", "교양")
    compiled = compile_only(context, ir)
    sql, parameters = tamper(compiled.sql, list(compiled.parameters))
    with pytest.raises(HarnessFailure):
        validate(context, ir, compiled.model_copy(update={"sql": sql, "parameters": parameters}))


def test_a_label_list_where_none_belongs_is_refused() -> None:
    context = coded_context()
    ir = category("contains", "교양필수")  # a whole label: no list
    compiled = compile_only(context, ir)
    assert compiled.parameters == ["%21%", 50]
    column = compiled.sql.split(" WHERE ")[1].split(" LIKE ")[0]
    forged = compiled.sql.split(" WHERE ")[0] + f" WHERE ({column} LIKE %s OR {column} IN (%s)) LIMIT %s"
    with pytest.raises(HarnessFailure):
        validate(context, ir, compiled.model_copy(update={"sql": forged, "parameters": ["%21%", "21", 50]}))


# --- chat -------------------------------------------------------------------------------------


def test_the_chat_finds_coded_rows_by_a_label_word() -> None:
    executor = FakeExecutor()
    answer = answer_chat_query(
        database_id=DATABASE_ID,
        question="교양 과목",
        pg_contexts={DATABASE_ID: coded_context(executor)},
        model=_Model([{"field": "Course.category", "operator": "equals", "value": "교양"}]),
    )
    assert answer["status"] == "SUCCEEDED"
    assert executor.queries[0].parameters == ["%교양%", "21", "22", 200]
    assert answer["understood"]["filters"] == [
        {"field": "Course.category", "label": "이수 구분", "operator": "contains", "value": "교양"}
    ]


# --- hints ------------------------------------------------------------------------------------


def test_the_start_shared_by_labels_is_a_hint() -> None:
    index = ValueIndex({"Course.category": ("11", "12", "21", "22", "30")}, {"Course.category": ISU})
    assert [hint.payload() for hint in index.find("소웨 전공 과목")] == [
        {"text": "전공", "field": "Course.category", "value": "전공"}
    ]
    # a start only one label has is no group; a whole label still names its code
    assert index.find("일반 과목") == []
    assert [hint.value for hint in index.find("교양필수 과목")] == ["21"]


def test_labels_of_several_words_give_no_start_hints() -> None:
    names = ValueDictionary(
        entries=(
            ValueEntry(stored="1", label="공과대학 전자공학과"),
            ValueEntry(stored="2", label="공과대학 기계공학부"),
        ),
        reviewer="t",
        reason="t",
    )
    index = ValueIndex({}, {"Department.name": names})
    assert index.find("공과 과목") == []
