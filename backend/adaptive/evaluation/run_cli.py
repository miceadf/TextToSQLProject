"""Oracle-only smoke CLI; it never measures a real model's accuracy."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

from ..fixtures import build_fixture_bundle
from ..harness.model import FakeModel
from ..ontology.loader import load_default_ontology
from .loader import load_evaluation_set
from .run_contracts import EvaluationRunConfig
from .run_report import render_run_summary
from .runner import run_evaluation


DEFAULT_GOLD_PATH = Path(__file__).with_name("data") / "fixture_v1_cases.yaml"


def _parse_variants(value: str) -> list[str]:
    variants = [item.strip() for item in value.split(",")]
    if any(not item for item in variants):
        raise argparse.ArgumentTypeError("variants must be comma-separated names")
    return variants


def _reject_colliding_paths(gold_path: Path, output_path: Path) -> None:
    if gold_path.resolve() == output_path.resolve():
        raise ValueError("gold input and report output must be different files")
    if gold_path.exists() and output_path.exists() and os.path.samefile(gold_path, output_path):
        raise ValueError("gold input and report output must be different files")


def _write_report(path: Path, content: str, *, force: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        if force:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError as exc:
                raise FileExistsError("report already exists; use --force") from exc
            temporary.unlink()
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run oracle smoke on synthetic gold; not model accuracy"
    )
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD_PATH)
    parser.add_argument("--variants", type=_parse_variants, default=["flat", "normalized", "legacy"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    _reject_colliding_paths(args.gold, args.output)
    if args.output.exists() and not args.force:
        raise FileExistsError("report already exists; use --force")

    ontology = load_default_ontology()
    gold = load_evaluation_set(args.gold, ontology=ontology)
    config = EvaluationRunConfig(
        model_run_id="oracle-smoke-v1",
        model_label="gold-ir-echo",
        variants=args.variants,
        oracle_smoke=True,
    )
    responses = {
        case.question_ko: case.gold_query_ir.model_dump(mode="json")
        for case in gold.cases
    }
    with tempfile.TemporaryDirectory(prefix="fixture-run-smoke-") as directory:
        bundle = build_fixture_bundle(Path(directory) / "databases", ontology=ontology)
        report = run_evaluation(
            gold, FakeModel(responses), config, bundle, ontology=ontology
        )
    _write_report(args.output, report.model_dump_json(indent=2) + "\n", force=args.force)
    print(render_run_summary(report), end="")
    return 0
