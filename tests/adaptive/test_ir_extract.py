"""Phase 5-22: evaluation-only single-fence IR extraction."""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.adaptive.evaluation.direct_sql import extract_sql, find_code_fences
from backend.adaptive.evaluation.ir_extract import SingleFenceIrModel, extract_single_fence_ir

F = "```"
IR = {"entity": "Course", "select": ["Course.name"], "filters": [], "limit": 50}


def test_find_code_fences_matches_sql_rule() -> None:
    assert find_code_fences(F + "sql" + chr(10) + "SELECT 1" + chr(10) + F) == ["SELECT 1" + chr(10)]
    assert extract_sql(F + "sql" + chr(10) + "SELECT 1;" + chr(10) + F) == "SELECT 1"


@pytest.mark.parametrize(
    "raw",
    [
        F + "json" + chr(10) + json.dumps(IR) + chr(10) + F,
        "  " + F + chr(10) + json.dumps(IR) + F + "  ",
        "Here it is:" + chr(10) + F + "json" + chr(10) + json.dumps(IR) + chr(10) + F + chr(10) + "done",
    ],
)
def test_single_fence_is_parsed(raw: str) -> None:
    assert extract_single_fence_ir(raw) == IR


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps(IR),
        F + "json" + chr(10) + "not json" + chr(10) + F,
        F + "json" + chr(10) + '{"a": 1, "a": 2}' + chr(10) + F,
        F + "json" + chr(10) + '{"a": NaN}' + chr(10) + F,
        F + chr(10) + json.dumps(IR) + chr(10) + F + chr(10) + F + chr(10) + json.dumps(IR) + chr(10) + F,
        "",
    ],
)
def test_other_strings_are_untouched(raw: str) -> None:
    assert extract_single_fence_ir(raw) is raw


@pytest.mark.parametrize("value", [IR, None, 5, ["x"]])
def test_non_strings_pass_through(value: Any) -> None:
    assert extract_single_fence_ir(value) is value


def test_wrapper_delegates_once() -> None:
    calls: list[Any] = []

    class _Model:
        def generate_ir(self, request: Any, ontology: Any, mapping: Any) -> Any:
            calls.append((request, ontology, mapping))
            return F + "json" + chr(10) + json.dumps(IR) + chr(10) + F

    wrapped = SingleFenceIrModel(_Model())
    assert wrapped.generate_ir("r", "o", "m") == IR
    assert calls == [("r", "o", "m")]
    with pytest.raises(ValueError):
        SingleFenceIrModel(object())



def test_deeply_nested_fence_is_untouched() -> None:
    deep = "```json" + chr(10) + "[" * 200000 + "]" * 200000 + chr(10) + "```"
    assert extract_single_fence_ir(deep) is deep
