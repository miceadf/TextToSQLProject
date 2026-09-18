"""Read-only CLI for evaluating a private, externally produced recording."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

from ..fixtures import build_fixture_bundle
from ..harness.recorded import RecordedModel, load_model_recording
from ..ontology.loader import load_default_ontology
from .loader import load_evaluation_set
from .run_cli import DEFAULT_GOLD_PATH, _parse_variants, _write_report
from .run_contracts import EvaluationRunConfig
from .run_report import render_run_summary
from .runner import run_evaluation


OFFLINE_REPLAY_WARNING = "OFFLINE_REPLAY_NOT_LIVE_MODEL"
UNVERIFIED_PROVENANCE_WARNING = "RECORDING_PROVENANCE_UNVERIFIED"


def _same_file(left: Path, right: Path) -> bool:
    if left.resolve() == right.resolve():
        return True
    return left.exists() and right.exists() and os.path.samefile(left, right)


def _reject_colliding_paths(gold: Path, recording: Path, output: Path) -> None:
    paths = (gold, recording, output)
    if any(
        _same_file(paths[left], paths[right])
        for left in range(len(paths))
        for right in range(left + 1, len(paths))
    ):
        raise ValueError("gold, recording, and report output must be different files")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay a private recording offline; not proof of live model performance"
    )
    parser.add_argument("--recording", type=Path, required=True)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD_PATH)
    parser.add_argument(
        "--variants",
        type=_parse_variants,
        default=["flat", "normalized", "legacy"],
    )
    parser.add_argument("--model-label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    _reject_colliding_paths(args.gold, args.recording, args.output)
    if args.output.exists() and not args.force:
        raise FileExistsError("report already exists; use --force")

    recording = load_model_recording(args.recording)
    model = RecordedModel(recording)
    ontology = load_default_ontology()
    gold = load_evaluation_set(args.gold, ontology=ontology)
    config = EvaluationRunConfig(
        model_run_id=recording.model_run_id,
        model_label=args.model_label,
        variants=args.variants,
        oracle_smoke=False,
    )
    with tempfile.TemporaryDirectory(prefix="fixture-run-recorded-") as directory:
        bundle = build_fixture_bundle(Path(directory) / "databases", ontology=ontology)
        report = run_evaluation(gold, model, config, bundle, ontology=ontology)

    report = report.model_copy(update={
        "warnings": [
            *report.warnings,
            OFFLINE_REPLAY_WARNING,
            UNVERIFIED_PROVENANCE_WARNING,
        ]
    })
    _write_report(args.output, report.model_dump_json(indent=2) + "\n", force=args.force)
    print(render_run_summary(report), end="")
    print("WARNING: OFFLINE REPLAY ONLY - no live model was invoked.")
    print("WARNING: Recording provenance is not independently verified.")
    return 0
