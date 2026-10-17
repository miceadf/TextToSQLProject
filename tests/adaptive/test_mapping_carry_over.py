"""Phase 8-03: carry-over review start with stale fields (synthetic only)."""

from __future__ import annotations

import json
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
from backend.adaptive.fixtures.hybrid import build_hybrid_database
from backend.adaptive.fixtures.seed import DEFAULT_SEED
from backend.adaptive.registration import reviews as review_storage
from tests.adaptive.test_private_mapping_publish import _action, _ignore_newer, _make_repo, _publish_flow, _upload
from tests.adaptive.test_schema_compare import _derived

BASE = "carry-base"
TARGET = "carry-target"


@pytest.fixture
def client_repo(tmp_path: Path):
    repo = _make_repo(tmp_path, "repo")
    app = create_synthetic_clarification_prototype()
    app.state.sqlite_upload_repository_root = str(repo)
    app.state.mapping_review_repository_root = str(repo)
    set_private_repository_root_for_tests(repo)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        _publish_flow(client, build_flat_database(tmp_path / "base.sqlite3", seed=DEFAULT_SEED).read_bytes(), BASE)
        yield client, repo
    finally:
        client.close()
        set_private_repository_root_for_tests(None)


def _carry(client: TestClient, target: str = TARGET, base: str = BASE, reviewer: object = "admin", **extra):
    body = {"action": "CARRY_OVER", "database_id": target, "base_database_id": base, "reviewer": reviewer}
    body.update(extra)
    return _action(client, body)


def _review_dir(repo: Path, database_id: str = TARGET) -> Path:
    return repo / ".schema-adaptive" / "registration-reviews" / database_id


def _fields(detail: dict) -> dict[str, dict]:
    return {item["semantic_field"]: item for item in detail["review"]["fields"]}


def test_same_schema_carries_every_field_and_publishes(client_repo, tmp_path: Path) -> None:
    client, repo = client_repo
    same = _derived(tmp_path, "same", ["UPDATE course_offerings SET enrolled = enrolled"])
    assert _upload(client, TARGET, "Next", same.read_bytes()).status_code == 200
    response = _carry(client)
    assert response.status_code == 200, response.text[:300]
    detail = response.json()
    assert detail["status"] == "PENDING_REVIEW"
    assert detail["succession"]["base_database_id"] == BASE
    assert detail["succession"]["base_mapping_version"] == 1
    assert len(detail["succession"]["carried_fields"]) == 14  # v3 excludes year.
    assert detail["succession"]["stale_fields"] == [{"semantic_field": "Course.target_grade", "status": "NOT_IN_BASE"}]
    # Phase 8-18: a field the base never mapped (target grade) waits for review.
    assert {item["decision"] for item in detail["review"]["fields"] if item["semantic_field"] != "Course.target_grade"} == {"REMAPPED"}
    # Carry-over grants nothing: the target is not executable until published.
    assert TARGET not in {entry["database_id"] for entry in client.get(DATABASES_PATH).json()["databases"]}
    assert client.get(CATALOG_PATH, params={"database_id": TARGET}).status_code == 404
    _ignore_newer(client, TARGET, "admin")
    published = _action(client, {"action": "PUBLISH", "database_id": TARGET, "published_by": "admin"})
    assert published.status_code == 200, published.text[:300]
    base_profile = review_storage.load_activation(repository_root=repo, database_id=BASE).profile
    target_profile = review_storage.load_activation(repository_root=repo, database_id=TARGET).profile
    assert {k: (v.table, v.column) for k, v in target_profile.fields.items()} == {
        k: (v.table, v.column) for k, v in base_profile.fields.items()
    }
    assert "succession" in client.get(REVIEW_PATH, params={"database_id": TARGET}).json()


def test_renamed_column_leaves_only_that_field_stale(client_repo, tmp_path: Path) -> None:
    client, _repo = client_repo
    renamed = _derived(tmp_path, "renamed", ["ALTER TABLE course_offerings RENAME COLUMN course_name TO title"])
    assert _upload(client, TARGET, "Next", renamed.read_bytes()).status_code == 200
    detail = _carry(client).json()
    assert detail["succession"]["stale_fields"] == [
        {"semantic_field": "Course.name", "status": "COLUMN_MISSING"},
        # Phase 8-18: the base never mapped the newer target-grade field.
        {"semantic_field": "Course.target_grade", "status": "NOT_IN_BASE"},
    ]
    fields = _fields(detail)
    assert fields["Course.name"]["decision"] == "PENDING"
    assert sum(item["decision"] == "REMAPPED" for item in fields.values()) == 13  # v3 excludes year.
    _ignore_newer(client, TARGET, "admin")
    blocked = _action(client, {"action": "PUBLISH", "database_id": TARGET, "published_by": "admin"})
    assert blocked.status_code == 422
    assert blocked.json()["error"]["fields"] == ["Course.name"]
    remapped = _action(client, {
        "action": "REMAP", "database_id": TARGET, "semantic_field": "Course.name",
        "table": "course_offerings", "column": "title", "reviewer": "admin", "reason": "renamed column",
    })
    assert remapped.status_code == 200
    _ignore_newer(client, TARGET, "admin")
    assert _action(client, {"action": "PUBLISH", "database_id": TARGET, "published_by": "admin"}).status_code == 200


