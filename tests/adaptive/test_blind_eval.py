"""Phase 8-22: blind evaluation runner for teammate-built databases."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.adaptive.evaluation import blind_eval as be

SECRET_QUESTION = "비밀 질문: 컴공 전공필수 과목"


def write_questions(root: Path, eval_id: str = "school-a", **overrides: Any) -> Path:
    document = {
        "format": "blind-eval-questions-v1",
        "database_id": "school-a",
        "author": "팀원",
        "fixed_at": "2026-10-10",
        "questions": [
            {"id": "a01", "text": SECRET_QUESTION, "expect": {"codes": ["CS101", "CS205"]}},
            {"id": "a02", "text": "강의실이 공학관인 수업", "expect": "decline"},
            {"id": "a03", "text": "월요일 수업", "expect": {"codes": ["CS101"]}},
        ],
        **overrides,
    }
    directory = be.eval_dir(root, eval_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "questions.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def rows(*codes: str) -> list[dict[str, Any]]:
    return [{"course_code": code, "course_name": "x"} for code in codes]


ANSWERS = {
    SECRET_QUESTION: {"status": "SUCCEEDED", "rows": rows("CS101", "CS205", "CS205"),
                      "understood": {"filters": [{"field": "Course.category", "label": "이수 구분", "operator": "contains", "value": "전공필수"}]}},
    "강의실이 공학관인 수업": {"status": "UNSUPPORTED", "unsupported": [{"concept": "classroom", "label": "강의실"}]},
    "월요일 수업": {"status": "SUCCEEDED", "rows": rows("CS101", "MA100")},
}


def ask(_database_id: str, text: str) -> dict[str, Any]:
    return ANSWERS[text]


@pytest.mark.parametrize("sanitize", [False, True])
@pytest.mark.parametrize("reported_elapsed", [None, 12.5])
def test_run_latency_excludes_grading_and_preserves_answer_elapsed(tmp_path, monkeypatch, sanitize, reported_elapsed):
    write_questions(tmp_path)
    clock = [0.0]
    monkeypatch.setattr(be.time, "perf_counter", lambda: clock[0])
    original_grade = be.grade

    def timed_ask(database_id, text):
        clock[0] += 0.025
        answer = dict(ANSWERS[text])
        if reported_elapsed is not None:
            answer["elapsed_ms"] = reported_elapsed
        return answer

    def slow_grade(question, answer):
        clock[0] += 10.0
        return original_grade(question, answer)

    monkeypatch.setattr(be, "grade", slow_grade)
    document = be.run(tmp_path, "school-a", ask=timed_ask, sanitize=sanitize)
    assert [record["elapsed_ms"] for record in document["results"]] == [
        25.0 if reported_elapsed is None else reported_elapsed
    ] * 3


# --- checking the file without showing it -----------------------------------------------------


def test_check_prints_counts_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_questions(tmp_path)
    assert be.main(["check", "school-a", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    summary = json.loads(out)
    assert (summary["questions"], summary["code_answers"], summary["declines"]) == (3, 2, 1)
    assert SECRET_QUESTION not in out and "CS101" not in out


@pytest.mark.parametrize(
    ("overrides", "positions"),
    [
        ({"questions": [{"id": "a01", "text": "x", "expect": {"codes": []}}]}, [1]),
        ({"questions": [{"id": "a01", "text": "x", "expect": "maybe"}, {"id": "a02", "text": "y", "expect": "decline"}]}, [1]),
        ({"questions": [{"id": "a 1", "text": "x", "expect": "decline"}]}, [1]),
        ({"questions": [{"id": "a01", "text": "x", "expect": {"codes": ["A", "A"]}}]}, [1]),
        ({"questions": [{"id": "a01", "text": "x", "expect": "decline", "note": "extra"}]}, [1]),
        ({"questions": [{"id": "a01", "text": "x", "expect": "decline"}, {"id": "a01", "text": "y", "expect": "decline"}]}, []),
        ({"format": "v0"}, []),
        ({"database_id": "../etc"}, []),
    ],
)
def test_invalid_files_name_positions_not_text(tmp_path: Path, overrides: dict[str, Any], positions: list[int], capsys) -> None:
    write_questions(tmp_path, **overrides)
    with pytest.raises(be.BlindEvalError) as caught:
        be.check(tmp_path, "school-a")
    assert caught.value.code == "QUESTIONS_INVALID" and caught.value.positions == positions
    assert be.main(["check", "school-a", "--root", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "QUESTIONS_INVALID" in err and SECRET_QUESTION not in err


def test_eval_ids_cannot_leave_the_private_folder(tmp_path: Path) -> None:
    for eval_id in ("../x", "A", "", "a/b"):
        with pytest.raises(be.BlindEvalError):
            be.eval_dir(tmp_path, eval_id)
    with pytest.raises(be.BlindEvalError) as caught:
        be.check(tmp_path, "missing")
    assert caught.value.code == "QUESTIONS_MISSING"


# --- grading ----------------------------------------------------------------------------------


def question(expect: Any) -> be.Question:
    return be.Question(id="q", text="t", expect=expect)


@pytest.mark.parametrize(
    ("expect", "answer", "result"),
    [
        ({"codes": ["A", "B"]}, {"status": "SUCCEEDED", "rows": rows("A", "B")}, "PASS"),
        ({"codes": ["A", "B"]}, {"status": "SUCCEEDED", "rows": rows("A")}, "FAIL"),
        ({"codes": ["A"]}, {"status": "SUCCEEDED", "rows": rows(*["A"] * 200)}, "CAPPED"),
        ({"codes": ["A"]}, {"status": "UNSUPPORTED"}, "FAIL"),
        ({"codes": ["A"]}, {"http_status": 500}, "ERROR"),
        ("decline", {"status": "UNSUPPORTED"}, "PASS"),
        ("decline", {"status": "NOT_UNDERSTOOD", "failure_code": "UNKNOWN_FIELD"}, "PASS"),
        ("decline", {"status": "SUCCEEDED", "rows": []}, "FAIL"),
        ("decline", {}, "ERROR"),
    ],
)
def test_grading(expect: Any, answer: dict[str, Any], result: str) -> None:
    assert be.grade(question(expect), answer)["result"] == result


def test_records_keep_counts_not_rows() -> None:
    record = be.grade(question({"codes": ["A", "B"]}), {"status": "SUCCEEDED", "rows": rows("A", "ZX999")})
    assert (record["missing"], record["extra"], record["row_count"]) == (1, 1, 2)
    assert "rows" not in record and "ZX999" not in json.dumps(record)


# --- runs -------------------------------------------------------------------------------------


def test_the_first_run_is_pinned_and_never_overwritten(tmp_path: Path) -> None:
    path = write_questions(tmp_path)
    first = be.run(tmp_path, "school-a", ask=ask, commit="abc", model="m")
    assert first["first_run"] and first["run"] == 1
    assert first["summary"] == {"total": 3, "pass": 2, "fail": 1, "capped": 0, "error": 0}
    second = be.run(tmp_path, "school-a", ask=ask, commit="def", model="m")
    assert not second["first_run"] and second["run"] == 2
    directory = be.eval_dir(tmp_path, "school-a")
    assert json.loads((directory / "run-001.json").read_text(encoding="utf-8"))["code_commit"] == "abc"
    assert [row["first_run"] for row in be.report(tmp_path, "school-a")] == [True, False]
    # changing the questions after the first run is refused; a new eval id is needed
    data = json.loads(path.read_text(encoding="utf-8"))
    data["questions"][2]["expect"] = {"codes": ["CS101", "MA100"]}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(be.BlindEvalError) as caught:
        be.run(tmp_path, "school-a", ask=ask)
    assert caught.value.code == "QUESTIONS_CHANGED"
    assert sorted(item.name for item in directory.glob("run-*.json")) == ["run-001.json", "run-002.json"]


def test_a_failing_question_does_not_stop_the_run(tmp_path: Path) -> None:
    write_questions(tmp_path)

    def flaky(database_id: str, text: str) -> dict[str, Any]:
        if text == "월요일 수업":
            raise RuntimeError("connection reset")
        return ask(database_id, text)

    document = be.run(tmp_path, "school-a", ask=flaky)
    assert [item["result"] for item in document["results"]] == ["PASS", "PASS", "ERROR"]


def test_the_runner_only_talks_to_loopback() -> None:
    assert be._base_url("http://127.0.0.1:8123/") == "http://127.0.0.1:8123"
    for url in ("https://127.0.0.1:8123", "http://example.com:8123", "http://127.0.0.1:8123/api", "http://10.0.0.1"):
        with pytest.raises(be.BlindEvalError):
            be._base_url(url)


_LISTING = {
    "version": "adaptive-chat-models-v1",
    "default": "general",
    "models": [
        {"id": "general", "label": "일반 모델", "tag": "qwen2.5-coder:7b-instruct"},
        {"id": "legacy-tuned", "label": "레거시 학습 모델(비교용)", "tag": "text2sql_qwen:latest"},
    ],
}


def test_the_recorded_model_comes_from_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    # Phase 9-08: the chat may not answer with OLLAMA_MODEL, so .env is not read.
    asked: list[str] = []

    def fake(method: str, url: str, body: Any = None, timeout: float = 0) -> Any:
        asked.append(url)
        return _LISTING

    monkeypatch.setattr(be, "_http_json", fake)
    base = "http://127.0.0.1:8124"
    assert be._chat_model(base, None) == {"id": "general", "tag": "qwen2.5-coder:7b-instruct"}
    assert be._chat_model(base, "legacy-tuned") == {"id": "legacy-tuned", "tag": "text2sql_qwen:latest"}
    assert asked == [base + be.CHAT_MODELS_PATH] * 2
    with pytest.raises(be.BlindEvalError, match="UNKNOWN_MODEL"):
        be._chat_model(base, "gpt")


def test_an_older_server_records_no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(be, "_http_json", lambda *_args, **_kwargs: {"http_status": 404})
    assert be._chat_model("http://127.0.0.1:8124", None) is None
    with pytest.raises(be.BlindEvalError, match="UNKNOWN_MODEL"):
        be._chat_model("http://127.0.0.1:8124", "general")


@pytest.mark.parametrize("listing", [{"http_status": 500}, {}, {"version": "x"}, {"models": "general"}])
def test_an_unreadable_listing_stops_the_run(monkeypatch: pytest.MonkeyPatch, listing: Any) -> None:
    # Luna 9-08 review: a failed listing must not leave the run's model unknown.
    monkeypatch.setattr(be, "_http_json", lambda *_args, **_kwargs: listing)
    with pytest.raises(be.BlindEvalError, match="MODELS_UNAVAILABLE"):
        be._chat_model("http://127.0.0.1:8124", None)


def test_the_chosen_model_goes_into_every_question(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[Any] = []
    recorded: list[Any] = []

    def fake(method: str, url: str, body: Any = None, timeout: float = 0) -> Any:
        if url.endswith(be.CHAT_MODELS_PATH):
            return _LISTING
        if url.endswith(be.REGISTRATIONS_PATH):
            return {"registrations": []}
        sent.append(body)
        return {}

    def fake_run(_root: Any, _eval_id: str, **kwargs: Any) -> dict[str, Any]:
        kwargs["ask"]("db", "질문")
        recorded.append(kwargs["model"])
        return {"run": 1, "first_run": True, "summary": {}}

    monkeypatch.setattr(be, "_http_json", fake)
    monkeypatch.setattr(be, "run", fake_run)
    monkeypatch.setattr(be, "load_questions", lambda _root, _eval_id: (SimpleNamespace(database_id="db"), "x"))
    assert be.main(["run", "e", "--base-url", "http://127.0.0.1:8124", "--model", "legacy-tuned"]) == 0
    assert sent == [{"database_id": "db", "query": "질문", "model": "legacy-tuned"}]
    assert recorded == [{"id": "legacy-tuned", "tag": "text2sql_qwen:latest"}]


# --- Luna 8-22 review regressions ---------------------------------------------------------------


def test_a_deleted_lock_does_not_let_changed_questions_pass(tmp_path: Path) -> None:
    path = write_questions(tmp_path)
    be.run(tmp_path, "school-a", ask=ask)
    directory = be.eval_dir(tmp_path, "school-a")
    (directory / "questions.lock.json").unlink()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["questions"][2]["expect"] = {"codes": ["CS101", "MA100"]}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(be.BlindEvalError) as caught:
        be.run(tmp_path, "school-a", ask=ask)
    assert caught.value.code == "QUESTIONS_CHANGED"


def test_run_numbers_are_reserved_before_asking(tmp_path: Path) -> None:
    write_questions(tmp_path)
    directory = be.eval_dir(tmp_path, "school-a")
    seen: list[list[str]] = []

    def watching(database_id: str, text: str) -> dict[str, Any]:
        seen.append(sorted(item.name for item in directory.glob("run-*.json")))
        return ask(database_id, text)

    document = be.run(tmp_path, "school-a", ask=watching)
    assert seen[0] == ["run-001.json"] and document["run"] == 1
    # a second run started while the first is open takes the next number
    (directory / "run-002.json").write_text(json.dumps({"run": 2, "status": "IN_PROGRESS"}), encoding="utf-8")
    assert be.run(tmp_path, "school-a", ask=ask)["run"] == 3


def test_the_report_names_gaps_and_unfinished_runs(tmp_path: Path) -> None:
    write_questions(tmp_path)
    for _ in range(3):
        be.run(tmp_path, "school-a", ask=ask)
    directory = be.eval_dir(tmp_path, "school-a")
    (directory / "run-001.json").unlink()
    (directory / "run-003.json").write_text(json.dumps({"run": 3, "status": "IN_PROGRESS"}), encoding="utf-8")
    rows = be.report(tmp_path, "school-a")
    assert [row["warnings"] for row in rows] == [["MISSING_RUN"], [], ["IN_PROGRESS"]]


def test_a_linked_eval_folder_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    base = tmp_path / ".schema-adaptive" / "blind-evals"
    base.mkdir(parents=True)
    try:
        (base / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are not available here")
    with pytest.raises(be.BlindEvalError) as caught:
        be.eval_dir(tmp_path, "linked")
    assert caught.value.code == "INVALID_EVAL_ID"


def test_requests_use_no_proxy_and_follow_no_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    handlers = {type(handler) for handler in be._OPENER.handlers}
    proxies = [handler for handler in be._OPENER.handlers if isinstance(handler, urllib.request.ProxyHandler)]
    # the empty ProxyHandler replaces the environment one and registers nothing
    assert all(not handler.proxies for handler in proxies)
    assert be._NoRedirect in handlers
    assert be._NoRedirect().redirect_request(None, None, 302, "Found", {}, "http://example.com/") is None
