"""Phase 8-02: read-only schema comparison and active-mapping impact (synthetic only)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.adaptive.api.review_publish import COMPARE_PATH
from backend.adaptive.api.app_factory import (
    create_synthetic_clarification_prototype,
    set_private_repository_root_for_tests,
)
from backend.adaptive.catalog import SQLiteAdapter
from backend.adaptive.catalog.compare import (
    MAX_DIFF_ENTRIES,
    ColumnChangeKind,
    FieldImpactStatus,
    ForeignKeyChangeKind,
    compare_catalogs,
    mapping_impact,
)
from backend.adaptive.evaluation.fixture_mapping import _COLUMNS, _TABLES
from backend.adaptive.fixtures import FixtureVariant
from backend.adaptive.fixtures.flat import build_flat_database
from backend.adaptive.fixtures.hybrid import build_hybrid_database
from backend.adaptive.fixtures.seed import DEFAULT_SEED
from tests.adaptive.test_private_mapping_publish import _action, _ignore_newer, _make_repo, _publish_flow, _upload


def _catalog(path: Path, database_id: str = "probe"):
    return SQLiteAdapter({database_id: path}).inspect(database_id)


def _derived(tmp_path: Path, name: str, statements: list[str]) -> Path:
    """Flat fixture copy with extra DDL/DML applied."""
    path = build_flat_database(tmp_path / f"{name}.sqlite3", seed=DEFAULT_SEED)
    with sqlite3.connect(path) as connection:
        for statement in statements:
            connection.execute(statement)
    return path


# --------------------------------------------------------------------------
# pure comparison


def test_identical_catalogs_have_no_changes(tmp_path: Path) -> None:
    first = _catalog(build_flat_database(tmp_path / "a.sqlite3", seed=DEFAULT_SEED))
    second = _catalog(build_flat_database(tmp_path / "b.sqlite3", seed=DEFAULT_SEED))
    diff = compare_catalogs(first, second)
    assert diff.identical is True
    assert (diff.tables_added, diff.tables_removed, diff.column_changes, diff.foreign_key_changes) == ([], [], [], [])
    assert diff.truncated is False


def test_column_changes_are_classified_and_ordered(tmp_path: Path) -> None:
    base = _catalog(build_flat_database(tmp_path / "base.sqlite3", seed=DEFAULT_SEED))
    changed = _catalog(_derived(tmp_path, "changed", [
        "ALTER TABLE course_offerings ADD COLUMN note TEXT DEFAULT 'secret-default'",
        "ALTER TABLE course_offerings RENAME COLUMN course_name TO title",
        "CREATE TABLE extra (id INTEGER PRIMARY KEY)",
    ]))
    diff = compare_catalogs(base, changed)
    assert diff.identical is False
    assert diff.tables_added == ["extra"]
    kinds = [(item.column, item.change) for item in diff.column_changes]
    assert kinds == [
        ("course_name", ColumnChangeKind.REMOVED),
        ("note", ColumnChangeKind.ADDED),
        ("title", ColumnChangeKind.ADDED),
    ]
    assert "secret-default" not in diff.model_dump_json()


def test_type_nullability_and_foreign_keys(tmp_path: Path) -> None:
    def build(path: Path, credits: str, name_null: str, with_fk: bool) -> Path:
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE subjects (code TEXT PRIMARY KEY)")
            reference = " REFERENCES subjects(code)" if with_fk else ""
            connection.execute(
                f"CREATE TABLE sections (id INTEGER PRIMARY KEY, code TEXT{reference}, "
                f"credits {credits}, name TEXT {name_null})"
            )
        return path

    base = _catalog(build(tmp_path / "b.sqlite3", "REAL", "NOT NULL", True))
    candidate = _catalog(build(tmp_path / "c.sqlite3", "TEXT", "", False))
    diff = compare_catalogs(base, candidate)
    assert [(item.column, item.change) for item in diff.column_changes] == [
        ("credits", ColumnChangeKind.TYPE_CHANGED),
        ("name", ColumnChangeKind.NULLABILITY_CHANGED),
    ]
    assert [(item.table, item.column, item.change) for item in diff.foreign_key_changes] == [
        ("sections", "code", ForeignKeyChangeKind.REMOVED),
    ]
    impacts = mapping_impact(
        {"Course.credits": ("sections", "credits"), "Course.code": ("sections", "code"),
         "Course.name": ("sections", "missing"), "Department.name": ("gone", "x")},
        base, candidate,
    )
    assert [(item.semantic_field, item.status) for item in impacts] == [
        ("Course.code", FieldImpactStatus.UNCHANGED),
        ("Course.credits", FieldImpactStatus.TYPE_CHANGED),
        ("Course.name", FieldImpactStatus.COLUMN_MISSING),
        ("Department.name", FieldImpactStatus.TABLE_MISSING),
    ]


def test_change_list_is_capped(tmp_path: Path) -> None:
    base_path = tmp_path / "few.sqlite3"
    with sqlite3.connect(base_path) as connection:
        connection.execute("CREATE TABLE t (id INTEGER)")
    many_path = tmp_path / "many.sqlite3"
    with sqlite3.connect(many_path) as connection:
        connection.execute("CREATE TABLE t (id INTEGER)")
        for index in range(MAX_DIFF_ENTRIES + 20):
            connection.execute(f"CREATE TABLE extra_{index:04d} (id INTEGER)")
    diff = compare_catalogs(_catalog(base_path), _catalog(many_path))
    assert diff.truncated is True
    assert len(diff.tables_added) == MAX_DIFF_ENTRIES
    assert diff.tables_added == sorted(diff.tables_added)


# --------------------------------------------------------------------------
# registrations over HTTP


@pytest.fixture
def client_repo(tmp_path: Path):
    repo = _make_repo(tmp_path, "repo")
    app = create_synthetic_clarification_prototype()
    app.state.sqlite_upload_repository_root = str(repo)
    app.state.mapping_review_repository_root = str(repo)
    set_private_repository_root_for_tests(repo)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, repo
    finally:
        client.close()
        set_private_repository_root_for_tests(None)


def _compare(client: TestClient, base: str, candidate: str):
    return client.get(COMPARE_PATH, params={"base_database_id": base, "candidate_database_id": candidate})


def _snapshot(repo: Path) -> dict[str, bytes]:
    root = repo / ".schema-adaptive"
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def test_same_schema_carries_over(client_repo, tmp_path: Path) -> None:
    client, repo = client_repo
    _publish_flow(client, build_flat_database(tmp_path / "base.sqlite3", seed=DEFAULT_SEED).read_bytes(), "compare-base")
    candidate = _derived(tmp_path, "same", ["UPDATE course_offerings SET enrolled = enrolled"])
    assert _upload(client, "compare-same", "Label", candidate.read_bytes()).status_code == 200
    before = _snapshot(repo)
    response = _compare(client, "compare-base", "compare-same")
    assert response.status_code == 200, response.text[:300]
    body = response.json()
    assert body["version"] == "registration-compare-v1"
    assert body["identical"] is True
    assert body["carry_over_executable"] is True
    assert body["ontology_version_match"] is True
    assert body["base_mapping_version"] == 1
    assert {item["status"] for item in body["field_impacts"]} == {"UNCHANGED"}
    assert len(body["field_impacts"]) == 14  # v3 excludes the retired year.
    assert _snapshot(repo) == before
    for marker in (str(repo), "SELECT", "프로그래밍기초", "sample"):
        assert marker not in response.text


def test_renamed_column_breaks_carry_over(client_repo, tmp_path: Path) -> None:
    client, _repo = client_repo
    _publish_flow(client, build_flat_database(tmp_path / "base.sqlite3", seed=DEFAULT_SEED).read_bytes(), "compare-base")
    renamed = _derived(tmp_path, "renamed", [
        "ALTER TABLE course_offerings RENAME COLUMN course_name TO title",
        "ALTER TABLE course_offerings ADD COLUMN memo TEXT",
    ])
    assert _upload(client, "compare-renamed", "Label", renamed.read_bytes()).status_code == 200
    body = _compare(client, "compare-base", "compare-renamed").json()
    assert body["identical"] is False
    assert body["carry_over_executable"] is False
    statuses = {item["semantic_field"]: item["status"] for item in body["field_impacts"]}
    assert statuses["Course.name"] == "COLUMN_MISSING"
    assert sum(value != "UNCHANGED" for value in statuses.values()) == 1
    assert {(item["column"], item["change"]) for item in body["column_changes"]} == {
        ("course_name", "REMOVED"), ("title", "ADDED"), ("memo", "ADDED"),
    }


def test_added_column_keeps_carry_over(client_repo, tmp_path: Path) -> None:
    client, _repo = client_repo
    _publish_flow(client, build_flat_database(tmp_path / "base.sqlite3", seed=DEFAULT_SEED).read_bytes(), "compare-base")
    added = _derived(tmp_path, "added", ["ALTER TABLE course_offerings ADD COLUMN memo TEXT"])
    assert _upload(client, "compare-added", "Label", added.read_bytes()).status_code == 200
    body = _compare(client, "compare-base", "compare-added").json()
    assert body["identical"] is False
    assert body["carry_over_executable"] is True
    assert {item["status"] for item in body["field_impacts"]} == {"UNCHANGED"}


def _publish_hybrid(client: TestClient, raw: bytes, database_id: str) -> None:
    assert _upload(client, database_id, "Label", raw).status_code == 200
    assert _action(client, {"action": "INIT", "database_id": database_id, "reviewer": "tester"}).status_code == 200
    for semantic_field, column in _COLUMNS[FixtureVariant.HYBRID].items():
        # Phase 9-24: the historical fixture's year is not a v3 review field.
        if semantic_field == "CourseOffering.year":
            continue
        table = _TABLES[FixtureVariant.HYBRID][semantic_field.split(".", 1)[0]]
        response = _action(client, {
            "action": "REMAP", "database_id": database_id, "semantic_field": semantic_field,
            "table": table, "column": column, "reviewer": "tester", "reason": "synthetic hybrid",
        })
        assert response.status_code == 200, (semantic_field, response.text[:200])
    _ignore_newer(client, database_id)
    published = _action(client, {"action": "PUBLISH", "database_id": database_id, "published_by": "tester"})
    assert published.status_code == 200, published.text[:300]


def test_dropped_foreign_key_blocks_carry_over(client_repo, tmp_path: Path) -> None:
    client, _repo = client_repo
    _publish_hybrid(client, build_hybrid_database(tmp_path / "hybrid.sqlite3", seed=DEFAULT_SEED).read_bytes(), "compare-hybrid")
    # Same tables and columns, but class_sections no longer references subjects.
    source = build_hybrid_database(tmp_path / "hybrid-src.sqlite3", seed=DEFAULT_SEED)
    unlinked = tmp_path / "unlinked.sqlite3"
    with sqlite3.connect(source) as old, sqlite3.connect(unlinked) as new:
        for (sql,) in old.execute("SELECT sql FROM sqlite_master WHERE type = 'table'"):
            new.execute(sql.replace("FOREIGN KEY (subject_code) REFERENCES subjects (subject_code)", "CHECK (1)"))
    assert _upload(client, "compare-unlinked", "Label", unlinked.read_bytes()).status_code == 200
    body = _compare(client, "compare-hybrid", "compare-unlinked").json()
    assert {item["status"] for item in body["field_impacts"]} == {"UNCHANGED"}
    assert [item["change"] for item in body["foreign_key_changes"]] == ["REMOVED"]
    assert body["carry_over_executable"] is False


def test_structure_change_reports_missing_tables(client_repo, tmp_path: Path) -> None:
    client, _repo = client_repo
    _publish_flow(client, build_flat_database(tmp_path / "base.sqlite3", seed=DEFAULT_SEED).read_bytes(), "compare-base")
    hybrid = build_hybrid_database(tmp_path / "hybrid.sqlite3", seed=DEFAULT_SEED)
    assert _upload(client, "compare-structure", "Label", hybrid.read_bytes()).status_code == 200
    body = _compare(client, "compare-base", "compare-structure").json()
    assert body["tables_removed"] == ["course_offerings"]
    assert body["tables_added"] == ["class_sections", "subjects"]
    assert {item["status"] for item in body["field_impacts"]} == {"TABLE_MISSING"}
    assert body["carry_over_executable"] is False


def test_invalid_requests_fail_closed(client_repo, tmp_path: Path) -> None:
    client, repo = client_repo
    flat = build_flat_database(tmp_path / "base.sqlite3", seed=DEFAULT_SEED).read_bytes()
    _publish_flow(client, flat, "compare-base")
    assert _upload(client, "compare-pending", "Label", flat).status_code == 200
    cases = [
        (COMPARE_PATH, 400),
        (COMPARE_PATH + "?base_database_id=compare-base", 400),
        (COMPARE_PATH + "?base_database_id=compare-base&candidate_database_id=compare-base", 400),
        (COMPARE_PATH + "?base_database_id=compare-base&candidate_database_id=compare-pending&x=1", 400),
        (COMPARE_PATH + "?base_database_id=compare-base&base_database_id=compare-base&candidate_database_id=compare-pending", 400),
        (COMPARE_PATH + "?base_database_id=../escape&candidate_database_id=compare-pending", 400),
        (COMPARE_PATH + "?base_database_id=compare-base&candidate_database_id=missing-db", 404),
        # A pending (unpublished) registration cannot be the base.
        (COMPARE_PATH + "?base_database_id=compare-pending&candidate_database_id=compare-base", 404),
    ]
    for url, status in cases:
        response = client.get(url)
        assert response.status_code == status, (url, response.text[:200])
        assert set(response.json()) == {"error"}
        assert set(response.json()["error"]) == {"code"}
        assert str(repo) not in response.text
    detail = json.loads(client.get(COMPARE_PATH, params={
        "base_database_id": "compare-base", "candidate_database_id": "compare-pending",
    }).text)
    assert detail["identical"] is True


def test_candidate_storage_failure_is_internal_error(client_repo, tmp_path: Path, monkeypatch) -> None:
    from backend.adaptive.registration import storage as registration_storage
    from backend.adaptive.registration.errors import RegistrationError, RegistrationErrorCode

    client, repo = client_repo
    flat = build_flat_database(tmp_path / "base.sqlite3", seed=DEFAULT_SEED).read_bytes()
    _publish_flow(client, flat, "compare-base")
    assert _upload(client, "compare-pending", "Label", flat).status_code == 200
    original = registration_storage.load_registration

    def failing(*, repository_root, database_id):
        if database_id == "compare-pending":
            raise RegistrationError(RegistrationErrorCode.STORAGE_ERROR)
        return original(repository_root=repository_root, database_id=database_id)

    monkeypatch.setattr(registration_storage, "load_registration", failing)
    response = _compare(client, "compare-base", "compare-pending")
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "SCHEMA_COMPARE_INTERNAL_ERROR"}}
