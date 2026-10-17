"""Focused tests for Phase 6-19 private mapping review, publish, and activation."""

from __future__ import annotations

import json
import hashlib
import sqlite3
import subprocess
from pathlib import Path

import pytest

from backend.adaptive.ontology import load_default_ontology
from fastapi.testclient import TestClient

from backend.adaptive.api.clarification_http import START_PATH, SUBMIT_PATH
from backend.adaptive.api.registration_upload import (
    SQLITE_UPLOAD_CONTENT_TYPE,
    SQLITE_UPLOAD_PATH,
)
from backend.adaptive.api.review_publish import REVIEW_PATH, REVIEW_VERSION
from backend.adaptive.api.app_factory import (
    SYNTHETIC_LEGACY_DATABASE_ID,
    SYNTHETIC_PROTOTYPE_DATABASE_ID,
    create_synthetic_clarification_prototype,
    set_private_repository_root_for_tests,
)
from backend.adaptive.api.database_registry import (
    CANONICAL_SELECT,
    CATALOG_PATH,
    DATABASES_PATH,
)
from backend.adaptive.fixtures.flat import build_flat_database
from backend.adaptive.fixtures.seed import DEFAULT_SEED
from backend.adaptive.registration import reviews as review_storage
from backend.adaptive.registration import storage as registration_storage

TRIGGER = "SYNTH_Q_P616_k9w1 강의 목록 보여줘"


def _make_repo(base: Path, name: str) -> Path:
    repo = base / name
    repo.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-q", str(repo)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (repo / ".gitignore").write_text(".schema-adaptive/\n", encoding="utf-8")
    return repo


@pytest.fixture
def review_client(tmp_path: Path):
    repo = _make_repo(tmp_path, "repo")
    source = build_flat_database(tmp_path / "flat.sqlite3", seed=DEFAULT_SEED)
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


def _upload(client: TestClient, database_id: str, label: str, raw: bytes):
    return client.post(
        SQLITE_UPLOAD_PATH + f"?database_id={database_id}&label={label}",
        content=raw,
        headers={"Content-Type": SQLITE_UPLOAD_CONTENT_TYPE},
    )


def _action(client: TestClient, payload: dict):
    return client.post(REVIEW_PATH, json=payload)


def _approve_all(client: TestClient, database_id: str, reviewer: str = "tester") -> None:
    detail = client.get(REVIEW_PATH, params={"database_id": database_id}).json()
    assert detail["version"] == REVIEW_VERSION
    for field in detail["review"]["fields"]:
        if not field["candidates"] and field["semantic_field"] not in CANONICAL_SELECT:
            # Phase 8-18: a newer optional field these fixtures do not store.
            payload = {"action": "IGNORE", "reason": "이 DB에 없음"}
        else:
            payload = {"action": "APPROVE", "candidate_index": 0}
        response = _action(
            client,
            {
                **payload,
                "database_id": database_id,
                "semantic_field": field["semantic_field"],
                "reviewer": reviewer,
            },
        )
        assert response.status_code == 200, (field["semantic_field"], response.text[:300])


def _ignore_newer(client: TestClient, database_id: str, reviewer: str = "tester") -> None:
    """Phase 8-18: mark the fields these fixtures do not store (target grade) as absent."""
    response = _action(client, {
        "action": "IGNORE", "database_id": database_id, "semantic_field": "Course.target_grade",
        "reviewer": reviewer, "reason": "이 DB에 없음",
    })
    assert response.status_code == 200, response.text[:300]


def _publish_flow(client: TestClient, raw: bytes, database_id: str) -> None:
    uploaded = _upload(client, database_id, "Label", raw)
    assert uploaded.status_code == 200, uploaded.text[:300]
    initiated = _action(
        client, {"action": "INIT", "database_id": database_id, "reviewer": "tester"}
    )
    assert initiated.status_code == 200, initiated.text[:300]
    _approve_all(client, database_id)
    published = _action(
        client,
        {"action": "PUBLISH", "database_id": database_id, "published_by": "tester"},
    )
    assert published.status_code == 200, published.text[:300]
    assert published.json()["status"] == "PUBLISHED"


