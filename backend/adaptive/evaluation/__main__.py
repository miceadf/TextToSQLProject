"""Inspect synthetic gold coverage or a human review table; never score models."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from ..catalog.contracts import DatabaseCatalog
from ..fixtures import FixtureVariant, build_fixture_bundle
from ..ontology.loader import load_default_ontology
from ..relations.physical import build_physical_relation_graph
from .coverage import build_coverage_report, render_review_table
from .fixture_mapping import fixture_published_mapping
from .loader import load_evaluation_set


DEFAULT_GOLD_PATH = Path(__file__).with_name("data") / "fixture_v1_cases.yaml"


def main(argv: list[str] | None = None) -> int:
    effective_argv = sys.argv[1:] if argv is None else argv
    if effective_argv and effective_argv[0] == "run-smoke":
        from .run_cli import main as run_smoke

        return run_smoke(effective_argv[1:])
    if effective_argv and effective_argv[0] == "run-recorded":
        from .recorded_cli import main as run_recorded

        return run_recorded(effective_argv[1:])

    parser = argparse.ArgumentParser(description="Inspect synthetic fixture gold coverage")
    parser.add_argument("--input", type=Path, default=DEFAULT_GOLD_PATH)
    parser.add_argument("--format", choices=("json", "review-table"), default="json")
    args = parser.parse_args(effective_argv)

    ontology = load_default_ontology()
    gold = load_evaluation_set(args.input, ontology=ontology)
    with tempfile.TemporaryDirectory(prefix="fixture-gold-") as directory:
        bundle = build_fixture_bundle(Path(directory) / "databases")
        artifact = bundle.artifacts[FixtureVariant.NORMALIZED]
        catalog = DatabaseCatalog.model_validate_json(
            artifact.catalog_path.read_text(encoding="utf-8")
        )
        graph = build_physical_relation_graph(catalog)
        mapping = fixture_published_mapping(FixtureVariant.NORMALIZED, catalog)
        coverage = build_coverage_report(gold, ontology, graph, mapping)

    if args.format == "review-table":
        print(render_review_table(gold, coverage), end="")
    else:
        print(json.dumps(
            coverage.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
