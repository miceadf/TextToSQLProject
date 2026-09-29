"""Opt-in live paired run of the QueryIR arm and the direct-SQL arm (Phase 5-19).

Runs the fixed development set (40 cases × 3 or 5 synthetic fixtures) once per
arm against one loopback Ollama model, then writes the IR report, the SQL
report, and their paired comparison under a new directory in the Git-ignored
``.schema-adaptive/reports`` tree. Reports carry codes and fingerprints only.
Results are provisional observations: gold is still pending human review.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..api.ollama_startup import build_startup_ollama_model, parse_ollama_startup_config
from ..fixtures import FIXTURE_SUITES, build_fixture_bundle
from ..ontology.loader import load_default_ontology
from .arm_comparison import compare_arms, schema_consistency
from .direct_sql import run_direct_sql_evaluation
from .direct_sql_ollama import OllamaDirectSqlModel
from .ir_extract import IR_EXTRACT_NONE, IR_EXTRACT_RULES, IR_EXTRACT_SINGLE_FENCE, SingleFenceIrModel
from .loader import load_evaluation_set
from .ollama_request_format import (
    REQUEST_FORMAT_MODEL_TEMPLATE,
    request_format_label,
)
from .run_contracts import EvaluationRunConfig
from .runner import run_evaluation


DEVELOPMENT_SET_PATH = Path(__file__).resolve().parent / "data" / "fixture_v1_cases.yaml"
FIXTURE_SUITE_CORE = "core-v1"
FIXTURE_SUITE_EXTENDED = "extended-v1"
_OUTPUT_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_RUN_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}")
_FIXED_ERROR = "arm comparison invalid configuration"
_CONFIG_ERROR = {"status": "CONFIG_ERROR"}
_RUN_ERROR = {"status": "RUN_ERROR"}


class _StrictArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:  # noqa: ARG002
        print(_FIXED_ERROR, file=sys.stderr, flush=True)
        raise SystemExit(2)


class _FlagOnce(argparse.Action):
    def __init__(self, option_strings, dest, **kwargs):  # noqa: ANN001
        super().__init__(option_strings, dest, nargs=0, default=False, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):  # noqa: ANN001
        if getattr(namespace, self.dest, False):
            parser.error(_FIXED_ERROR)
        setattr(namespace, self.dest, True)


class _StoreOnce(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):  # noqa: ANN001
        if getattr(namespace, self.dest, None) is not None:
            parser.error(_FIXED_ERROR)
        setattr(namespace, self.dest, values)


def _build_parser() -> argparse.ArgumentParser:
    parser = _StrictArgumentParser(
        prog="backend.adaptive.evaluation.arm_live_cli",
        description="Opt-in live paired IR/SQL arm run on synthetic fixtures.",
        allow_abbrev=False,
    )
    parser.add_argument("--check", action=_FlagOnce)
    parser.add_argument("--live-synthetic", action=_FlagOnce, dest="live_synthetic")
    for name in (
        "--ollama-model-tag", "--ollama-base-url", "--ollama-timeout", "--ollama-request-format", "--ir-extract",
        "--repository-root", "--output-name", "--fixture-suite",
    ):
        parser.add_argument(name, action=_StoreOnce, default=None)
    return parser


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def _metric(value: Any) -> dict[str, int]:
    return {"count": value.count, "denominator": value.denominator}


def _summary(
    ir: Any, sql: Any, comparison: Any, consistency: Any,
    request_format: str, ir_extract: str, fixture_suite: str,
) -> dict[str, Any]:
    return {
        "status": "COMPLETED",
        "model_label": comparison.ir_model_label,
        "request_format": request_format,
        "ir_extract": ir_extract,
        "fixture_suite": fixture_suite,
        "gold_human_review_pending": comparison.gold_human_review_pending,
        "warnings": comparison.warnings,
        "ir_arm": {
            "execution_success": _metric(ir.totals.execution_success),
            "semantic_correct": _metric(ir.totals.semantic_correct),
            "structural_ir_exact": _metric(ir.totals.structural_ir_exact),
        },
        "sql_arm": {
            "valid_sql": _metric(sql.totals.valid_sql),
            "execution_success": _metric(sql.totals.execution_success),
            "execution_correct": _metric(sql.totals.execution_correct),
            "abstain": _metric(sql.totals.abstain),
        },
        "paired": comparison.totals.model_dump(mode="json"),
        "paired_by_variant": {
            name: counts.model_dump(mode="json") for name, counts in comparison.by_variant.items()
        },
        "consistency": {
            "ir": consistency.ir.model_dump(mode="json"),
            "sql": consistency.sql.model_dump(mode="json"),
        },
    }


def _ir_model(config: Any, ir_extract: str) -> Any:
    """IR arm model; ``single-fence-v1`` mirrors the SQL arm's fence rule."""
    model = build_startup_ollama_model(config)
    return SingleFenceIrModel(model) if ir_extract == IR_EXTRACT_SINGLE_FENCE else model


