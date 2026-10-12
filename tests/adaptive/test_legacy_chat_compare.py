"""Phase 7-15 legacy chatbot vs QueryIR comparison: loading, scoring, live flow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.adaptive.evaluation import legacy_chat_compare as lcc
from backend.adaptive.evaluation.legacy_chat_compare import Scope, score_answer

A, B, C, D = ("0001", "01"), ("0002", "01"), ("0003", "02"), ("0004", "01")


def test_question_set_loads_with_fixed_scopes() -> None:
    question_set = lcc.load_question_set()
    scopes = [case.scope for case in question_set.cases]
    assert len(question_set.cases) == 30
    assert scopes.count(Scope.ONTOLOGY) == 22
    assert scopes.count(Scope.OUTSIDE_SCOPE) == 5
    assert scopes.count(Scope.UNSUPPORTED_FIELD) == 3
    for case in question_set.cases:
        if case.reference_sql is not None:
            assert case.reference_sql.startswith(lcc.REFERENCE_PREFIX)


def _write(tmp_path: Path, cases: list[dict[str, Any]], **extra: Any) -> Path:
    path = tmp_path / "set.json"
    payload = {
        "version": lcc.QUESTION_SET_VERSION,
        "database": "x",
        "authored_by": "x",
        "cases": cases,
        **extra,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


GOOD = {"case_id": "q01", "scope": "ONTOLOGY", "question": "질문",
        "reference_sql": lcc.REFERENCE_PREFIX + "day_of_week = '월'"}


@pytest.mark.parametrize(
    "cases",
    [
        [GOOD, GOOD],
        [{**GOOD, "reference_sql": None}],
        [{**GOOD, "scope": "UNSUPPORTED_FIELD"}],
        [{**GOOD, "reference_sql": "SELECT * FROM student"}],
        [{**GOOD, "reference_sql": GOOD["reference_sql"] + "; DROP TABLE x"}],
        [{**GOOD, "reference_sql": GOOD["reference_sql"] + " -- x"}],
        [{**GOOD, "extra": 1}],
        [{**GOOD, "case_id": "1"}],
        [{**GOOD, "reference_sql": GOOD["reference_sql"] + " UNION SELECT student_id, name FROM student"}],
        [{**GOOD, "reference_sql": GOOD["reference_sql"] + " AND subject_code IN (SELECT student_id FROM student)"}],
        [{**GOOD, "reference_sql": GOOD["reference_sql"] + " LIMIT 5"}],
        [{**GOOD, "reference_sql": "SELECT DISTINCT subject_code, section FROM v_course_info WHERE TRUE ORDER BY 1"}],
    ],
)
def test_question_set_rejects_malformed_cases(tmp_path: Path, cases: list[dict[str, Any]]) -> None:
    with pytest.raises(Exception):
        lcc.load_question_set(_write(tmp_path, cases))


def test_question_set_rejects_duplicate_keys_and_versions(tmp_path: Path) -> None:
    path = tmp_path / "dup.json"
    path.write_text('{"version": "x", "version": "y"}', encoding="utf-8")
    with pytest.raises(ValueError):
        lcc.load_question_set(path)
    with pytest.raises(Exception):
        lcc.load_question_set(_write(tmp_path, [GOOD], version="other"))


@pytest.mark.parametrize(
    ("keys", "answered", "expected"),
    [
        ({A, B}, True, "EXACT"),
        ({A}, True, "INCOMPLETE"),
        (set(), True, "EMPTY"),
        ({A, D}, True, "INCORRECT"),
        ({D}, True, "INCORRECT"),
        ({A, B, D}, True, "INCORRECT"),
        (None, True, "UNKEYED"),
        (None, False, "NO_ANSWER"),
    ],
)
def test_scoring_with_reference(keys: Any, answered: bool, expected: str) -> None:
    result = score_answer(Scope.ONTOLOGY, answered=answered, keys=keys, reference={A, B})
    assert result["outcome"] == expected


def test_proper_subset_counts_as_correct_only_when_the_cap_was_reached() -> None:
    capped = score_answer(Scope.ONTOLOGY, answered=True, keys={A}, reference={A, B}, truncated=True)
    uncapped = score_answer(Scope.ONTOLOGY, answered=True, keys={A}, reference={A, B}, truncated=False)
    assert capped["outcome"] == "CORRECT_SUBSET" and uncapped["outcome"] == "INCOMPLETE"
    assert "INCOMPLETE" not in lcc.ACCEPTABLE


def test_null_sections_do_not_collide_with_text() -> None:
    assert lcc._as_keys([["0001", None], ["0001", "None"]]) == {("0001", None), ("0001", "None")}
    assert lcc._sorted_keys({("0001", None), ("0001", "01")}) == [("0001", "01"), ("0001", None)]


def test_legacy_limit_is_read_from_the_executed_sql() -> None:
    assert lcc._sql_limit("SELECT * FROM v_course_info LIMIT 50") == 50
    assert lcc._sql_limit("SELECT c.* FROM (SELECT x LIMIT 50) m LIMIT 200") == 200
    assert lcc._sql_limit("SELECT 1") is None


def test_scoring_precision_and_recall() -> None:
    result = score_answer(Scope.ONTOLOGY, answered=True, keys={A, D}, reference={A, B, C})
    assert result["precision"] == 0.5 and result["recall"] == pytest.approx(0.3333)
    assert result["correct_count"] == 1


@pytest.mark.parametrize(
    ("answered", "refused", "keys", "expected"),
    [
        (False, True, None, "REFUSED"),
        (False, False, None, "NO_ANSWER"),
        (True, False, set(), "EMPTY_ANSWER"),
        (True, False, {A}, "ANSWERED_WITHOUT_BASIS"),
        (True, False, None, "ANSWERED_WITHOUT_BASIS"),
    ],
)
def test_scoring_unsupported_field(answered: bool, refused: bool, keys: Any, expected: str) -> None:
    result = score_answer(Scope.UNSUPPORTED_FIELD, answered=answered, keys=keys, reference=None, refused=refused)
    assert result["outcome"] == expected


def test_relax_filters_only_touches_string_equals() -> None:
    filters = [
        {"field": "Instructor.name", "operator": "equals", "value": "김 교수"},
        {"field": "Course.category", "operator": "contains", "value": "교양"},
        {"field": "Course.credits", "operator": "equals", "value": 3},
        {"field": "Schedule.day", "operator": "equals", "value": "월"},
    ]
    relaxed = lcc.relax_filters(filters)
    assert [item["operator"] for item in relaxed] == ["contains", "contains", "equals", "equals"]
    assert filters[0]["operator"] == "equals"


class FakeCursor:
    def __init__(self, results: dict[str, list[tuple[str, str]]]) -> None:
        self.results = results
        self._rows: list[tuple[str, str]] = []

    def execute(self, sql: str) -> None:
        self._rows = self.results[sql]

    def fetchall(self) -> list[tuple[str, str]]:
        return self._rows


def test_live_flow_scores_both_paths_and_prints_no_values(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    question_set = lcc.load_question_set(_write(tmp_path, [
        GOOD,
        {"case_id": "q28", "scope": "UNSUPPORTED_FIELD", "question": "2024년 강의"},
    ]))
    calls: list[str] = []

    def post(url: str, payload: dict[str, Any], _timeout: float) -> tuple[int, Any]:
        calls.append(url)
        if url == lcc.LEGACY_URL:
            return 200, {"sql": "SELECT * FROM v_course_info", "data": [
                {"subject_code": "0001", "section": "01", "subject_name": "자료구조"},
            ]}
        if payload["query"] == "2024년 강의":
            return 200, {"version": "adaptive-chat-query-v1", "database_id": "legacy-view", "status": "UNSUPPORTED",
                         "unsupported": [{"field": "CourseOffering.year", "label": "개설 연도"}]}
        return 200, {"version": "adaptive-chat-query-v1", "database_id": "legacy-view", "status": "SUCCEEDED",
                     "columns": ["course_code", "section"], "row_count": 2, "limit": 50,
                     "rows": [{"course_code": "0001", "section": "01"}, {"course_code": "0002", "section": "01"}],
                     "understood": {"filters": []}}

    cursor = FakeCursor({GOOD["reference_sql"]: [A, B]})
    run = lcc.run_live(question_set, post=post, cursor=cursor, context=None, label="unit")
    first, second = run["cases"]
    # One legacy row without a reached LIMIT is an incomplete answer, not a capped one.
    assert first["scores"]["legacy"]["outcome"] == "INCOMPLETE"
    assert first["scores"]["queryir"]["outcome"] == "EXACT"
    assert second["scores"]["queryir"]["outcome"] == "REFUSED"
    assert second["scores"]["legacy"]["outcome"] == "ANSWERED_WITHOUT_BASIS"
    assert calls.count(lcc.LEGACY_URL) == 2 and calls.count(lcc.QUERYIR_URL) == 2
    summary = lcc.summarize(run)
    assert summary["totals"]["queryir"]["ALL"]["acceptable"] == 2
    assert summary["totals"]["legacy"]["ALL"]["acceptable"] == 0
    assert all("question" not in row for row in summary["cases"])
    lcc._print_totals(summary)
    printed = capsys.readouterr().out
    assert "자료구조" not in printed and "SELECT" not in printed and "0001" not in printed

    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
    assert lcc.main(["--summarize", str(run_path)]) == 0
    printed = capsys.readouterr().out
    for secret in ("자료구조", "SELECT", "0001", "질문", "2024년"):
        assert secret not in printed

    path = lcc._write_private(tmp_path, "unit", run)
    assert path == tmp_path / lcc.REPORT_DIR / "unit.json"
    with pytest.raises(FileExistsError):
        lcc._write_private(tmp_path, "unit", run)


def test_legacy_errors_and_unkeyed_rows() -> None:
    def post_error(_url: str, _payload: dict[str, Any], _timeout: float) -> tuple[int, Any]:
        return 500, {"detail": "boom"}

    assert lcc.ask_legacy(post_error, "q")["answered"] is False

    def post_unkeyed(_url: str, _payload: dict[str, Any], _timeout: float) -> tuple[int, Any]:
        return 200, {"sql": "SELECT subject_name FROM v_course_info", "data": [{"subject_name": "x"}]}

    record = lcc.ask_legacy(post_unkeyed, "q")
    assert record["answered"] is True and record["keys"] is None

    def post_raises(*_args: Any) -> tuple[int, Any]:
        raise OSError("down")

    assert lcc.ask_queryir(post_raises, "q") == {
        "answered": False, "error": "TRANSPORT", "seconds": pytest.approx(0, abs=5),
        "elapsed_ms": pytest.approx(0, abs=5000),
    }


def test_policy_variants_replay_the_same_filters_without_a_model_call() -> None:
    from decimal import Decimal

    from tests.adaptive.test_chat_query_endpoint import RecordingExecutor
    from tests.adaptive.test_legacy_view_partial_mapping import make_context

    row = {
        "Course.code": "0001", "Course.name": "자료구조", "Course.credits": Decimal("3"),
        "Course.category": "전공", "Course.target_grade": 2, "Department.name": "컴퓨터학과",
        "Instructor.name": "홍길동",
        "CourseOffering.section": "01", "CourseOffering.capacity": 40, "CourseOffering.enrolled": 1,
        "Schedule.day": "월", "Schedule.start_time": "13:00", "Schedule.end_time": "14:00",
    }
    executor = RecordingExecutor(rows=[row])
    context = make_context(executor=executor)
    filters = [
        {"field": "Instructor.name", "label": "교원명", "operator": "equals", "value": "홍길동"},
        {"field": "Course.credits", "label": "학점", "operator": "equals", "value": 3},
    ]
    plain = lcc.rerun_with_policy(context, "q", filters, limit=200, relax=False)
    relaxed = lcc.rerun_with_policy(context, "q", filters, limit=200, relax=True)
    assert plain["answered"] and relaxed["answered"]
    assert plain["keys"] == [("0001", "01")] and plain["limit"] == 200 and plain["capped"] is False
    first, second = executor.queries
    # Same compiled path. Since Phase 7-16 the chat endpoint itself relaxes
    # name-like equals and fixes the row cap, so both replays coincide.
    assert first.parameters == second.parameters == ["%홍길동%", 3, 200]
    assert first.sql == second.sql


def test_post_json_is_loopback_only_and_refuses_redirects() -> None:
    assert isinstance(
        next(h for h in lcc._OPENER.handlers if isinstance(h, lcc.urllib.request.HTTPRedirectHandler)),
        lcc._NoRedirect,
    )


def test_post_json_is_loopback_only() -> None:
    with pytest.raises(ValueError):
        lcc._post_json("http://example.com/api", {}, 1.0)


def test_cli_check_and_live_without_env(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    assert lcc.main(["--check"]) == 0
    assert json.loads(capsys.readouterr().out)["cases"] == 30
    monkeypatch.delenv(lcc.DSN_ENV, raising=False)
    assert lcc.main(["--live"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "ENV_MISSING"
    assert lcc.main(["--live", "--label", "Bad Label"]) == 2
