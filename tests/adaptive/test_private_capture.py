from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.adaptive.harness import (
    CaptureTarget,
    FieldMapping,
    HarnessRequest,
    MappingProfile,
    MappingStatus,
    Ontology,
    ProviderKind,
    RecordedFailureCode,
    RecordedModel,
    RecordedModelReplayError,
    RecordedReplayErrorCode,
    RecordingBundleError,
    RecordingBundleErrorCode,
    RecordingCaptureError,
    RecordingOrchestrationError,
    RecordingProviderError,
    RecordingProvenance,
    capture_private_bundle,
    load_recording_bundle,
)
from backend.adaptive.harness import ollama
from backend.adaptive.harness.ollama import OllamaQueryModel
from backend.adaptive.harness.synthetic_capture_cli import main as synthetic_main


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text(".schema-adaptive/\n", encoding="utf-8")
    return root


@pytest.fixture
def inputs() -> tuple[list[CaptureTarget], RecordingProvenance]:
    mapping = MappingProfile(
        database_id="synthetic-db",
        version=1,
        status=MappingStatus.PUBLISHED,
        fields={"Course.name": FieldMapping(table="synthetic_table", column="synthetic_column")},
    )
    ontology = Ontology(entities={"Course"}, fields={})
    targets = [
        CaptureTarget(
            request=HarnessRequest(
                request_id=f"synthetic-{index}",
                database_id=mapping.database_id,
                question=f"PRIVATE_SYNTHETIC_QUESTION_{index}",
            ),
            ontology=ontology,
            mapping=mapping,
        )
        for index in range(6)
    ]
    provenance = RecordingProvenance(
        provider_kind=ProviderKind.SYNTHETIC,
        provider_id="test-provider",
        provider_version="1",
        model_id="synthetic-model",
        prompt_template_sha256="a" * 64,
        generation_config_sha256="b" * 64,
        collector_version="test-collector-v1",
    )
    return targets, provenance