def _is_link(path: Path) -> bool:
    """Symlink or Windows junction (``is_junction`` exists on Python 3.12+)."""
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _publish(reports_root: Path, output_dir: Path, root: Path, reports: Any) -> None:
    """Write into a staging directory, then publish with one no-overwrite rename."""
    reports_root.mkdir(parents=True, exist_ok=True)
    if _is_link(reports_root) or not reports_root.resolve().is_relative_to(root):
        raise ValueError(_FIXED_ERROR)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=reports_root))
    try:
        for name, report in reports:
            (staging / name).write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        if output_dir.exists():
            raise ValueError(_FIXED_ERROR)
        os.rename(staging, output_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if bool(args.check) == bool(args.live_synthetic):
        _emit(_CONFIG_ERROR)
        return 2
    try:
        if args.ollama_model_tag is None or args.output_name is None:
            raise ValueError(_FIXED_ERROR)
        config = parse_ollama_startup_config(
            args.ollama_model_tag,
            args.ollama_base_url,
            args.ollama_timeout,
            # None only means "not given"; an empty value must fail closed in the parser.
            args.ollama_request_format,
        )
        if _OUTPUT_NAME.fullmatch(args.output_name) is None:
            raise ValueError(_FIXED_ERROR)
        request_format = config.request_format
        label = config.model_tag.replace(":", "-")
        # model-template keeps the Phase 5-19 IDs; other formats get a prefix a
        # model tag cannot produce, so tag/format combinations never share run IDs.
        run_prefix = "p519"
        if request_format != REQUEST_FORMAT_MODEL_TEMPLATE:
            short = request_format_label(request_format)
            label = f"{label}.{short}"
            run_prefix = f"p520-{short}"
        # None only means "not given"; an empty or unknown rule fails closed.
        ir_extract = IR_EXTRACT_NONE if args.ir_extract is None else args.ir_extract
        if ir_extract not in IR_EXTRACT_RULES:
            raise ValueError(_FIXED_ERROR)
        if ir_extract == IR_EXTRACT_SINGLE_FENCE:
            label = f"{label}.fence"
            run_prefix = "p522-" + (
                f"{request_format_label(request_format)}-fence"
                if request_format != REQUEST_FORMAT_MODEL_TEMPLATE
                else "fence"
            )
        # None only means "not given"; an empty or unknown suite fails closed.
        fixture_suite = FIXTURE_SUITE_CORE if args.fixture_suite is None else args.fixture_suite
        if fixture_suite not in FIXTURE_SUITES:
            raise ValueError(_FIXED_ERROR)
        suite_variants = FIXTURE_SUITES[fixture_suite]
        if fixture_suite == FIXTURE_SUITE_EXTENDED:
            # Core runs keep their 5-19~5-22 IDs; extended runs never share them.
            label = f"{label}.ext"
            run_prefix = "p523-ext" + "".join(
                (
                    f"-{request_format_label(request_format)}"
                    if request_format != REQUEST_FORMAT_MODEL_TEMPLATE else "",
                    "-fence" if ir_extract == IR_EXTRACT_SINGLE_FENCE else "",
                )
            )
        variants = [variant.value for variant in suite_variants]
        ir_run_id, sql_run_id = f"{run_prefix}-ir-{label}", f"{run_prefix}-sql-{label}"
        if any(_RUN_ID.fullmatch(value) is None for value in (label, ir_run_id, sql_run_id)):
            raise ValueError(_FIXED_ERROR)
        if args.repository_root is not None and not args.repository_root:
            raise ValueError(_FIXED_ERROR)
        root = Path(Path.cwd() if args.repository_root is None else args.repository_root).resolve()
        private_dir = root / ".schema-adaptive"
        reports_root = private_dir / "reports"
        output_dir = reports_root / args.output_name
        # No symlinked component, stay inside the repository, never overwrite.
        if any(_is_link(item) for item in (private_dir, reports_root, output_dir)):
            raise ValueError(_FIXED_ERROR)
        if not reports_root.resolve().is_relative_to(root) or output_dir.exists():
            raise ValueError(_FIXED_ERROR)
    except Exception:
        _emit(_CONFIG_ERROR)
        return 2

    try:
        ontology = load_default_ontology()
        gold = load_evaluation_set(DEVELOPMENT_SET_PATH, ontology=ontology)
        ir_config = EvaluationRunConfig(
            model_run_id=ir_run_id, model_label=label, variants=variants, oracle_smoke=False
        )
        sql_config = EvaluationRunConfig(
            model_run_id=sql_run_id, model_label=label, variants=variants, oracle_smoke=False
        )
        with tempfile.TemporaryDirectory(prefix="p519-fixtures-") as directory:
            bundle = build_fixture_bundle(
                Path(directory) / "databases", ontology=ontology, variants=suite_variants
            )
            if args.check:
                _emit({"status": "CHECKED", "cases": len(gold.cases), "variants": variants, "model_calls": 0})
                return 0
            ir_report = run_evaluation(
                gold,
                _ir_model(config, ir_extract),
                ir_config,
                bundle,
                ontology=ontology,
            )
            sql_report = run_direct_sql_evaluation(
                gold,
                OllamaDirectSqlModel(config),
                sql_config,
                bundle,
                ontology=ontology,
            )
        comparison = compare_arms(ir_report, sql_report)
        consistency = schema_consistency(ir_report, sql_report)
        _publish(reports_root, output_dir, root, (
            ("ir-report.json", ir_report),
            ("sql-report.json", sql_report),
            ("comparison.json", comparison),
            ("consistency.json", consistency),
        ))
        _emit(_summary(
            ir_report, sql_report, comparison, consistency, request_format, ir_extract, fixture_suite
        ))
        return 0
    except Exception:
        _emit(_RUN_ERROR)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
