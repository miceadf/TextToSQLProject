"""Phase 8-15: PostgreSQL registration from server-owned connections."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.adaptive.api import dev_launcher
from backend.adaptive.api.chat_query import answer_chat_query
from backend.adaptive.api.review_publish import REVIEW_PATH
from backend.adaptive.api.synthetic_prototype import (
    create_synthetic_clarification_prototype,
    set_private_repository_root_for_tests,
)
from backend.adaptive.api.synthetic_registry import DATABASES_PATH
from backend.adaptive.mapping.transforms import FieldTransform
from backend.adaptive.ontology import DataType
from backend.adaptive.registration import postgres_registrations as pg
from tests.adaptive.test_chat_query_endpoint import FakeModel, ir
from tests.adaptive.test_legacy_view_partial_mapping import VIEW, FakeExecutor, make_catalog

SECRET_DSN = "postgresql://svc:s3cret-815@127.0.0.1:5432/campus"
DB_ID = "campus-pg"
SOURCES = "/api/adaptive/v1/postgres/sources"
SCHEMAS = "/api/adaptive/v1/postgres/schemas"
REGISTER = "/api/adaptive/v1/postgres/registrations"
VALUES = {
    "credit_hours": ["3/3/0", "2/2/0"],
    "day_of_week": ["월", "화"],
    "category": ["전공(핵심)", "교양"],
}


def _repo(base: Path) -> Path:
    repo = base / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (repo / ".gitignore").write_text(".schema-adaptive/\n", encoding="utf-8")
    return repo


class _Cursor:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.rows: list[Any] = []

    def execute(self, query: Any, params: Any = None) -> None:
        text = str(query)
        self.log.append(text)
        if "information_schema.schemata" in text:
            self.rows = [("public",), ("campus",), ("bad-name",)]
            return
        column = text.split('"')[1] if '"' in text else ""
        rows = VALUES.get(column, ["x"])
        if "ANY(" in text:  # exact presence check (Phase 8-07)
            rows = [value for value in rows if value in params[0]]
        self.rows = [(value,) for value in rows]

    def fetchall(self) -> list[Any]:
        return self.rows


class _Connection:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    def cursor(self) -> _Cursor:
        return _Cursor(self.log)

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


class _Adapter:
    def __init__(self, database_id: str) -> None:
        self.database_id = database_id

    def inspect(self, database_id: str, **_kwargs: Any) -> Any:
        return make_catalog().model_copy(update={"database_id": database_id})


@pytest.fixture
def client_repo(tmp_path: Path):
    repo = _repo(tmp_path)
    log: list[str] = []
    executor = FakeExecutor()
    app = create_synthetic_clarification_prototype(
        postgres_sources={"campus": pg.PostgresSource("campus", "Campus server", SECRET_DSN)},
        postgres_registration_options={
            "repository_root": repo,
            "adapter_factory": lambda database_id, _config: _Adapter(database_id),
            "executor_factory": lambda _database_id, _config: executor,
            "connector": lambda _config: _Connection(log),
        },
    )
    app.state.mapping_review_repository_root = str(repo)
    set_private_repository_root_for_tests(repo)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, repo, app, executor
    finally:
        client.close()
        set_private_repository_root_for_tests(None)


def _register(client: TestClient, database_id: str = DB_ID) -> Any:
    return client.post(REGISTER, json={"source_id": "campus", "schema": "campus", "database_id": database_id, "label": "Campus"})


def _review(client: TestClient, payload: dict[str, Any]) -> Any:
    return client.post(REVIEW_PATH, json=payload)


def _review_all(client: TestClient, database_id: str = DB_ID) -> dict[str, str]:
    detail = _review(client, {"action": "INIT", "database_id": database_id, "reviewer": "admin"}).json()
    decisions = {}
    used: set[tuple[str, str]] = set()
    for field in detail["review"]["fields"]:
        name = field["semantic_field"]
        top = field["candidates"][0] if field["candidates"] else None
        if top is not None and (top["table"], top["column"]) not in used:
            used.add((top["table"], top["column"]))
            response = _review(client, {
                "action": "APPROVE", "database_id": database_id, "semantic_field": name,
                "candidate_index": 0, "reviewer": "admin",
            })
            decisions[name] = top["column"]
        else:
            response = _review(client, {
                "action": "IGNORE", "database_id": database_id, "semantic_field": name,
                "reviewer": "admin", "reason": "이 DB에 없음",
            })
            decisions[name] = "IGNORED"
        assert response.status_code == 200, (name, response.text[:200])
    return decisions


# --- connections ---------------------------------------------------------------------------


def test_sources_come_from_server_settings_only() -> None:
    sources = pg.sources_from_env({
        "DATABASE_URL": SECRET_DSN, "PG_SOURCE_CAMPUS_B": "postgresql://b", "PG_SOURCE_": "x", "OTHER": "y",
    })
    assert sorted(sources) == ["campus-b", "default"]
    assert "s3cret" not in repr(sources) and "s3cret" not in str(sources["default"])
    assert pg.sources_from_env({}) == {}


def test_source_and_schema_lists_never_carry_the_dsn(client_repo) -> None:
    client, _repo_path, _app, _executor = client_repo
    sources = client.get(SOURCES)
    assert sources.json() == {"version": "postgres-sources-v1", "sources": [{"source_id": "campus", "label": "Campus server"}]}
    schemas = client.get(SCHEMAS, params={"source_id": "campus"})
    assert schemas.json()["schemas"] == ["public", "campus"]  # invalid names dropped
    assert client.get(SCHEMAS, params={"source_id": "nope"}).status_code == 404
    for response in (sources, schemas):
        assert "s3cret" not in response.text and "127.0.0.1" not in response.text


def test_schema_listing_failure_is_a_fixed_code() -> None:
    def broken(_config: Any) -> Any:
        raise RuntimeError("could not connect to " + SECRET_DSN)

    with pytest.raises(pg.PostgresRegistrationError) as caught:
        pg.list_schemas(pg.PostgresSource("x", "x", SECRET_DSN), connector=broken)
    assert caught.value.code == "SOURCE_UNAVAILABLE" and "s3cret" not in str(caught.value)


# --- register, review, publish -------------------------------------------------------------


def test_register_review_publish_and_serve_without_restart(client_repo) -> None:
    client, repo, app, executor = client_repo
    registered = _register(client)
    assert registered.status_code == 200, registered.text[:300]
    body = registered.json()
    assert body["version"] == "private-postgres-registration-v1" and body["status"] == "PENDING_REVIEW"
    assert body["mapping"]["proposer"] == "RULES"
    assert _register(client).status_code == 409
    assert client.get(REVIEW_PATH, params={"database_id": DB_ID}).status_code == 404  # before INIT

    decisions = _review_all(client)
    assert decisions["Department.code"] == "IGNORED"
    assert _review(client, {"action": "ACTIVATE", "database_id": DB_ID, "mapping_version": 1, "activated_by": "admin"}).status_code == 400
    published = _review(client, {"action": "PUBLISH", "database_id": DB_ID, "published_by": "admin"})
    assert published.status_code == 200, published.text[:300]
    assert published.json() == {
        "version": "private-mapping-review-v2", "database_id": DB_ID, "status": "PUBLISHED",
        "mapping_version": 1, "registry_eligible": True,
    }

    # The startup loader format, with the rules' decisions and no secret.
    profile_dir = repo / ".schema-adaptive" / "postgres-profiles" / DB_ID
    profile = json.loads((profile_dir / "profile.json").read_text(encoding="utf-8"))
    assert profile["field_transforms"] == {
        "Course.credits": "SLASH_FIRST_NUMERIC",
        "Schedule.day": "KOREAN_WEEKDAY",
        "Schedule.end_time": "TIME_TO_HHMM",
        "Schedule.start_time": "TIME_TO_HHMM",
    }
    assert profile["anchor_table"] == VIEW
    assert "Department.code" in profile["unsupported_fields"]
    assert profile["dsn_env"] == "SCHEMA_ADAPTIVE_POSTGRES_DSN_REG_CAMPUS_PG"
    for path in (repo / ".schema-adaptive").rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert "s3cret" not in text and "3/3/0" not in text, path

    # Listed and answerable in the running server.
    listed = client.get(DATABASES_PATH).json()["databases"]
    assert {"database_id": DB_ID, "label": "Campus", "schema": "postgres"} in listed
    live = app.state.synthetic_postgres_contexts
    answer = answer_chat_query(
        database_id=DB_ID, question="3학점 과목", pg_contexts=live,
        model=FakeModel(ir(["Course.name"], [{"field": "Course.credits", "operator": "equals", "value": 3}])),
    )
    assert answer["status"] == "SUCCEEDED"
    assert "SPLIT_PART" in executor.queries[-1].sql
    detail = client.get(REVIEW_PATH, params={"database_id": DB_ID}).json()
    assert detail["status"] == "PUBLISHED" and detail["mapping_versions"][0]["activatable"] is False

    # Publishing again makes a new version.
    again = _review(client, {"action": "PUBLISH", "database_id": DB_ID, "published_by": "admin"})
    assert again.json()["mapping_version"] == 2


def test_publish_waits_for_every_field(client_repo) -> None:
    client, repo, _app, _executor = client_repo
    assert _register(client).status_code == 200
    _review(client, {"action": "INIT", "database_id": DB_ID, "reviewer": "admin"})
    blocked = _review(client, {"action": "PUBLISH", "database_id": DB_ID, "published_by": "admin"})
    assert blocked.status_code == 422
    assert "Course.code" in blocked.json()["error"]["fields"]
    assert not (repo / ".schema-adaptive" / "postgres-profiles" / DB_ID).exists()


def test_a_rejected_profile_writes_nothing(client_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    client, repo, _app, _executor = client_repo
    assert _register(client).status_code == 200
    _review_all(client)
    from backend.adaptive.api import postgres_runtime

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise postgres_runtime.PostgresRuntimeError()

    monkeypatch.setattr(postgres_runtime, "build_validated_contexts", refuse)
    blocked = _review(client, {"action": "PUBLISH", "database_id": DB_ID, "published_by": "admin"})
    assert blocked.status_code == 422
    assert not (repo / ".schema-adaptive" / "postgres-profiles" / DB_ID).exists()
    assert DB_ID not in client.get(DATABASES_PATH).text


@pytest.mark.parametrize(
    "body",
    [
        {"source_id": "campus", "schema": "campus", "database_id": "Bad ID", "label": "x"},
        {"source_id": "campus", "schema": "pg_catalog", "database_id": "ok-id", "label": "x"},
        {"source_id": "campus", "schema": "a;drop", "database_id": "ok-id", "label": "x"},
        {"source_id": "campus", "schema": "campus", "database_id": "ok-id"},
        {"source_id": "campus", "schema": "campus", "database_id": "synth-clarification-prototype", "label": "x"},
    ],
)
def test_invalid_registrations_are_refused(client_repo, body: dict[str, Any]) -> None:
    client, _repo_path, _app, _executor = client_repo
    response = client.post(REGISTER, json=body)
    assert response.status_code in (400, 409)
    assert "s3cret" not in response.text


def test_a_registration_cannot_take_an_existing_profile_id(client_repo) -> None:
    client, repo, _app, _executor = client_repo
    (repo / ".schema-adaptive" / "postgres-profiles" / "legacy-base").mkdir(parents=True)
    assert _register(client, "legacy-base").status_code == 409


# --- decisions -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("transform", "declared", "data_type", "values", "fits"),
    [
        (FieldTransform.SLASH_FIRST_NUMERIC, "character varying(20)", DataType.NUMBER, ["3/3/0"], True),
        (FieldTransform.SLASH_FIRST_NUMERIC, "character varying(20)", DataType.NUMBER, ["3"], False),
        (FieldTransform.SLASH_FIRST_NUMERIC, "character varying(20)", DataType.NUMBER, [], False),
        (FieldTransform.SLASH_FIRST_NUMERIC, "integer", DataType.NUMBER, ["3/3/0"], False),
        (FieldTransform.KOREAN_WEEKDAY, "character varying", DataType.STRING, ["월", "화"], True),
        (FieldTransform.KOREAN_WEEKDAY, "character varying", DataType.STRING, ["MON"], False),
        (FieldTransform.TIME_TO_HHMM, "time without time zone", DataType.TIME, [], True),
        (FieldTransform.TIME_TO_HHMM, "character varying", DataType.TIME, ["09:00"], False),
    ],
)
def test_transforms_follow_type_and_stored_values(transform, declared, data_type, values, fits) -> None:
    assert pg.transform_fits(transform, declared, data_type, values) is fits


# --- restart --------------------------------------------------------------------------------


class _App:
    class state:  # noqa: N801 - mimics FastAPI app.state
        synthetic_prototype_cleanup = None


def test_launcher_reloads_published_registrations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / ".env").write_text(
        f"DATABASE_URL={SECRET_DSN}\nPG_SOURCE_OTHER=postgresql://other\n", encoding="utf-8"
    )
    monkeypatch.setattr(dev_launcher, "_published_postgres", lambda _root: [("campus-pg", "default"), ("gone-pg", "missing")])
    requested: list[list[str]] = []
    seen_sources: list[Any] = []

    def fake_load(ids: list[str], _root: Path) -> list[str]:
        requested.append(list(ids))
        return list(ids)

    def fake_create(profiles: Any, model: Any, mapping_model: Any = None, show_sql: bool = False, postgres_sources: Any = None) -> Any:
        seen_sources.append(postgres_sources)
        return _App()

    monkeypatch.setattr(dev_launcher, "_load_profiles", fake_load)
    monkeypatch.setattr(dev_launcher, "_create_app", fake_create)
    monkeypatch.delenv(pg.dsn_env_for("campus-pg"), raising=False)
    import os

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: None)
    assert dev_launcher.main(["--repository-root", str(tmp_path)]) == 0
    assert ["campus-pg"] in requested
    assert os.environ[pg.dsn_env_for("campus-pg")] == SECRET_DSN
    assert sorted(seen_sources[0]) == ["default", "other"]
    out = capsys.readouterr().out
    assert "gone-pg=disabled (SOURCE_MISSING)" in out and "pg_registration=enabled (2 sources)" in out
    assert "s3cret" not in out
    monkeypatch.delenv(pg.dsn_env_for("campus-pg"), raising=False)


def test_ignore_on_an_sqlite_review_blocks_its_publish(tmp_path: Path) -> None:
    from backend.adaptive.api.registration_upload import SQLITE_UPLOAD_CONTENT_TYPE, SQLITE_UPLOAD_PATH
    from backend.adaptive.fixtures.flat import build_flat_database
    from backend.adaptive.fixtures.seed import DEFAULT_SEED

    repo = _repo(tmp_path)
    source = build_flat_database(tmp_path / "flat.sqlite3", seed=DEFAULT_SEED)
    app = create_synthetic_clarification_prototype()
    app.state.sqlite_upload_repository_root = str(repo)
    app.state.mapping_review_repository_root = str(repo)
    with TestClient(app, raise_server_exceptions=False) as client:
        uploaded = client.post(
            SQLITE_UPLOAD_PATH + "?database_id=flat-ignore&label=Flat",
            content=source.read_bytes(),
            headers={"Content-Type": SQLITE_UPLOAD_CONTENT_TYPE},
        )
        assert uploaded.status_code == 200
        detail = _review(client, {"action": "INIT", "database_id": "flat-ignore", "reviewer": "admin"}).json()
        for field in detail["review"]["fields"]:
            _review(client, {
                "action": "APPROVE", "database_id": "flat-ignore", "semantic_field": field["semantic_field"],
                "candidate_index": 0, "reviewer": "admin",
            })
        ignored = _review(client, {
            "action": "IGNORE", "database_id": "flat-ignore", "semantic_field": "Schedule.day",
            "reviewer": "admin", "reason": "없음",
        })
        assert ignored.status_code == 200
        day = next(f for f in ignored.json()["review"]["fields"] if f["semantic_field"] == "Schedule.day")
        assert day["decision"] == "IGNORED"
        blocked = _review(client, {"action": "PUBLISH", "database_id": "flat-ignore", "published_by": "admin"})
        assert blocked.status_code == 422


# --- review fixes -------------------------------------------------------------------------


def test_the_clarification_loader_shares_the_live_set(client_repo) -> None:
    _client, _repo_path, app, _executor = client_repo
    assert app.state.synthetic_prototype_loader._pg_contexts is app.state.synthetic_postgres_contexts


def test_live_publishing_respects_the_database_limit(client_repo) -> None:
    client, repo, app, _executor = client_repo
    service = app.state.postgres_registration_service
    service._max_databases = len(service._reserved) + 1  # room for one PostgreSQL database
    for database_id in (DB_ID, "campus-pg-two"):
        assert _register(client, database_id).status_code == 200
        _review_all(client, database_id)
    assert _review(client, {"action": "PUBLISH", "database_id": DB_ID, "published_by": "admin"}).status_code == 200
    # Re-publishing a served database is not a new one.
    assert _review(client, {"action": "PUBLISH", "database_id": DB_ID, "published_by": "admin"}).status_code == 200
    full = _review(client, {"action": "PUBLISH", "database_id": "campus-pg-two", "published_by": "admin"})
    assert full.status_code == 409
    assert not (repo / ".schema-adaptive" / "postgres-profiles" / "campus-pg-two").exists()


def test_the_profile_pair_is_replaced_together(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "mapping.json").write_bytes(b"old mapping")
    (tmp_path / "profile.json").write_bytes(b"old profile")
    real = pg._write_atomic

    def failing(path: Path, content: bytes) -> None:
        if path.name == "profile.json" and content == b"new profile":
            raise pg.PostgresRegistrationError("STORAGE_ERROR")
        real(path, content)

    monkeypatch.setattr(pg, "_write_atomic", failing)
    with pytest.raises(pg.PostgresRegistrationError):
        pg._write_pair(tmp_path, {"mapping.json": b"new mapping", "profile.json": b"new profile"})
    assert (tmp_path / "mapping.json").read_bytes() == b"old mapping"
    assert (tmp_path / "profile.json").read_bytes() == b"old profile"
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    with pytest.raises(pg.PostgresRegistrationError):
        pg._write_pair(fresh, {"mapping.json": b"new mapping", "profile.json": b"new profile"})
    assert list(fresh.iterdir()) == []
