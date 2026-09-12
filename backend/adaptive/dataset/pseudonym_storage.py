"""Atomic, private bundle storage for pseudonymized Query IR artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Sequence

from pydantic import BaseModel

from .pseudonym_contracts import (
    PseudonymReviewItem,
    PseudonymSummaryReport,
    PseudonymizedRecord,
)


BUNDLE_SCHEMA_VERSION = "pseudonymization-bundle-v1"
STORAGE_TOOL_VERSION = "pseudonymization-storage-v1"
_ARTIFACT_NAMES = (
    "pseudonymized-query-ir.jsonl",
    "pseudonymization-summary.json",
    "pseudonymization-review.json",
)


def write_pseudonym_bundle(
    output_dir: str | Path,
    records: Sequence[PseudonymizedRecord],
    summary_report: PseudonymSummaryReport,
    review_items: Sequence[PseudonymReviewItem],
    *,
    input_path: str | Path,
    key_path: str | Path,
    overwrite: bool = False,
    repository_root: str | Path,
) -> Path:
    """Commit a complete generation by publishing its manifest last.

    The manifest is the only commit marker. An interrupted write can leave an
    unreferenced generation, but cannot expose a mixture of generations.
    """

    if not isinstance(summary_report, PseudonymSummaryReport):
        raise TypeError("summary report must use the pseudonymization contract")
    if any(not isinstance(item, PseudonymizedRecord) for item in records):
        raise TypeError("records must use the pseudonymized record contract")
    if any(not isinstance(item, PseudonymReviewItem) for item in review_items):
        raise TypeError("review items must use the pseudonymization review contract")

    try:
        root = Path(repository_root).resolve(strict=True)
        target = _validate_target(Path(output_dir), root)
        inputs = (
            Path(input_path).resolve(strict=True),
            Path(key_path).resolve(strict=True),
        )
    except OSError:
        raise ValueError("unable to validate private bundle paths") from None
    if not all(path.is_file() for path in inputs):
        raise ValueError("input and key must be regular files")
    _reject_git_tracked_target(target, root)
    _reject_collisions(target, inputs)

    manifest_path = target / "manifest.json"
    if manifest_path.exists() and not overwrite:
        raise FileExistsError("bundle already exists; use --force to replace it")

    ordered_records = sorted(records, key=lambda item: str(item.record_id))
    record_ids = [str(item.record_id) for item in ordered_records]
    if len(record_ids) != len(set(record_ids)):
        raise ValueError("pseudonymized record ids must be unique")
    ordered_review = sorted(
        review_items,
        key=lambda item: (
            item.source_line is None,
            item.source_line or 0,
            item.input_line or 0,
        ),
    )
    review_origins = [
        (item.source_line, item.input_line) for item in ordered_review
    ]
    if len(review_origins) != len(set(review_origins)):
        raise ValueError("review origins must be unique")

    report_data = _json_data(summary_report)
    for item in ordered_records:
        if (
            item.dataset_id != summary_report.dataset_id
            or item.profile_id != summary_report.profile_id
            or item.profile_version != summary_report.profile_version
            or item.ontology_version != summary_report.ontology_version
            or item.key_id != summary_report.key_id
        ):
            raise ValueError("record and summary metadata mismatch")

    contents = {
        _ARTIFACT_NAMES[0]: b"".join(
            _json_bytes(_json_data(item), compact=True)
            for item in ordered_records
        ),
        _ARTIFACT_NAMES[1]: _json_bytes(report_data),
        _ARTIFACT_NAMES[2]: _json_bytes(
            [_json_data(item) for item in ordered_review]
        ),
    }
    output_hash = hashlib.sha256(contents[_ARTIFACT_NAMES[0]]).hexdigest()
    if summary_report.output_artifact_sha256 != output_hash:
        raise ValueError("summary output fingerprint does not match JSONL")
    content_hashes = {
        name: hashlib.sha256(contents[name]).hexdigest()
        for name in _ARTIFACT_NAMES
    }
    generation_material = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "tool_version": STORAGE_TOOL_VERSION,
        "profile_id": report_data["profile_id"],
        "profile_version": report_data["profile_version"],
        "key_id": report_data["key_id"],
        "content_sha256": content_hashes,
    }
    generation_id = hashlib.sha256(
        _json_bytes(generation_material, compact=True)
    ).hexdigest()
    generation_relative = Path("generations") / generation_id
    manifest = {
        **generation_material,
        "generation_id": generation_id,
        "report_schema_version": report_data["schema_version"],
        "artifacts": {
            name: {
                "path": (generation_relative / name).as_posix(),
                "sha256": content_hashes[name],
                "bytes": len(contents[name]),
            }
            for name in _ARTIFACT_NAMES
        },
    }
    manifest_bytes = _json_bytes(manifest)

    generation_root = target / "generations"
    if generation_root.is_symlink():
        raise ValueError("generation directory must not be a symlink")
    generation_root.mkdir(parents=True, exist_ok=True)
    if generation_root.resolve() != generation_root:
        raise ValueError("generation directory must not redirect elsewhere")
    generation = generation_root / generation_id
    _reject_collisions(target, inputs, candidate_paths=(manifest_path, generation))
    if generation.exists() or generation.is_symlink():
        _verify_existing_generation(generation, contents, inputs)
    else:
        temp_generation = Path(
            tempfile.mkdtemp(prefix=".pending-", dir=generation_root)
        )
        try:
            for name in _ARTIFACT_NAMES:
                path = temp_generation / name
                _write_new_file(path, contents[name])
                _verify_file(path, contents[name], inputs)
            _fsync_directory(temp_generation)
            os.replace(temp_generation, generation)
            _fsync_directory(generation_root)
        finally:
            if temp_generation.exists():
                shutil.rmtree(temp_generation)

    _reject_collisions(target, inputs, candidate_paths=(manifest_path, generation))
    _verify_existing_generation(generation, contents, inputs)
    _publish_manifest(manifest_path, manifest_bytes, overwrite=overwrite)
    return manifest_path


def _json_data(value: BaseModel) -> Any:
    return value.model_dump(mode="json")


def _json_bytes(value: Any, *, compact: bool = False) -> bytes:
    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":") if compact else None,
        indent=None if compact else 2,
        allow_nan=False,
    )
    return (text + "\n").encode("utf-8")


def _validate_target(target: Path, repository_root: Path) -> Path:
    private_root = repository_root / ".schema-adaptive"
    lexical = Path(os.path.abspath(target))
    resolved = target.resolve(strict=False)
    for candidate in (lexical, resolved):
        try:
            relative = candidate.relative_to(private_root)
        except ValueError as exc:
            raise ValueError(
                "output directory must be inside repository .schema-adaptive"
            ) from exc
        if not relative.parts:
            raise ValueError("output directory must be below .schema-adaptive")
    if private_root.exists() and private_root.is_symlink():
        raise ValueError("private workspace must not be a symlink")
    if target.exists() and target.is_symlink():
        raise ValueError("output directory must not be a symlink")
    return resolved


def _reject_git_tracked_target(target: Path, repository_root: Path) -> None:
    relative = target.relative_to(repository_root)
    result = subprocess.run(
        ["git", "-C", str(repository_root), "ls-files", "-z", "--", relative.as_posix()],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("cannot verify git tracking for private output")
    if result.stdout:
        raise ValueError("private output must not contain git-tracked files")


def _reject_collisions(
    target: Path,
    inputs: tuple[Path, Path],
    *,
    candidate_paths: Sequence[Path] = (),
) -> None:
    if inputs[0] == inputs[1] or os.path.samefile(*inputs):
        raise ValueError("input and key must be distinct files")
    for source in inputs:
        if source == target or target in source.parents:
            raise ValueError("input and key must be outside output directory")
    for candidate in candidate_paths:
        if candidate.is_symlink():
            raise ValueError("output artifacts must not be symlinks")
        if candidate.exists() and candidate.is_file():
            _reject_samefile(candidate, inputs)


def _reject_samefile(path: Path, inputs: tuple[Path, Path]) -> None:
    for source in inputs:
        if os.path.samefile(path, source):
            raise ValueError("output artifact aliases input or key")


def _verify_existing_generation(
    generation: Path,
    contents: dict[str, bytes],
    inputs: tuple[Path, Path],
) -> None:
    if generation.is_symlink() or not generation.is_dir():
        raise ValueError("existing generation is not a private directory")
    if sorted(path.name for path in generation.iterdir()) != sorted(_ARTIFACT_NAMES):
        raise ValueError("existing generation has unexpected artifacts")
    for name in _ARTIFACT_NAMES:
        _verify_file(generation / name, contents[name], inputs)


def _verify_file(path: Path, expected: bytes, inputs: tuple[Path, Path]) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("generation artifact is not a regular file")
    _reject_samefile(path, inputs)
    if path.read_bytes() != expected:
        raise ValueError("existing generation content differs from expected bytes")
    json.loads(expected.decode("utf-8")) if path.suffix == ".json" else _validate_jsonl(expected)


def _validate_jsonl(content: bytes) -> None:
    for line in content.splitlines():
        parsed = json.loads(line)
        if not isinstance(parsed, dict):
            raise ValueError("record JSONL must contain objects")


def _write_new_file(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _publish_manifest(path: Path, content: bytes, *, overwrite: bool) -> None:
    descriptor, temp_name = tempfile.mkstemp(prefix=".manifest-", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temp_path, path)
        else:
            try:
                os.link(temp_path, path)
            except FileExistsError as exc:
                raise FileExistsError("bundle already exists; use --force") from exc
            temp_path.unlink()
        _fsync_directory(path.parent)
    finally:
        temp_path.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return  # Windows does not expose directory handles through os.open.
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
