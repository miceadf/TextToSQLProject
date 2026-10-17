"""Phase 8-05: verified query baselines and version regression (synthetic only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.adaptive.api.admin_test_queries import (
    REGRESSION_PATH,
    TEST_QUERIES,
    VERIFIED_QUERIES_PATH,
)
from backend.adaptive.api.app_factory import (
    create_synthetic_clarification_prototype,
    set_private_repository_root_for_tests,
)
from backend.adaptive.api.database_registry import CATALOG_PATH
from backend.adaptive.fixtures.flat import build_flat_database
from backend.adaptive.fixtures.seed import DEFAULT_SEED
from backend.adaptive.registration import reviews as review_storage
from tests.adaptive.test_mapping_version_rollback import _source_with_alternate_title
from tests.adaptive.test_private_mapping_publish import _action, _make_repo, _publish_flow, _upload

DB = "vq-db"


@pytest.fixture
def client_repo(tmp_path: Path):
    repo = _make_repo(tmp_path, "repo")
    app = create_synthetic_clarification_prototype()
    app.state.sqlite_upload_repository_root = str(repo)
    app.state.mapping_review_repository_root = str(repo)
    set_private_repository_root_for_tests(repo)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        _publish_flow(client, _source_with_alternate_title(tmp_path).read_bytes(), DB)
        yield client, repo
    finally:
        client.close()
        set_private_repository_root_for_tests(None)


def _verify(client: TestClient, query_id: str, by: object = "admin", **extra):
    body = {"action": "VERIFY", "database_id": DB, "query_id": query_id, "verified_by": by}
    body.update(extra)
    return client.post(VERIFIED_QUERIES_PATH, json=body)


def _regression(client: TestClient, version: object):
    return client.get(REGRESSION_PATH, params={"database_id": DB, "mapping_version": version})


def _publish_v2(client: TestClient) -> None:
    assert _action(client, {
        "action": "REMAP", "database_id": DB, "semantic_field": "Course.name",
        "table": "course_offerings", "column": "course_title", "reviewer": "admin", "reason": "alternate",
    }).status_code == 200
    assert _action(client, {"action": "PUBLISH", "database_id": DB, "published_by": "admin"}).status_code == 200


def _statuses(body: dict) -> dict[str, str]:
    return {item["query_id"]: item["status"] for item in body["results"]}


def _review_dir(repo: Path) -> Path:
    return repo / ".schema-adaptive" / "registration-reviews" / DB


def test_verified_baselines_match_on_the_same_version(client_repo) -> None:
    client, repo = client_repo
    for query_id in TEST_QUERIES:
        response = _verify(client, query_id)
        assert response.status_code == 200, response.text[:300]
    body = response.json()
    assert body["version"] == "verified-queries-v1"
    assert [item["query_id"] for item in body["entries"]] == sorted(TEST_QUERIES)
    for item in body["entries"]:
        assert set(item) == {"query_id", "mapping_version", "result_sha256", "row_count", "verified_by", "verified_at"}
        assert item["mapping_version"] == 1 and item["row_count"] == len(DEFAULT_SEED.offerings)
    stored = (_review_dir(repo) / "verified-queries.json").read_text(encoding="utf-8")
    assert "프로그래밍기초" not in stored and "CSE101" not in stored
    regression = _regression(client, 1).json()
    assert regression["version"] == "verified-query-regression-v1"
    assert regression["mapping_version"] == 1 and regression["active_mapping_version"] == 1
    assert set(_statuses(regression).values()) == {"MATCH"}
    assert regression["summary"] == {"match": 5, "changed": 0, "failed": 0, "unverified": 0}


def test_new_version_changes_only_affected_queries_and_rollback_target_matches(client_repo) -> None:
    client, _repo = client_repo
    for query_id in TEST_QUERIES:
        assert _verify(client, query_id).status_code == 200
    _publish_v2(client)
    after = _regression(client, 2).json()
    statuses = _statuses(after)
    # offerings and courses select Course.name; the others do not.
    assert statuses == {
        "offerings": "CHANGED", "courses": "CHANGED",
        "departments": "MATCH", "schedules": "MATCH", "seats": "MATCH",
    }
    assert after["active_mapping_version"] == 2
    # Before re-activating, the non-active v1 can be checked and still matches.
    before_rollback = _regression(client, 1).json()
    assert set(_statuses(before_rollback).values()) == {"MATCH"}
    assert before_rollback["active_mapping_version"] == 2
    # Regression never changes execution authority.
    assert review_storage.load_activation(repository_root=_repo, database_id=DB).mapping_version == 2


def test_unverified_queries_are_reported(client_repo) -> None:
    client, _repo = client_repo
    assert _verify(client, "seats").status_code == 200
    body = _regression(client, 1).json()
    statuses = _statuses(body)
    assert statuses["seats"] == "MATCH"
    assert {statuses[q] for q in TEST_QUERIES if q != "seats"} == {"UNVERIFIED"}
    unverified = next(item for item in body["results"] if item["query_id"] == "offerings")
    assert unverified["row_count"] == len(DEFAULT_SEED.offerings)
    assert unverified["baseline_mapping_version"] is None


def test_damaged_baseline_is_unverified_and_harmless(client_repo) -> None:
    client, repo = client_repo
    assert _verify(client, "seats").status_code == 200
    path = _review_dir(repo) / "verified-queries.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["entries"][0]["result_sha256"] = "zz"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert set(_statuses(_regression(client, 1).json()).values()) == {"UNVERIFIED"}
    assert client.get(CATALOG_PATH, params={"database_id": DB}).status_code == 200
    assert _action(client, {"action": "INIT", "database_id": DB, "reviewer": "admin"}).status_code == 200
    # An explicit verify replaces the damaged baseline.
    assert _verify(client, "seats").status_code == 200
    assert _statuses(_regression(client, 1).json())["seats"] == "MATCH"


def test_unrecorded_versions_and_unpublished_databases_fail_closed(client_repo, tmp_path: Path) -> None:
    client, repo = client_repo
    _publish_v2(client)
    review_dir = _review_dir(repo)
    payload = json.loads((review_dir / "mapping-v0002.json").read_text(encoding="utf-8"))
    payload["version"] = 3
    (review_dir / "mapping-v0003.json").write_text(json.dumps(payload), encoding="utf-8")
    flat = build_flat_database(tmp_path / "pending.sqlite3", seed=DEFAULT_SEED).read_bytes()
    assert _upload(client, "vq-pending", "Pending", flat).status_code == 200
    cases = [
        (_regression(client, 3), 404),           # orphan, never published
        (_regression(client, 9), 404),
        (client.get(REGRESSION_PATH, params={"database_id": "vq-pending", "mapping_version": 1}), 404),
        (_regression(client, 0), 400),
        (_regression(client, "1.0"), 400),
        (_regression(client, "-1"), 400),
        (_regression(client, "٣"), 400),
        (client.get(REGRESSION_PATH, params={"database_id": DB}), 400),
        (client.post(VERIFIED_QUERIES_PATH, json={
            "action": "VERIFY", "database_id": "vq-pending", "query_id": "seats", "verified_by": "admin",
        }), 404),
        (_verify(client, "unknown"), 400),
        (_verify(client, "seats", by=""), 400),
        (_verify(client, "seats", by=1), 400),
        (_verify(client, "seats", result_sha256="0" * 64), 400),
        (_verify(client, "seats", action="PUBLISH"), 400),
    ]
    for response, status in cases:
        assert response.status_code == status, response.text[:200]
        assert set(response.json()) == {"error"}
        assert str(repo) not in response.text
    # Substituting a recorded version's bytes is also refused.
    v1 = json.loads((review_dir / "mapping-v0001.json").read_text(encoding="utf-8"))
    v1["published_by"] = "someone-else"
    (review_dir / "mapping-v0001.json").write_text(json.dumps(v1), encoding="utf-8")
    assert _regression(client, 1).status_code == 404


def test_lock_conflict_on_verify(client_repo) -> None:
    client, repo = client_repo
    lock = _review_dir(repo).parent / f".lock-{DB}.lock"
    lock.write_text("", encoding="utf-8")
    try:
        assert _verify(client, "seats").status_code == 409
    finally:
        lock.unlink()
    assert not (_review_dir(repo) / "verified-queries.json").exists()


# ---------------------------------------------------------------------------
# Luna review regressions


def test_leftover_staging_file_does_not_disable_activation(client_repo) -> None:
    client, repo = client_repo
    assert _verify(client, "seats").status_code == 200
    # A crash between mkstemp and os.replace leaves a staging file behind.
    for name in ("verified-queries.json.k3x_9q.tmp", "draft.json.a1b2c3.tmp"):
        (_review_dir(repo) / name).write_text("{", encoding="utf-8")
    assert review_storage.load_activation(repository_root=repo, database_id=DB).mapping_version == 1
    assert client.get(CATALOG_PATH, params={"database_id": DB}).status_code == 200
    assert _statuses(_regression(client, 1).json())["seats"] == "MATCH"
    # Anything that is not a staging leftover still fails closed.
    (_review_dir(repo) / "notes.txt").write_text("x", encoding="utf-8")
    assert client.get(CATALOG_PATH, params={"database_id": DB}).status_code == 404