def test_round_trip_publish_activation(review_client) -> None:
    client, repo, source = review_client
    raw = source.read_bytes()
    database_id = "review-round-trip"

    missing = client.get(REVIEW_PATH, params={"database_id": database_id})
    assert missing.status_code == 404
    assert missing.json() == {"error": {"code": "MAPPING_REVIEW_NOT_FOUND"}}

    assert _upload(client, database_id, "Label", raw).status_code == 200
    first = _action(
        client, {"action": "INIT", "database_id": database_id, "reviewer": "tester"}
    )
    assert first.status_code == 200
    first_detail = first.json()
    assert "CourseOffering.year" not in {item["semantic_field"] for item in first_detail["review"]["fields"]}
    assert first_detail["status"] == "PENDING_REVIEW"
    assert first_detail["review"]["field_count"] == len(load_default_ontology().active_fields)
    assert {item["semantic_field"] for item in first_detail["review"]["fields"]} == set(
        load_default_ontology().active_fields
    )
    assert all(item["decision"] == "PENDING" for item in first_detail["review"]["fields"])

    second = _action(
        client, {"action": "INIT", "database_id": database_id, "reviewer": "tester"}
    )
    assert second.status_code == 200
    assert second.json()["review"] == first_detail["review"]

    _approve_all(client, database_id)
    published = _action(
        client,
        {"action": "PUBLISH", "database_id": database_id, "published_by": "tester"},
    )
    assert published.status_code == 200
    assert published.json() == {
        "version": REVIEW_VERSION,
        "database_id": database_id,
        "status": "PUBLISHED",
        "mapping_version": 1,
        "registry_eligible": True,
    }

    activation = review_storage.load_activation(
        repository_root=repo, database_id=database_id
    )
    assert set(activation.profile.fields) == set(CANONICAL_SELECT)
    assert activation.database_path.is_file()
    assert activation.database_path.name == "database.sqlite3"

    detail = client.get(REVIEW_PATH, params={"database_id": database_id}).json()
    assert detail["status"] == "PUBLISHED"
    assert detail["mapping_version"] == 1

    retry = _action(
        client,
        {"action": "PUBLISH", "database_id": database_id, "published_by": "tester"},
    )
    assert retry.status_code == 200
    assert retry.json()["mapping_version"] == 1
    review_dir = repo / ".schema-adaptive" / "registration-reviews" / database_id
    assert not (review_dir / "mapping-v0002.json").exists()

    blob = published.text + detail["status"]
    assert "Traceback" not in published.text
    assert str(repo) not in published.text