class SequenceProvider:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []

    def generate_ir(self, request, ontology, mapping):
        del ontology, mapping
        self.calls.append(request.request_id)
        outcome = self.outcomes[len(self.calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _capture(
    repository: Path,
    inputs: tuple[list[CaptureTarget], RecordingProvenance],
    provider: SequenceProvider,
    *,
    output: Path | None = None,
    targets: list[CaptureTarget] | None = None,
    provenance: RecordingProvenance | None = None,
) -> Path:
    given_targets, given_provenance = inputs
    return capture_private_bundle(
        "synthetic-run-v1",
        provider,
        given_targets if targets is None else targets,
        given_provenance if provenance is None else provenance,
        output or repository / ".schema-adaptive" / "capture",
        repository_root=repository,
    )


def test_capture_strict_load_and_replay_preserve_order_and_failures(
    repository: Path, inputs: tuple[list[CaptureTarget], RecordingProvenance]
) -> None:
    targets, _ = inputs
    payload = {"entity": "Course", "select": ["Course.name"], "private": "PRIVATE_RESPONSE"}
    failures = [
        RecordedFailureCode.TIMEOUT,
        RecordedFailureCode.EMPTY_RESPONSE,
        RecordedFailureCode.CANCELLED,
        RecordedFailureCode.PROVIDER_ERROR,
    ]
    outcomes = [payload, *[RecordingProviderError(code) for code in failures], None]
    provider = SequenceProvider(outcomes)
    manifest_path = _capture(repository, inputs, provider)
    bundle = load_recording_bundle(manifest_path, repository_root=repository)
    assert provider.calls == [target.request.request_id for target in targets]
    assert bundle.manifest.record_count == 6
    assert bundle.manifest.success_count == 2
    assert bundle.manifest.failure_count == 4
    assert bundle.manifest.provenance.verification_status == "DECLARED_UNVERIFIED"
    assert bundle.manifest.recording.sha256
    replay = RecordedModel(bundle.recording)
    assert replay.generate_ir(targets[0].request, targets[0].ontology, targets[0].mapping) == payload
    assert replay.generate_ir(targets[5].request, targets[5].ontology, targets[5].mapping) is None
    expected = [
        RecordedReplayErrorCode.TIMEOUT,
        RecordedReplayErrorCode.EMPTY_RESPONSE,
        RecordedReplayErrorCode.CANCELLED,
        RecordedReplayErrorCode.PROVIDER_ERROR,
    ]
    for target, code in zip(targets[1:5], expected, strict=True):
        with pytest.raises(RecordedModelReplayError) as caught:
            replay.generate_ir(target.request, target.ontology, target.mapping)
        assert caught.value.code is code
    manifest = manifest_path.read_text(encoding="utf-8")
    assert "PRIVATE_RESPONSE" not in manifest
    assert "PRIVATE_SYNTHETIC_QUESTION" not in manifest
    assert "synthetic_table" not in manifest
    assert str(repository) not in manifest


def test_preflight_blocks_bad_inputs_before_provider_or_files(
    repository: Path, inputs: tuple[list[CaptureTarget], RecordingProvenance]
) -> None:
    targets, provenance = inputs
    outside = repository / "outside"
    tracked = repository / ".schema-adaptive" / "tracked"
    tracked.mkdir(parents=True)
    (tracked / "marker.txt").write_text("tracked", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "-f", ".schema-adaptive/tracked/marker.txt"], check=True)
    cases = [
        ({"output": outside}, RecordingBundleError, RecordingBundleErrorCode.INVALID_PATH),
        ({"output": tracked}, RecordingBundleError, RecordingBundleErrorCode.GIT_TRACKED_TARGET),
        ({"targets": [targets[0], targets[0]]}, RecordingOrchestrationError, None),
        ({"provenance": provenance.model_copy(update={"provider_id": "PRIVATE BAD"})}, RecordingBundleError, RecordingBundleErrorCode.INVALID_CONTRACT),
    ]
    for kwargs, error_type, code in cases:
        provider = SequenceProvider([{"unused": True}])
        with pytest.raises(error_type) as caught:
            _capture(repository, inputs, provider, **kwargs)
        if code is not None:
            assert caught.value.code is code
        assert provider.calls == []
    assert not outside.exists()
    assert not (repository / ".schema-adaptive" / "capture").exists()


def test_existing_bundle_rejected_before_provider_and_manifest_preserved(
    repository: Path, inputs: tuple[list[CaptureTarget], RecordingProvenance]
) -> None:
    targets, _ = inputs
    first = SequenceProvider([{"safe": True}] * len(targets))
    manifest_path = _capture(repository, inputs, first)
    original = manifest_path.read_bytes()
    second = SequenceProvider([{"unused": True}] * len(targets))
    with pytest.raises(FileExistsError):
        _capture(repository, inputs, second)
    assert second.calls == []
    assert manifest_path.read_bytes() == original


def test_bad_run_id_stops_before_provider_and_storage(
    repository: Path, inputs: tuple[list[CaptureTarget], RecordingProvenance]
) -> None:
    targets, provenance = inputs
    provider = SequenceProvider([{"unused": True}])
    output = repository / ".schema-adaptive" / "capture"
    with pytest.raises(RecordingCaptureError):
        capture_private_bundle(
            "INVALID RUN ID", provider, targets, provenance, output,
            repository_root=repository,
        )
    assert provider.calls == []
    assert not output.exists()


def test_synthetic_cli_requires_opt_in_and_never_uses_network(
    repository: Path,
) -> None:
    output = repository / ".schema-adaptive" / "manual"
    arguments = [
        "--repository-root", str(repository),
        "--output", str(output),
        "--model-run-id", "synthetic-cli-v1",
    ]
    stdout = io.StringIO()
    with patch("urllib.request.urlopen", side_effect=AssertionError("NETWORK_USED")):
        with contextlib.redirect_stdout(stdout):
            assert synthetic_main(arguments) == 2
        assert not output.exists()
        with contextlib.redirect_stdout(stdout):
            assert synthetic_main(["--synthetic", *arguments]) == 0
    summary = json.loads(stdout.getvalue().splitlines()[-1])
    assert summary == {
        "status": "SYNTHETIC_CAPTURED",
        "record_count": 2,
        "verification_status": "DECLARED_UNVERIFIED",
    }
    assert "합성 질문" not in stdout.getvalue()
    assert str(repository) not in stdout.getvalue()
    bundle = load_recording_bundle(output / "manifest.json", repository_root=repository)
    assert bundle.manifest.provenance.provider_kind is ProviderKind.SYNTHETIC


def test_synthetic_cli_error_omits_local_path(repository: Path) -> None:
    outside = repository / "PRIVATE_LOCAL_PATH"
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = synthetic_main([
            "--synthetic", "--repository-root", str(repository),
            "--output", str(outside), "--model-run-id", "synthetic-cli-v1",
        ])
    assert code == 1
    assert json.loads(stdout.getvalue()) == {"status": "FAILED", "code": "INVALID_PATH"}
    assert "PRIVATE_LOCAL_PATH" not in stdout.getvalue()
    assert not outside.exists()


def test_ollama_provenance_hashes_actual_template_and_settings(monkeypatch) -> None:
    model = OllamaQueryModel("http://localhost:11434", "exact-model:tag", 7)
    first = model.declared_provenance("0.34.2", "a" * 64)
    second = model.declared_provenance("0.34.2", "a" * 64)
    assert first == second
    assert first.provider_kind is ProviderKind.OLLAMA
    assert first.verification_status == "DECLARED_UNVERIFIED"
    assert first.model_id == "exact-model:tag"
    assert first.model_artifact_sha256 == "a" * 64
    versioned_template = ollama.PROMPT_TEMPLATE.replace(
        "{prompt_version}", ollama.PROMPT_VERSION
    )
    assert first.prompt_template_sha256 == hashlib.sha256(
        versioned_template.encode("utf-8")
    ).hexdigest()
    canonical_config = json.dumps(
        {"stream": False, "options": {"temperature": 0}, "timeout_seconds": 7.0},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert first.generation_config_sha256 == hashlib.sha256(canonical_config).hexdigest()
    assert first.prompt_template_sha256 != model.declared_provenance("0.34.2").generation_config_sha256
    assert model.declared_provenance("0.34.2").model_artifact_sha256 is None
    assert OllamaQueryModel("http://localhost:11434", "exact-model:tag", 8).declared_provenance("0.34.2").generation_config_sha256 != first.generation_config_sha256
    assert OllamaQueryModel("http://localhost:11434", "another-model", 7).declared_provenance("0.34.2").generation_config_sha256 == first.generation_config_sha256
    old_template = ollama.PROMPT_TEMPLATE
    monkeypatch.setattr(ollama, "PROMPT_TEMPLATE", old_template + "\nnew-version")
    changed = model.declared_provenance("0.34.2")
    assert changed.prompt_template_sha256 != first.prompt_template_sha256
    assert changed.generation_config_sha256 == first.generation_config_sha256
    monkeypatch.setattr(ollama, "PROMPT_TEMPLATE", old_template)
    monkeypatch.setattr(ollama, "PROMPT_VERSION", "query-ir-v2")
    assert model.declared_provenance("0.34.2").prompt_template_sha256 == first.prompt_template_sha256
    assert OllamaQueryModel("http://localhost:11434", "exact-model:tag", 7).declared_provenance("0.34.2").prompt_template_sha256 != first.prompt_template_sha256
    monkeypatch.setattr(ollama, "GENERATION_OPTIONS", {"temperature": 0.5})
    assert model.declared_provenance("0.34.2").generation_config_sha256 != first.generation_config_sha256


def test_ollama_provenance_rejects_invalid_declarations() -> None:
    model = OllamaQueryModel("http://localhost:11434", "exact-model:tag", 7)
    for provider_version, digest in [("", None), ("PRIVATE PATH", None), ("1", "bad-digest")]:
        with pytest.raises(ValueError, match="^INVALID_OLLAMA_PROVENANCE$"):
            model.declared_provenance(provider_version, digest)
