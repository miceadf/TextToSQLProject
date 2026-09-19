from __future__ import annotations

import contextlib
import io
import json
import subprocess
from concurrent.futures import CancelledError
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.adaptive.harness import (
    CaptureTarget, FieldMapping, HarnessRequest, MappingProfile, MappingStatus,
    Ontology, RecordedModel, RecordedModelReplayError,
    RecordingBundleError, load_recording_bundle,
)
from backend.adaptive.harness.live_capture import (
    LiveCaptureError, TARGET_SCHEMA_VERSION, capture_live, load_private_targets, main,
)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text(".schema-adaptive/\n", encoding="utf-8")
    (root / ".schema-adaptive").mkdir()
    return root


def _targets() -> list[CaptureTarget]:
    mapping = MappingProfile(
        database_id="private-db", version=1, status=MappingStatus.PUBLISHED,
        fields={"Course.name": FieldMapping(table="PRIVATE_TABLE", column="PRIVATE_COLUMN")},
    )
    ontology = Ontology(entities={"Course"}, fields={})
    return [
        CaptureTarget(
            request=HarnessRequest(request_id=f"case-{index}", database_id="private-db", question=f"PRIVATE_QUESTION_{index}"),
            ontology=ontology, mapping=mapping,
        )
        for index in range(8)
    ]


def _write_targets(repository: Path, targets: list[CaptureTarget] | None = None) -> Path:
    path = repository / ".schema-adaptive" / "targets.json"
    path.write_text(json.dumps({
        "schema_version": TARGET_SCHEMA_VERSION,
        "targets": [target.model_dump(mode="json") for target in (targets if targets is not None else _targets())],
    }, ensure_ascii=False), encoding="utf-8")
    return path


def _capture(repository: Path, path: Path, transport, *, output: Path | None = None, **changes):
    settings = dict(
        target_file=path, model_tag="exact-model:tag", base_url="http://localhost:11434",
        timeout=7.5, ollama_version="0.34.2", model_digest="a" * 64,
        model_run_id="private-run-v1", repository_root=repository,
        output=output or repository / ".schema-adaptive" / "bundle", transport=transport,
    )
    settings.update(changes)
    return capture_live(**settings)


