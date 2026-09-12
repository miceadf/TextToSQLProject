from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from backend.adaptive.dataset import pseudonym_storage
from backend.adaptive.dataset.conversion_contracts import ConversionStatus, LimitProvenance
from backend.adaptive.dataset.pseudonym_contracts import (
    PseudonymReasonCode,
    PseudonymReviewItem,
    PseudonymSummaryReport,
    PseudonymizedRecord,
)
from backend.adaptive.harness.contracts import QueryIR


@pytest.fixture
def paths(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(repository)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    private = repository / ".schema-adaptive"
    private.mkdir()
    input_path = private / "input.jsonl"
    input_path.write_text("{}\n", encoding="utf-8")
    key_path = private / "key.bin"
    key_path.write_bytes(b"k" * 32)
    return repository, private / "bundle", input_path, key_path


def record(letter: str) -> PseudonymizedRecord:
    marker = letter.upper() * 64
    return PseudonymizedRecord(
        dataset_id="synthetic-dataset",
        profile_id="synthetic-v1",
        profile_version=1,
        ontology_version=1,
        key_id="test-key",
        record_id=f"LIN_{marker}",
        canonical_lineage=f"LIN_{'A' * 64}",
        shape_lineage=f"LIN_{'B' * 64}",
        instruction="Synthetic instruction",
        question=f"<COURSE_{letter.upper()}>",
        query_ir=QueryIR(entity="CourseOffering", select=["Course.name"]),
        conversion_status=ConversionStatus.CONVERTED_WITH_EXECUTION_POLICY,
        limit_provenance=LimitProvenance.EXECUTION_POLICY_DEFAULT,
    )


def report(records: list[PseudonymizedRecord]) -> PseudonymSummaryReport:
    jsonl = b"".join(
        pseudonym_storage._json_bytes(item.model_dump(mode="json"), compact=True)
        for item in sorted(records, key=lambda item: item.record_id)
    )
    return PseudonymSummaryReport(
        dataset_id="synthetic-dataset",
        dataset_fingerprint="c" * 64,
        input_artifact_sha256="d" * 64,
        output_artifact_sha256=hashlib.sha256(jsonl).hexdigest(),
        profile_id="synthetic-v1",
        profile_version=1,
        ontology_version=1,
        key_id="test-key",
        input_record_count=len(records) + 1,
        output_record_count=len(records),
        review_record_count=1,
        unsupported_record_count=0,
        status_counts={"PSEUDONYMIZED_EXACT": len(records), "REVIEW_REQUIRED": 1},
        known_value_leak_count=0,
    )


def write(
    paths: tuple[Path, Path, Path, Path],
    *,
    records: list[PseudonymizedRecord] | None = None,
    overwrite: bool = False,
) -> Path:
    repository, output, input_path, key_path = paths
    values = records if records is not None else [record("b"), record("a")]
    return pseudonym_storage.write_pseudonym_bundle(
        output,
        values,
        report(values),
        [PseudonymReviewItem(source_line=7, reason_codes=[PseudonymReasonCode.EMPTY_SENSITIVE_VALUE])],
        input_path=input_path,
        key_path=key_path,
        overwrite=overwrite,
        repository_root=repository,
    )


def test_complete_deterministic_generation(paths: tuple[Path, Path, Path, Path]) -> None:
    manifest_path = write(paths)
    first_manifest = manifest_path.read_bytes()
    manifest = json.loads(first_manifest)
    assert manifest["schema_version"] == "pseudonymization-bundle-v1"
    generation = manifest_path.parent / "generations" / manifest["generation_id"]
    assert sorted(manifest["artifacts"]) == sorted(
        [
            "pseudonymized-query-ir.jsonl",
            "pseudonymization-summary.json",
            "pseudonymization-review.json",
        ]
    )
    rows = (generation / "pseudonymized-query-ir.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(row)["record_id"] for row in rows] == [
        "LIN_" + "A" * 64,
        "LIN_" + "B" * 64,
    ]
    assert json.loads((generation / "pseudonymization-review.json").read_text()) == [
        {"input_line": None, "reason_codes": ["EMPTY_SENSITIVE_VALUE"], "source_line": 7}
    ]
    for name, metadata in manifest["artifacts"].items():
        content = (generation / name).read_bytes()
        assert metadata["sha256"] == hashlib.sha256(content).hexdigest()
        assert metadata["bytes"] == len(content)
    assert b"source_line" not in first_manifest
    assert b"COURSE_A" not in first_manifest

    write(paths, records=[record("a"), record("b")], overwrite=True)
    assert manifest_path.read_bytes() == first_manifest
    assert len(list((manifest_path.parent / "generations").iterdir())) == 1


def test_nonoverwrite_and_changed_generation(paths: tuple[Path, Path, Path, Path]) -> None:
    manifest_path = write(paths)
    before = json.loads(manifest_path.read_text())
    with pytest.raises(FileExistsError):
        write(paths)
    write(paths, records=[record("c")], overwrite=True)
    after = json.loads(manifest_path.read_text())
    assert after["generation_id"] != before["generation_id"]
    assert (manifest_path.parent / "generations" / before["generation_id"]).is_dir()


def test_existing_generation_corruption_preserves_manifest(
    paths: tuple[Path, Path, Path, Path],
) -> None:
    manifest_path = write(paths)
    original = manifest_path.read_bytes()
    generation_id = json.loads(original)["generation_id"]
    artifact = manifest_path.parent / "generations" / generation_id / "pseudonymized-query-ir.jsonl"
    artifact.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        write(paths, overwrite=True)
    assert manifest_path.read_bytes() == original


def test_publish_failure_preserves_previous_commit(
    paths: tuple[Path, Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = write(paths)
    original = manifest_path.read_bytes()
    old_generation = manifest_path.parent / "generations" / json.loads(original)["generation_id"]

    def fail_publish(*_args: object, **_kwargs: object) -> None:
        raise OSError("synthetic manifest failure")

    monkeypatch.setattr(pseudonym_storage, "_publish_manifest", fail_publish)
    with pytest.raises(OSError, match="synthetic manifest failure"):
        write(paths, records=[record("c")], overwrite=True)
    assert manifest_path.read_bytes() == original
    assert old_generation.is_dir()


def test_rejects_outside_path_and_input_inside_output(
    paths: tuple[Path, Path, Path, Path],
) -> None:
    repository, output, input_path, key_path = paths
    with pytest.raises(ValueError, match="inside repository"):
        pseudonym_storage.write_pseudonym_bundle(
            repository / "public", [], report([]), [],
            input_path=input_path, key_path=key_path, repository_root=repository,
        )
    output.mkdir()
    nested_input = output / "input.jsonl"
    nested_input.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="outside output"):
        pseudonym_storage.write_pseudonym_bundle(
            output, [], report([]), [],
            input_path=nested_input, key_path=key_path, repository_root=repository,
        )


def test_rejects_manifest_hardlinked_to_key(paths: tuple[Path, Path, Path, Path]) -> None:
    _repository, output, _input_path, key_path = paths
    output.mkdir()
    os.link(key_path, output / "manifest.json")
    before = key_path.read_bytes()
    with pytest.raises(ValueError, match="aliases"):
        write(paths, overwrite=True)
    assert key_path.read_bytes() == before


def test_rejects_git_tracked_private_target(paths: tuple[Path, Path, Path, Path]) -> None:
    repository, output, _input_path, _key_path = paths
    output.mkdir()
    (output / "tracked.txt").write_text("synthetic", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repository), "add", "-f", ".schema-adaptive/bundle/tracked.txt"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    with pytest.raises(ValueError, match="git-tracked"):
        write(paths)


def test_rejects_wrong_output_hash(paths: tuple[Path, Path, Path, Path]) -> None:
    repository, output, input_path, key_path = paths
    bad = report([record("a")]).model_copy(update={"output_artifact_sha256": "0" * 64})
    with pytest.raises(ValueError, match="fingerprint"):
        pseudonym_storage.write_pseudonym_bundle(
            output, [record("a")], bad, [],
            input_path=input_path, key_path=key_path, repository_root=repository,
        )
    assert not (output / "manifest.json").exists()


def test_loader_issue_review_is_stored_deterministically(
    paths: tuple[Path, Path, Path, Path],
) -> None:
    repository, output, input_path, key_path = paths
    values = [record("a")]
    manifest_path = pseudonym_storage.write_pseudonym_bundle(
        output,
        values,
        report(values),
        [
            PseudonymReviewItem(
                input_line=5,
                reason_codes=[PseudonymReasonCode.INPUT_SCHEMA_VERSION_MISMATCH],
            ),
            PseudonymReviewItem(
                source_line=7,
                reason_codes=[PseudonymReasonCode.EMPTY_SENSITIVE_VALUE],
            ),
        ],
        input_path=input_path,
        key_path=key_path,
        repository_root=repository,
    )
    manifest = json.loads(manifest_path.read_text())
    review_path = (
        manifest_path.parent
        / "generations"
        / manifest["generation_id"]
        / "pseudonymization-review.json"
    )
    assert [item["source_line"] for item in json.loads(review_path.read_text())] == [7, None]


def test_rejects_key_aliasing_input(paths: tuple[Path, Path, Path, Path]) -> None:
    repository, output, input_path, _key_path = paths
    alias_key = repository / ".schema-adaptive" / "alias-key.bin"
    os.link(input_path, alias_key)
    with pytest.raises(ValueError, match="distinct files"):
        pseudonym_storage.write_pseudonym_bundle(
            output, [], report([]), [],
            input_path=input_path, key_path=alias_key, repository_root=repository,
        )


def test_rejects_output_directory_symlink(
    paths: tuple[Path, Path, Path, Path],
) -> None:
    repository, output, _input_path, _key_path = paths
    redirected = repository / ".schema-adaptive" / "redirected"
    redirected.mkdir()
    try:
        output.symlink_to(redirected, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks unavailable")
    with pytest.raises(ValueError, match="symlink"):
        write(paths)
