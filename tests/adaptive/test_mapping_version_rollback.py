"""Phase 8-01: mapping version history and re-activation (synthetic only)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.adaptive.api.review_publish import REVIEW_PATH
from backend.adaptive.api.app_factory import (
    create_synthetic_clarification_prototype,
    set_private_repository_root_for_tests,
)
from backend.adaptive.api.database_registry import CATALOG_PATH, DATABASES_PATH
from backend.adaptive.fixtures.flat import build_flat_database
from backend.adaptive.fixtures.seed import DEFAULT_SEED
from backend.adaptive.registration import reviews as review_storage
from backend.adaptive.registration.reviews import ReviewError, ReviewErrorCode
from tests.adaptive.test_private_mapping_publish import _action, _make_repo, _publish_flow

DATABASE_ID = "rollback-private"


def _source_with_alternate_title(tmp_path: Path) -> Path:
    """Flat fixture plus one spare column so a second publish can differ."""
    path = build_flat_database(tmp_path / "flat-alt.sqlite3", seed=DEFAULT_SEED)
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE course_offerings ADD COLUMN course_title TEXT")
        connection.execute("UPDATE course_offerings SET course_title = 'ALT-' || course_code")
    return path


@pytest.fixture
def client_repo(tmp_path: Path):
    repo = _make_repo(tmp_path, "repo")
    source = _source_with_alternate_title(tmp_path)
    app = create_synthetic_clarification_prototype()
    app.state.sqlite_upload_repository_root = str(repo)
    app.state.mapping_review_repository_root = str(repo)
    set_private_repository_root_for_tests(repo)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, repo, source
    finally:
        client.close()
        set_private_repository_root_for_tests(None)


def _publish_two_versions(client: TestClient, source: Path) -> Path:
    _publish_flow(client, source.read_bytes(), DATABASE_ID)
    remapped = _action(client, {
        "action": "REMAP", "database_id": DATABASE_ID, "semantic_field": "Course.name",
        "table": "course_offerings", "column": "course_title",
        "reviewer": "tester", "reason": "synthetic alternate title",
    })
    assert remapped.status_code == 200, remapped.text[:300]
    published = _action(client, {"action": "PUBLISH", "database_id": DATABASE_ID, "published_by": "second"})
    assert published.status_code == 200
    assert published.json()["mapping_version"] == 2
    root = Path(client.app.state.mapping_review_repository_root)
    return root / ".schema-adaptive" / "registration-reviews" / DATABASE_ID


def _catalog_names(client: TestClient) -> list[str]:
    response = client.get(CATALOG_PATH, params={"database_id": DATABASE_ID})
    assert response.status_code == 200
    return sorted(str(item["course_name"]) for item in response.json()["items"])


def _activate(client: TestClient, version: object, by: object = "admin", **extra: object):
    body = {"action": "ACTIVATE", "database_id": DATABASE_ID, "mapping_version": version, "activated_by": by}
    body.update(extra)
    return _action(client, body)


ORIGINAL_NAMES = sorted(offering.course_name for offering in DEFAULT_SEED.offerings)
ALTERNATE_NAMES = sorted(f"ALT-{offering.course_code}" for offering in DEFAULT_SEED.offerings)


def test_rollback_switches_execution_and_keeps_latest_pointer(client_repo) -> None:
    client, repo, source = client_repo
    review_dir = _publish_two_versions(client, source)
    assert _catalog_names(client) == ALTERNATE_NAMES

    before = {path.name: path.read_bytes() for path in review_dir.glob("mapping-v*.json")}
    current_before = (review_dir / "current.json").read_bytes()
    response = _activate(client, 1)
    assert response.status_code == 200, response.text[:300]
    assert response.json() == {
        "version": "private-mapping-review-v2",
        "database_id": DATABASE_ID,
        "status": "PUBLISHED",
        "mapping_version": 1,
        "registry_eligible": True,
    }
    assert _catalog_names(client) == ORIGINAL_NAMES
    assert DATABASE_ID in {entry["database_id"] for entry in client.get(DATABASES_PATH).json()["databases"]}
    # Immutable mapping files and the latest pointer never change.
    assert {path.name: path.read_bytes() for path in review_dir.glob("mapping-v*.json")} == before
    assert (review_dir / "current.json").read_bytes() == current_before
    manifest = json.loads((review_dir / "activation.json").read_text(encoding="utf-8"))
    assert manifest["mapping_version"] == 1
    assert manifest["activated_by"] == "admin"

    detail = client.get(REVIEW_PATH, params={"database_id": DATABASE_ID}).json()
    assert detail["mapping_version"] == 2
    assert detail["active_mapping_version"] == 1
    assert [
        (item["version"], item["published_by"], item["active"], item["latest"], item["activatable"])
        for item in detail["mapping_versions"]
    ] == [(1, "tester", True, False, True), (2, "second", False, True, True)]
    for item in detail["mapping_versions"]:
        assert item["published_at"].endswith("Z")
    blob = json.dumps(detail, ensure_ascii=False)
    # Physical names are part of the existing admin review view; paths and rows are not.
    for marker in (str(repo), "mapping-v0001", "ALT-", "activation.json"):
        assert marker not in blob

    forward = _activate(client, 2)
    assert forward.status_code == 200
    assert _catalog_names(client) == ALTERNATE_NAMES


def test_same_version_activation_is_idempotent(client_repo) -> None:
    client, _repo, source = client_repo
    review_dir = _publish_two_versions(client, source)
    manifest = (review_dir / "activation.json").read_bytes()
    response = _activate(client, 2, by="other")
    assert response.status_code == 200
    assert (review_dir / "activation.json").read_bytes() == manifest


def test_republish_of_latest_content_reactivates_latest(client_repo) -> None:
    client, _repo, source = client_repo
    review_dir = _publish_two_versions(client, source)
    assert _activate(client, 1).status_code == 200
    assert _catalog_names(client) == ORIGINAL_NAMES
    again = _action(client, {"action": "PUBLISH", "database_id": DATABASE_ID, "published_by": "tester"})
    assert again.status_code == 200
    assert again.json()["mapping_version"] == 2
    assert _catalog_names(client) == ALTERNATE_NAMES
    assert sorted(path.name for path in review_dir.glob("mapping-v*.json")) == [
        "mapping-v0001.json", "mapping-v0002.json"
    ]


def test_interrupted_activation_is_repaired_by_republish(client_repo) -> None:
    client, _repo, source = client_repo
    review_dir = _publish_two_versions(client, source)
    (review_dir / "activation.json").unlink()
    assert client.get(CATALOG_PATH, params={"database_id": DATABASE_ID}).status_code == 404
    # ACTIVATE requires an existing activation record; PUBLISH repairs it.
    assert _activate(client, 2).status_code == 404
    again = _action(client, {"action": "PUBLISH", "database_id": DATABASE_ID, "published_by": "tester"})
    assert again.status_code == 200
    assert _catalog_names(client) == ALTERNATE_NAMES


@pytest.mark.parametrize(
    "version,by,extra,status",
    [
        (3, "admin", {}, 404),
        (0, "admin", {}, 400),
        (10000, "admin", {}, 400),
        ("1", "admin", {}, 400),
        (True, "admin", {}, 400),
        (1.0, "admin", {}, 400),
        (1, "", {}, 400),
        (1, " admin", {}, 400),
        (1, "admin", {"reason": "x"}, 400),
    ],
)
def test_invalid_activation_requests_fail_closed(client_repo, version, by, extra, status) -> None:
    client, repo, source = client_repo
    review_dir = _publish_two_versions(client, source)
    manifest = (review_dir / "activation.json").read_bytes()
    response = _activate(client, version, by, **extra)
    assert response.status_code == status
    assert set(response.json()) == {"error"}
    assert set(response.json()["error"]) == {"code"}
    assert str(repo) not in response.text
    assert (review_dir / "activation.json").read_bytes() == manifest


def test_duplicate_key_body_is_rejected(client_repo) -> None:
    client, _repo, source = client_repo
    _publish_two_versions(client, source)
    raw = (
        '{"action":"ACTIVATE","database_id":"%s","mapping_version":1,"mapping_version":2,'
        '"activated_by":"admin"}' % DATABASE_ID
    )
    response = client.post(REVIEW_PATH, content=raw.encode("utf-8"), headers={"Content-Type": "application/json"})
    assert response.status_code == 400


@pytest.mark.parametrize("damage", ["bytes", "fingerprint", "coverage", "version"])
def test_tampered_target_version_is_blocked_and_active_kept(client_repo, damage: str) -> None:
    client, _repo, source = client_repo
    review_dir = _publish_two_versions(client, source)
    target = review_dir / "mapping-v0001.json"
    if damage == "bytes":
        target.write_bytes(target.read_bytes()[:-5])
    else:
        payload = json.loads(target.read_text(encoding="utf-8"))
        if damage == "fingerprint":
            payload["schema_fingerprint"] = "0" * 64
        elif damage == "coverage":
            payload["fields"].pop("Course.name")
        else:
            payload["version"] = 2
        target.write_text(json.dumps(payload), encoding="utf-8")
    manifest = (review_dir / "activation.json").read_bytes()
    response = _activate(client, 1)
    assert response.status_code in (404, 422)
    assert set(response.json()["error"]) == {"code"}
    assert (review_dir / "activation.json").read_bytes() == manifest
    assert _catalog_names(client) == ALTERNATE_NAMES
    history = client.get(REVIEW_PATH, params={"database_id": DATABASE_ID}).json()["mapping_versions"]
    assert all(item["version"] != 1 or item["activatable"] is False for item in history)


def test_unpublished_database_cannot_be_activated(client_repo) -> None:
    client, repo, source = client_repo
    from tests.adaptive.test_private_mapping_publish import _upload

    assert _upload(client, DATABASE_ID, "Label", source.read_bytes()).status_code == 200
    assert _action(client, {"action": "INIT", "database_id": DATABASE_ID, "reviewer": "tester"}).status_code == 200
    response = _activate(client, 1)
    assert response.status_code == 404
    detail = client.get(REVIEW_PATH, params={"database_id": DATABASE_ID}).json()
    assert detail["status"] == "PENDING_REVIEW"
    assert "mapping_versions" not in detail and "active_mapping_version" not in detail
    with pytest.raises(ReviewError) as caught:
        review_storage.activate_mapping_version(
            repository_root=repo, database_id=DATABASE_ID, mapping_version=1, activated_by="admin"
        )
    assert caught.value.code is ReviewErrorCode.ARTIFACT_MISMATCH


def test_orphan_version_above_pointer_is_not_listed_or_activatable(client_repo) -> None:
    client, repo, source = client_repo
    review_dir = _publish_two_versions(client, source)
    payload = json.loads((review_dir / "mapping-v0002.json").read_text(encoding="utf-8"))
    payload["version"] = 3
    (review_dir / "mapping-v0003.json").write_text(json.dumps(payload), encoding="utf-8")
    versions = review_storage.list_mapping_versions(repository_root=repo, database_id=DATABASE_ID)
    assert [item.version for item in versions] == [1, 2]
    assert _activate(client, 3).status_code == 404


def test_manifest_without_activation_fields_still_loads(client_repo) -> None:
    client, repo, source = client_repo
    review_dir = _publish_two_versions(client, source)
    manifest = json.loads((review_dir / "activation.json").read_text(encoding="utf-8"))
    manifest.pop("activated_by")
    manifest.pop("activated_at")
    (review_dir / "activation.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert review_storage.load_activation(repository_root=repo, database_id=DATABASE_ID).mapping_version == 2
    assert _catalog_names(client) == ALTERNATE_NAMES


def test_activation_conflicts_with_held_lock(client_repo) -> None:
    client, repo, source = client_repo
    review_dir = _publish_two_versions(client, source)
    lock = review_dir.parent / f".lock-{DATABASE_ID}.lock"
    lock.write_text("", encoding="utf-8")
    try:
        response = _activate(client, 1)
        assert response.status_code == 409
    finally:
        lock.unlink()
    assert _catalog_names(client) == ALTERNATE_NAMES


def test_manifest_with_explicit_null_activation_fields_is_rejected(client_repo) -> None:
    client, repo, source = client_repo
    review_dir = _publish_two_versions(client, source)
    manifest = json.loads((review_dir / "activation.json").read_text(encoding="utf-8"))
    manifest["activated_by"] = None
    (review_dir / "activation.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ReviewError):
        review_storage.load_activation(repository_root=repo, database_id=DATABASE_ID)
    assert client.get(CATALOG_PATH, params={"database_id": DATABASE_ID}).status_code == 404


# ---------------------------------------------------------------------------
# Luna review regressions


def test_orphan_below_pointer_from_interrupted_publish_is_not_activatable(client_repo) -> None:
    client, repo, source = client_repo
    review_dir = _publish_two_versions(client, source)
    # An interrupted publish leaves v3 written but never pointed to.
    payload = json.loads((review_dir / "mapping-v0001.json").read_text(encoding="utf-8"))
    payload["version"] = 3
    (review_dir / "mapping-v0003.json").write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    # The next publish skips past the orphan and advances the pointer to v4.
    remapped = _action(client, {
        "action": "REMAP", "database_id": DATABASE_ID, "semantic_field": "Course.name",
        "table": "course_offerings", "column": "course_name",
        "reviewer": "tester", "reason": "back to original",
    })
    assert remapped.status_code == 200
    published = _action(client, {"action": "PUBLISH", "database_id": DATABASE_ID, "published_by": "third"})
    assert published.json()["mapping_version"] == 4
    assert _activate(client, 3).status_code in (404, 422)
    listed = review_storage.list_mapping_versions(repository_root=repo, database_id=DATABASE_ID)
    assert [item.version for item in listed] == [1, 2, 4]


def test_valid_but_substituted_mapping_file_is_not_activatable(client_repo) -> None:
    client, _repo, source = client_repo
    review_dir = _publish_two_versions(client, source)
    # Replace v1 with v2's executable content relabelled as v1.
    payload = json.loads((review_dir / "mapping-v0002.json").read_text(encoding="utf-8"))
    payload["version"] = 1
    (review_dir / "mapping-v0001.json").write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    manifest = (review_dir / "activation.json").read_bytes()
    assert _activate(client, 1).status_code in (404, 422)
    assert (review_dir / "activation.json").read_bytes() == manifest


def test_pending_clarification_cannot_execute_a_deactivated_mapping(client_repo) -> None:
    from backend.adaptive.api.clarification_http import START_PATH, SUBMIT_PATH

    client, _repo, source = client_repo
    _publish_two_versions(client, source)
    started = client.post(START_PATH, json={
        "request_id": "req-rollback-stale", "database_id": DATABASE_ID,
        "question": "SYNTH_Q_P616_k9w1 강의 목록 보여줘",
    })
    assert started.status_code == 200
    course = next(item for item in started.json()["options"] if item["semantic_ref"] == "Course.name")
    assert _activate(client, 1).status_code == 200
    submitted = client.post(SUBMIT_PATH, json={
        "session_token": started.json()["session_token"], "selection": [course["option_id"]],
    })
    body = submitted.json()
    executed = body.get("execution") or {}
    assert executed.get("status") != "SUCCEEDED", body
    assert "ALT-" not in submitted.text


def test_active_version_is_listed_beyond_the_listing_cap(client_repo, monkeypatch) -> None:
    client, repo, source = client_repo
    _publish_two_versions(client, source)
    assert _activate(client, 1).status_code == 200
    monkeypatch.setattr(review_storage, "MAX_LISTED_VERSIONS", 1)
    listed = review_storage.list_mapping_versions(repository_root=repo, database_id=DATABASE_ID)
    assert [(item.version, item.active, item.latest) for item in listed] == [(1, True, False), (2, False, True)]
    detail = client.get(REVIEW_PATH, params={"database_id": DATABASE_ID}).json()
    assert detail["active_mapping_version"] == 1
