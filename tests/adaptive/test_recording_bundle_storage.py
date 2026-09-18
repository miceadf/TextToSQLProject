from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.adaptive.harness import (
    FieldMapping,
    HarnessRequest,
    MappingProfile,
    MappingStatus,
    Ontology,
    ProviderKind,
    QueryIR,
    RecordedFailureCode,
    RecordedModel,
    RecordingBundleError,
    RecordingBundleErrorCode,
    RecordingCaptureSession,
    RecordingProvenance,
    load_recording_bundle,
    provenance_fingerprint,
    write_recording_bundle,
)
from backend.adaptive.harness import recording_bundle_storage


PRIVATE_QUESTION = "PRIVATE_SYNTHETIC_QUESTION"
PRIVATE_RESPONSE = "PRIVATE_SYNTHETIC_RESPONSE"


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
def mapping() -> MappingProfile:
    return MappingProfile(
        database_id="fixture-flat-v1",
        version=1,
        status=MappingStatus.PUBLISHED,
        fields={"Course.name": FieldMapping(table="courses", column="name")},
    )


def provenance(*, model_id: str = "synthetic-model-v1") -> RecordingProvenance:
    return RecordingProvenance(
        provider_kind=ProviderKind.SYNTHETIC,
        provider_id="test-provider",
        provider_version="1.0",
        model_id=model_id,
        model_artifact_sha256="a" * 64,
        prompt_template_sha256="b" * 64,
        generation_config_sha256="c" * 64,
        collector_version="test-collector-v1",
    )


def recording(mapping: MappingProfile, *, run_id: str = "bundle-run-v1"):
    success = HarnessRequest(
        request_id=f"{run_id}:P2-001:flat",
        database_id=mapping.database_id,
        question=PRIVATE_QUESTION,
    )
    failure = HarnessRequest(
        request_id=f"{run_id}:P2-002:flat",
        database_id=mapping.database_id,
        question="synthetic failure question",
    )
    session = RecordingCaptureSession(run_id)
    session.record_success(
        success,
        mapping,
        {
            "entity": "Course",
            "select": ["Course.name"],
            "filters": [
                {
                    "field": "Course.name",
                    "operator": "equals",
                    "value": PRIVATE_RESPONSE,
                }
            ],
            "limit": 10,
        },
    )
    session.record_failure(failure, mapping, RecordedFailureCode.TIMEOUT)
    return session.finalize(), success


def output_path(repository: Path, name: str = "bundle") -> Path:
    return repository / ".schema-adaptive" / name


def test_deterministic_bundle_round_trip_and_private_manifest(
    repository: Path,
    mapping: MappingProfile,
) -> None:
    source, success_request = recording(mapping)
    declared = provenance()
    output = output_path(repository)
    manifest_path = write_recording_bundle(
        output, source, declared, repository_root=repository
    )
    first_manifest = manifest_path.read_bytes()
    manifest_json = json.loads(first_manifest)
    generation = output / "generations" / manifest_json["generation_id"]
    recording_path = generation / "recording.json"

    assert manifest_json["schema_version"] == "recording-bundle-v1"
    assert manifest_json["provenance"]["verification_status"] == "DECLARED_UNVERIFIED"
    assert manifest_json["record_count"] == 2
    assert manifest_json["success_count"] == 1
    assert manifest_json["failure_count"] == 1
    assert manifest_json["recording"]["bytes"] == len(recording_path.read_bytes())
    assert manifest_json["recording"]["sha256"] == hashlib.sha256(
        recording_path.read_bytes()
    ).hexdigest()
    assert manifest_json["provenance_sha256"] == provenance_fingerprint(declared)
    assert PRIVATE_QUESTION.encode() not in first_manifest
    assert PRIVATE_RESPONSE.encode() not in first_manifest
    assert str(repository).encode() not in first_manifest
    assert b"prompt" in first_manifest
    assert b"prompt template contents" not in first_manifest

    loaded = load_recording_bundle(manifest_path, repository_root=repository)
    assert loaded.recording == source
    replayed = RecordedModel(loaded.recording).generate_ir(
        success_request, Ontology(entities=set(), fields={}), mapping
    )
    assert QueryIR.model_validate(replayed).filters[0].value == PRIVATE_RESPONSE

    write_recording_bundle(
        output, source, declared, repository_root=repository, overwrite=True
    )
    assert manifest_path.read_bytes() == first_manifest
    assert len(list((output / "generations").iterdir())) == 1