def test_structure_change_marks_every_field_stale(client_repo, tmp_path: Path) -> None:
    client, _repo = client_repo
    hybrid = build_hybrid_database(tmp_path / "hybrid.sqlite3", seed=DEFAULT_SEED)
    assert _upload(client, TARGET, "Next", hybrid.read_bytes()).status_code == 200
    detail = _carry(client).json()
    assert detail["succession"]["carried_fields"] == []
    assert {item["status"] for item in detail["succession"]["stale_fields"]} == {"TABLE_MISSING", "NOT_IN_BASE"}
    assert len(detail["succession"]["stale_fields"]) == 15  # v3 excludes year.


def test_retry_is_idempotent_and_keeps_later_edits(client_repo, tmp_path: Path) -> None:
    client, repo = client_repo
    renamed = _derived(tmp_path, "renamed", ["ALTER TABLE course_offerings RENAME COLUMN course_name TO title"])
    assert _upload(client, TARGET, "Next", renamed.read_bytes()).status_code == 200
    assert _carry(client).status_code == 200
    record = (_review_dir(repo) / "succession.json").read_bytes()
    assert _action(client, {
        "action": "REMAP", "database_id": TARGET, "semantic_field": "Course.name",
        "table": "course_offerings", "column": "title", "reviewer": "admin", "reason": "renamed column",
    }).status_code == 200
    again = _carry(client)
    assert again.status_code == 200
    assert _fields(again.json())["Course.name"]["decision"] == "REMAPPED"
    assert (_review_dir(repo) / "succession.json").read_bytes() == record


def test_interrupted_carry_over_completes_the_record(client_repo, tmp_path: Path) -> None:
    client, repo = client_repo
    same = _derived(tmp_path, "same", ["UPDATE course_offerings SET enrolled = enrolled"])
    assert _upload(client, TARGET, "Next", same.read_bytes()).status_code == 200
    assert _carry(client).status_code == 200
    (_review_dir(repo) / "succession.json").unlink()
    draft = (_review_dir(repo) / "draft.json").read_bytes()
    again = _carry(client)
    assert again.status_code == 200
    assert (_review_dir(repo) / "succession.json").exists()
    assert (_review_dir(repo) / "draft.json").read_bytes() == draft


def test_fresh_init_draft_can_be_replaced_but_reviewed_one_cannot(client_repo, tmp_path: Path) -> None:
    client, _repo = client_repo
    same = _derived(tmp_path, "same", ["UPDATE course_offerings SET enrolled = enrolled"])
    assert _upload(client, TARGET, "Next", same.read_bytes()).status_code == 200
    assert _action(client, {"action": "INIT", "database_id": TARGET, "reviewer": "admin"}).status_code == 200
    assert _carry(client).status_code == 200

    other = "carry-other"
    assert _upload(client, other, "Other", same.read_bytes()).status_code == 200
    assert _action(client, {"action": "INIT", "database_id": other, "reviewer": "admin"}).status_code == 200
    detail = client.get(REVIEW_PATH, params={"database_id": other}).json()
    first = detail["review"]["fields"][0]["semantic_field"]
    assert _action(client, {
        "action": "APPROVE", "database_id": other, "semantic_field": first,
        "candidate_index": 0, "reviewer": "admin",
    }).status_code == 200
    assert _carry(client, target=other).status_code == 409


