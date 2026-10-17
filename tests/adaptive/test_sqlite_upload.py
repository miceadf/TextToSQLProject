"""Focused tests for Phase 6-18 loopback SQLite upload boundary."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.adaptive.api.registration_upload import (
    SQLITE_UPLOAD_CONTENT_TYPE,
    SQLITE_UPLOAD_PATH,
    SQLITE_UPLOAD_VERSION,
)
from backend.adaptive.api.app_factory import (
    SYNTHETIC_LEGACY_DATABASE_ID,
    SYNTHETIC_PROTOTYPE_DATABASE_ID,
    create_synthetic_clarification_prototype,
)
from backend.adaptive.api.database_registry import (
    CATALOG_PATH,
    DATABASES_PATH,
)
from backend.adaptive.api.clarification_http import START_PATH
from backend.adaptive.fixtures.flat import build_flat_database
from backend.adaptive.fixtures.seed import DEFAULT_SEED
from backend.adaptive.registration import storage as registration_storage


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
def upload_client(tmp_path: Path):
    repo = _make_repo(tmp_path, "repo")
    source = build_flat_database(tmp_path / "flat.sqlite3", seed=DEFAULT_SEED)
    app = create_synthetic_clarification_prototype()
    app.state.sqlite_upload_repository_root = str(repo)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, repo, source
    finally:
        client.close()


def _upload(client: TestClient, query: str, body: bytes, headers: dict | None = None):
    heads = dict(headers or {})
    return client.post(SQLITE_UPLOAD_PATH + query, content=body, headers=heads)


def _ok_headers() -> dict:
    return {"Content-Type": SQLITE_UPLOAD_CONTENT_TYPE}


def test_reserved_selection_id_upload_is_invalid_without_writing(upload_client, monkeypatch) -> None:
    client, repo, source = upload_client
    before = sorted(path.relative_to(repo) for path in repo.rglob("*"))

    def forbidden(*_args, **_kwargs):
        pytest.fail("reserved upload must not create a temporary file")

    monkeypatch.setattr(tempfile, "mkstemp", forbidden)
    response = _upload(client, "?database_id=legacy&label=Legacy", source.read_bytes(), _ok_headers())
    assert response.status_code == 400
    assert response.json() == {"error": {"code": "SQLITE_UPLOAD_INVALID_REQUEST"}}
    assert sorted(path.relative_to(repo) for path in repo.rglob("*")) == before


def test_reserved_selection_id_storage_is_invalid_before_any_write(tmp_path: Path) -> None:
    from backend.adaptive.registration.errors import RegistrationError, RegistrationErrorCode

    with pytest.raises(RegistrationError) as caught:
        registration_storage.register_database(
            repository_root=tmp_path, source_path=tmp_path / "missing.sqlite3",
            database_id="legacy", label="Legacy",
        )
    assert caught.value.code is RegistrationErrorCode.INVALID_INPUT
    assert list(tmp_path.iterdir()) == []
    assert registration_storage.validate_database_id("legacy") == "legacy"


def test_raw_upload_success_pending_review_summary_without_samples_or_paths(
    upload_client,
) -> None:
    client, repo, source = upload_client
    raw = source.read_bytes()
    response = _upload(
        client, "?database_id=upload-ok-one&label=Upload+Ok", raw, _ok_headers()
    )
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {
        "version",
        "database_id",
        "label",
        "status",
        "schema_fingerprint",
        "schema",
        "mapping",
    }
    assert data["version"] == SQLITE_UPLOAD_VERSION
    assert data["database_id"] == "upload-ok-one"
    assert data["label"] == "Upload Ok"
    assert data["status"] == "PENDING_REVIEW"
    assert len(data["schema_fingerprint"]) == 64
    schema = data["schema"]
    assert schema["table_count"] == len(schema["tables"]) >= 1
    assert schema["column_count"] == sum(t["column_count"] for t in schema["tables"])
    for table in schema["tables"]:
        assert set(table) == {
            "name",
            "column_count",
            "columns",
            "primary_key",
            "foreign_key_count",
        }
        for column in table["columns"]:
            assert set(column) == {
                "name",
                "declared_type",
                "affinity",
                "nullable",
                "primary_key_position",
            }
    mapping = data["mapping"]
    assert mapping["field_count"] == len(mapping["fields"])
    for field in mapping["fields"]:
        assert set(field) == {"semantic_field", "review_status", "top_candidate"}
        top = field["top_candidate"]
        if top is not None:
            assert set(top) == {"table", "column", "confidence", "reasons"}
            for reason in top["reasons"]:
                assert set(reason) == {"signal", "score", "detail"}
    blob = response.text
    assert str(repo) not in blob
    assert str(source) not in blob
    assert source.name not in blob
    assert "SELECT" not in blob
    assert "Traceback" not in blob
    for offering in DEFAULT_SEED.offerings:
        assert offering.course_name not in blob
    # strict reload artifacts exist on disk
    final_dir = repo / ".schema-adaptive" / "registered-databases" / "upload-ok-one"
    assert (final_dir / "registration.json").is_file()
    # temp upload files are cleaned up
    leftovers = [
        item.name
        for item in Path(tempfile.gettempdir()).iterdir()
        if item.name.startswith(".upload-")
    ]
    assert leftovers == []


def test_registry_identical_and_new_id_not_executable(upload_client) -> None:
    client, _repo, source = upload_client
    before = client.get(DATABASES_PATH).text
    raw = source.read_bytes()
    response = _upload(
        client, "?database_id=upload-quarantined&label=Quarantined", raw, _ok_headers()
    )
    assert response.status_code == 200
    after = client.get(DATABASES_PATH).text
    assert after == before
    assert "upload-quarantined" not in after
    catalog = client.get(CATALOG_PATH, params={"database_id": "upload-quarantined"})
    assert catalog.status_code == 404
    assert catalog.json() == {"error": {"code": "SYNTHETIC_CATALOG_CONTEXT_UNAVAILABLE"}}
    started = client.post(
        START_PATH,
        json={
            "request_id": "req-upload-quarantine",
            "database_id": "upload-quarantined",
            "question": "SYNTH_Q_P616_k9w1 강의 목록 보여줘",
        },
    )
    assert started.status_code == 503
    blob = catalog.text + started.text
    assert "upload-quarantined" not in blob or "CONTEXT_UNAVAILABLE" in blob


def test_http_boundary_failures_have_fixed_mapping(upload_client) -> None:
    client, _repo, source = upload_client
    raw = source.read_bytes()

    def check(query: str, body: bytes, headers: dict | None, status: int, code: str):
        response = _upload(client, query, body, headers)
        assert response.status_code == status, (query, response.text[:300])
        assert response.json() == {"error": {"code": code}}, response.text[:300]

    check("?database_id=only-id", raw, _ok_headers(), 400, "SQLITE_UPLOAD_INVALID_REQUEST")
    check("?label=only-label", raw, _ok_headers(), 400, "SQLITE_UPLOAD_INVALID_REQUEST")
    check("", raw, _ok_headers(), 400, "SQLITE_UPLOAD_INVALID_REQUEST")
    check(
        "?database_id=dup-a&database_id=dup-b&label=x",
        raw,
        _ok_headers(),
        400,
        "SQLITE_UPLOAD_INVALID_REQUEST",
    )
    check(
        "?database_id=extra-param&label=x&injected=1",
        raw,
        _ok_headers(),
        400,
        "SQLITE_UPLOAD_INVALID_REQUEST",
    )
    check(
        "?database_id=empty-segment&label=x&",
        raw,
        _ok_headers(),
        400,
        "SQLITE_UPLOAD_INVALID_REQUEST",
    )
    check(
        "?database_id=bad-ctype&label=x",
        raw,
        {"Content-Type": "application/json"},
        400,
        "SQLITE_UPLOAD_INVALID_REQUEST",
    )
    check("?database_id=empty-body&label=x", b"", _ok_headers(), 400, "SQLITE_UPLOAD_INVALID_REQUEST")
    check(
        "?database_id=ctype-missing&label=x",
        raw,
        {},
        400,
        "SQLITE_UPLOAD_INVALID_REQUEST",
    )
    # declared over 32 MiB
    big_headers = dict(_ok_headers())
    big_headers["Content-Length"] = str(registration_storage.MAX_SOURCE_BYTES + 1)
    check(
        "?database_id=declared-big&label=x",
        b"SQLite format 3\x00",
        big_headers,
        413,
        "SQLITE_UPLOAD_TOO_LARGE",
    )
    # malformed body inputs
    check(
        "?database_id=forged-body&label=x",
        b"not a sqlite database" * 64,
        _ok_headers(),
        400,
        "SQLITE_UPLOAD_INVALID_REQUEST",
    )
    truncated = raw[:512]
    check(
        "?database_id=corrupt-body&label=x",
        truncated,
        _ok_headers(),
        400,
        "SQLITE_UPLOAD_INVALID_REQUEST",
    )


def test_conflict_and_oversize_stream_are_mapped(upload_client) -> None:
    client, _repo, source = upload_client
    raw = source.read_bytes()
    first = _upload(client, "?database_id=dup-target&label=First", raw, _ok_headers())
    assert first.status_code == 200
    second = _upload(client, "?database_id=dup-target&label=Second", raw, _ok_headers())
    assert second.status_code == 409
    assert second.json() == {"error": {"code": "SQLITE_UPLOAD_CONFLICT"}}
    reserved = _upload(
        client,
        f"?database_id={SYNTHETIC_PROTOTYPE_DATABASE_ID}&label=x",
        raw,
        _ok_headers(),
    )
    assert reserved.status_code == 409
    assert reserved.json() == {"error": {"code": "SQLITE_UPLOAD_CONFLICT"}}
    legacy_reserved = _upload(
        client,
        f"?database_id={SYNTHETIC_LEGACY_DATABASE_ID}&label=x",
        raw,
        _ok_headers(),
    )
    assert legacy_reserved.status_code == 409

    # actual streaming over the limit (declared valid, body too large)
    over = b"SQLite format 3\x00" + b"\x00" * registration_storage.MAX_SOURCE_BYTES
    headers = dict(_ok_headers())
    headers["Content-Length"] = str(len(raw))
    response = _upload(client, "?database_id=stream-big&label=x", over, headers)
    assert response.status_code in (400, 413)
    assert set(response.json()) == {"error"}
    assert set(response.json()["error"]) == {"code"}


def test_failures_redacted_and_temp_cleaned(upload_client, monkeypatch) -> None:
    client, repo, source = upload_client
    raw = source.read_bytes()

    response = _upload(client, "?database_id=BAD!!&label=x", raw, _ok_headers())
    assert response.status_code == 400
    assert response.json() == {"error": {"code": "SQLITE_UPLOAD_INVALID_REQUEST"}}
    assert "Traceback" not in response.text
    assert str(repo) not in response.text
    assert "SQLite format" not in response.text
    assert "SELECT" not in response.text

    monkeypatch.setattr(
        registration_storage.RuleBasedSchemaAligner,
        "align",
        lambda self, catalog, ontology: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    response = _upload(client, "?database_id=align-boom&label=x", raw, _ok_headers())
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "SQLITE_UPLOAD_INTERNAL_ERROR"}}
    assert "boom" not in response.text
    assert "Traceback" not in response.text
    assert not (repo / ".schema-adaptive" / "registered-databases" / "align-boom").exists()


def test_duplicate_params_and_strict_extra_rejected(upload_client) -> None:
    client, _repo, source = upload_client
    raw = source.read_bytes()
    response = _upload(
        client,
        "?database_id=strict-extra&label=x&database_id=strict-extra",
        raw,
        _ok_headers(),
    )
    assert response.status_code == 400
    assert response.json() == {"error": {"code": "SQLITE_UPLOAD_INVALID_REQUEST"}}
    unknown = _upload(
        client, "?database_id=strict-unknown&label=x&mode=fast", raw, _ok_headers()
    )
    assert unknown.status_code == 400
    ok = _upload(client, "?database_id=strict-ok&label=Strict+Ok", raw, _ok_headers())
    assert ok.status_code == 200
    assert set(ok.json()) == {
        "version",
        "database_id",
        "label",
        "status",
        "schema_fingerprint",
        "schema",
        "mapping",
    }