def test_nonoverwrite_and_manifest_only_generation_switch(
    repository: Path,
    mapping: MappingProfile,
) -> None:
    output = output_path(repository)
    first, _ = recording(mapping, run_id="first-run")
    manifest_path = write_recording_bundle(
        output, first, provenance(model_id="model-a"), repository_root=repository
    )
    before = json.loads(manifest_path.read_text(encoding="utf-8"))
    old_generation = output / "generations" / before["generation_id"]
    old_bytes = (old_generation / "recording.json").read_bytes()

    with pytest.raises(FileExistsError):
        write_recording_bundle(
            output, first, provenance(model_id="model-a"), repository_root=repository
        )

    second, _ = recording(mapping, run_id="second-run")
    write_recording_bundle(
        output,
        second,
        provenance(model_id="model-b"),
        repository_root=repository,
        overwrite=True,
    )
    after = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert after["generation_id"] != before["generation_id"]
    assert (old_generation / "recording.json").read_bytes() == old_bytes
    assert len(list((output / "generations").iterdir())) == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("verification_status", "VERIFIED"),
        ("provider_id", "local/provider"),
        ("provider_version", ""),
        ("model_id", "x" * 161),
        ("prompt_template_sha256", "not-a-hash"),
        ("generation_config_sha256", "D" * 64),
        ("model_artifact_sha256", "0" * 63),
    ],
)
def test_provenance_contract_rejects_unsafe_or_invalid_values(
    field: str, value: str
) -> None:
    payload = provenance().model_dump(mode="json")
    payload[field] = value
    with pytest.raises(ValidationError):
        RecordingProvenance.model_validate(payload)