def test_preconditions_fail_closed(client_repo, tmp_path: Path) -> None:
    client, repo = client_repo
    same = _derived(tmp_path, "same", ["UPDATE course_offerings SET enrolled = enrolled"])
    assert _upload(client, TARGET, "Next", same.read_bytes()).status_code == 200
    assert _upload(client, "carry-pending", "Pending", same.read_bytes()).status_code == 200
    cases = [
        (_carry(client, base=TARGET), 400),                       # same id
        (_carry(client, base="carry-pending"), 404),              # base not published
        (_carry(client, base="missing-db"), 404),
        (_carry(client, target="missing-db"), 404),
        (_carry(client, reviewer=""), 400),
        (_carry(client, reviewer=1), 400),
        (_carry(client, extra_key="x"), 400),
        (_carry(client, target=BASE, base=TARGET), 404),          # base (TARGET) is not published
    ]
    for response, status in cases:
        assert response.status_code == status, response.text[:200]
        assert set(response.json()) == {"error"} and set(response.json()["error"]) == {"code"}
        assert str(repo) not in response.text
    assert not (_review_dir(repo) / "succession.json").exists()


def test_published_target_and_different_base_are_conflicts(client_repo, tmp_path: Path) -> None:
    client, _repo = client_repo
    flat = build_flat_database(tmp_path / "second.sqlite3", seed=DEFAULT_SEED).read_bytes()
    _publish_flow(client, flat, "carry-second")
    same = _derived(tmp_path, "same", ["UPDATE course_offerings SET enrolled = enrolled"])
    assert _upload(client, TARGET, "Next", same.read_bytes()).status_code == 200
    assert _carry(client).status_code == 200
    # A different base for an already carried target conflicts.
    assert _carry(client, base="carry-second").status_code == 409
    # A published registration cannot restart from a base.
    assert _carry(client, target="carry-second").status_code == 409


def test_damaged_record_is_hidden_and_does_not_affect_execution(client_repo, tmp_path: Path) -> None:
    client, repo = client_repo
    same = _derived(tmp_path, "same", ["UPDATE course_offerings SET enrolled = enrolled"])
    assert _upload(client, TARGET, "Next", same.read_bytes()).status_code == 200
    assert _carry(client).status_code == 200
    _ignore_newer(client, TARGET, "admin")
    assert _action(client, {"action": "PUBLISH", "database_id": TARGET, "published_by": "admin"}).status_code == 200
    path = _review_dir(repo) / "succession.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["carried_fields"] = ["Unknown.field"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    detail = client.get(REVIEW_PATH, params={"database_id": TARGET})
    assert detail.status_code == 200
    assert "succession" not in detail.json()
    assert client.get(CATALOG_PATH, params={"database_id": TARGET}).status_code == 200
    assert TARGET in {entry["database_id"] for entry in client.get(DATABASES_PATH).json()["databases"]}


# ---------------------------------------------------------------------------
# Luna review regressions


def test_base_version_change_between_compare_and_copy_conflicts(client_repo, tmp_path: Path, monkeypatch) -> None:
    import dataclasses

    client, repo = client_repo
    same = _derived(tmp_path, "same", ["UPDATE course_offerings SET enrolled = enrolled"])
    assert _upload(client, TARGET, "Next", same.read_bytes()).status_code == 200
    original = review_storage.compare_registrations

    def compared_against_other_version(**kwargs):
        result = original(**kwargs)
        return dataclasses.replace(result, base_mapping_version=result.base_mapping_version + 1)

    monkeypatch.setattr(review_storage, "compare_registrations", compared_against_other_version)
    response = _carry(client)
    assert response.status_code == 409
    assert not (_review_dir(repo) / "succession.json").exists()


def test_record_must_classify_every_field(client_repo, tmp_path: Path) -> None:
    client, repo = client_repo
    same = _derived(tmp_path, "same", ["UPDATE course_offerings SET enrolled = enrolled"])
    assert _upload(client, TARGET, "Next", same.read_bytes()).status_code == 200
    assert _carry(client).status_code == 200
    path = _review_dir(repo) / "succession.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["carried_fields"] = []
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    detail = client.get(REVIEW_PATH, params={"database_id": TARGET}).json()
    assert "succession" not in detail


def test_failed_record_write_leaves_no_partial_file(client_repo, tmp_path: Path, monkeypatch) -> None:
    import os

    client, repo = client_repo
    same = _derived(tmp_path, "same", ["UPDATE course_offerings SET enrolled = enrolled"])
    assert _upload(client, TARGET, "Next", same.read_bytes()).status_code == 200
    real_link = os.link

    def failing_link(src, dst, *args, **kwargs):
        if str(dst).endswith("succession.json"):
            raise OSError("simulated")
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", failing_link)
    assert _carry(client).status_code == 500
    monkeypatch.setattr(os, "link", real_link)
    names = sorted(path.name for path in _review_dir(repo).iterdir())
    assert names == ["draft.json"]
    assert _carry(client).status_code == 200
    assert (_review_dir(repo) / "succession.json").exists()


