"""Phase 8-15: PostgreSQL registration from server-owned connections."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.adaptive.api import dev_launcher
from backend.adaptive.api.chat_query import ChatQueryError, ChatQueryErrorCode, answer_chat_query
from backend.adaptive.api.review_publish import REVIEW_PATH
from backend.adaptive.api.app_factory import (
    create_synthetic_clarification_prototype,
    set_private_repository_root_for_tests,
)
from backend.adaptive.api.database_registry import DATABASES_PATH
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


def test_reserved_selection_id_is_invalid_and_writes_nothing(client_repo) -> None:
    client, repo, _app, _executor = client_repo
    before = sorted(path.relative_to(repo) for path in repo.rglob("*"))
    response = _register(client, "legacy")
    assert response.status_code == 400
    assert response.json() == {"error": {"code": "POSTGRES_REGISTRATION_INVALID_INPUT"}}
    assert sorted(path.relative_to(repo) for path in repo.rglob("*")) == before


def test_delete_published_registration_without_restart(client_repo) -> None:
    client, repo, app, _executor = client_repo
    assert _register(client).status_code == 200
    _review_all(client)
    assert _review(client, {"action": "PUBLISH", "database_id": DB_ID, "published_by": "admin"}).status_code == 200
    service = app.state.postgres_registration_service
    assert DB_ID in service._reader._configs
    for folder in ("blind-evals", "direct-sql-pg"):
        artifact = repo / ".schema-adaptive" / folder / DB_ID / "result.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_text('{"keep":true}', encoding="utf-8")
    registration = pg._private_dir(repo, pg.REGISTRATION_ROOT, DB_ID)[1]
    for name in ("dictionaries.json", "extensions.json", "verified-queries.json"):
        (registration / name).write_text("{}", encoding="utf-8")
    response = client.delete(REGISTER, params={"database_id": DB_ID})
    assert response.json() == {"database_id": DB_ID, "deleted": True}
    assert response.status_code == 200
    assert not registration.exists()
    assert not pg._private_dir(repo, pg.PROFILE_ROOT, DB_ID)[1].exists()
    assert DB_ID not in app.state.synthetic_postgres_contexts
    with pytest.raises(ChatQueryError) as caught:
        answer_chat_query(database_id=DB_ID, question="강의 목록", pg_contexts=app.state.synthetic_postgres_contexts, model=None)
    assert caught.value.code == ChatQueryErrorCode.CONTEXT_UNAVAILABLE
    assert DB_ID not in service._reader._configs
    assert service._reader.read(DB_ID, VIEW, "category", 10) is None
    assert all(item["database_id"] != DB_ID for item in client.get(DATABASES_PATH).json()["databases"])
    assert client.get(REGISTER).json()["registrations"] == []
    assert client.get(REVIEW_PATH, params={"database_id": DB_ID}).status_code == 404
    for folder in ("blind-evals", "direct-sql-pg"):
        assert (repo / ".schema-adaptive" / folder / DB_ID / "result.json").read_text(encoding="utf-8") == '{"keep":true}'
    assert client.delete(REGISTER, params={"database_id": DB_ID}).status_code == 404
    assert _register(client).status_code == 200


def test_delete_existing_reserved_legacy_registration(client_repo) -> None:
    client, repo, _app, _executor = client_repo
    # Imitate a registration saved before Phase 9-17, only in this temp repo.
    directory = pg._private_dir(repo, pg.REGISTRATION_ROOT, "legacy")[1]
    directory.mkdir(parents=True)
    (directory / "registration.json").write_text("{}", encoding="utf-8")
    profile = pg._private_dir(repo, pg.PROFILE_ROOT, "legacy")[1]
    profile.mkdir(parents=True)
    assert client.delete(REGISTER, params={"database_id": "legacy"}).json() == {"database_id": "legacy", "deleted": True}
    assert not directory.exists() and not profile.exists()
    assert _register(client, "legacy").status_code == 400


def test_delete_forgets_value_hints_before_same_id_and_key_republish(client_repo, monkeypatch) -> None:
    client, _repo_path, app, _executor = client_repo
    assert _register(client).status_code == 200
    _review_all(client)
    assert _review(client, {"action": "PUBLISH", "database_id": DB_ID, "published_by": "admin"}).status_code == 200
    service = app.state.postgres_registration_service
    indexes = service._value_indexes
    old_context = service._live.get(DB_ID)
    old_index = indexes.get(old_context)
    assert old_index.find("교양")
    # A dictionary alias and an unrelated database entry must also be handled.
    from backend.adaptive.api.value_hints import ValueIndex
    from backend.adaptive.mapping.values import ValueDictionary, ValueEntry
    old_key = next(key for key in indexes._cache if key[0] == DB_ID)
    old_index = ValueIndex({}, {"Course.category": ValueDictionary(
        entries=(ValueEntry(stored="교양", label="교양", aliases=("옛별칭",)),),
        reviewer="test", reason="fixture",
    )})
    indexes._cache[old_key] = (old_index, None)
    other_version_key = (DB_ID, old_key[1], old_key[2] + 1)
    indexes._cache[other_version_key] = (old_index, None)
    other_key = ("other-db", old_key[1], old_key[2])
    indexes._cache[other_key] = (old_index, None)
    assert old_index.find("옛별칭")
    assert client.delete(REGISTER, params={"database_id": DB_ID}).status_code == 200
    assert old_key not in indexes._cache and other_version_key not in indexes._cache
    assert other_key in indexes._cache
    assert _register(client).status_code == 200
    _review_all(client)
    assert _review(client, {"action": "PUBLISH", "database_id": DB_ID, "published_by": "admin"}).status_code == 200
    new_context = service._live.get(DB_ID)
    assert (new_context.database_id, new_context.expected_fingerprint, new_context.published_mapping().version) == old_key
    reads = []
    monkeypatch.setattr(service._reader, "read", lambda *args: reads.append(args) or ["새값"])
    new_index = indexes.get(new_context)
    assert reads and new_index is not old_index
    assert new_index.find("새값") and not new_index.find("교양") and not new_index.find("옛별칭")


@pytest.mark.parametrize("reparse", [False, True])
def test_delete_without_path_is_junction_uses_reparse_attributes(client_repo, monkeypatch, reparse) -> None:
    from types import SimpleNamespace
    client, repo, _app, _executor = client_repo
    assert _register(client).status_code == 200
    registration = pg._private_dir(repo, pg.REGISTRATION_ROOT, DB_ID)[1]
    marked = registration / "marked"
    marked.mkdir()
    # Shadow any PathBase fallback in newer Python as well as Path's method.
    monkeypatch.setattr(Path, "is_junction", None, raising=False)
    monkeypatch.delattr(pg.os.path, "isjunction", raising=False)
    original_lstat = pg.os.lstat
    def lstat(path, *args, **kwargs):
        if reparse and Path(path) == marked:
            return SimpleNamespace(st_file_attributes=0x400, st_mode=original_lstat(path).st_mode)
        return original_lstat(path, *args, **kwargs)
    monkeypatch.setattr(pg.os, "lstat", lstat)
    # os.name is already nt on Windows; isolate the helper's platform on other hosts.
    monkeypatch.setattr(pg, "os", SimpleNamespace(name="nt", path=pg.os.path, lstat=lstat))
    if reparse:
        with pytest.raises(pg.PostgresRegistrationError) as caught:
            pg.delete_registration(repo, DB_ID)
        assert caught.value.code == "PRIVATE_STORAGE_INVALID"
        assert registration.exists() and marked.exists()
    else:
        pg.delete_registration(repo, DB_ID)
        assert not registration.exists()


@pytest.mark.parametrize("attributes,expected", [(0, False), (0x400, True), (None, False), (OSError(), False)])
def test_junction_helper_without_path_method(monkeypatch, attributes, expected) -> None:
    from types import SimpleNamespace
    path = SimpleNamespace()  # no is_junction attribute, as in Python 3.10/3.11
    def lstat(actual):
        assert actual is path
        if isinstance(attributes, OSError):
            raise attributes
        return SimpleNamespace() if attributes is None else SimpleNamespace(st_file_attributes=attributes)
    monkeypatch.setattr(pg, "os", SimpleNamespace(name="nt", path=SimpleNamespace(), lstat=lstat))
    assert pg._is_junction(path) is expected


@pytest.mark.parametrize("database_id", ["missing", "legacy-view", "legacy-base", "sqlite-only", *sorted(pg.sqlite_storage.RESERVED_SYNTHETIC_DATABASE_IDS)])
def test_delete_cannot_remove_non_registration_artifacts(client_repo, database_id: str) -> None:
    client, repo, _app, _executor = client_repo
    profile = repo / ".schema-adaptive" / "postgres-profiles" / database_id
    profile.mkdir(parents=True)
    marker = profile / "profile.json"
    marker.write_text("{}", encoding="utf-8")
    sqlite = repo / ".schema-adaptive" / "registered-databases" / database_id
    sqlite.mkdir(parents=True)
    response = client.delete(REGISTER, params={"database_id": database_id})
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "POSTGRES_REGISTRATION_NOT_FOUND"}}
    assert marker.exists() and sqlite.exists()


@pytest.mark.parametrize("query,body", [
    ("", b""), ("database_id=", b""), ("database_id=..%2Foutside", b""),
    ("database_id=C%3A%5Coutside", b""), ("database_id=campus-pg&extra=x", b""),
    ("database_id=campus-pg&database_id=other", b""), ("database_id=campus-pg", b"{}"),
])
def test_delete_strict_request_validation(client_repo, query: str, body: bytes) -> None:
    client, _repo_path, _app, _executor = client_repo
    assert _register(client).status_code == 200
    response = client.request("DELETE", REGISTER + "?" + query, content=body)
    assert response.status_code == 400
    assert response.json() == {"error": {"code": "POSTGRES_REGISTRATION_INVALID_INPUT"}}
    assert len(client.get(REGISTER).json()["registrations"]) == 1


@pytest.mark.parametrize("location", ["registration", "profile", "nested"])
@pytest.mark.parametrize("link_kind", ["symlink", "junction"])
def test_delete_rejects_symlinks_before_removing_either_tree(client_repo, location: str, link_kind: str) -> None:
    client, repo, _app, _executor = client_repo
    assert _register(client).status_code == 200
    registration = pg._private_dir(repo, pg.REGISTRATION_ROOT, DB_ID)[1]
    profile = pg._private_dir(repo, pg.PROFILE_ROOT, DB_ID)[1]
    outside = repo / "outside"
    outside.mkdir()
    (outside / "registration.json").write_text("{}", encoding="utf-8")
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    link = registration / "linked" if location == "nested" else profile
    moved = repo / "saved-registration"
    if location == "registration":
        registration.rename(moved)
        link = registration
    else:
        profile.parent.mkdir(parents=True, exist_ok=True)
    try:
        if link_kind == "junction":
            import sys
            if sys.platform != "win32":
                pytest.skip("Windows junction test")
            # Both resolved paths are inside this fixture's temporary repository.
            assert link.absolute().is_relative_to(repo) and outside.resolve().is_relative_to(repo)
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            link.symlink_to(outside, target_is_directory=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("Windows symlink privilege unavailable")
    response = client.delete(REGISTER, params={"database_id": DB_ID})
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "POSTGRES_REGISTRATION_INTERNAL_ERROR"}}
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert link.is_symlink() or link.is_junction()
    assert (moved if location == "registration" else registration).is_dir()


def test_storage_rejects_reserved_selection_id_before_inspection(tmp_path: Path) -> None:
    def forbidden(*_args: Any) -> Any:
        pytest.fail("reserved registration must not inspect or call a model")

    with pytest.raises(pg.PostgresRegistrationError) as caught:
        pg.register_postgres_database(
            repository_root=tmp_path, source=pg.PostgresSource("campus", "Campus", SECRET_DSN),
            schema="public", database_id="legacy", label="Legacy", adapter_factory=forbidden,
            mapping_proposer=forbidden,
        )
    assert caught.value.code == "INVALID_INPUT"
    assert list(tmp_path.iterdir()) == []
    # Existing records still pass the ID validator used by loaders.
    assert pg.sqlite_storage.validate_database_id("legacy") == "legacy"


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
    assert "CourseOffering.year" not in decisions
    assert pg.load_draft(repository_root=repo, database_id=DB_ID).ontology_version == 3
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
    assert detail["status"] == "PUBLISHED" and detail["mapping_versions"][0]["activatable"] is True

    # Publishing again makes a new version.
    again = _review(client, {"action": "PUBLISH", "database_id": DB_ID, "published_by": "admin"})
    assert again.json()["mapping_version"] == 2


@pytest.mark.parametrize("action", ["GET", "INIT"])
@pytest.mark.parametrize("stored_at", ["2026-10-12T12:39:49.389950Z", "2026-10-12T12:39:49Z"])
def test_published_review_matches_frontend_history_contract(client_repo, action: str, stored_at: str) -> None:
    client, repo, _app, _executor = client_repo
    assert _register(client).status_code == 200
    _review_all(client)
    for version in (1, 2):
        published = _review(client, {"action": "PUBLISH", "database_id": DB_ID, "published_by": "admin"})
        assert published.status_code == 200
        assert published.json()["mapping_version"] == version

    # Existing records retain their microseconds; only the review response normalizes them.
    path = repo / ".schema-adaptive" / "postgres-registrations" / DB_ID / "published.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["published_at"] = stored_at
    path.write_text(json.dumps(record), encoding="utf-8")
    stored_bytes = path.read_bytes()
    assert pg.load_published(repository_root=repo, database_id=DB_ID).published_at == stored_at

    if action == "GET":
        response = client.get(REVIEW_PATH, params={"database_id": DB_ID})
    else:
        response = _review(client, {"action": "INIT", "database_id": DB_ID, "reviewer": "admin"})
    assert response.status_code == 200
    detail = response.json()
    assert detail["status"] == "PUBLISHED"
    assert detail["mapping_version"] == detail["active_mapping_version"] == 2
    assert detail["mapping_versions"] == [{
        "version": 2, "published_by": "admin", "published_at": "2026-10-12T12:39:49Z",
        "active": True, "latest": True, "activatable": True,
    }]
    assert path.read_bytes() == stored_bytes
    # The history flag does not add PostgreSQL rollback support.
    assert _review(client, {
        "action": "ACTIVATE", "database_id": DB_ID, "mapping_version": 1, "activated_by": "admin",
    }).status_code == 400
    assert path.read_bytes() == stored_bytes


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
        adaptive_app_cleanup = None


def test_launcher_reloads_published_registrations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / ".env").write_text(
        f"DATABASE_URL={SECRET_DSN}\nPG_SOURCE_OTHER=postgresql://other\n", encoding="utf-8"
    )
    monkeypatch.setattr(dev_launcher, "_published_postgres", lambda _root: [("campus-pg", "default", pg.connection_identity(SECRET_DSN)), ("gone-pg", "missing", "0" * 64), ("moved-pg", "other", "0" * 64)])
    requested: list[list[str]] = []
    seen_sources: list[Any] = []

    def fake_load(ids: list[str], _root: Path) -> list[str]:
        requested.append(list(ids))
        return list(ids)

    def fake_create(profiles: Any, model: Any, mapping_model: Any = None, show_sql: bool = False, postgres_sources: Any = None,
                    **_custom_chat_models: Any) -> Any:  # Phase 9-31: repository root and Ollama URL for user models
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
    assert "gone-pg=disabled (SOURCE_MISSING)" in out and "moved-pg=disabled (SOURCE_CHANGED)" in out
    assert "pg_registration=enabled (2 sources)" in out
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
    assert app.state.adaptive_app_loader._pg_contexts is app.state.synthetic_postgres_contexts


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


# --- Phase 8-16: registration list ------------------------------------------------------------


def test_registrations_are_listed_for_reopening(client_repo) -> None:
    client, repo, _app, _executor = client_repo
    assert client.get(REGISTER).json() == {"version": "postgres-registrations-v1", "registrations": []}
    assert _register(client).status_code == 200
    assert _register(client, "campus-pg-two").status_code == 200
    _review_all(client)
    assert _review(client, {"action": "PUBLISH", "database_id": DB_ID, "published_by": "admin"}).status_code == 200
    # A damaged registration is left out instead of failing the list.
    (repo / ".schema-adaptive" / "postgres-registrations" / "campus-pg-two" / "catalog.json").write_text("{}", encoding="utf-8")
    listed = client.get(REGISTER)
    assert listed.status_code == 200
    assert listed.json()["registrations"] == [{
        "database_id": DB_ID, "label": "Campus", "source_id": "campus", "schema": "campus",
        "status": "PUBLISHED", "mapping_version": 1,
    }]
    assert "s3cret" not in listed.text
    assert client.get(REGISTER, params={"x": "1"}).status_code == 400


def test_a_label_the_runtime_would_refuse_is_refused_at_registration(client_repo) -> None:
    client, repo, _app, _executor = client_repo
    response = client.post(REGISTER, json={"source_id": "campus", "schema": "campus", "database_id": DB_ID, "label": "레거시 DB"})
    assert response.status_code == 400
    assert response.json() == {"error": {"code": "POSTGRES_REGISTRATION_INVALID_LABEL"}}
    assert not (repo / ".schema-adaptive" / "postgres-registrations" / DB_ID).exists()


def test_slash_values_may_omit_the_leading_zero() -> None:
    assert pg.transform_fits(FieldTransform.SLASH_FIRST_NUMERIC, "character varying(20)", DataType.NUMBER, ["1/.5/1", "3/3/0"])
    assert not pg.transform_fits(FieldTransform.SLASH_FIRST_NUMERIC, "character varying(20)", DataType.NUMBER, ["3//0"])


def test_publishing_refuses_a_source_that_now_points_elsewhere(client_repo) -> None:
    client, repo, app, _executor = client_repo
    assert _register(client).status_code == 200
    _review_all(client)
    service = app.state.postgres_registration_service
    service._sources = {"campus": pg.PostgresSource("campus", "Campus server", SECRET_DSN.replace("/campus", "/elsewhere"))}
    changed = _review(client, {"action": "PUBLISH", "database_id": DB_ID, "published_by": "admin"})
    assert changed.status_code == 409
    assert not (repo / ".schema-adaptive" / "postgres-profiles" / DB_ID).exists()
    # Only the password changed: still the same database.
    service._sources = {"campus": pg.PostgresSource("campus", "Campus server", SECRET_DSN.replace("s3cret-815", "rotated"))}
    assert _review(client, {"action": "PUBLISH", "database_id": DB_ID, "published_by": "admin"}).status_code == 200


def test_the_list_skips_damaged_drafts(client_repo) -> None:
    client, repo, _app, _executor = client_repo
    assert _register(client).status_code == 200
    _review(client, {"action": "INIT", "database_id": DB_ID, "reviewer": "admin"})
    assert [item["database_id"] for item in client.get(REGISTER).json()["registrations"]] == [DB_ID]
    (repo / ".schema-adaptive" / "postgres-registrations" / DB_ID / "draft.json").write_text("{", encoding="utf-8")
    assert client.get(REGISTER).json()["registrations"] == []


# Phase 9-13: fixed PostgreSQL queries share the published chat runtime.
def _publish_for_test_queries(client):
    assert _register(client).status_code == 200
    _review_all(client)
    assert _review(client, {"action": "PUBLISH", "database_id": DB_ID, "published_by": "admin"}).status_code == 200


def test_postgres_fixed_queries_baseline_and_regression(client_repo):
    from backend.adaptive.api.admin_test_queries import TEST_QUERIES, TEST_QUERY_PATH, VERIFIED_QUERIES_PATH, REGRESSION_PATH

    client, repo, app, executor = client_repo
    _publish_for_test_queries(client)
    context = app.state.postgres_registration_service.test_query_context(repo, DB_ID)
    for query_id, definition in TEST_QUERIES.items():
        response = client.get(TEST_QUERY_PATH, params={"database_id": DB_ID, "query_id": query_id})
        assert response.status_code == 200, response.text
        result = response.json()
        omitted = [field for field in definition["select"] if field in context.unsupported_fields()]
        assert result["omitted_fields"] == omitted
        assert result["columns"] == [field for field in definition["select"] if field not in omitted]
        assert result["mapping_version"] == 1
        assert result["rows"] == [] and result["row_count"] == 0
        assert result["row_count_capped"] is False
        assert len(result["result_sha256"]) == 64
        assert VIEW in executor.queries[-1].sql
        verified = client.post(VERIFIED_QUERIES_PATH, json={"action": "VERIFY", "database_id": DB_ID, "query_id": query_id, "verified_by": "admin"})
        assert verified.status_code == 200, verified.text
    baseline = repo / ".schema-adaptive" / "postgres-registrations" / DB_ID / "verified-queries.json"
    saved = json.loads(baseline.read_text(encoding="utf-8"))
    assert len(saved["entries"]) == 5
    assert all(set(entry) == {"query_id", "mapping_version", "result_sha256", "row_count", "verified_by", "verified_at"} for entry in saved["entries"])
    response = client.get(REGRESSION_PATH, params={"database_id": DB_ID, "mapping_version": 1})
    assert response.status_code == 200, response.text
    assert response.json()["summary"] == {"match": 5, "changed": 0, "failed": 0, "unverified": 0}
    # Return one row using the compiled canonical projection, including normalized values.
    def changed(database_id, compiled):
        columns = list(compiled.expected_columns)
        return columns, [{field: None for field in columns}]
    executor.execute = changed
    response = client.get(REGRESSION_PATH, params={"database_id": DB_ID, "mapping_version": 1})
    assert response.json()["summary"]["changed"] == 5
    response = client.get(REGRESSION_PATH, params={"database_id": DB_ID, "mapping_version": 2})
    assert response.status_code == 422
    assert response.json()["error"]["failure_code"] == "POSTGRES_CURRENT_VERSION_ONLY"
    assert "s3cret" not in baseline.read_text(encoding="utf-8")


def test_postgres_all_unsupported_query_and_stale_profile(client_repo):
    from backend.adaptive.api.admin_test_queries import TEST_QUERIES, TEST_QUERY_PATH

    client, repo, app, executor = client_repo
    assert _register(client).status_code == 200
    _review_all(client)
    for name in TEST_QUERIES["seats"]["select"]:
        assert _review(client, {"action": "IGNORE", "database_id": DB_ID, "semantic_field": name, "reviewer": "admin", "reason": "absent"}).status_code == 200
    assert _review(client, {"action": "PUBLISH", "database_id": DB_ID, "published_by": "admin"}).status_code == 200
    response = client.get(TEST_QUERY_PATH, params={"database_id": DB_ID, "query_id": "seats"})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["failure_code"] == "TEST_QUERY_UNSUPPORTED"
    assert not executor.queries

    path = repo / ".schema-adaptive" / "postgres-profiles" / DB_ID / "mapping.json"
    path.write_text('{}', encoding="utf-8")
    response = client.get(TEST_QUERY_PATH, params={"database_id": DB_ID, "query_id": "offerings"})
    assert response.status_code == 404
    assert not executor.queries


def test_postgres_preview_cap_normalization_and_verifier_validation(client_repo):
    from decimal import Decimal
    from backend.adaptive.api.admin_test_queries import TEST_QUERY_PATH, VERIFIED_QUERIES_PATH

    client, repo, _app, executor = client_repo
    _publish_for_test_queries(client)

    def execute(database_id, compiled):
        columns = list(compiled.expected_columns)
        assert compiled.parameters[-1] == 200
        rows = [
            {field: Decimal("3") if field == "Course.credits" else str(index) for field in columns}
            for index in range(200)
        ]
        return columns, rows

    executor.execute = execute
    result = client.get(TEST_QUERY_PATH, params={"database_id": DB_ID, "query_id": "courses"})
    assert result.status_code == 200, result.text
    body = result.json()
    assert len(body["rows"]) == 5
    assert body["row_count"] == 200 and body["row_count_capped"] is True
    credit_index = body["columns"].index("Course.credits")
    assert all(row[credit_index] == 3 for row in body["rows"])
    result = client.post(VERIFIED_QUERIES_PATH, json={
        "action": "VERIFY", "database_id": DB_ID, "query_id": "courses", "verified_by": "",
    })
    assert result.status_code == 400
    assert not (repo / ".schema-adaptive" / "postgres-registrations" / DB_ID / "verified-queries.json").exists()
