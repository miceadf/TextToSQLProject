"""Phase 7-05 private PostgreSQL startup profile tests (synthetic only)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.adaptive.api.postgres_startup_profile import (
    PostgresStartupProfileError,
    load_postgres_startup_profiles,
)

SECRET_DSN = "postgresql://svc-user:s3cret-pw-705@127.0.0.1:5432/synth705"
SECRET_MARKER = "SECRET_P705_k8m2_TOKEN"
FAKE_PATH = "C:\\synth_p705\\private\\notes.txt"
FIXED_FAILURE = "postgres startup profile unavailable"
CLI_FAILURE = "synthetic prototype invalid configuration"


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo705"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(root)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (root / ".gitignore").write_text(".schema-adaptive/\n", encoding="utf-8")
    return root


def _mapping_doc(database_id: str = "synthetic-postgres") -> dict:
    return {
        "database_id": database_id,
        "version": 1,
        "status": "PUBLISHED",
        "schema_fingerprint": "ab" * 32,
        "ontology_version": 1,
        "published_by": "synthetic-test",
        "published_at": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
        "fields": {
            "Course.name": {
                "table": "courses",
                "column": "course_name",
                "reviewer": "synthetic-test",
                "reason": None,
            }
        },
    }


def _write_profile(
    repository: Path,
    database_id: str,
    *,
    dsn_env: str = "SCHEMA_ADAPTIVE_POSTGRES_DSN_SYNTHETIC",
    mapping_doc: dict | None = None,
    profile_overrides: dict | None = None,
    raw_mapping: bytes | None = None,
) -> tuple[Path, Path]:
    directory = repository / ".schema-adaptive" / "postgres-profiles" / database_id
    directory.mkdir(parents=True, exist_ok=True)
    if raw_mapping is not None:
        mapping_bytes = raw_mapping
    else:
        doc = mapping_doc if mapping_doc is not None else _mapping_doc(database_id)
        mapping_bytes = (json.dumps(doc, sort_keys=True) + "\n").encode("utf-8")
    (directory / "mapping.json").write_bytes(mapping_bytes)
    sha = hashlib.sha256(mapping_bytes).hexdigest()
    profile = {
        "version": "postgres-runtime-profile-v1",
        "database_id": database_id,
        "label": "Synthetic PostgreSQL",
        "schema": "public",
        "dsn_env": dsn_env,
        "mapping_sha256": sha,
    }
    if profile_overrides:
        profile.update(profile_overrides)
    (directory / "profile.json").write_bytes(
        (json.dumps(profile, sort_keys=True) + "\n").encode("utf-8")
    )
    return directory / "profile.json", directory / "mapping.json"


def test_success_creates_runtime_profile(repository: Path, monkeypatch) -> None:
    monkeypatch.setenv("SCHEMA_ADAPTIVE_POSTGRES_DSN_SYNTHETIC", SECRET_DSN)
    _write_profile(repository, "synthetic-postgres")
    profiles = load_postgres_startup_profiles(
        repository_root=repository, database_ids=["synthetic-postgres"]
    )
    assert len(profiles) == 1
    assert profiles[0].database_id == "synthetic-postgres"
    assert profiles[0].label == "Synthetic PostgreSQL"
    blob = repr(profiles[0]) + str(profiles[0])
    assert "s3cret-pw-705" not in blob
    assert "public" not in blob or "Synthetic PostgreSQL" in blob


def test_deterministic_order_and_duplicate_rejections(
    repository: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SCHEMA_ADAPTIVE_POSTGRES_DSN_BBB", SECRET_DSN)
    monkeypatch.setenv("SCHEMA_ADAPTIVE_POSTGRES_DSN_AAA", SECRET_DSN)
    _write_profile(repository, "bbb-postgres", dsn_env="SCHEMA_ADAPTIVE_POSTGRES_DSN_BBB")
    _write_profile(repository, "aaa-postgres", dsn_env="SCHEMA_ADAPTIVE_POSTGRES_DSN_AAA")
    profiles = load_postgres_startup_profiles(
        repository_root=repository, database_ids=["bbb-postgres", "aaa-postgres"]
    )
    assert [p.database_id for p in profiles] == ["aaa-postgres", "bbb-postgres"]
    with pytest.raises(PostgresStartupProfileError, match="^postgres startup profile unavailable$"):
        load_postgres_startup_profiles(
            repository_root=repository,
            database_ids=["aaa-postgres", "aaa-postgres"],
        )
    _write_profile(repository, "ccc-postgres", dsn_env="SCHEMA_ADAPTIVE_POSTGRES_DSN_AAA")
    with pytest.raises(PostgresStartupProfileError):
        load_postgres_startup_profiles(
            repository_root=repository,
            database_ids=["aaa-postgres", "ccc-postgres"],
        )


def test_path_guards_reject_tracked_outside_symlink(repository: Path, monkeypatch) -> None:
    monkeypatch.setenv("SCHEMA_ADAPTIVE_POSTGRES_DSN_SYNTHETIC", SECRET_DSN)
    profile_path, _ = _write_profile(repository, "synthetic-postgres")
    subprocess.run(
        ["git", "-C", str(repository), "add", "-f", ".schema-adaptive/postgres-profiles/synthetic-postgres/profile.json"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with pytest.raises(PostgresStartupProfileError):
        load_postgres_startup_profiles(
            repository_root=repository, database_ids=["synthetic-postgres"]
        )
    subprocess.run(
        ["git", "-C", str(repository), "rm", "--cached", "-q", ".schema-adaptive/postgres-profiles/synthetic-postgres/profile.json"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    outside = repository.parent / "outside705"
    outside.mkdir(exist_ok=True)
    with pytest.raises(PostgresStartupProfileError):
        load_postgres_startup_profiles(
            repository_root=outside, database_ids=["synthetic-postgres"]
        )
    link = repository / ".schema-adaptive" / "postgres-profiles" / "link-postgres"
    try:
        link.symlink_to(repository / ".schema-adaptive" / "postgres-profiles" / "synthetic-postgres", target_is_directory=True)
    except OSError:
        pass
    else:
        with pytest.raises(PostgresStartupProfileError):
            load_postgres_startup_profiles(
                repository_root=repository, database_ids=["link-postgres"]
            )
    assert profile_path.is_file()


def test_strict_contract_rejections(repository: Path, monkeypatch) -> None:
    monkeypatch.setenv("SCHEMA_ADAPTIVE_POSTGRES_DSN_SYNTHETIC", SECRET_DSN)
    _write_profile(repository, "synthetic-postgres")
    _profile_path = repository / ".schema-adaptive/postgres-profiles/synthetic-postgres/profile.json"
    mapping_path = repository / ".schema-adaptive/postgres-profiles/synthetic-postgres/mapping.json"

    def load_raw(mapping_bytes: bytes | None = None, profile_bytes: bytes | None = None):
        if mapping_bytes is not None:
            mapping_path.write_bytes(mapping_bytes)
        if profile_bytes is not None:
            _profile_path.write_bytes(profile_bytes)
        with pytest.raises(PostgresStartupProfileError):
            load_postgres_startup_profiles(
                repository_root=repository, database_ids=["synthetic-postgres"]
            )

    load_raw(mapping_bytes=b"x" * (256 * 1024 + 1))
    _write_profile(repository, "synthetic-postgres")
    load_raw(mapping_bytes=b"\xff\xfe invalid")
    _write_profile(repository, "synthetic-postgres")
    load_raw(mapping_bytes=b'{"a": 1, "a": 2}')
    _write_profile(repository, "synthetic-postgres")
    doc = _mapping_doc()
    doc["extra"] = 1
    load_raw(mapping_bytes=(json.dumps(doc) + "\n").encode())
    _write_profile(repository, "synthetic-postgres")
    bad = _mapping_doc()
    del bad["status"]
    load_raw(mapping_bytes=(json.dumps(bad) + "\n").encode())
    _write_profile(repository, "synthetic-postgres")
    bad = _mapping_doc()
    bad["version"] = "1"
    load_raw(mapping_bytes=(json.dumps(bad) + "\n").encode())
    _write_profile(repository, "synthetic-postgres")
    bad = _mapping_doc()
    bad["published_at"] = "2026-01-01T00:00:00"
    load_raw(mapping_bytes=(json.dumps(bad) + "\n").encode())
    _write_profile(repository, "synthetic-postgres")
    bad = _mapping_doc()
    bad["fields"] = {"": {"table": "t", "column": "c", "reviewer": "r", "reason": None}}
    load_raw(mapping_bytes=(json.dumps(bad) + "\n").encode())
    _write_profile(repository, "synthetic-postgres")
    bad_profile = (json.dumps({**json.loads(_profile_path.read_bytes()), "version": "bad"}) + "\n").encode()
    load_raw(profile_bytes=bad_profile)


def test_missing_empty_env_and_id_mismatch_rejected(repository: Path, monkeypatch) -> None:
    monkeypatch.delenv("SCHEMA_ADAPTIVE_POSTGRES_DSN_SYNTHETIC", raising=False)
    _write_profile(repository, "synthetic-postgres")
    with pytest.raises(PostgresStartupProfileError):
        load_postgres_startup_profiles(
            repository_root=repository, database_ids=["synthetic-postgres"]
        )
    monkeypatch.setenv("SCHEMA_ADAPTIVE_POSTGRES_DSN_SYNTHETIC", "")
    with pytest.raises(PostgresStartupProfileError):
        load_postgres_startup_profiles(
            repository_root=repository, database_ids=["synthetic-postgres"]
        )
    monkeypatch.setenv("SCHEMA_ADAPTIVE_POSTGRES_DSN_SYNTHETIC", SECRET_DSN)
    other = _mapping_doc("other-postgres")
    _write_profile(repository, "synthetic-postgres", mapping_doc=other)
    with pytest.raises(PostgresStartupProfileError):
        load_postgres_startup_profiles(
            repository_root=repository, database_ids=["synthetic-postgres"]
        )
    with pytest.raises(PostgresStartupProfileError):
        load_postgres_startup_profiles(
            repository_root=repository, database_ids=["../escape"]
        )


def test_redaction(repository: Path, monkeypatch, caplog) -> None:
    monkeypatch.setenv("SCHEMA_ADAPTIVE_POSTGRES_DSN_SYNTHETIC", SECRET_DSN)
    doc = _mapping_doc()
    _write_profile(repository, "synthetic-postgres", mapping_doc=doc)
    mapping_path = repository / ".schema-adaptive/postgres-profiles/synthetic-postgres/mapping.json"
    mapping_path.write_bytes(b"not json " + SECRET_MARKER.encode() + b" " + FAKE_PATH.encode())
    _profile = repository / ".schema-adaptive/postgres-profiles/synthetic-postgres/profile.json"
    raw_profile = json.loads(_profile.read_bytes())
    raw_profile["mapping_sha256"] = hashlib.sha256(mapping_path.read_bytes()).hexdigest()
    _profile.write_bytes((json.dumps(raw_profile) + "\n").encode())
    try:
        load_postgres_startup_profiles(
            repository_root=repository, database_ids=["synthetic-postgres"]
        )
        raise AssertionError("must fail")
    except PostgresStartupProfileError as exc:
        blob = str(exc) + repr(exc)
        assert blob == FIXED_FAILURE or FIXED_FAILURE in blob
        for secret in ("s3cret-pw-705", SECRET_MARKER, FAKE_PATH, "127.0.0.1", "course_name", "SELECT"):
            assert secret not in blob
    logs = "\n".join(r.getMessage() for r in caplog.records)
    for secret in ("s3cret-pw-705", SECRET_MARKER, FAKE_PATH):
        assert secret not in logs


def test_cli_no_option_keeps_loopback_and_touches_nothing(monkeypatch, capsys) -> None:
    import backend.adaptive.api.app_factory as proto

    def explode(*_a: object, **_k: object):
        raise AssertionError("loader must not run")

    monkeypatch.setattr(
        "backend.adaptive.catalog.postgres.PostgresAdapter", explode, raising=False
    )
    monkeypatch.setattr(
        "backend.adaptive.harness.postgres.PostgresReadOnlyExecutor", explode, raising=False
    )
    captured: dict = {}

    class _FakeUvicorn:
        def run(self, app, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "uvicorn", _FakeUvicorn())
    cleaned: list = []

    class _State:
        def __init__(self) -> None:
            self.adaptive_app_cleanup = lambda: cleaned.append(True)

    class _App:
        def __init__(self) -> None:
            self.state = _State()

    seen: dict = {}

    def _fake_factory(*_a: object, **_k: object):
        seen["kwargs"] = dict(_k)
        return _App()

    monkeypatch.setattr(proto, "create_synthetic_clarification_prototype", _fake_factory)
    assert proto.main([]) == 0
    assert seen["kwargs"] == {}
    assert captured["host"] == "127.0.0.1"
    assert captured["reload"] is False
    assert cleaned == [True]
    out, err = capsys.readouterr()
    assert SECRET_DSN not in out + err


def test_cli_success_passes_profiles_and_failure_blocks_uvicorn(
    repository: Path, monkeypatch, capsys
) -> None:
    import backend.adaptive.api.app_factory as proto

    monkeypatch.setenv("SCHEMA_ADAPTIVE_POSTGRES_DSN_SYNTHETIC", SECRET_DSN)
    _write_profile(repository, "synthetic-postgres")
    captured: dict = {}
    factory_seen: dict = {}

    class _FakeUvicorn:
        def run(self, app, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "uvicorn", _FakeUvicorn())

    class _State:
        def __init__(self) -> None:
            self.adaptive_app_cleanup = lambda: cleaned.append(True)

    cleaned: list = []

    class _App:
        def __init__(self) -> None:
            self.state = _State()

    real_loader = load_postgres_startup_profiles

    def _spy(*, repository_root, database_ids):
        profiles = real_loader(repository_root=repository_root, database_ids=database_ids)
        factory_seen["count"] = len(profiles)
        return profiles

    monkeypatch.setattr(
        "backend.adaptive.api.postgres_startup_profile.load_postgres_startup_profiles",
        _spy,
    )
    monkeypatch.setattr(
        proto, "create_synthetic_clarification_prototype", lambda **_k: _App()
    )
    assert (
        proto.main(
            [
                "--repository-root",
                str(repository),
                "--postgres-profile-id",
                "synthetic-postgres",
            ]
        )
        == 0
    )
    assert factory_seen["count"] == 1
    assert captured["host"] == "127.0.0.1"

    calls: dict = {"factory": 0, "uvicorn": 0}

    def _factory_fail(**_k: object):
        calls["factory"] += 1
        raise RuntimeError("bad")

    class _NoRun:
        def run(self, *_a: object, **_k: object):
            calls["uvicorn"] += 1

    monkeypatch.setitem(sys.modules, "uvicorn", _NoRun())
    monkeypatch.setattr(proto, "create_synthetic_clarification_prototype", _factory_fail)
    assert (
        proto.main(
            [
                "--repository-root",
                str(repository),
                "--postgres-profile-id",
                "synthetic-postgres",
            ]
        )
        == 2
    )
    assert calls["uvicorn"] == 0
    _, err = capsys.readouterr()
    assert CLI_FAILURE in err
    assert "synthetic-postgres" not in err
    assert "s3cret" not in err
