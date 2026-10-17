"""Synthetic tests for Phase 6-13 clarification ASGI prototype."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.adaptive.api.clarification_http import (
    CLARIFICATION_HTTP_VERSION,
    START_PATH,
    SUBMIT_PATH,
)
from backend.adaptive.api.app_factory import (
    SYNTHETIC_CLARIFICATION_PROTOTYPE_VERSION,
    SYNTHETIC_PROTOTYPE_DATABASE_ID,
    create_synthetic_clarification_prototype,
    main as prototype_main,
)
from backend.adaptive.fixtures.seed import DEFAULT_SEED

QUESTION_BASE = "SYNTH_Q_P613_k7d2"
TRIGGER_QUESTION = f"{QUESTION_BASE} 강의 목록 보여줘"
NO_MATCH_QUESTION = f"{QUESTION_BASE} plain timetable without trigger xyz"
SECRET_MARKER = "SECRET_P613_k7d2_TOKEN"
FAKE_PATH = "C:\\synth_p613\\private\\notes.txt"

EXPECTED_REFS = ("Course.name", "Instructor.name")


def _start(client: TestClient, **overrides):
    body = {
        "request_id": "req-synth-p613",
        "database_id": SYNTHETIC_PROTOTYPE_DATABASE_ID,
        "question": TRIGGER_QUESTION,
    }
    body.update(overrides)
    return client.post(START_PATH, json=body)


def _expected_values(ref: str) -> list:
    if ref == "Course.name":
        return [o.course_name for o in DEFAULT_SEED.offerings]
    assert ref == "Instructor.name"
    return [o.instructor_name for o in DEFAULT_SEED.offerings]


def test_version_and_fixed_database_id():
    assert SYNTHETIC_CLARIFICATION_PROTOTYPE_VERSION == "synthetic-clarification-prototype-v1"
    assert SYNTHETIC_PROTOTYPE_DATABASE_ID == "synth-clarification-prototype"


def test_start_each_option_submit_matches_seed():
    for ref in EXPECTED_REFS:
        app = create_synthetic_clarification_prototype()
        client = TestClient(app, raise_server_exceptions=False)
        started = _start(client)
        assert started.status_code == 200
        data = started.json()
        assert data["status"] == "PENDING"
        assert data["version"] == CLARIFICATION_HTTP_VERSION
        refs = sorted(o["semantic_ref"] for o in data["options"])
        assert refs == sorted(EXPECTED_REFS)
        target = [o for o in data["options"] if o["semantic_ref"] == ref][0]
        submitted = client.post(
            SUBMIT_PATH,
            json={"session_token": data["session_token"], "selection": [target["option_id"]]},
        )
        assert submitted.status_code == 200
        out = submitted.json()
        assert out["status"] == "EXECUTED"
        assert out["execution"]["status"] == "SUCCEEDED"
        assert out["execution"]["columns"] == [ref]
        got = sorted(row[ref] for row in out["execution"]["rows"])
        assert got == sorted(_expected_values(ref))


def test_cancel_reuse_not_required_unknown_database():
    app = create_synthetic_clarification_prototype()
    client = TestClient(app, raise_server_exceptions=False)
    started = _start(client).json()
    cancelled = client.post(
        SUBMIT_PATH,
        json={"session_token": started["session_token"], "selection": [], "cancelled": True},
    )
    assert cancelled.status_code == 200
    assert cancelled.json() == {"version": CLARIFICATION_HTTP_VERSION, "status": "CANCELLED"}
    reuse = client.post(
        SUBMIT_PATH,
        json={"session_token": started["session_token"], "selection": [], "cancelled": True},
    )
    assert reuse.status_code == 409
    assert reuse.json() == {"error": {"code": "CLARIFICATION_SESSION_ALREADY_CONSUMED"}}

    nomatch = _start(client, request_id="req-nomatch", question=NO_MATCH_QUESTION)
    assert nomatch.status_code == 200
    assert nomatch.json() == {"version": CLARIFICATION_HTTP_VERSION, "status": "NOT_REQUIRED"}

    unknown = _start(client, request_id="req-unknown", database_id="unknown-db-id")
    assert unknown.status_code == 503
    assert unknown.json() == {"error": {"code": "CLARIFICATION_HTTP_CONTEXT_UNAVAILABLE"}}


def test_fixed_deps_defensive_copy_readonly_cleanup():
    from backend.adaptive.harness.compiler import SingleTableSQLiteCompiler
    from backend.adaptive.harness.executor import SQLiteReadOnlyExecutor
    from backend.adaptive.harness.policy import ConservativeSqlPolicy

    app = create_synthetic_clarification_prototype()
    tmpdir = Path(app.state.adaptive_app_tmpdir)
    cleanup = app.state.adaptive_app_cleanup
    with TestClient(app, raise_server_exceptions=False):
        loader = app.state.adaptive_app_loader
        first = loader.load(SYNTHETIC_PROTOTYPE_DATABASE_ID)
        second = loader.load(SYNTHETIC_PROTOTYPE_DATABASE_ID)
        assert first[0].model_dump(mode="json") == second[0].model_dump(mode="json")
        assert first[0] is not second[0]
        first[0].entities.add("Mutated")
        third = loader.load(SYNTHETIC_PROTOTYPE_DATABASE_ID)
        assert "Mutated" not in third[0].entities

        assert isinstance(app.state.adaptive_app_executor, SQLiteReadOnlyExecutor)
        # Phase 8-01 wraps the compiler with the private activation guard.
        assert isinstance(app.state.adaptive_app_compiler.inner, SingleTableSQLiteCompiler)
        assert isinstance(app.state.adaptive_app_policy, ConservativeSqlPolicy)
        assert tmpdir.is_dir()

    assert not tmpdir.exists()
    cleanup()
    assert not tmpdir.exists()


def test_cli_loopback_reload_off_access_log_off(monkeypatch, capsys):
    import sys

    captured: dict = {}

    class _FakeUvicorn:
        def run(self, app, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "uvicorn", _FakeUvicorn())
    import backend.adaptive.api.app_factory as proto

    cleaned = []

    class _State:
        adaptive_app_cleanup = lambda self: cleaned.append(True)

    class _App:
        state = _State()

    monkeypatch.setattr(proto, "create_synthetic_clarification_prototype", _App)
    assert prototype_main(["--port", "8123"]) == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8123
    assert captured["reload"] is False
    assert captured["access_log"] is False
    assert cleaned == [True]

    import argparse

    parser_names = set()
    for action in proto._build_parser()._actions:
        parser_names.update(action.option_strings)
    assert "--host" not in parser_names
    assert "--port" in parser_names

    with pytest.raises(SystemExit):
        prototype_main(["--port", "7999"])
    with pytest.raises(SystemExit):
        prototype_main(["--port", SECRET_MARKER])
    error_output = capsys.readouterr().err
    assert SECRET_MARKER not in error_output
    assert "synthetic prototype invalid configuration" in error_output
    assert captured["port"] == 8123


def test_import_isolation_and_package_has_no_app_side_effect():
    import backend.adaptive.api as api_pkg

    assert "backend.main" not in sys.modules
    assert "ollama" not in sys.modules
    assert "psycopg" not in sys.modules
    assert "psycopg2" not in sys.modules
    assert "dotenv" not in sys.modules
    assert not hasattr(api_pkg, "app")
    source = Path("backend/adaptive/api/app_factory.py").read_text(encoding="utf-8")
    assert "from backend.main" not in source
    assert "import backend.main" not in source


def test_responses_stdout_stderr_logs_do_not_leak(capsys, caplog):
    app = create_synthetic_clarification_prototype()
    client = TestClient(app, raise_server_exceptions=False)
    question = f"{QUESTION_BASE} 강의 {SECRET_MARKER} {FAKE_PATH} SELECT * FROM t"
    started = client.post(
        START_PATH,
        json={"request_id": "req-leak", "database_id": SYNTHETIC_PROTOTYPE_DATABASE_ID, "question": question},
    )
    assert started.status_code == 200
    data = started.json()
    token = data["session_token"]
    target = data["options"][0]
    submitted = client.post(SUBMIT_PATH, json={"session_token": token, "selection": [target["option_id"]]})
    assert submitted.status_code == 200
    blob = started.text + submitted.text
    assert "SELECT" not in blob
    assert SECRET_MARKER not in blob
    assert FAKE_PATH not in blob
    assert QUESTION_BASE not in blob
    assert token not in submitted.text
    tmpdir = app.state.adaptive_app_tmpdir
    assert tmpdir not in blob
    out, err = capsys.readouterr()
    logs = "\n".join(r.getMessage() for r in caplog.records)
    for secret in (SECRET_MARKER, QUESTION_BASE, FAKE_PATH, token):
        assert secret not in out
        assert secret not in err
        assert secret not in logs
    assert "SELECT" not in out
    assert tmpdir not in out
