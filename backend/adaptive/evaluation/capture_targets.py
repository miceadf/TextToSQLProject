"""Build private capture targets for the synthetic fixture benchmark only."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from ..fixtures import build_fixture_bundle
from ..fixtures.contracts import FixtureBundle, FixtureVariant
from ..fixtures.seed import DEFAULT_SEED
from ..harness.contracts import HarnessRequest
from ..harness.recording_bundle_storage import _validate_repository, _validate_target
from ..harness.recording_orchestrator import CaptureTarget
from ..mapping import to_harness_mapping
from ..ontology.contracts import DomainOntology
from ..ontology.loader import load_default_ontology
from .contracts import EvaluationSet
from .loader import load_evaluation_set
from .run_cli import DEFAULT_GOLD_PATH
from .run_contracts import EvaluationRunConfig
from .run_report import deterministic_request_id
from .runner import _prepare_fixture_contexts, _runtime_ontology
from .validator import validate_evaluation_set
from .ontology_version import recorded_ontology


VARIANTS = (FixtureVariant.FLAT, FixtureVariant.NORMALIZED, FixtureVariant.LEGACY)


def build_synthetic_capture_targets(
    gold: EvaluationSet,
    bundle: FixtureBundle,
    *,
    model_run_id: str,
    ontology: DomainOntology | None = None,
) -> list[CaptureTarget]:
    """Match runner request IDs and runtime contexts after fixture drift checks."""
    domain = ontology or load_default_ontology()
    validate_evaluation_set(gold, DEFAULT_SEED, domain)
    domain = recorded_ontology(domain, version=gold.ontology_version)
    contexts, _ = _prepare_fixture_contexts(bundle, DEFAULT_SEED, domain, None)
    config = EvaluationRunConfig(
        model_run_id=model_run_id,
        model_label="synthetic-exploratory",
        variants=[variant.value for variant in VARIANTS],
        oracle_smoke=False,
    )
    runtime_ontology = _runtime_ontology(domain)
    result: list[CaptureTarget] = []
    for case in gold.cases:
        for variant in VARIANTS:
            artifact, _, published = contexts[variant]
            result.append(CaptureTarget(
                request=HarnessRequest(
                    request_id=deterministic_request_id(
                        config, gold, case.case_id, variant.value
                    ),
                    database_id=artifact.database_id,
                    question=case.question_ko,
                ),
                ontology=runtime_ontology,
                mapping=to_harness_mapping(published),
            ))
    return result


def write_private_capture_targets(
    output: str | Path,
    targets: list[CaptureTarget],
    *,
    repository_root: str | Path,
) -> None:
    """Publish a complete versioned target file without overwriting an existing one."""
    root = _validate_repository(repository_root)
    path = _validate_target(output, root)
    if path.exists():
        raise FileExistsError("TARGET_EXISTS")
    if not targets:
        raise ValueError("EMPTY_TARGETS")
    document = {
        "schema_version": "capture-targets-v1",
        "targets": [target.model_dump(mode="json") for target in targets],
    }
    content = (json.dumps(document, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path = _validate_target(path, root)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".capture-targets-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError("INVALID_INPUT")


def main(argv: list[str] | None = None) -> int:
    parser = _SafeParser(description="Generate synthetic benchmark capture targets")
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-run-id", required=True)
    try:
        args = parser.parse_args(argv)
        ontology = load_default_ontology()
        gold = load_evaluation_set(DEFAULT_GOLD_PATH, ontology=ontology)
        with tempfile.TemporaryDirectory(prefix="fixture-capture-targets-") as directory:
            bundle = build_fixture_bundle(Path(directory) / "databases", ontology=ontology)
            targets = build_synthetic_capture_targets(
                gold, bundle, model_run_id=args.model_run_id, ontology=ontology
            )
        write_private_capture_targets(
            args.output, targets, repository_root=args.repository_root
        )
    except FileExistsError:
        print('{"status":"FAILED","code":"TARGET_EXISTS"}')
        return 1
    except Exception:
        print('{"status":"FAILED","code":"TARGET_PREPARATION_ERROR"}')
        return 1
    print(json.dumps({"status": "TARGETS_READY", "target_count": len(targets)},
                     separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