def test_writer_revalidates_constructed_contract_instances(
    repository: Path,
    mapping: MappingProfile,
) -> None:
    source, _ = recording(mapping)
    invalid_provenance = provenance().model_copy(
        update={"verification_status": "VERIFIED"}
    )
    with pytest.raises(RecordingBundleError) as caught:
        write_recording_bundle(
            output_path(repository),
            source,
            invalid_provenance,
            repository_root=repository,
        )
    assert caught.value.code is RecordingBundleErrorCode.INVALID_CONTRACT

    invalid_recording = source.model_copy(update={"records": []})
    with pytest.raises(RecordingBundleError) as caught:
        write_recording_bundle(
            output_path(repository),
            invalid_recording,
            provenance(),
            repository_root=repository,
        )
    assert caught.value.code is RecordingBundleErrorCode.INVALID_CONTRACT
    assert not (output_path(repository) / "manifest.json").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "recording-content",
        "recording-hash",
        "recording-bytes",
        "model-run-id",
        "record-count",
        "provenance-hash",
        "generation-id",
        "relative-path",
        "extra-artifact",
    ],
)
def test_loader_detects_bundle_tampering(
    repository: Path,
    mapping: MappingProfile,
    mutation: str,
) -> None:
    output = output_path(repository, mutation)
    source, _ = recording(mapping)
    manifest_path = write_recording_bundle(
        output, source, provenance(), repository_root=repository
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    generation = output / "generations" / manifest["generation_id"]
    recording_path = generation / "recording.json"

    if mutation == "recording-content":
        recording_path.write_text("{}\n", encoding="utf-8")
    elif mutation == "extra-artifact":
        (generation / "unexpected.txt").write_text("x", encoding="utf-8")
    else:
        if mutation == "recording-hash":
            manifest["recording"]["sha256"] = "0" * 64
        elif mutation == "recording-bytes":
            manifest["recording"]["bytes"] += 1
        elif mutation == "model-run-id":
            manifest["model_run_id"] = "different-run"
        elif mutation == "record-count":
            manifest["record_count"] += 1
            manifest["success_count"] += 1
        elif mutation == "provenance-hash":
            manifest["provenance_sha256"] = "0" * 64
        elif mutation == "generation-id":
            manifest["generation_id"] = "0" * 64
        elif mutation == "relative-path":
            manifest["recording"]["path"] = "../outside/recording.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )

    with pytest.raises(RecordingBundleError) as caught:
        load_recording_bundle(manifest_path, repository_root=repository)
    assert caught.value.code in {
        RecordingBundleErrorCode.INVALID_CONTRACT,
        RecordingBundleErrorCode.INCOMPLETE_BUNDLE,
        RecordingBundleErrorCode.ARTIFACT_MISMATCH,
    }
    assert PRIVATE_RESPONSE not in str(caught.value)
    assert str(repository) not in str(caught.value)


def test_loader_rejects_missing_manifest_and_incomplete_generation(
    repository: Path,
    mapping: MappingProfile,
) -> None:
    output = output_path(repository)
    source, _ = recording(mapping)
    manifest_path = write_recording_bundle(
        output, source, provenance(), repository_root=repository
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_path.unlink()
    with pytest.raises(RecordingBundleError) as caught:
        load_recording_bundle(manifest_path, repository_root=repository)
    assert caught.value.code is RecordingBundleErrorCode.INCOMPLETE_BUNDLE

    manifest_path = write_recording_bundle(
        output, source, provenance(), repository_root=repository
    )
    generation = output / "generations" / json.loads(
        manifest_path.read_text(encoding="utf-8")
    )["generation_id"]
    (generation / "recording.json").unlink()
    with pytest.raises(RecordingBundleError) as caught:
        load_recording_bundle(manifest_path, repository_root=repository)
    assert caught.value.code is RecordingBundleErrorCode.INCOMPLETE_BUNDLE


def test_rejects_outside_root_unignored_and_git_tracked_targets(
    repository: Path,
    mapping: MappingProfile,
) -> None:
    source, _ = recording(mapping)
    declared = provenance()
    with pytest.raises(RecordingBundleError) as caught:
        write_recording_bundle(
            repository / "public", source, declared, repository_root=repository
        )
    assert caught.value.code is RecordingBundleErrorCode.INVALID_PATH
    with pytest.raises(RecordingBundleError) as caught:
        write_recording_bundle(
            repository / ".schema-adaptive",
            source,
            declared,
            repository_root=repository,
        )
    assert caught.value.code is RecordingBundleErrorCode.INVALID_PATH

    unignored = repository / "private-output"
    with pytest.raises(RecordingBundleError) as caught:
        write_recording_bundle(
            unignored, source, declared, repository_root=repository
        )
    assert caught.value.code is RecordingBundleErrorCode.INVALID_PATH

    tracked = output_path(repository, "tracked")
    tracked.mkdir(parents=True)
    (tracked / "tracked.txt").write_text("synthetic", encoding="utf-8")
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "add",
            "-f",
            ".schema-adaptive/tracked/tracked.txt",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with pytest.raises(RecordingBundleError) as caught:
        write_recording_bundle(
            tracked, source, declared, repository_root=repository
        )
    assert caught.value.code is RecordingBundleErrorCode.GIT_TRACKED_TARGET


def test_rejects_unignored_private_workspace(
    tmp_path: Path,
    mapping: MappingProfile,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(root)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    source, _ = recording(mapping)
    with pytest.raises(RecordingBundleError) as caught:
        write_recording_bundle(
            root / ".schema-adaptive" / "bundle",
            source,
            provenance(),
            repository_root=root,
        )
    assert caught.value.code is RecordingBundleErrorCode.PRIVATE_PATH_NOT_IGNORED


def test_symlink_redirect_is_rejected(
    repository: Path,
    mapping: MappingProfile,
) -> None:
    private = repository / ".schema-adaptive"
    private.mkdir()
    redirected = private / "redirected"
    redirected.mkdir()
    output = private / "bundle"
    try:
        output.symlink_to(redirected, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks unavailable")
    source, _ = recording(mapping)
    with pytest.raises(RecordingBundleError) as caught:
        write_recording_bundle(
            output, source, provenance(), repository_root=repository
        )
    assert caught.value.code is RecordingBundleErrorCode.INVALID_PATH


def test_publish_failure_preserves_previous_manifest_and_cleans_temp(
    repository: Path,
    mapping: MappingProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = output_path(repository)
    first, _ = recording(mapping, run_id="first-run")
    manifest_path = write_recording_bundle(
        output, first, provenance(model_id="model-a"), repository_root=repository
    )
    original = manifest_path.read_bytes()

    def fail_publish(*_args: object, **_kwargs: object) -> None:
        raise OSError("PRIVATE_PATH_OR_PAYLOAD")

    monkeypatch.setattr(recording_bundle_storage, "_publish_manifest", fail_publish)
    second, _ = recording(mapping, run_id="second-run")
    with pytest.raises(RecordingBundleError) as caught:
        write_recording_bundle(
            output,
            second,
            provenance(model_id="model-b"),
            repository_root=repository,
            overwrite=True,
        )
    assert caught.value.code is RecordingBundleErrorCode.STORAGE_ERROR
    assert str(caught.value) == "STORAGE_ERROR"
    assert manifest_path.read_bytes() == original
    assert not list((output / "generations").glob(".pending-*"))


def test_manifest_and_generation_symlinks_are_rejected(
    repository: Path,
    mapping: MappingProfile,
) -> None:
    output = output_path(repository)
    source, _ = recording(mapping)
    manifest_path = write_recording_bundle(
        output, source, provenance(), repository_root=repository
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    generation = output / "generations" / manifest["generation_id"]
    external = output.parent / "external-recording.json"
    external.write_bytes((generation / "recording.json").read_bytes())
    (generation / "recording.json").unlink()
    try:
        (generation / "recording.json").symlink_to(external)
    except (OSError, NotImplementedError):
        pytest.skip("file symlinks unavailable")
    with pytest.raises(RecordingBundleError):
        load_recording_bundle(manifest_path, repository_root=repository)


def test_hardlinked_recording_is_rejected_by_writer_and_loader(
    repository: Path,
    mapping: MappingProfile,
) -> None:
    output = output_path(repository)
    source, _ = recording(mapping)
    manifest_path = write_recording_bundle(
        output, source, provenance(), repository_root=repository
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = (
        output
        / "generations"
        / manifest["generation_id"]
        / "recording.json"
    )
    external = repository / ".schema-adaptive" / "external-hardlink-source.json"
    external.write_bytes(artifact.read_bytes())
    artifact.unlink()
    try:
        os.link(external, artifact)
    except OSError:
        pytest.skip("hard links unavailable")

    with pytest.raises(RecordingBundleError) as caught:
        load_recording_bundle(manifest_path, repository_root=repository)
    assert caught.value.code is RecordingBundleErrorCode.INCOMPLETE_BUNDLE
    with pytest.raises(RecordingBundleError) as caught:
        write_recording_bundle(
            output,
            source,
            provenance(),
            repository_root=repository,
            overwrite=True,
        )
    assert caught.value.code is RecordingBundleErrorCode.ARTIFACT_MISMATCH


def test_windows_generation_junction_escape_is_rejected(
    repository: Path,
    mapping: MappingProfile,
) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction test")
    output = output_path(repository)
    source, _ = recording(mapping)
    manifest_path = write_recording_bundle(
        output, source, provenance(), repository_root=repository
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    generation = output / "generations" / manifest["generation_id"]
    external = repository / ".schema-adaptive" / "external-generation"
    shutil.move(str(generation), str(external))
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(generation), str(external)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("junction creation unavailable")
    try:
        with pytest.raises(RecordingBundleError) as caught:
            load_recording_bundle(manifest_path, repository_root=repository)
        assert caught.value.code is RecordingBundleErrorCode.INCOMPLETE_BUNDLE
        with pytest.raises(RecordingBundleError) as caught:
            write_recording_bundle(
                output,
                source,
                provenance(),
                repository_root=repository,
                overwrite=True,
            )
        assert caught.value.code is RecordingBundleErrorCode.ARTIFACT_MISMATCH
    finally:
        os.rmdir(generation)


def test_raw_filesystem_error_is_sanitized(
    repository: Path,
    mapping: MappingProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _ = recording(mapping)

    def fail_layout(*_args: object, **_kwargs: object) -> None:
        raise OSError("C:\\PRIVATE\\bundle")

    monkeypatch.setattr(
        recording_bundle_storage, "_validate_root_layout", fail_layout
    )
    with pytest.raises(RecordingBundleError) as caught:
        write_recording_bundle(
            output_path(repository),
            source,
            provenance(),
            repository_root=repository,
        )
    assert caught.value.code is RecordingBundleErrorCode.INVALID_PATH
    assert "PRIVATE" not in str(caught.value)
