"""Focused tests for Phase 6-17 private SQLite registration boundary."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.adaptive.api.database_registry import SyntheticDatabaseRegistry
from backend.adaptive.fixtures.flat import build_flat_database
from backend.adaptive.fixtures.legacy import build_legacy_database
from backend.adaptive.fixtures.seed import DEFAULT_SEED
from backend.adaptive.registration import (
    REGISTRATION_STATUS,
    RegistrationError,
    RegistrationErrorCode,
    load_registration,
    register_database,
)
from backend.adaptive.registration import storage as registration_storage


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(root)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (root / ".gitignore").write_text(".schema-adaptive/\n", encoding="utf-8")
    return root


@pytest.fixture
def flat_source(tmp_path: Path) -> Path:
    sources = tmp_path / "sources"
    sources.mkdir()
    return build_flat_database(sources / "flat.sqlite3", seed=DEFAULT_SEED)


@pytest.fixture
def legacy_source(tmp_path: Path) -> Path:
    sources = tmp_path / "sources"
    sources.mkdir(exist_ok=True)
    return build_legacy_database(sources / "legacy.sqlite3", seed=DEFAULT_SEED)


def _private_root(repository: Path) -> Path:
    return repository / ".schema-adaptive" / "registered-databases"


def _raw_texts(manifest_path: Path) -> bytes:
    final_dir = manifest_path.parent
    blob = b""
    for name in ("database.sqlite3", "catalog.json", "proposal.json", "registration.json"):
        blob += (final_dir / name).read_bytes()
    return blob


def _fixed_registry_snapshot() -> list[dict[str, str]]:
    registry = SyntheticDatabaseRegistry(
        [
            {
                "database_id": "synth-clarification-prototype",
                "label": "Synthetic flat (2026 seed)",
                "schema": "flat",
            },
            {
                "database_id": "synth-clarification-legacy",
                "label": "Synthetic legacy (2026 seed)",
                "schema": "legacy",
            },
        ]
    )
    return registry.snapshot()


def test_register_flat_is_deterministic_without_samples_or_source_path(
    repository: Path, flat_source: Path
) -> None:
    first = register_database(
        repository_root=repository,
        source_path=flat_source,
        database_id="synth-flat-copy-a",
        label="Flat copy A",
    )
    second = register_database(
        repository_root=repository,
        source_path=flat_source,
        database_id="synth-flat-copy-b",
        label="Flat copy B",
    )
    loaded_a = load_registration(repository_root=repository, database_id="synth-flat-copy-a")
    loaded_b = load_registration(repository_root=repository, database_id="synth-flat-copy-b")

    assert loaded_a.manifest.status == REGISTRATION_STATUS == "PENDING_REVIEW"
    assert loaded_a.manifest.content_sha256 == hashlib.sha256(flat_source.read_bytes()).hexdigest()
    assert loaded_a.manifest.content_bytes == flat_source.stat().st_size
    assert loaded_b.manifest.content_sha256 == loaded_a.manifest.content_sha256
    assert loaded_b.catalog.schema_fingerprint == loaded_a.catalog.schema_fingerprint
    assert loaded_b.proposal.model_dump(mode="json")["candidates"] == loaded_a.proposal.model_dump(
        mode="json"
    )["candidates"]
    assert (first.parent / "database.sqlite3").read_bytes() == flat_source.read_bytes()
    assert second.parent != first.parent

    for loaded in (loaded_a, loaded_b):
        for table in loaded.catalog.tables:
            for column in table.columns:
                assert column.sample_values == []

    blob = _raw_texts(first) + _raw_texts(second)
    assert str(flat_source).encode() not in blob
    assert flat_source.name.encode() not in blob
    with pytest.raises(RegistrationError) as exc_info:
        register_database(
            repository_root=repository,
            source_path=flat_source,
            database_id="BAD ID!!",
            label="x",
        )
    assert exc_info.value.code is RegistrationErrorCode.INVALID_INPUT
    assert str(flat_source) not in str(exc_info.value)
    assert "sqlite" not in str(exc_info.value).lower()


def test_strict_load_round_trip_keeps_phase616_registry_identical(
    repository: Path, legacy_source: Path
) -> None:
    before = _fixed_registry_snapshot()
    manifest_path = register_database(
        repository_root=repository,
        source_path=legacy_source,
        database_id="synth-legacy-private",
        label="Legacy private",
    )
    loaded = load_registration(
        repository_root=repository, database_id="synth-legacy-private"
    )
    assert manifest_path.name == "registration.json"
    assert loaded.manifest.status == "PENDING_REVIEW"
    assert loaded.manifest.database_id == "synth-legacy-private"
    assert loaded.catalog.database_id == "synth-legacy-private"
    assert loaded.proposal.database_id == "synth-legacy-private"
    after = _fixed_registry_snapshot()
    assert after == before
    assert [entry["database_id"] for entry in after] == [
        "synth-clarification-prototype",
        "synth-clarification-legacy",
    ]
    probe = SyntheticDatabaseRegistry(before)
    assert not probe.is_known("synth-legacy-private")
    assert registration_storage.RESERVED_SYNTHETIC_DATABASE_IDS == frozenset(
        {entry["database_id"] for entry in before}
    )


@pytest.mark.parametrize(
    ("kind", "code"),
    [
        ("empty", RegistrationErrorCode.INVALID_SQLITE),
        ("forged-header", RegistrationErrorCode.INVALID_SQLITE),
        ("corrupt", RegistrationErrorCode.INVALID_SQLITE),
        ("oversize", RegistrationErrorCode.SOURCE_TOO_LARGE),
        ("sidecar-journal", RegistrationErrorCode.UNSAFE_SOURCE),
        ("sidecar-wal", RegistrationErrorCode.UNSAFE_SOURCE),
    ],
)
def test_invalid_sources_rejected_with_fixed_codes(
    repository: Path, flat_source: Path, tmp_path: Path, kind: str, code: RegistrationErrorCode
) -> None:
    work = tmp_path / "bad-sources"
    work.mkdir()
    if kind == "empty":
        source = work / "empty.sqlite3"
        source.write_bytes(b"")
    elif kind == "forged-header":
        source = work / "forged.sqlite3"
        source.write_bytes(b"not a sqlite database" * 64)
    elif kind == "corrupt":
        source = work / "corrupt.sqlite3"
        raw = bytearray(flat_source.read_bytes())
        del raw[512:]
        source.write_bytes(bytes(raw))
    elif kind == "oversize":
        source = work / "big.sqlite3"
        with source.open("wb") as stream:
            stream.write(b"SQLite format 3\x00")
            stream.seek(registration_storage.MAX_SOURCE_BYTES)
            stream.write(b"\x00")
    else:
        source = work / "with-sidecar.sqlite3"
        source.write_bytes(flat_source.read_bytes())
        suffix = "-journal" if kind == "sidecar-journal" else "-wal"
        (work / f"with-sidecar.sqlite3{suffix}").write_bytes(b"sidecar")
    database_id = f"bad-source-{kind.replace('_', '-')}"
    with pytest.raises(RegistrationError) as exc_info:
        register_database(
            repository_root=repository,
            source_path=source,
            database_id=database_id,
            label="bad source",
        )
    assert exc_info.value.code is code
    assert str(exc_info.value) == code.value
    assert str(source) not in str(exc_info.value)
    assert not (_private_root(repository) / database_id).exists()


def test_source_symlink_rejected(repository: Path, flat_source: Path, tmp_path: Path) -> None:
    link = tmp_path / "linked.sqlite3"
    try:
        link.symlink_to(flat_source)
    except (OSError, NotImplementedError):
        pytest.skip("file symlinks unavailable")
    with pytest.raises(RegistrationError) as exc_info:
        register_database(
            repository_root=repository,
            source_path=link,
            database_id="symlink-source",
            label="symlink",
        )
    assert exc_info.value.code is RegistrationErrorCode.UNSAFE_SOURCE
    assert not (_private_root(repository) / "symlink-source").exists()


def test_source_hardlink_rejected_where_supported(
    repository: Path, flat_source: Path, tmp_path: Path
) -> None:
    link = tmp_path / "hardlinked.sqlite3"
    try:
        os.link(flat_source, link)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("hardlinks unavailable")
    with pytest.raises(RegistrationError) as exc_info:
        register_database(
            repository_root=repository,
            source_path=link,
            database_id="hardlink-source",
            label="hardlink",
        )
    assert exc_info.value.code is RegistrationErrorCode.UNSAFE_SOURCE


def test_private_target_redirect_path_escape_and_git_boundaries(
    repository: Path, flat_source: Path, tmp_path: Path
) -> None:
    private_root = _private_root(repository)
    private_root.mkdir(parents=True, exist_ok=True)
    redirect_target = tmp_path / "redirected"
    redirect_target.mkdir()
    link = private_root / "redirected-id"
    try:
        link.symlink_to(redirect_target, target_is_directory=True)
    except (OSError, NotImplementedError):
        link_created = False
    else:
        link_created = True
    if link_created:
        with pytest.raises(RegistrationError) as exc_info:
            register_database(
                repository_root=repository,
                source_path=flat_source,
                database_id="redirected-id",
                label="redirected",
            )
        assert exc_info.value.code is RegistrationErrorCode.PRIVATE_STORAGE_INVALID

    for bad_id in ("../escape", "a/b", "..", ""):
        with pytest.raises(RegistrationError) as exc_info:
            register_database(
                repository_root=repository,
                source_path=flat_source,
                database_id=bad_id,
                label="escape",
            )
        assert exc_info.value.code is RegistrationErrorCode.INVALID_INPUT

    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    with pytest.raises(RegistrationError) as exc_info:
        register_database(
            repository_root=outside,
            source_path=flat_source,
            database_id="outside-repo",
            label="outside",
        )
    assert exc_info.value.code is RegistrationErrorCode.PRIVATE_STORAGE_INVALID
    unignored = tmp_path / "plain-repo"
    unignored.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(unignored)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with pytest.raises(RegistrationError) as exc_info:
        register_database(
            repository_root=unignored,
            source_path=flat_source,
            database_id="unignored-id",
            label="unignored",
        )
    assert exc_info.value.code is RegistrationErrorCode.PRIVATE_STORAGE_INVALID

    tracked_dir = private_root / "tracked-id"
    tracked_dir.mkdir(exist_ok=True)
    (tracked_dir / "placeholder.txt").write_text("tracked", encoding="utf-8")
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "add",
            "-f",
            "--",
            ".schema-adaptive/registered-databases/tracked-id/placeholder.txt",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with pytest.raises(RegistrationError) as exc_info:
        register_database(
            repository_root=repository,
            source_path=flat_source,
            database_id="tracked-id",
            label="tracked",
        )
    assert exc_info.value.code is RegistrationErrorCode.PRIVATE_STORAGE_INVALID


def test_redirected_private_parent_is_rejected_before_directory_creation(
    repository: Path,
    flat_source: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_parent = repository / ".schema-adaptive"
    original = registration_storage._is_redirect

    def _redirect_private_parent(path: Path) -> bool:
        return path == private_parent or original(path)

    monkeypatch.setattr(registration_storage, "_is_redirect", _redirect_private_parent)
    with pytest.raises(RegistrationError) as exc_info:
        register_database(
            repository_root=repository,
            source_path=flat_source,
            database_id="redirected-parent",
            label="redirected parent",
        )
    assert exc_info.value.code is RegistrationErrorCode.PRIVATE_STORAGE_INVALID
    assert not private_parent.exists()


def test_existing_id_and_reserved_ids_conflict(
    repository: Path, flat_source: Path
) -> None:
    register_database(
        repository_root=repository,
        source_path=flat_source,
        database_id="duplicate-id",
        label="first",
    )
    with pytest.raises(RegistrationError) as exc_info:
        register_database(
            repository_root=repository,
            source_path=flat_source,
            database_id="duplicate-id",
            label="second",
        )
    assert exc_info.value.code is RegistrationErrorCode.REGISTRATION_CONFLICT
    for reserved in (
        "synth-clarification-prototype",
        "synth-clarification-legacy",
        "con",
        "COM1",
    ):
        with pytest.raises(RegistrationError):
            register_database(
                repository_root=repository,
                source_path=flat_source,
                database_id=reserved,
                label="reserved",
            )
    for bad_label in ("   ", "has\x00control", "x" * 65, ""):
        with pytest.raises(RegistrationError) as exc_info:
            register_database(
                repository_root=repository,
                source_path=flat_source,
                database_id="label-check",
                label=bad_label,
            )
        assert exc_info.value.code is RegistrationErrorCode.INVALID_INPUT


def _rewrite_manifest(manifest_path: Path, mutate: dict) -> None:
    payload = json.loads(manifest_path.read_bytes())
    payload.update(mutate)
    manifest_path.write_bytes(
        (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )


def test_artifact_and_manifest_tampering_fails_closed(
    repository: Path, flat_source: Path
) -> None:
    manifest_path = register_database(
        repository_root=repository,
        source_path=flat_source,
        database_id="tamper-target",
        label="tamper",
    )
    final_dir = manifest_path.parent

    (final_dir / "database.sqlite3").write_bytes(
        (final_dir / "database.sqlite3").read_bytes() + b"\x00"
    )
    with pytest.raises(RegistrationError) as exc_info:
        load_registration(repository_root=repository, database_id="tamper-target")
    assert exc_info.value.code is RegistrationErrorCode.ARTIFACT_MISMATCH


    (final_dir / "database.sqlite3").write_bytes(flat_source.read_bytes())
    load_registration(repository_root=repository, database_id="tamper-target")

    (final_dir / "catalog.json").rename(final_dir / "catalog.json.bak")
    try:
        with pytest.raises(RegistrationError) as exc_info:
            load_registration(repository_root=repository, database_id="tamper-target")
        assert exc_info.value.code is RegistrationErrorCode.ARTIFACT_MISMATCH
    finally:
        (final_dir / "catalog.json.bak").rename(final_dir / "catalog.json")

    (final_dir / "extra.txt").write_text("extra", encoding="utf-8")
    try:
        with pytest.raises(RegistrationError) as exc_info:
            load_registration(repository_root=repository, database_id="tamper-target")
        assert exc_info.value.code is RegistrationErrorCode.ARTIFACT_MISMATCH
    finally:
        (final_dir / "extra.txt").unlink()

    manifest = json.loads(manifest_path.read_bytes())
    for mutate in (
        {"database_id": "other-id"},
        {"schema_fingerprint": "0" * 64},
        {"ontology_version": manifest["ontology_version"] + 1},
        {"content_sha256": "f" * 64},
        {"status": "PUBLISHED"},
    ):
        _rewrite_manifest(manifest_path, mutate)
        with pytest.raises(RegistrationError) as exc_info:
            load_registration(repository_root=repository, database_id="tamper-target")
        assert exc_info.value.code is RegistrationErrorCode.ARTIFACT_MISMATCH

    payload = json.loads((final_dir / "registration.json").read_bytes())
    payload["artifacts"][1]["path"] = "catalog-renamed.json"
    manifest_path.write_bytes(
        (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )
    with pytest.raises(RegistrationError) as exc_info:
        load_registration(repository_root=repository, database_id="tamper-target")
    assert exc_info.value.code is RegistrationErrorCode.ARTIFACT_MISMATCH


def test_strict_loader_rejects_extra_catalog_fields_even_when_rehashed(
    repository: Path, flat_source: Path
) -> None:
    manifest_path = register_database(
        repository_root=repository,
        source_path=flat_source,
        database_id="extra-field-tamper",
        label="extra field tamper",
    )
    catalog_path = manifest_path.parent / "catalog.json"
    catalog_payload = json.loads(catalog_path.read_bytes())
    catalog_payload["unexpected"] = "ignored-by-default-pydantic"
    catalog_raw = (
        json.dumps(catalog_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    catalog_path.write_bytes(catalog_raw)
    manifest_payload = json.loads(manifest_path.read_bytes())
    for artifact in manifest_payload["artifacts"]:
        if artifact["path"] == "catalog.json":
            artifact["sha256"] = hashlib.sha256(catalog_raw).hexdigest()
            artifact["bytes"] = len(catalog_raw)
    manifest_path.write_bytes(
        (
            json.dumps(
                manifest_payload, ensure_ascii=False, sort_keys=True, indent=2
            )
            + "\n"
        ).encode()
    )

    with pytest.raises(RegistrationError) as exc_info:
        load_registration(repository_root=repository, database_id="extra-field-tamper")
    assert exc_info.value.code is RegistrationErrorCode.ARTIFACT_MISMATCH


def test_schema_change_with_rehashed_manifest_still_fails_closed(
    repository: Path, flat_source: Path
) -> None:
    manifest_path = register_database(
        repository_root=repository,
        source_path=flat_source,
        database_id="schema-tamper",
        label="schema tamper",
    )
    final_dir = manifest_path.parent
    connection = sqlite3.connect(final_dir / "database.sqlite3")
    try:
        connection.execute("ALTER TABLE course_offerings ADD COLUMN injected TEXT")
        connection.commit()
    finally:
        connection.close()
    raw = (final_dir / "database.sqlite3").read_bytes()
    payload = json.loads(manifest_path.read_bytes())
    payload["content_sha256"] = hashlib.sha256(raw).hexdigest()
    payload["content_bytes"] = len(raw)
    for artifact in payload["artifacts"]:
        if artifact["path"] == "database.sqlite3":
            artifact["sha256"] = hashlib.sha256(raw).hexdigest()
            artifact["bytes"] = len(raw)
    manifest_path.write_bytes(
        (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )
    with pytest.raises(RegistrationError) as exc_info:
        load_registration(repository_root=repository, database_id="schema-tamper")
    assert exc_info.value.code is RegistrationErrorCode.ARTIFACT_MISMATCH


def test_failures_leave_no_published_dir_or_staging_residue(
    repository: Path, flat_source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "corrupt-work"
    work.mkdir()
    corrupt = work / "corrupt.sqlite3"
    raw = bytearray(flat_source.read_bytes())
    del raw[512:]
    corrupt.write_bytes(bytes(raw))
    with pytest.raises(RegistrationError) as exc_info:
        register_database(
            repository_root=repository,
            source_path=corrupt,
            database_id="failed-inspection",
            label="failed",
        )
    assert exc_info.value.code is RegistrationErrorCode.INVALID_SQLITE
    assert not (_private_root(repository) / "failed-inspection").exists()

    def _broken_align(self, catalog, ontology):
        raise RuntimeError("synthetic mapping failure")

    with monkeypatch.context() as patch_align:
        patch_align.setattr(
            registration_storage.RuleBasedSchemaAligner, "align", _broken_align
        )
        with pytest.raises(RegistrationError) as exc_info:
            register_database(
                repository_root=repository,
                source_path=flat_source,
                database_id="failed-mapping",
                label="failed",
            )
        assert exc_info.value.code is RegistrationErrorCode.MAPPING_FAILED
        assert not (_private_root(repository) / "failed-mapping").exists()

    real_rename = os.rename

    def _broken_rename(source, destination):
        raise OSError("synthetic rename failure")

    monkeypatch.setattr(os, "rename", _broken_rename)
    try:
        with pytest.raises(RegistrationError) as exc_info:
            register_database(
                repository_root=repository,
                source_path=flat_source,
                database_id="failed-write",
                label="failed",
            )
        assert exc_info.value.code is RegistrationErrorCode.STORAGE_ERROR
    finally:
        monkeypatch.setattr(os, "rename", real_rename)
    assert not (_private_root(repository) / "failed-write").exists()

    leftovers = [
        item.name
        for item in _private_root(repository).iterdir()
        if item.name.startswith((".staging-", ".lock-"))
    ]
    assert leftovers == []


def test_concurrent_same_id_registration_allows_exactly_one(
    repository: Path, flat_source: Path
) -> None:
    barrier = threading.Barrier(4)
    outcomes: list[str] = []
    lock = threading.Lock()

    def _attempt(index: int) -> None:
        barrier.wait(timeout=30)
        try:
            register_database(
                repository_root=repository,
                source_path=flat_source,
                database_id="concurrent-id",
                label=f"attempt {index}",
            )
            result = "ok"
        except RegistrationError as exc:
            result = exc.code.value
        except Exception:  # noqa: BLE001 - any leak fails the test below
            result = "UNEXPECTED"
        with lock:
            outcomes.append(result)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(_attempt, range(4)))
    assert sorted(outcomes).count("ok") == 1
    assert sorted(outcomes).count("REGISTRATION_CONFLICT") == 3
    loaded = load_registration(repository_root=repository, database_id="concurrent-id")
    assert loaded.manifest.status == "PENDING_REVIEW"
    leftovers = [
        item.name
        for item in _private_root(repository).iterdir()
        if item.name.startswith((".staging-", ".lock-"))
    ]
    assert leftovers == []


def test_error_strings_never_carry_paths_or_rows(
    repository: Path, flat_source: Path
) -> None:
    for kwargs in (
        {"database_id": "BAD!!", "label": "x"},
        {"database_id": "ok-id", "label": "x" * 65},
    ):
        with pytest.raises(RegistrationError) as exc_info:
            register_database(
                repository_root=repository, source_path=flat_source, **kwargs
            )
        assert str(exc_info.value) == exc_info.value.code.value
        assert str(flat_source) not in str(exc_info.value)
        assert "Traceback" not in str(exc_info.value)
    assert DEFAULT_SEED.offerings[0].course_name not in str(
        RegistrationError(RegistrationErrorCode.INVALID_SQLITE)
    )
