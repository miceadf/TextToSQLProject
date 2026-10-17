"""Phase 8-04: fixed test-query preview on active private mappings (synthetic only)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.adaptive.api.admin_test_queries import (
    MAX_PREVIEW_ROWS,
    TEST_QUERIES,
    TEST_QUERIES_PATH,
    TEST_QUERY_PATH,
    _sort_key,
    _tagged,
)
from backend.adaptive.api.app_factory import (
    create_synthetic_clarification_prototype,
    set_private_repository_root_for_tests,
)
from backend.adaptive.evaluation.reference import FIELD_TO_ATTRIBUTE
from backend.adaptive.fixtures.coded import build_coded_database
from backend.adaptive.fixtures.flat import build_flat_database
from backend.adaptive.fixtures.hybrid import build_hybrid_database
from backend.adaptive.fixtures.seed import DEFAULT_SEED
from backend.adaptive.ontology import load_default_ontology
from tests.adaptive.test_mapping_version_rollback import _source_with_alternate_title
from tests.adaptive.test_private_mapping_publish import _action, _make_repo, _publish_flow, _upload
from tests.adaptive.test_schema_compare import _publish_hybrid


def _publish_fixture(client: TestClient, raw: bytes, database_id: str, variant) -> None:
    """Publish a multi-table fixture with its explicit synthetic mapping."""
    from backend.adaptive.evaluation.fixture_mapping import _COLUMNS, _TABLES

    assert _upload(client, database_id, "Label", raw).status_code == 200
    assert _action(client, {"action": "INIT", "database_id": database_id, "reviewer": "tester"}).status_code == 200
    for semantic_field, column in _COLUMNS[variant].items():
        # Phase 9-24: fixture metadata is historical; current review excludes year.
        if semantic_field not in load_default_ontology().active_fields:
            continue
        table = _TABLES[variant][semantic_field.split(".", 1)[0]]
        response = _action(client, {
            "action": "REMAP", "database_id": database_id, "semantic_field": semantic_field,
            "table": table, "column": column, "reviewer": "tester", "reason": "synthetic fixture",
        })
        assert response.status_code == 200, response.text[:200]
    # Phase 8-18: the fixtures store no target grade.
    response = _action(client, {
        "action": "IGNORE", "database_id": database_id, "semantic_field": "Course.target_grade",
        "reviewer": "tester", "reason": "이 DB에 없음",
    })
    assert response.status_code == 200, response.text[:200]
    assert _action(client, {"action": "PUBLISH", "database_id": database_id, "published_by": "tester"}).status_code == 200


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


def _run(client: TestClient, database_id: str, query_id: str):
    return client.get(TEST_QUERY_PATH, params={"database_id": database_id, "query_id": query_id})


def _expected(query_id: str) -> tuple[list[list[object]], str]:
    columns = TEST_QUERIES[query_id]["select"]
    rows = [
        [getattr(offering, FIELD_TO_ATTRIBUTE[name]) for name in columns]
        for offering in DEFAULT_SEED.offerings
    ]
    from backend.adaptive.api.admin_test_queries import _ordered_rows

    rows = _ordered_rows(rows)
    digest = hashlib.sha256(json.dumps(
        {"columns": columns, "rows": [_tagged(row) for row in rows]},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return rows, digest


def test_every_query_matches_seed_on_three_schemas(client_repo, tmp_path: Path) -> None:
    from backend.adaptive.fixtures import FixtureVariant

    client, repo = client_repo
    _publish_flow(client, build_flat_database(tmp_path / "flat.sqlite3", seed=DEFAULT_SEED).read_bytes(), "tq-flat")
    _publish_fixture(client, build_coded_database(tmp_path / "coded.sqlite3", seed=DEFAULT_SEED).read_bytes(), "tq-coded", FixtureVariant.CODED)
    _publish_hybrid(client, build_hybrid_database(tmp_path / "hybrid.sqlite3", seed=DEFAULT_SEED).read_bytes(), "tq-hybrid")
    for query_id in TEST_QUERIES:
        expected_rows, expected_hash = _expected(query_id)
        for database_id in ("tq-flat", "tq-coded", "tq-hybrid"):
            response = _run(client, database_id, query_id)
            assert response.status_code == 200, (database_id, query_id, response.text[:300])
            body = response.json()
            assert body["version"] == "admin-test-query-result-v1"
            assert body["mapping_version"] == 1
            assert body["columns"] == TEST_QUERIES[query_id]["select"]
            assert body["row_count"] == len(DEFAULT_SEED.offerings)
            assert body["row_count_capped"] is False
            assert body["rows"] == [list(row) for row in expected_rows[:MAX_PREVIEW_ROWS]]
            assert body["result_sha256"] == expected_hash
            for marker in ("SELECT", "course_offerings", "tb_", "class_sections", str(repo), "sqlite3"):
                assert marker not in response.text


def test_hash_follows_the_active_mapping_version(client_repo, tmp_path: Path) -> None:
    client, _repo = client_repo
    source = _source_with_alternate_title(tmp_path)
    _publish_flow(client, source.read_bytes(), "tq-versions")
    first = _run(client, "tq-versions", "offerings").json()
    assert _action(client, {
        "action": "REMAP", "database_id": "tq-versions", "semantic_field": "Course.name",
        "table": "course_offerings", "column": "course_title", "reviewer": "tester", "reason": "alternate",
    }).status_code == 200
    assert _action(client, {"action": "PUBLISH", "database_id": "tq-versions", "published_by": "tester"}).status_code == 200
    second = _run(client, "tq-versions", "offerings").json()
    assert second["mapping_version"] == 2
    assert second["result_sha256"] != first["result_sha256"]
    # A query that does not use the remapped field keeps its hash.
    assert _run(client, "tq-versions", "seats").json()["result_sha256"] == _expected("seats")[1]
    assert _action(client, {
        "action": "ACTIVATE", "database_id": "tq-versions", "mapping_version": 1, "activated_by": "tester",
    }).status_code == 200
    rolled_back = _run(client, "tq-versions", "offerings").json()
    assert rolled_back["mapping_version"] == 1
    assert rolled_back["result_sha256"] == first["result_sha256"]


def test_unpublished_and_invalid_requests_fail_closed(client_repo, tmp_path: Path) -> None:
    client, repo = client_repo
    flat = build_flat_database(tmp_path / "flat.sqlite3", seed=DEFAULT_SEED).read_bytes()
    _publish_flow(client, flat, "tq-flat")
    assert _upload(client, "tq-pending", "Pending", flat).status_code == 200
    cases = [
        (TEST_QUERY_PATH + "?database_id=tq-pending&query_id=offerings", 404),
        (TEST_QUERY_PATH + "?database_id=tq-missing&query_id=offerings", 404),
        (TEST_QUERY_PATH + "?database_id=tq-flat&query_id=unknown", 400),
        (TEST_QUERY_PATH + "?database_id=tq-flat&query_id=offerings&sql=SELECT", 400),
        (TEST_QUERY_PATH + "?database_id=tq-flat", 400),
        (TEST_QUERY_PATH + "?database_id=tq-flat&database_id=tq-flat&query_id=offerings", 400),
        (TEST_QUERY_PATH + "?database_id=..%2Fescape&query_id=offerings", 400),
        (TEST_QUERY_PATH + "?database_id=synth-clarification-prototype&query_id=offerings", 400),
        (TEST_QUERIES_PATH + "?x=1", 400),
    ]
    for url, status in cases:
        response = client.get(url)
        assert response.status_code == status, (url, response.text[:200])
        assert set(response.json()) == {"error"}
        assert set(response.json()["error"]) == {"code"}
        assert str(repo) not in response.text


def test_fixed_query_list(client_repo) -> None:
    client, _repo = client_repo
    body = client.get(TEST_QUERIES_PATH).json()
    assert body["version"] == "admin-test-queries-v1"
    assert [item["query_id"] for item in body["queries"]] == list(TEST_QUERIES)
    for item in body["queries"]:
        assert set(item) == {"query_id", "title", "entity", "select"}
        assert "CourseOffering.section" in item["select"]


def test_null_and_null_string_hash_differently() -> None:
    assert _tagged([None]) != _tagged(["NULL"])
    assert _tagged([1]) != _tagged(["1"])
    assert _tagged([1]) != _tagged([1.0])
    assert sorted([[None], ["a"], [2], [10], [True]], key=_sort_key) == [[None], [True], [2], [10], ["a"]]


# ---------------------------------------------------------------------------
# Luna review regressions


def test_hash_ignores_input_row_order_for_near_equal_numbers() -> None:
    from backend.adaptive.api.admin_test_queries import _ordered_rows

    for rows in (
        [[9007199254740993], [9007199254740992]],
        [[1.0], [1]],
        [[True], [1]],
    ):
        forward = [_tagged(row) for row in _ordered_rows([list(row) for row in rows])]
        backward = [_tagged(row) for row in _ordered_rows([list(row) for row in reversed(rows)])]
        assert forward == backward, rows


def test_storage_failure_is_internal_error(client_repo, tmp_path: Path, monkeypatch) -> None:
    from backend.adaptive.registration import reviews as review_storage

    client, _repo = client_repo
    _publish_flow(client, build_flat_database(tmp_path / "flat.sqlite3", seed=DEFAULT_SEED).read_bytes(), "tq-flat")

    def broken(**_kwargs):
        raise review_storage.ReviewError(review_storage.ReviewErrorCode.PRIVATE_STORAGE_INVALID)

    monkeypatch.setattr(review_storage, "load_activation", broken)
    response = _run(client, "tq-flat", "offerings")
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "ADMIN_TEST_QUERY_INTERNAL_ERROR"}}
