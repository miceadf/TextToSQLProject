"""Phase 8-14: development option that shows the executed chat SQL."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.adaptive.api import dev_launcher
from backend.adaptive.api.chat_query import CHAT_QUERY_PATH, register_chat_query_routes, render_sql_preview
from tests.adaptive.test_chat_query_endpoint import FakeModel, RecordingExecutor, ir
from tests.adaptive.test_legacy_view_partial_mapping import DATABASE_ID, VIEW, make_context


def test_preview_writes_values_in_as_sql_literals() -> None:
    sql = "SELECT a FROM t WHERE b LIKE %s AND c = %s AND d = %s AND e = %s AND f LIKE '%%x' LIMIT %s"
    assert render_sql_preview(sql, ["%수학%", "O'Neil", 3, True, 200]) == (
        "SELECT a FROM t WHERE b LIKE '%수학%' AND c = 'O''Neil' AND d = 3 AND e = TRUE "
        "AND f LIKE '%x' LIMIT 200"
    )
    for parameters in ([], [1, 2]):
        with pytest.raises(ValueError):
            render_sql_preview("SELECT %s", parameters)
    with pytest.raises(ValueError):
        render_sql_preview("SELECT %s", [None])


def chat(show_sql: bool, output: Any, question: str = "수학과 3학점 과목") -> tuple[dict[str, Any], RecordingExecutor]:
    executor = RecordingExecutor()
    app = FastAPI()
    register_chat_query_routes(
        app, pg_contexts={DATABASE_ID: make_context(executor=executor)}, model=FakeModel(output), show_sql=show_sql
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        body = client.post(CHAT_QUERY_PATH, json={"database_id": DATABASE_ID, "query": question}).json()
    return body, executor


GOOD = ir(["Course.name"], [
    {"field": "Department.name", "operator": "equals", "value": "수학과"},
    {"field": "Course.credits", "operator": "equals", "value": 3},
])


def test_sql_is_shown_only_when_the_server_enables_it() -> None:
    hidden, _executor = chat(False, GOOD)
    assert hidden["status"] == "SUCCEEDED" and "sql" not in hidden
    shown, executor = chat(True, GOOD)
    [compiled] = executor.queries
    assert shown["sql"] == render_sql_preview(compiled.sql, compiled.parameters)
    assert "'%수학과%'" in shown["sql"] and VIEW in shown["sql"]
    assert "%s" not in shown["sql"]
    # Everything else in the answer is unchanged.
    assert {key: value for key, value in shown.items() if key != "sql"} == hidden


@pytest.mark.parametrize(
    "output",
    [
        ir(["Course.name"], [{"field": "CourseOffering.year", "operator": "equals", "value": 2024}]),
        ir(["Course.nickname"]),
    ],
)
def test_answers_without_execution_carry_no_sql(output: Any) -> None:
    body, executor = chat(True, output)
    assert body["status"] in {"UNSUPPORTED", "NOT_UNDERSTOOD"} and "sql" not in body
    assert executor.queries == []


class _App:
    class state:  # noqa: N801 - mimics FastAPI app.state
        adaptive_app_cleanup = None


@pytest.mark.parametrize(("env", "expected"), [("", True), ("SHOW_SQL=1\n", True), ("SHOW_SQL=0\n", False)])
def test_launcher_shows_sql_unless_turned_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], env: str, expected: bool
) -> None:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / ".env").write_text(env, encoding="utf-8")
    seen: list[bool] = []

    def fake_create(
        profiles: Any, model: Any, mapping_model: Any = None, show_sql: bool = False, postgres_sources: Any = None,
        **_custom_chat_models: Any,  # Phase 9-31: repository root and Ollama URL for user models
    ) -> Any:
        seen.append(show_sql)
        return _App()

    monkeypatch.setattr(dev_launcher, "_create_app", fake_create)
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: None)
    assert dev_launcher.main(["--repository-root", str(tmp_path)]) == 0
    assert seen == [expected]
    assert f"chat_sql={'shown' if expected else 'hidden'}" in capsys.readouterr().out


def test_cli_option_turns_the_display_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.adaptive.api import app_factory as proto

    apps: list[Any] = []

    class FakeUvicorn:
        @staticmethod
        def run(app: Any, **_kwargs: Any) -> None:
            apps.append(app)

    monkeypatch.setitem(sys.modules, "uvicorn", FakeUvicorn())
    assert proto.main([]) == 0 and proto.main(["--show-sql"]) == 0
    assert [app.state.adaptive_app_chat_sql_shown for app in apps] == [False, True]


def test_backslashes_render_the_same_under_any_string_setting() -> None:
    # A LIKE pattern for a literal "%" carries a backslash escape.
    assert render_sql_preview("WHERE a LIKE %s", [r"%A\%B%"]) == r"WHERE a LIKE E'%A\\%B%'"
    assert render_sql_preview("WHERE a = %s", ["it's" + "\\"]) == r"WHERE a = E'it''s\\'"


def test_the_length_cap_counts_like_the_browser() -> None:
    from backend.adaptive.api.chat_query import MAX_SQL_PREVIEW

    emoji = "\U0001F600"  # one code point, two UTF-16 code units
    fits = "x" * (MAX_SQL_PREVIEW - 2)
    assert render_sql_preview(fits + "%s", [""]).startswith("x")
    with pytest.raises(ValueError):
        render_sql_preview("x" * (MAX_SQL_PREVIEW // 2) + "%s", [emoji * (MAX_SQL_PREVIEW // 4 + 1)])