class SequenceTransport:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []

    def __call__(self, url, payload, timeout):
        self.calls.append((url, payload, timeout))
        outcome = self.outcomes[len(self.calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def test_private_targets_strict_order_and_fields(repository: Path) -> None:
    path = _write_targets(repository)
    assert load_private_targets(path, repository_root=repository) == _targets()


@pytest.mark.parametrize("mutation", [
    lambda data: data.update(schema_version="wrong"),
    lambda data: data.update(targets=[]),
    lambda data: data["targets"].append(data["targets"][0]),
    lambda data: data["targets"][1]["mapping"].update(database_id="different"),
    lambda data: data["targets"][1]["mapping"].update(status="DRAFT"),
    lambda data: data["targets"][1]["request"].pop("request_id"),
    lambda data: data["targets"][1]["request"].update(extra="PRIVATE_VALUE"),
])
def test_bad_batch_blocks_all_calls_and_files(repository: Path, mutation) -> None:
    path = _write_targets(repository)
    data = json.loads(path.read_text(encoding="utf-8"))
    mutation(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    transport = SequenceTransport([])
    with pytest.raises((LiveCaptureError, RecordingBundleError, ValueError)):
        _capture(repository, path, transport)
    assert transport.calls == []
    assert not (repository / ".schema-adaptive" / "bundle").exists()


def test_bad_json_and_nonprivate_input_rejected(repository: Path) -> None:
    path = _write_targets(repository)
    path.write_text('{"schema_version":"capture-targets-v1","schema_version":"capture-targets-v1"}', encoding="utf-8")
    with pytest.raises(LiveCaptureError):
        load_private_targets(path, repository_root=repository)
    outside = repository / "PRIVATE_OUTSIDE.json"
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(RecordingBundleError):
        load_private_targets(outside, repository_root=repository)


def test_preflight_provenance_path_and_existing_bundle(repository: Path) -> None:
    path = _write_targets(repository)
    transport = SequenceTransport([{"response": "null"}] * 8)
    with pytest.raises(ValueError):
        _capture(repository, path, transport, model_digest="bad")
    with pytest.raises(RecordingBundleError):
        _capture(repository, path, transport, output=repository / "outside")
    assert transport.calls == []
    assert not (repository / ".schema-adaptive" / "bundle").exists()
    _capture(repository, path, transport)
    manifest = repository / ".schema-adaptive" / "bundle" / "manifest.json"
    original = manifest.read_bytes()
    another = SequenceTransport([])
    with pytest.raises(FileExistsError):
        _capture(repository, path, another)
    assert another.calls == []
    assert manifest.read_bytes() == original


def test_transport_outcomes_and_private_metadata(repository: Path) -> None:
    path = _write_targets(repository)
    outcomes = [
        {"response": '{"entity":"Course","select":["Course.name"],"secret":"PRIVATE_RESPONSE"}'},
        {"response": "[1,2]"}, {"response": "PRIVATE_SQL_TEXT"},
        TimeoutError("PRIVATE_HTTP_BODY"), {"response": ""},
        CancelledError("PRIVATE_HTTP_BODY"), RuntimeError("PRIVATE_HTTP_BODY"),
        {"response": "null"},
    ]
    transport = SequenceTransport(outcomes)
    assert _capture(repository, path, transport)[:3] == (8, 4, 4)
    assert len(transport.calls) == 8
    for url, payload, timeout in transport.calls:
        assert url == "http://localhost:11434/api/generate"
        assert payload["model"] == "exact-model:tag"
        assert payload["stream"] is False
        assert payload["options"] == {"temperature": 0}
        assert timeout == 7.5
        assert "PRIVATE_TABLE" not in payload["prompt"]
        assert "PRIVATE_COLUMN" not in payload["prompt"]
    bundle = load_recording_bundle(repository / ".schema-adaptive" / "bundle" / "manifest.json", repository_root=repository)
    assert [entry.request_id for entry in bundle.recording.records] == [f"case-{i}" for i in range(8)]
    model = RecordedModel(bundle.recording)
    targets = _targets()
    assert model.generate_ir(targets[0].request, targets[0].ontology, targets[0].mapping)["secret"] == "PRIVATE_RESPONSE"
    assert model.generate_ir(targets[1].request, targets[1].ontology, targets[1].mapping) == [1, 2]
    assert model.generate_ir(targets[2].request, targets[2].ontology, targets[2].mapping) == "PRIVATE_SQL_TEXT"
    assert model.generate_ir(targets[7].request, targets[7].ontology, targets[7].mapping) is None
    for index, code in zip(range(3, 7), ("TIMEOUT", "EMPTY_RESPONSE", "CANCELLED", "PROVIDER_ERROR"), strict=True):
        with pytest.raises(RecordedModelReplayError) as caught:
            model.generate_ir(targets[index].request, targets[index].ontology, targets[index].mapping)
        assert caught.value.code.value == f"RECORDED_{code}"
    metadata = (repository / ".schema-adaptive" / "bundle" / "manifest.json").read_text(encoding="utf-8")
    for secret in ("PRIVATE_QUESTION", "PRIVATE_RESPONSE", "PRIVATE_SQL_TEXT", "PRIVATE_TABLE", "PRIVATE_COLUMN", str(repository)):
        assert secret not in metadata


def test_cli_opt_in_and_safe_error(repository: Path) -> None:
    path = _write_targets(repository)
    args = [
        "--target-file", str(path), "--model-tag", "exact-model:tag",
        "--base-url", "http://localhost:11434", "--timeout", "7.5",
        "--ollama-version", "0.34.2", "--model-run-id", "private-run-v1",
        "--repository-root", str(repository), "--output", str(repository / ".schema-adaptive" / "bundle"),
    ]
    output = io.StringIO()
    with patch("backend.adaptive.harness.live_capture.capture_live", side_effect=AssertionError("CALLED")):
        with contextlib.redirect_stdout(output):
            assert main(args) == 2
    assert json.loads(output.getvalue()) == {"status": "NOT_RUN"}
    assert not (repository / ".schema-adaptive" / "bundle").exists()
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert main(["--live", *args, "--model-digest", "PRIVATE_INVALID_DIGEST"]) == 1
    assert json.loads(output.getvalue()) == {"status": "FAILED", "code": "INVALID_CONFIG"}
    for secret in ("PRIVATE_INVALID_DIGEST", "PRIVATE_QUESTION", str(repository)):
        assert secret not in output.getvalue()


def test_cli_live_with_synthetic_transport_safe_summary(repository: Path) -> None:
    path = _write_targets(repository)
    transport = SequenceTransport([{"response": "null"}] * 8)
    args = [
        "--live", "--target-file", str(path), "--model-tag", "exact-model:tag",
        "--base-url", "http://localhost:11434", "--timeout", "7.5",
        "--ollama-version", "0.34.2", "--model-run-id", "private-run-v1",
        "--repository-root", str(repository), "--output", str(repository / ".schema-adaptive" / "bundle"),
    ]
    output = io.StringIO()
    with patch("backend.adaptive.harness.live_capture._default_transport", transport):
        with contextlib.redirect_stdout(output):
            assert main(args) == 0
    summary = json.loads(output.getvalue())
    assert summary["status"] == "CAPTURED"
    assert summary["record_count"] == summary["success_count"] == 8
    assert summary["failure_count"] == 0
    assert len(summary["provenance_sha256"]) == 64
    assert summary["verification_status"] == "DECLARED_UNVERIFIED"
    assert len(transport.calls) == 8
    for secret in ("PRIVATE_QUESTION", "PRIVATE_TABLE", "PRIVATE_COLUMN", str(repository)):
        assert secret not in output.getvalue()


def test_cli_suppresses_transport_error_text(repository: Path) -> None:
    path = _write_targets(repository)
    transport = SequenceTransport([RuntimeError("PRIVATE_HTTP_BODY")] * 8)
    output = io.StringIO()
    args = [
        "--live", "--target-file", str(path), "--model-tag", "exact-model:tag",
        "--base-url", "http://localhost:11434", "--timeout", "7.5",
        "--ollama-version", "0.34.2", "--model-run-id", "private-run-v1",
        "--repository-root", str(repository), "--output", str(repository / ".schema-adaptive" / "bundle"),
    ]
    with patch("backend.adaptive.harness.live_capture._default_transport", transport):
        with contextlib.redirect_stdout(output):
            assert main(args) == 0
    summary = json.loads(output.getvalue())
    assert summary["failure_count"] == 8
    assert "PRIVATE_HTTP_BODY" not in output.getvalue()
    assert "PRIVATE_QUESTION" not in output.getvalue()
    assert str(repository) not in output.getvalue()
