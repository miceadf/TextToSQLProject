from __future__ import annotations

import contextlib
import io
import json
import subprocess
from pathlib import Path

import pytest

from backend.adaptive.evaluation.capture_targets import (
    VARIANTS,
    build_synthetic_capture_targets,
    main,
    write_private_capture_targets,
)
from backend.adaptive.evaluation.loader import load_evaluation_set
from backend.adaptive.evaluation.recorded_cli import main as recorded_main
from backend.adaptive.evaluation.run_cli import DEFAULT_GOLD_PATH
from backend.adaptive.evaluation.run_contracts import EvaluationRunConfig
from backend.adaptive.evaluation.run_report import deterministic_request_id
from backend.adaptive.fixtures import build_fixture_bundle
from backend.adaptive.fixtures.contracts import FixtureVariant
from backend.adaptive.harness import (
    OllamaQueryModel,
    capture_private_bundle,
    load_recording_bundle,
)
from backend.adaptive.harness.live_capture import load_private_targets
from backend.adaptive.ontology.loader import load_default_ontology


@pytest.fixture
def prepared(tmp_path: Path):
    ontology = load_default_ontology()
    gold = load_evaluation_set(DEFAULT_GOLD_PATH, ontology=ontology)
    bundle = build_fixture_bundle(tmp_path / "fixtures", ontology=ontology)
    return ontology, gold, bundle


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text(".schema-adaptive/\n", encoding="utf-8")
    return root


def test_targets_match_runner_context_and_exclude_gold(prepared) -> None:
    ontology, gold, bundle = prepared
    run_id = "synthetic-120-v1"
    targets = build_synthetic_capture_targets(
        gold, bundle, model_run_id=run_id, ontology=ontology
    )
    assert len(targets) == 120
    config = EvaluationRunConfig(
        model_run_id=run_id, model_label="synthetic-exploratory",
        variants=[variant.value for variant in VARIANTS], oracle_smoke=False,
    )
    assert [target.request.request_id for target in targets] == [
        deterministic_request_id(config, gold, case.case_id, variant.value)
        for case in gold.cases for variant in VARIANTS
    ]
    for case_index, case in enumerate(gold.cases):
        for variant_index, variant in enumerate(VARIANTS):
            target = targets[case_index * 3 + variant_index]
            assert target.request.question == case.question_ko
            assert target.request.database_id == f"fixture-{variant.value}-v1"
            assert target.mapping.database_id == target.request.database_id
            assert target.mapping.status.value == "PUBLISHED"
            # Offline capture uses the gold's declared version, not current v3.
            assert set(target.ontology.fields) == set(ontology.fields_at(gold.ontology_version))
    serialized = json.dumps([target.model_dump(mode="json") for target in targets])
    for forbidden in ("gold_query_ir", "expected", "offering_keys", "semantic_correct"):
        assert forbidden not in serialized


def test_drift_blocks_target_generation(prepared) -> None:
    ontology, gold, bundle = prepared
    artifact = bundle.artifacts[FixtureVariant.FLAT]
    document = json.loads(artifact.catalog_path.read_text(encoding="utf-8"))
    document["schema_fingerprint"] = "0" * 64
    artifact.catalog_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="drift"):
        build_synthetic_capture_targets(
            gold, bundle, model_run_id="synthetic-120-v1", ontology=ontology
        )


def test_private_writer_and_cli_preflight(repository: Path, prepared) -> None:
    ontology, gold, bundle = prepared
    targets = build_synthetic_capture_targets(
        gold, bundle, model_run_id="synthetic-120-v1", ontology=ontology
    )
    output = repository / ".schema-adaptive" / "targets.json"
    write_private_capture_targets(output, targets, repository_root=repository)
    loaded = load_private_targets(output, repository_root=repository)
    assert loaded == targets
    original = output.read_bytes()
    with pytest.raises(FileExistsError):
        write_private_capture_targets(output, targets, repository_root=repository)
    assert output.read_bytes() == original
    with pytest.raises(ValueError):
        write_private_capture_targets(repository / "public.json", targets,
                                      repository_root=repository)


def test_generator_cli_reports_only_count_and_preserves_target(repository: Path, prepared) -> None:
    _, gold, _ = prepared
    output = repository / ".schema-adaptive" / "generated.json"
    args = [
        "--repository-root", str(repository), "--output", str(output),
        "--model-run-id", "synthetic-120-v1",
    ]
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        assert main(args) == 0
    assert json.loads(stdout.getvalue()) == {"status": "TARGETS_READY", "target_count": 120}
    original = output.read_bytes()
    assert len(load_private_targets(output, repository_root=repository)) == 120
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        assert main(args) == 1
    assert json.loads(stdout.getvalue()) == {"status": "FAILED", "code": "TARGET_EXISTS"}
    assert output.read_bytes() == original
    for secret in (gold.cases[0].question_ko, str(repository), "gold_query_ir"):
        assert secret not in stdout.getvalue()


def test_generated_targets_replay_with_failure_and_missing_attempts(
    repository: Path, prepared
) -> None:
    ontology, gold, bundle = prepared
    targets = build_synthetic_capture_targets(
        gold, bundle, model_run_id="synthetic-120-v1", ontology=ontology
    )
    calls = []

    def transport(url, payload, timeout):
        calls.append((url, payload["model"], timeout))
        if len(calls) == 1:
            raise TimeoutError("PRIVATE_BODY")
        return {"response": "null"}

    model = OllamaQueryModel("http://localhost:11434", "synthetic:model", 3, transport=transport)
    manifest = capture_private_bundle(
        "synthetic-120-v1", model, targets[:3], model.declared_provenance("test-v1"),
        repository / ".schema-adaptive" / "bundle", repository_root=repository,
    )
    loaded = load_recording_bundle(manifest, repository_root=repository)
    assert loaded.manifest.record_count == 3
    assert loaded.manifest.failure_count == 1
    assert len(calls) == 3
    report_path = repository / ".schema-adaptive" / "report.json"
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        assert recorded_main([
            "--recording", str(manifest.parent / loaded.manifest.recording.path),
            "--model-label", "synthetic-exploratory", "--output", str(report_path),
        ]) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["totals"]["scheduled_attempts"] == 120
    assert report["totals"]["completed_attempts"] == 120
    assert report["attempts"][0]["failure_code"] == "RECORDED_TIMEOUT"
    assert report["attempts"][3]["failure_code"] == "RECORDED_RESPONSE_MISSING"
    for secret in ("PRIVATE_BODY", str(repository), "gold_query_ir", "question_ko",
                   *(case.question_ko for case in gold.cases)):
        assert secret not in stdout.getvalue()
        assert secret not in report_path.read_text(encoding="utf-8")