def test_a_version_one_base_carries_active_fields_to_a_version_three_target(tmp_path: Path, monkeypatch) -> None:
    """ADR-101: an older base carries active fields, excluding retired year."""
    from backend.adaptive.ontology import load_default_ontology
    from backend.adaptive.registration import storage as registration_storage

    repo = _make_repo(tmp_path, "repo-v1")
    app = create_synthetic_clarification_prototype()
    app.state.sqlite_upload_repository_root = str(repo)
    app.state.mapping_review_repository_root = str(repo)
    set_private_repository_root_for_tests(repo)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        version_one = load_default_ontology().at_version(1)
        with monkeypatch.context() as patch:
            patch.setattr(registration_storage, "load_default_ontology", lambda: version_one)
            raw = build_flat_database(tmp_path / "base.sqlite3", seed=DEFAULT_SEED).read_bytes()
            _publish_flow(client, raw, BASE)
        base = review_storage.load_activation(repository_root=repo, database_id=BASE)
        assert base.ontology_version == 1 and "Course.target_grade" not in base.profile.fields
        same = _derived(tmp_path, "same", ["UPDATE course_offerings SET enrolled = enrolled"])
        assert _upload(client, TARGET, "Next", same.read_bytes()).status_code == 200
        detail = _carry(client).json()
        assert len(detail["succession"]["carried_fields"]) == 14
        assert "CourseOffering.year" not in detail["succession"]["carried_fields"]
        assert detail["succession"]["stale_fields"] == [
            {"semantic_field": "Course.target_grade", "status": "NOT_IN_BASE"}
        ]
        _ignore_newer(client, TARGET, "admin")
        published = _action(client, {"action": "PUBLISH", "database_id": TARGET, "published_by": "admin"})
        assert published.status_code == 200, published.text[:300]
    finally:
        client.close()
        set_private_repository_root_for_tests(None)


def test_v2_year_mapping_does_not_affect_v3_compare_or_carry_over(tmp_path, monkeypatch):
    """ADR-101: an actual v2 year mapping is preserved but never copied to v3."""
    from backend.adaptive import ontology as ontology_module
    from backend.adaptive.mapping import review as mapping_review
    from backend.adaptive.registration import storage as registration_storage
    from backend.adaptive.api.review_publish import COMPARE_PATH

    repo = _make_repo(tmp_path, "repo-v2")
    app = create_synthetic_clarification_prototype()
    app.state.sqlite_upload_repository_root = str(repo)
    app.state.mapping_review_repository_root = str(repo)
    set_private_repository_root_for_tests(repo)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        version_two = ontology_module.load_default_ontology().at_version(2)
        with monkeypatch.context() as patch:
            for module in (ontology_module, registration_storage, review_storage, mapping_review):
                patch.setattr(module, "load_default_ontology", lambda: version_two)
            _publish_flow(client, build_flat_database(tmp_path / "base-v2.sqlite3").read_bytes(), BASE)
        base = review_storage.load_activation(repository_root=repo, database_id=BASE)
        assert base.ontology_version == 2 and "CourseOffering.year" in base.profile.fields
        before = {path: path.read_bytes() for path in _review_dir(repo, BASE).iterdir() if path.is_file()}
        changed = _derived(tmp_path, "year-renamed", ["ALTER TABLE course_offerings RENAME COLUMN year TO old_year"])
        assert _upload(client, TARGET, "Next", changed.read_bytes()).status_code == 200
        comparison = client.get(COMPARE_PATH, params={"base_database_id": BASE, "candidate_database_id": TARGET})
        assert comparison.status_code == 200, comparison.text[:300]
        assert comparison.json()["carry_over_executable"] is True
        assert "CourseOffering.year" not in {item["semantic_field"] for item in comparison.json()["field_impacts"]}
        carried = _carry(client)
        assert carried.status_code == 200, carried.text[:300]
        assert len(carried.json()["succession"]["carried_fields"]) == 14
        assert "CourseOffering.year" not in _fields(carried.json())
        _ignore_newer(client, TARGET, "admin")
        assert _action(client, {"action": "PUBLISH", "database_id": TARGET, "published_by": "admin"}).status_code == 200
        target = review_storage.load_activation(repository_root=repo, database_id=TARGET)
        assert target.ontology_version == 3 and "CourseOffering.year" not in target.profile.fields
        assert all(path.read_bytes() == content for path, content in before.items())
    finally:
        client.close()
        set_private_repository_root_for_tests(None)
