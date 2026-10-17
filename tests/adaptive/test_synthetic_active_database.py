"""Synthetic tests for Phase 6-16 active database selection."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.adaptive.api.clarification_http import (
    CLARIFICATION_HTTP_VERSION,
    START_PATH,
    SUBMIT_PATH,
)
from backend.adaptive.api.app_factory import (
    SYNTHETIC_LEGACY_DATABASE_ID,
    SYNTHETIC_PROTOTYPE_DATABASE_ID,
    create_synthetic_clarification_prototype,
)
from backend.adaptive.api.database_registry import (
    CANONICAL_SELECT,
    CATALOG_PATH,
    DATABASES_PATH,
    SYNTHETIC_CATALOG_VERSION,
    SYNTHETIC_REGISTRY_VERSION,
)
from backend.adaptive.fixtures.seed import DEFAULT_SEED

TRIGGER = "SYNTH_Q_P616_k9w1 강의 목록 보여줘"


def _client() -> tuple[TestClient, object]:
    app = create_synthetic_clarification_prototype()
    return TestClient(app, raise_server_exceptions=False), app


def test_registry_lists_two_databases_strict():
    client, app = _client()
    response = client.get(DATABASES_PATH)
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == SYNTHETIC_REGISTRY_VERSION
    assert set(data.keys()) == {"version", "databases"}
    ids = sorted(entry["database_id"] for entry in data["databases"])
    assert ids == sorted([SYNTHETIC_PROTOTYPE_DATABASE_ID, SYNTHETIC_LEGACY_DATABASE_ID])
    for entry in data["databases"]:
        assert set(entry.keys()) == {"database_id", "label", "schema"}
    schemas = sorted(entry["schema"] for entry in data["databases"])
    assert schemas == ["flat", "legacy"]
    snapshot = app.state.adaptive_app_registry.snapshot()
    snapshot[0]["database_id"] = "tampered"
    assert "tampered" not in app.state.adaptive_app_registry.database_ids()
    assert client.get(DATABASES_PATH).json()["databases"] != snapshot
    assert "CourseOffering.year" not in CANONICAL_SELECT  # v3 catalog excludes year.
    assert not any(field.startswith("Offering.") for field in CANONICAL_SELECT)


def _catalog(client: TestClient, database_id: str, keyword: str | None = None):
    params: dict[str, str] = {"database_id": database_id}
    if keyword is not None:
        params["keyword"] = keyword
    return client.get(CATALOG_PATH, params=params)


def test_catalog_equivalence_and_keyword():
    client, _ = _client()
    flat = _catalog(client, SYNTHETIC_PROTOTYPE_DATABASE_ID)
    legacy = _catalog(client, SYNTHETIC_LEGACY_DATABASE_ID)
    assert flat.status_code == 200
    assert legacy.status_code == 200
    flat_data = flat.json()
    legacy_data = legacy.json()
    assert flat_data["version"] == SYNTHETIC_CATALOG_VERSION
    assert flat_data["total"] == len(flat_data["items"]) == len(DEFAULT_SEED.offerings)
    assert flat_data["items"] == legacy_data["items"]
    # seed values are reachable only through the catalog DTO, not raw seed dump
    assert flat_data["items"][0]["course_code"] == DEFAULT_SEED.offerings[0].course_code
    filtered = _catalog(client, SYNTHETIC_PROTOTYPE_DATABASE_ID, keyword="자료구조")
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 2
    assert all("자료구조" in item["course_name"] for item in filtered.json()["items"])


def test_catalog_rejects_unknown_and_invalid_without_leak():
    client, app = _client()
    unknown = _catalog(client, "unknown-db-1")
    assert unknown.status_code == 404
    assert unknown.json() == {"error": {"code": "SYNTHETIC_CATALOG_CONTEXT_UNAVAILABLE"}}
    bad = client.get(CATALOG_PATH, params={"database_id": "BAD ID!!"})
    assert bad.status_code == 400
    assert bad.json() == {"error": {"code": "SYNTHETIC_CATALOG_INVALID_REQUEST"}}
    extra = client.get(
        CATALOG_PATH,
        params={"database_id": SYNTHETIC_PROTOTYPE_DATABASE_ID, "injected": "x"},
    )
    assert extra.status_code == 400
    blob = unknown.text + bad.text + extra.text
    assert "SELECT" not in blob
    assert "sqlite" not in blob.lower()
    assert "tmp" not in blob.lower()
    assert app.state.adaptive_app_tmpdir not in blob

    duplicate = client.get(
        f"{CATALOG_PATH}?database_id={SYNTHETIC_PROTOTYPE_DATABASE_ID}"
        f"&database_id={SYNTHETIC_LEGACY_DATABASE_ID}"
    )
    assert duplicate.status_code == 400
    assert duplicate.json() == {"error": {"code": "SYNTHETIC_CATALOG_INVALID_REQUEST"}}


def test_clarification_branches_both_databases():
    for database_id in (SYNTHETIC_PROTOTYPE_DATABASE_ID, SYNTHETIC_LEGACY_DATABASE_ID):
        client, _ = _client()
        started = client.post(
            START_PATH,
            json={"request_id": f"req-{database_id}", "database_id": database_id, "question": TRIGGER},
        )
        assert started.status_code == 200
        data = started.json()
        assert data["status"] == "PENDING"
        assert data["version"] == CLARIFICATION_HTTP_VERSION
        target = data["options"][0]
        submitted = client.post(
            SUBMIT_PATH,
            json={"session_token": data["session_token"], "selection": [target["option_id"]]},
        )
        assert submitted.status_code == 200
        result = submitted.json()
        assert result["status"] == "EXECUTED"
        assert result["execution"]["status"] == "SUCCEEDED"
        assert result["execution"]["columns"] == [target["semantic_ref"]]
        if target["semantic_ref"] == "Course.name":
            expected = [offering.course_name for offering in DEFAULT_SEED.offerings]
        else:
            expected = [offering.instructor_name for offering in DEFAULT_SEED.offerings]
        assert sorted(row[target["semantic_ref"]] for row in result["execution"]["rows"]) == sorted(expected)


def test_catalog_blocks_unpublished_mapping_and_unsafe_compiler(monkeypatch):
    from backend.adaptive.harness.contracts import CompiledQuery, MappingStatus
    import backend.adaptive.api.database_registry as database_registry

    client, app = _client()
    original_builder = database_registry.build_catalog_mapping
    original_compile = app.state.adaptive_app_compiler.compile
    built_mappings = []
    compile_calls = []

    def unpublished_mapping(database_id, schema):
        mapping = original_builder(database_id, schema).model_copy(update={"status": MappingStatus.DRAFT})
        built_mappings.append(mapping)
        return mapping

    monkeypatch.setattr(database_registry, "build_catalog_mapping", unpublished_mapping)
    monkeypatch.setattr(
        app.state.adaptive_app_compiler,
        "compile",
        lambda *args: compile_calls.append(args),
    )
    unpublished = _catalog(client, SYNTHETIC_PROTOTYPE_DATABASE_ID)
    assert unpublished.status_code == 404
    assert unpublished.json() == {"error": {"code": "SYNTHETIC_CATALOG_CONTEXT_UNAVAILABLE"}}
    assert built_mappings and built_mappings[0].status is MappingStatus.DRAFT
    assert compile_calls == []
    monkeypatch.setattr(database_registry, "build_catalog_mapping", original_builder)

    def unsafe_compile(query_ir, mapping):
        assert mapping.status is MappingStatus.PUBLISHED
        return CompiledQuery(sql="DELETE FROM course_offerings", parameters=[], expected_columns=[])

    monkeypatch.setattr(app.state.adaptive_app_compiler, "compile", unsafe_compile)
    response = _catalog(client, SYNTHETIC_PROTOTYPE_DATABASE_ID)
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "SYNTHETIC_CATALOG_CONTEXT_UNAVAILABLE"}}
    assert "DELETE" not in response.text
    assert "sqlite" not in response.text.lower()
    monkeypatch.setattr(app.state.adaptive_app_compiler, "compile", original_compile)


def test_schema_drift_fails_closed_for_catalog_and_clarification():
    client, app = _client()
    tmpdir = Path(app.state.adaptive_app_tmpdir)
    started = client.post(
        START_PATH,
        json={
            "request_id": "req-drift-before",
            "database_id": SYNTHETIC_PROTOTYPE_DATABASE_ID,
            "question": TRIGGER,
        },
    )
    assert started.status_code == 200
    pending = started.json()
    option = pending["options"][0]

    with sqlite3.connect(tmpdir / "flat.sqlite3") as connection:
        connection.execute("ALTER TABLE course_offerings RENAME TO drifted_course_offerings")

    catalog = _catalog(client, SYNTHETIC_PROTOTYPE_DATABASE_ID)
    assert catalog.status_code == 404
    assert catalog.json() == {"error": {"code": "SYNTHETIC_CATALOG_CONTEXT_UNAVAILABLE"}}
    after_drift = client.post(
        START_PATH,
        json={
            "request_id": "req-drift-after",
            "database_id": SYNTHETIC_PROTOTYPE_DATABASE_ID,
            "question": TRIGGER,
        },
    )
    assert after_drift.status_code == 503
    assert after_drift.json() == {"error": {"code": "CLARIFICATION_HTTP_CONTEXT_UNAVAILABLE"}}

    submitted = client.post(
        SUBMIT_PATH,
        json={"session_token": pending["session_token"], "selection": [option["option_id"]]},
    )
    assert submitted.status_code == 200
    assert submitted.json()["execution"]["status"] == "FAILED"
    assert submitted.json()["execution"]["error"] == {
        "code": "MAPPING_NOT_PUBLISHED",
        "stage": "EXECUTED",
    }
    blob = catalog.text + after_drift.text + submitted.text
    assert str(tmpdir) not in blob
    assert "drifted_course_offerings" not in blob
    assert "SELECT" not in blob


def test_cleanup_removes_both_temp_databases():
    app = create_synthetic_clarification_prototype()
    tmpdir = Path(app.state.adaptive_app_tmpdir)
    assert (tmpdir / "flat.sqlite3").is_file()
    assert (tmpdir / "legacy.sqlite3").is_file()
    with TestClient(app, raise_server_exceptions=False):
        assert tmpdir.is_dir()
    assert not tmpdir.exists()
