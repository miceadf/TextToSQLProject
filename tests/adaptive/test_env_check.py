"""Phase 9-03: the teammate environment check, without network, database or Ollama."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from backend.adaptive import env_check
from backend.adaptive.api import chat_models, dev_launcher, ollama_startup
from backend.adaptive.env_check import FAIL, OK, WARN, Check, report, run_checks
from backend.adaptive.mapping import ai_proposal

SECRET = "s3cret-pw"
LEGACY_DSN = f"postgresql://team:{SECRET}@db.example.internal:5432/legacy"
SYNTHETIC_DSN = f"postgresql://team:{SECRET}@127.0.0.1:5432/schema_adaptive_synthetic"


class Cursor:
    def __init__(self, rows: dict[str, list[tuple[Any, ...]]], log: list[str]) -> None:
        self.rows, self.log, self.last = rows, log, ""

    def execute(self, query: str, *_args: Any) -> None:
        self.log.append(query)
        self.last = query

    def fetchall(self) -> list[tuple[Any, ...]]:
        if "count(*)" in self.last:
            return [(9,)]
        if "schemata" in self.last:
            return [("public",), ("pg_catalog",), ("information_schema",), ("school_a",), ("other_school",)]
        return [(1,)]


class Connection:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.closed = False

    def cursor(self) -> Cursor:
        return Cursor({}, self.log)

    def close(self) -> None:
        self.closed = True


def make_root(tmp_path: Path, env: str | None) -> Path:
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    (tmp_path / ".venv" / "Scripts" / "python.exe").write_text("")
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("")
    bins = tmp_path / "node_modules" / ".bin"
    bins.mkdir(parents=True)
    for name in ("vite", "vite.cmd", "concurrently", "concurrently.cmd"):
        (bins / name).write_text("")
    (tmp_path / "backend").mkdir()
    if env is not None:
        (tmp_path / "backend" / ".env").write_text(env, encoding="utf-8")
    return tmp_path


GOOD_ENV = (
    f"DATABASE_URL={LEGACY_DSN}\n"
    "OLLAMA_BASE_URL=http://127.0.0.1:11434\n"
    "OLLAMA_MODEL=text2sql_qwen\n"
    f"PG_SOURCE_SCHOOL_A={SYNTHETIC_DSN}\n"
    "SECRET_TOKEN=do-not-read\n"
)
TAGS = {"models": [{"name": "text2sql_qwen:latest"}, {"name": "qwen2.5-coder:7b-instruct"}]}


def checks(root: Path, **overrides: Any) -> tuple[list[Check], dict[str, Any]]:
    seen: dict[str, Any] = {"dsns": [], "urls": [], "sql": [], "connections": []}

    def connect(dsn: str) -> Connection:
        seen["dsns"].append(dsn)
        connection = Connection(seen["sql"])
        seen["connections"].append(connection)
        return connection

    def http_get_json(url: str) -> Any:
        seen["urls"].append(url)
        return TAGS

    options: dict[str, Any] = {
        "environ": {env_check.SYNTHETIC_DSN_ENV: SYNTHETIC_DSN},
        "connect": connect,
        "http_get_json": http_get_json,
        "port_in_use": lambda _port: False,
        "run": lambda command: {"node": "v24.15.0", "npm": "11.0.0"}[command[0]],
        "find_module": lambda _name: object(),
    }
    options.update(overrides)
    return run_checks(root, **options), seen


def by_name(items: list[Check]) -> dict[str, Check]:
    return {item.name: item for item in items}


def test_a_ready_machine_passes_and_prints_no_secret(tmp_path: Path) -> None:
    items, seen = checks(make_root(tmp_path, GOOD_ENV))
    text, code = report(items)
    assert code == 0 and all(item.status == OK for item in items), text
    assert SECRET not in text and "db.example.internal" not in text and "do-not-read" not in text
    found = by_name(items)
    assert "school_a" in found["연결 school-a"].message and "pg_catalog" not in found["연결 school-a"].message
    assert "9개" in found["레거시 DB(DATABASE_URL)"].message
    assert seen["urls"] == ["http://127.0.0.1:11434/api/tags"]
    # only catalog reads, and every connection is closed
    assert all(query.lstrip().upper().startswith("SELECT") for query in seen["sql"])
    assert all(connection.closed for connection in seen["connections"])


def test_a_missing_env_file_fails_with_a_fix(tmp_path: Path) -> None:
    items, seen = checks(make_root(tmp_path, None))
    found = by_name(items)
    assert found["backend/.env"].status == FAIL and "OLLAMA_MODEL" in found["backend/.env"].fix
    assert seen["dsns"] == [SYNTHETIC_DSN]  # only the synthetic DSN from the environment
    assert report(items)[1] == 1


def test_connection_errors_show_only_the_error_type(tmp_path: Path) -> None:
    def connect(_dsn: str) -> Any:
        raise RuntimeError(f'connection to "db.example.internal" failed for user "team" password {SECRET}')

    items, _seen = checks(make_root(tmp_path, GOOD_ENV), connect=connect)
    text, code = report(items)
    assert code == 1
    assert by_name(items)["레거시 DB(DATABASE_URL)"].message == "접속 실패(RuntimeError)"
    assert SECRET not in text and "db.example.internal" not in text


def test_a_query_failure_still_closes_the_connection(tmp_path: Path) -> None:
    class Broken(Connection):
        def cursor(self) -> Any:
            raise RuntimeError(SECRET)

    made: list[Broken] = []

    def connect(_dsn: str) -> Broken:
        made.append(Broken([]))
        return made[-1]

    items, _seen = checks(make_root(tmp_path, GOOD_ENV), connect=connect)
    assert by_name(items)["레거시 DB(DATABASE_URL)"].message == "조회 실패(RuntimeError)"
    assert made and all(item.closed for item in made)


@pytest.mark.parametrize(
    ("dsn", "code"),
    [
        (f"postgresql://team:{SECRET}@10.0.0.5:5432/schema_adaptive_synthetic", "INVALID_HOST"),
        (f"postgresql://team:{SECRET}@127.0.0.1:5432/legacy", "INVALID_DATABASE"),
    ],
)
def test_the_synthetic_dsn_rule_is_checked_before_connecting(tmp_path: Path, dsn: str, code: str) -> None:
    items, seen = checks(make_root(tmp_path, GOOD_ENV), environ={env_check.SYNTHETIC_DSN_ENV: dsn})
    found = by_name(items)[env_check.SYNTHETIC_DSN_ENV]
    assert found.status == FAIL and code in found.message
    assert dsn not in seen["dsns"] and SECRET not in report(items)[0]


def test_without_the_synthetic_dsn_it_is_a_warning(tmp_path: Path) -> None:
    items, _seen = checks(make_root(tmp_path, GOOD_ENV), environ={})
    assert by_name(items)[env_check.SYNTHETIC_DSN_ENV].status == WARN


@pytest.mark.parametrize("url", ["http://192.168.0.10:11434", "https://127.0.0.1:11434", "http://localhost:11434"])
def test_a_non_loopback_ollama_url_is_never_requested(tmp_path: Path, url: str) -> None:
    env = GOOD_ENV.replace("http://127.0.0.1:11434", url)
    items, seen = checks(make_root(tmp_path, env))
    assert by_name(items)["Ollama"].status == FAIL and seen["urls"] == []


def test_missing_models_and_a_silent_ollama_fail(tmp_path: Path) -> None:
    items, _seen = checks(make_root(tmp_path, GOOD_ENV), http_get_json=lambda _url: {"models": []})
    found = by_name(items)
    assert found["챗봇 일반 모델"].status == FAIL and found["mapping 모델"].status == FAIL
    assert found["레거시 학습 모델"].status == FAIL
    assert "ollama pull" in found["챗봇 일반 모델"].fix

    def down(_url: str) -> Any:
        raise ConnectionRefusedError()

    items, _seen = checks(make_root(tmp_path / "b", GOOD_ENV), http_get_json=down)
    assert by_name(items)["Ollama"].message == "응답 없음(ConnectionRefusedError)"


def test_busy_ports_missing_tools_and_packages(tmp_path: Path) -> None:
    def run(command: list[str]) -> str:
        raise FileNotFoundError(command[0])

    items, _seen = checks(
        make_root(tmp_path, GOOD_ENV),
        port_in_use=lambda port: port == 8123,
        run=run,
        find_module=lambda name: None if name in ("fastapi", "transformers") else object(),
    )
    found = by_name(items)
    assert found["포트 8123"].status == WARN and found["포트 8000"].status == OK
    assert found["node"].status == FAIL and found["npm"].status == FAIL
    assert found["Python 패키지"].status == FAIL and "fastapi" in found["Python 패키지"].message
    assert found["레거시 백엔드 패키지"].status == WARN


def test_odd_names_are_masked(tmp_path: Path) -> None:
    env = GOOD_ENV.replace("OLLAMA_MODEL=text2sql_qwen", "OLLAMA_MODEL=model\nnext")
    items, _seen = checks(make_root(tmp_path, env))
    assert "\n" not in by_name(items)["OLLAMA_MODEL"].message


def test_the_copies_match_their_originals(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(GOOD_ENV + "export MAPPING_MODEL='m'\n# OLLAMA_MODEL=x\nSHOW_SQL=\"0\"\n", encoding="utf-8")
    assert env_check.read_env_file(env) == dev_launcher.read_env_file(env)
    assert env_check.ENV_KEYS == dev_launcher._ENV_KEYS
    assert env_check.OLLAMA_BASE_URL_PATTERN.pattern == ollama_startup._BASE_URL_PATTERN.pattern
    assert env_check.DEFAULT_MAPPING_MODEL == ai_proposal.DEFAULT_MAPPING_MODEL
    assert env_check.DEFAULT_CHAT_MODEL == chat_models.DEFAULT_CHAT_MODEL


def test_the_module_imports_only_the_standard_library_at_load() -> None:
    tree = ast.parse(Path(env_check.__file__).read_text(encoding="utf-8"))
    top = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert all(not (isinstance(node, ast.ImportFrom) and node.level) for node in top)
    assert {alias.name.split(".")[0] for node in top if isinstance(node, ast.Import) for alias in node.names} <= {
        "importlib", "json", "os", "re", "shutil", "socket", "subprocess", "sys", "tempfile",
    }


# --- Luna 9-03 review ------------------------------------------------------------------------


def test_a_redirect_is_refused_before_the_next_request() -> None:
    import http.server
    import threading

    hits: list[str] = []

    class Target(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_args: Any) -> None:
            pass

    target = http.server.HTTPServer(("127.0.0.1", 0), Target)

    class Redirect(Target):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/collect")
            self.end_headers()

    source = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
    for server in (target, source):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(Exception):
            env_check._http_get_json(f"http://127.0.0.1:{source.server_port}/api/tags")
        assert hits == []
    finally:
        for server in (target, source):
            server.shutdown()
            server.server_close()


def test_a_failing_read_only_switch_closes_the_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    class Raw:
        closed = False

        def set_session(self, **_kwargs: Any) -> None:
            raise RuntimeError("no read-only")

        def close(self) -> None:
            Raw.closed = True

    fake = types.ModuleType("psycopg2")
    fake.connect = lambda *_args, **_kwargs: Raw()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg2", fake)
    with pytest.raises(RuntimeError):
        env_check._connect("postgresql://u@127.0.0.1/db")
    assert Raw.closed


def test_packages_are_checked_as_the_venv_python_sees_them(tmp_path: Path) -> None:
    root = make_root(tmp_path, GOOD_ENV)
    calls: list[list[str]] = []

    def run(command: list[str]) -> str:
        calls.append(command)
        if command[0].endswith(("python.exe", "python")):
            return '["fastapi", "torch"]'
        return {"node": "v24.15.0", "npm": "11.0.0"}[command[0]]

    items, _seen = checks(root, run=run, find_module=None)
    found = by_name(items)
    assert found["Python 패키지"].status == FAIL and ".venv 기준" in found["Python 패키지"].message
    assert "fastapi" in found["Python 패키지"].message
    assert "torch" in found["레거시 백엔드 패키지"].message
    assert any(command[0].startswith(str(root)) for command in calls)


def test_a_venv_that_cannot_answer_falls_back_to_this_python(tmp_path: Path) -> None:
    def run(command: list[str]) -> str:
        if command[0].endswith(("python.exe", "python")):
            raise OSError("broken venv")
        return {"node": "v24.15.0", "npm": "11.0.0"}[command[0]]

    items, _seen = checks(make_root(tmp_path, GOOD_ENV), run=run, find_module=None)
    assert "지금 Python 기준" in by_name(items)["Python 패키지"].message


def test_only_real_shims_count_in_node_modules(tmp_path: Path) -> None:
    root = make_root(tmp_path, GOOD_ENV)
    bins = root / "node_modules" / ".bin"
    for name in ("vite", "vite.cmd", "concurrently", "concurrently.cmd"):
        (bins / name).unlink()
    (bins / "vite-bogus").write_text("")
    (bins / "concurrently-old").write_text("")
    items, _seen = checks(root)
    assert by_name(items)["node_modules"].status == FAIL


def test_posix_is_warned_that_dev_all_uses_windows_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_root(tmp_path, GOOD_ENV)
    monkeypatch.setattr(env_check.os, "name", "posix")
    found = by_name(env_check._python(root))
    assert found[".venv"].status == WARN and "Windows" in found[".venv"].message


def test_without_the_driver_here_the_database_check_says_so(tmp_path: Path) -> None:
    def connect(_dsn: str) -> Any:
        raise ModuleNotFoundError("psycopg2")

    found = by_name(checks(make_root(tmp_path, GOOD_ENV), connect=connect)[0])["레거시 DB(DATABASE_URL)"]
    assert found.status == WARN and ".venv" in found.fix