def test_pre_publish_hidden_through_stages(review_client) -> None:
    client, _repo, source = review_client
    raw = source.read_bytes()
    database_id = "hidden-stages"
    before = client.get(DATABASES_PATH).text

    def assert_hidden() -> None:
        after = client.get(DATABASES_PATH).text
        assert after == before
        assert database_id not in after
        catalog = client.get(CATALOG_PATH, params={"database_id": database_id})
        assert catalog.status_code == 404
        started = client.post(
            START_PATH,
            json={
                "request_id": f"req-{database_id}",
                "database_id": database_id,
                "question": TRIGGER,
            },
        )
        assert started.status_code == 503

    assert _upload(client, database_id, "Label", raw).status_code == 200
    assert_hidden()
    assert (
        _action(
            client,
            {"action": "INIT", "database_id": database_id, "reviewer": "tester"},
        ).status_code
        == 200
    )
    assert_hidden()
    detail = client.get(REVIEW_PATH, params={"database_id": database_id}).json()
    for field in detail["review"]["fields"][:3]:
        assert (
            _action(
                client,
                {
                    "action": "APPROVE",
                    "database_id": database_id,
                    "semantic_field": field["semantic_field"],
                    "candidate_index": 0,
                    "reviewer": "tester",
                },
            ).status_code
            == 200
        )
    assert_hidden()
    blocked = _action(
        client,
        {"action": "PUBLISH", "database_id": database_id, "published_by": "tester"},
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "MAPPING_REVIEW_PUBLISH_BLOCKED"
    assert_hidden()


def test_post_publish_registry_catalog_clarification(review_client) -> None:
    client, _repo, source = review_client
    raw = source.read_bytes()
    database_id = "executable-private"
    _publish_flow(client, raw, database_id)

    registry = client.get(DATABASES_PATH).json()
    matches = [
        entry
        for entry in registry["databases"]
        if entry["database_id"] == database_id
    ]
    assert len(matches) == 1
    assert matches[0]["schema"] == "private"

    catalog = client.get(CATALOG_PATH, params={"database_id": database_id})
    assert catalog.status_code == 200
    data = catalog.json()
    flat = client.get(
        CATALOG_PATH, params={"database_id": SYNTHETIC_PROTOTYPE_DATABASE_ID}
    ).json()
    assert data["total"] == len(data["items"]) == len(DEFAULT_SEED.offerings)
    assert data["items"] == flat["items"]

    started = client.post(
        START_PATH,
        json={
            "request_id": "req-private-exec",
            "database_id": database_id,
            "question": TRIGGER,
        },
    )
    assert started.status_code == 200
    assert started.json()["status"] == "PENDING"
    target = started.json()["options"][0]
    submitted = client.post(
        SUBMIT_PATH,
        json={
            "session_token": started.json()["session_token"],
            "selection": [target["option_id"]],
        },
    )
    assert submitted.status_code == 200
    result = submitted.json()
    assert result["execution"]["status"] == "SUCCEEDED"
    assert result["execution"]["columns"] == [target["semantic_ref"]]
    if target["semantic_ref"] == "Course.name":
        expected = [offering.course_name for offering in DEFAULT_SEED.offerings]
    else:
        expected = [offering.instructor_name for offering in DEFAULT_SEED.offerings]
    assert sorted(
        row[target["semantic_ref"]] for row in result["execution"]["rows"]
    ) == sorted(expected)


def test_restart_rehydrates_private_entry(review_client) -> None:
    client, repo, source = review_client
    raw = source.read_bytes()
    database_id = "restart-private"
    _publish_flow(client, raw, database_id)
    expected = client.get(CATALOG_PATH, params={"database_id": database_id}).json()

    fresh_app = create_synthetic_clarification_prototype()
    fresh_app.state.sqlite_upload_repository_root = str(repo)
    fresh_app.state.mapping_review_repository_root = str(repo)
    set_private_repository_root_for_tests(repo)
    fresh = TestClient(fresh_app, raise_server_exceptions=False)
    try:
        registry = fresh.get(DATABASES_PATH).json()
        assert any(
            entry["database_id"] == database_id and entry["schema"] == "private"
            for entry in registry["databases"]
        )
        catalog = fresh.get(CATALOG_PATH, params={"database_id": database_id})
        assert catalog.status_code == 200
        assert catalog.json() == expected
    finally:
        fresh.close()


def test_tamper_variants_fail_closed_and_hidden(review_client) -> None:
    client, repo, source = review_client
    raw = source.read_bytes()
    targets = {
        "tamper-mapping": "mapping",
        "tamper-extra": "extra",
        "tamper-removed": "removed",
        "tamper-draft": "draft",
    }
    for database_id in targets:
        _publish_flow(client, raw, database_id)
    review_root = repo / ".schema-adaptive" / "registration-reviews"

    mapping_path = review_root / "tamper-mapping" / "mapping-v0001.json"
    mapping_path.write_bytes(mapping_path.read_bytes() + b" ")
    (review_root / "tamper-extra" / "notes.txt").write_text("x", encoding="utf-8")
    (review_root / "tamper-removed" / "activation.json").unlink()
    draft_path = review_root / "tamper-draft" / "draft.json"
    draft_payload = json.loads(draft_path.read_text(encoding="utf-8"))
    draft_payload["schema_fingerprint"] = "0" * 64
    draft_path.write_text(
        json.dumps(draft_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    registry = client.get(DATABASES_PATH).json()
    ids = [entry["database_id"] for entry in registry["databases"]]
    for database_id in targets:
        assert database_id not in ids
        catalog = client.get(CATALOG_PATH, params={"database_id": database_id})
        assert catalog.status_code == 404
        started = client.post(
            START_PATH,
            json={
                "request_id": f"req-{database_id}",
                "database_id": database_id,
                "question": TRIGGER,
            },
        )
        assert started.status_code == 503
        blob = catalog.text + started.text
        assert "Traceback" not in blob
        assert str(repo) not in blob
    assert SYNTHETIC_PROTOTYPE_DATABASE_ID in ids
    assert SYNTHETIC_LEGACY_DATABASE_ID in ids


def test_activation_manifest_is_the_atomic_execution_authority(review_client) -> None:
    client, repo, source = review_client
    database_id = "atomic-authority"
    _publish_flow(client, source.read_bytes(), database_id)
    review_dir = repo / ".schema-adaptive" / "registration-reviews" / database_id

    first_catalog = client.get(CATALOG_PATH, params={"database_id": database_id})
    assert first_catalog.status_code == 200
    mapping_v1 = review_dir / "mapping-v0001.json"
    mapping_v2 = review_dir / "mapping-v0002.json"
    profile = json.loads(mapping_v1.read_text(encoding="utf-8"))
    profile["version"] = 2
    mapping_v2.write_text(
        json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    current = json.loads((review_dir / "current.json").read_text(encoding="utf-8"))
    raw_v2 = mapping_v2.read_bytes()
    current["version"] = 2
    current["mapping_sha256"] = hashlib.sha256(raw_v2).hexdigest()
    current["mapping_bytes"] = len(raw_v2)
    (review_dir / "current.json").write_text(
        json.dumps(current, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    registry = client.get(DATABASES_PATH).json()
    assert database_id in {entry["database_id"] for entry in registry["databases"]}
    assert client.get(CATALOG_PATH, params={"database_id": database_id}).json() == first_catalog.json()


def test_oversized_review_body_is_rejected(review_client) -> None:
    client, _repo, _source = review_client
    response = client.post(
        REVIEW_PATH,
        content=b"{" + (b"x" * (64 * 1024)),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json() == {"error": {"code": "MAPPING_REVIEW_INVALID_REQUEST"}}


def test_publish_blocked_lists_only_safe_field_ids(review_client) -> None:
    client, _repo, source = review_client
    raw = source.read_bytes()
    database_id = "blocked-fields"
    assert _upload(client, database_id, "Label", raw).status_code == 200
    assert (
        _action(
            client,
            {"action": "INIT", "database_id": database_id, "reviewer": "tester"},
        ).status_code
        == 200
    )
    blocked = _action(
        client,
        {"action": "PUBLISH", "database_id": database_id, "published_by": "tester"},
    )
    assert blocked.status_code == 422
    body = blocked.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "fields"}
    assert body["error"]["code"] == "MAPPING_REVIEW_PUBLISH_BLOCKED"
    assert sorted(body["error"]["fields"]) == sorted(load_default_ontology().active_fields)
    assert "Traceback" not in blocked.text
    assert "SELECT" not in blocked.text


def test_duplicate_physical_and_stale_fingerprint_blocked(review_client) -> None:
    client, repo, source = review_client
    raw = source.read_bytes()

    database_id = "blocked-duplicate"
    assert _upload(client, database_id, "Label", raw).status_code == 200
    assert (
        _action(
            client,
            {"action": "INIT", "database_id": database_id, "reviewer": "tester"},
        ).status_code
        == 200
    )
    _approve_all(client, database_id)
    # Course.name shares the physical column of Course.code -> duplicate.
    remapped = _action(
        client,
        {
            "action": "REMAP",
            "database_id": database_id,
            "semantic_field": "Course.name",
            "table": "course_offerings",
            "column": "course_code",
            "reviewer": "tester",
            "reason": "duplicate check",
        },
    )
    assert remapped.status_code == 200
    blocked = _action(
        client,
        {"action": "PUBLISH", "database_id": database_id, "published_by": "tester"},
    )
    assert blocked.status_code == 422

    stale_id = "blocked-stale"
    assert _upload(client, stale_id, "Label", raw).status_code == 200
    assert (
        _action(
            client, {"action": "INIT", "database_id": stale_id, "reviewer": "tester"}
        ).status_code
        == 200
    )
    draft_path = (
        repo / ".schema-adaptive" / "registration-reviews" / stale_id / "draft.json"
    )
    text = draft_path.read_text(encoding="utf-8")
    loaded = registration_storage.load_registration(repository_root=repo, database_id=stale_id)
    text = text.replace(loaded.manifest.schema_fingerprint, "0" * 64)
    draft_path.write_text(text, encoding="utf-8")
    stale_publish = _action(
        client,
        {"action": "PUBLISH", "database_id": stale_id, "published_by": "tester"},
    )
    assert stale_publish.status_code in (404, 409, 422)


def test_disconnected_join_blocked(review_client, tmp_path: Path) -> None:
    client, repo, _source = review_client
    custom = tmp_path / "split.sqlite3"
    connection = sqlite3.connect(custom)
    try:
        connection.execute(
            "CREATE TABLE t1 (c1 TEXT, c2 TEXT, c3 TEXT, c4 TEXT, c5 TEXT, c6 TEXT, c7 TEXT, c8 TEXT)"
        )
        connection.execute(
            "CREATE TABLE t2 (d1 TEXT, d2 TEXT, d3 TEXT, d4 TEXT, d5 TEXT, d6 TEXT, d7 TEXT, d8 TEXT)"
        )
        connection.commit()
    finally:
        connection.close()
    raw = custom.read_bytes()
    database_id = "blocked-disconnected"
    assert _upload(client, database_id, "Label", raw).status_code == 200
    assert (
        _action(
            client,
            {"action": "INIT", "database_id": database_id, "reviewer": "tester"},
        ).status_code
        == 200
    )
    detail = client.get(REVIEW_PATH, params={"database_id": database_id}).json()
    fields = [item["semantic_field"] for item in detail["review"]["fields"]]
    assert len(fields) == len(load_default_ontology().active_fields)
    for position, semantic_field in enumerate(fields):
        if position < 8:
            response = _action(
                client,
                {
                    "action": "REMAP",
                    "database_id": database_id,
                    "semantic_field": semantic_field,
                    "table": "t1",
                    "column": f"c{position + 1}",
                    "reviewer": "tester",
                    "reason": "split check",
                },
            )
        else:
            response = _action(
                client,
                {
                    "action": "REMAP",
                    "database_id": database_id,
                    "semantic_field": semantic_field,
                    "table": "t2",
                    "column": f"d{position - 7}",
                    "reviewer": "tester",
                    "reason": "split check",
                },
            )
        assert response.status_code == 200, (semantic_field, response.text[:300])
    blocked = _action(
        client,
        {"action": "PUBLISH", "database_id": database_id, "published_by": "tester"},
    )
    assert blocked.status_code == 422
    catalog = client.get(CATALOG_PATH, params={"database_id": database_id})
    assert catalog.status_code == 404


def test_strict_json_and_injection_rejected(review_client) -> None:
    client, _repo, source = review_client
    raw = source.read_bytes()
    database_id = "strict-json"
    assert _upload(client, database_id, "Label", raw).status_code == 200
    assert (
        _action(
            client,
            {"action": "INIT", "database_id": database_id, "reviewer": "tester"},
        ).status_code
        == 200
    )

    def check(payload: dict, status: int, code: str) -> None:
        response = _action(client, payload)
        assert response.status_code == status, (payload, response.text[:300])
        assert response.json()["error"]["code"] == code

    check({"action": "BOGUS", "database_id": database_id}, 400, "MAPPING_REVIEW_INVALID_REQUEST")
    check(
        {
            "action": "APPROVE",
            "database_id": database_id,
            "semantic_field": "Nope.field",
            "candidate_index": 0,
            "reviewer": "tester",
        },
        400,
        "MAPPING_REVIEW_INVALID_REQUEST",
    )
    check(
        {
            "action": "APPROVE",
            "database_id": database_id,
            "semantic_field": "Course.name",
            "candidate_index": 99,
            "reviewer": "tester",
        },
        400,
        "MAPPING_REVIEW_INVALID_REQUEST",
    )
    check(
        {
            "action": "REMAP",
            "database_id": database_id,
            "semantic_field": "Course.name",
            "table": "nope",
            "column": "nope",
            "reviewer": "tester",
            "reason": "x",
        },
        400,
        "MAPPING_REVIEW_INVALID_REQUEST",
    )
    check(
        {
            "action": "APPROVE",
            "database_id": database_id,
            "semantic_field": "Course.name",
            "candidate_index": 0,
            "reviewer": "x" * 65,
        },
        400,
        "MAPPING_REVIEW_INVALID_REQUEST",
    )
    check(
        {
            "action": "REMAP",
            "database_id": database_id,
            "semantic_field": "Course.name",
            "table": "course_offerings",
            "column": "course_name",
            "reviewer": "tester",
            "reason": "x" * 501,
        },
        400,
        "MAPPING_REVIEW_INVALID_REQUEST",
    )
    check(
        {
            "action": "PUBLISH",
            "database_id": database_id,
            "published_by": "tester",
            "status": "PUBLISHED",
            "version": 99,
            "draft": {},
            "mapping": {},
            "path": "/tmp/x",
            "sql": "SELECT 1",
        },
        400,
        "MAPPING_REVIEW_INVALID_REQUEST",
    )

    duplicated = client.post(
        REVIEW_PATH,
        content=b'{"action": "INIT", "action": "INIT", "database_id": "strict-json", "reviewer": "tester"}',
        headers={"Content-Type": "application/json"},
    )
    assert duplicated.status_code == 400
    assert duplicated.json() == {"error": {"code": "MAPPING_REVIEW_INVALID_REQUEST"}}

    extra_query = client.get(REVIEW_PATH + f"?database_id={database_id}&injected=1")
    assert extra_query.status_code == 400
    duplicated_query = client.get(
        REVIEW_PATH + f"?database_id={database_id}&database_id={database_id}"
    )
    assert duplicated_query.status_code == 400

    missing_column = _action(
        client,
        {
            "action": "REMAP",
            "database_id": database_id,
            "semantic_field": "Course.name",
            "table": "course_offerings",
            "column": "missing_column",
            "reviewer": "tester",
            "reason": "missing check",
        },
    )
    assert missing_column.status_code == 400
    detail = client.get(REVIEW_PATH, params={"database_id": database_id}).json()
    course_name = next(
        item for item in detail["review"]["fields"] if item["semantic_field"] == "Course.name"
    )
    assert course_name["decision"] == "PENDING"

    blob = duplicated.text + extra_query.text + missing_column.text
    assert "Traceback" not in blob
    assert "SELECT" not in blob


def test_lock_conflict_and_storage_failure_preserve_state(review_client, monkeypatch) -> None:
    client, repo, source = review_client
    raw = source.read_bytes()
    database_id = "lock-and-failure"
    assert _upload(client, database_id, "Label", raw).status_code == 200
    assert (
        _action(
            client,
            {"action": "INIT", "database_id": database_id, "reviewer": "tester"},
        ).status_code
        == 200
    )
    review_root = repo / ".schema-adaptive" / "registration-reviews"
    lock_path = review_root / f".lock-{database_id}.lock"
    lock_path.write_bytes(b"")
    try:
        conflicted = _action(
            client,
            {
                "action": "APPROVE",
                "database_id": database_id,
                "semantic_field": "Course.name",
                "candidate_index": 0,
                "reviewer": "tester",
            },
        )
        assert conflicted.status_code == 409
        assert conflicted.json() == {"error": {"code": "MAPPING_REVIEW_CONFLICT"}}
    finally:
        lock_path.unlink(missing_ok=True)

    before = client.get(REVIEW_PATH, params={"database_id": database_id}).json()

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(review_storage.MappingReviewService, "approve", _boom)
    failed = _action(
        client,
        {
            "action": "APPROVE",
            "database_id": database_id,
            "semantic_field": "Course.name",
            "candidate_index": 0,
            "reviewer": "tester",
        },
    )
    assert failed.status_code == 500
    assert failed.json() == {"error": {"code": "MAPPING_REVIEW_INTERNAL_ERROR"}}
    assert "boom" not in failed.text
    assert "Traceback" not in failed.text
    after = client.get(REVIEW_PATH, params={"database_id": database_id}).json()
    assert after["review"] == before["review"]


def test_fixed_entries_regression(review_client) -> None:
    client, _repo, _source = review_client
    registry = client.get(DATABASES_PATH).json()
    fixed = [entry for entry in registry["databases"] if entry["schema"] != "private"]
    assert sorted(entry["database_id"] for entry in fixed) == sorted(
        [SYNTHETIC_PROTOTYPE_DATABASE_ID, SYNTHETIC_LEGACY_DATABASE_ID]
    )
    flat = client.get(
        CATALOG_PATH, params={"database_id": SYNTHETIC_PROTOTYPE_DATABASE_ID}
    )
    legacy = client.get(
        CATALOG_PATH, params={"database_id": SYNTHETIC_LEGACY_DATABASE_ID}
    )
    assert flat.status_code == 200
    assert legacy.status_code == 200
    assert flat.json()["items"] == legacy.json()["items"]
    assert flat.json()["total"] == len(DEFAULT_SEED.offerings)


def test_only_course_list_fields_must_be_mapped_for_an_sqlite_publish(review_client) -> None:
    """Phase 8-18: an ignored newer field publishes; an ignored course-list field blocks."""
    client, _repo, source = review_client
    raw = source.read_bytes()
    database_id = "grade-optional"
    assert _upload(client, database_id, "Label", raw).status_code == 200
    assert _action(client, {"action": "INIT", "database_id": database_id, "reviewer": "tester"}).status_code == 200
    _approve_all(client, database_id)  # target grade: no candidate, ignored
    ignored = _action(client, {
        "action": "IGNORE", "database_id": database_id, "semantic_field": "Course.category",
        "reviewer": "tester", "reason": "check",
    })
    assert ignored.status_code == 200
    blocked = _action(client, {"action": "PUBLISH", "database_id": database_id, "published_by": "tester"})
    assert blocked.status_code == 422
    assert blocked.json()["error"]["fields"] == ["Course.category"]
    approved = _action(client, {
        "action": "APPROVE", "database_id": database_id, "semantic_field": "Course.category",
        "candidate_index": 0, "reviewer": "tester",
    })
    assert approved.status_code == 200
    published = _action(client, {"action": "PUBLISH", "database_id": database_id, "published_by": "tester"})
    assert published.status_code == 200, published.text[:300]
    assert client.get(CATALOG_PATH, params={"database_id": database_id}).status_code == 200
