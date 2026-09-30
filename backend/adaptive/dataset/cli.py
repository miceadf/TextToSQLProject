from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from pydantic import ValidationError

from ..ontology.loader import load_ontology
from .conversion_report import build_query_ir_conversion_report
from .loader import DatasetLoadFatalError, load_jsonl_dataset
from .report import build_quality_report
from .source_profile import load_dataset_schema_profile
from .sql_report import build_sql_analysis_report
from .pseudonym_loader import load_private_converted_jsonl
from .pseudonym_contracts import PseudonymReasonCode, PseudonymReviewItem
from .pseudonym_profile import load_pseudonymization_profile, read_secret_key
from .pseudonym_report import build_pseudonym_summary
from .pseudonym_storage import write_pseudonym_bundle
from .pseudonym_transform import pseudonymize_records
from .storage import (
    write_conversion_artifacts,
    write_quality_report,
    write_sql_analysis_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect a JSONL Text-to-SQL dataset without modifying it"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="write a dataset quality report")
    inspect.add_argument("--input", type=Path, required=True)
    inspect.add_argument("--dataset-id", required=True)
    inspect.add_argument("--output", type=Path, required=True)
    inspect.add_argument(
        "--include-previews",
        action="store_true",
        help="include a bounded number of question and SQL previews",
    )
    inspect.add_argument("--preview-group-limit", type=int, default=5)
    inspect.add_argument("--preview-char-limit", type=int, default=160)
    inspect.add_argument("--preview-variant-limit", type=int, default=3)
    inspect.add_argument(
        "--force",
        action="store_true",
        help="atomically replace an existing report",
    )
    analyze = commands.add_parser(
        "analyze-sql",
        help="write a privacy-preserving PostgreSQL AST analysis report",
    )
    analyze.add_argument("--input", type=Path, required=True)
    analyze.add_argument("--dataset-id", required=True)
    analyze.add_argument("--dialect", default="postgres")
    analyze.add_argument("--output", type=Path, required=True)
    analyze.add_argument(
        "--include-previews",
        action="store_true",
        help="include bounded literal-redacted SQL shape previews",
    )
    analyze.add_argument("--preview-record-limit", type=int, default=5)
    analyze.add_argument("--preview-char-limit", type=int, default=240)
    analyze.add_argument(
        "--force",
        action="store_true",
        help="atomically replace an existing report",
    )
    convert = commands.add_parser(
        "convert-ir",
        help="convert the approved legacy SQL subset to QueryIR",
    )
    convert.add_argument("--input", type=Path, required=True)
    convert.add_argument("--dataset-id", required=True)
    convert.add_argument("--profile", type=Path, required=True)
    convert.add_argument("--ontology", type=Path, required=True)
    convert.add_argument("--output", type=Path, required=True)
    convert.add_argument("--private-output", type=Path)
    convert.add_argument("--default-limit", type=int, default=50)
    convert.add_argument(
        "--include-previews",
        action="store_true",
        help="include bounded literal-redacted SQL shape previews",
    )
    convert.add_argument("--preview-record-limit", type=int, default=5)
    convert.add_argument("--preview-char-limit", type=int, default=240)
    convert.add_argument(
        "--force",
        action="store_true",
        help="atomically replace existing artifacts",
    )
    pseudonymize = commands.add_parser(
        "pseudonymize-ir",
        help="pseudonymize a private converted QueryIR artifact",
    )
    pseudonymize.add_argument("--input", type=Path, required=True)
    pseudonymize.add_argument("--dataset-id", required=True)
    pseudonymize.add_argument("--profile", type=Path, required=True)
    pseudonymize.add_argument("--ontology", type=Path, required=True)
    pseudonymize.add_argument("--key-file", type=Path, required=True)
    pseudonymize.add_argument("--key-id", required=True)
    pseudonymize.add_argument("--output-dir", type=Path, required=True)
    pseudonymize.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "pseudonymize-ir":
        return _run_pseudonymize(args)
    try:
        _reject_colliding_paths(
            args.input,
            args.output,
            getattr(args, "private_output", None),
        )
        loaded = load_jsonl_dataset(args.input)
        if args.command == "inspect":
            report = build_quality_report(
                loaded,
                dataset_id=args.dataset_id,
                include_previews=args.include_previews,
                preview_group_limit=args.preview_group_limit,
                preview_char_limit=args.preview_char_limit,
                preview_variant_limit=args.preview_variant_limit,
            )
            output = write_quality_report(args.output, report, overwrite=args.force)
        elif args.command == "analyze-sql":
            report = build_sql_analysis_report(
                loaded,
                dataset_id=args.dataset_id,
                dialect=args.dialect,
                include_previews=args.include_previews,
                preview_record_limit=args.preview_record_limit,
                preview_char_limit=args.preview_char_limit,
            )
            output = write_sql_analysis_report(
                args.output,
                report,
                overwrite=args.force,
            )
        else:
            ontology = load_ontology(args.ontology)
            profile = load_dataset_schema_profile(
                args.profile,
                ontology,
                expected_dialect="postgres",
            )
            report, private_records = build_query_ir_conversion_report(
                loaded,
                dataset_id=args.dataset_id,
                profile=profile,
                ontology=ontology,
                default_limit=args.default_limit,
                include_previews=args.include_previews,
                preview_record_limit=args.preview_record_limit,
                preview_char_limit=args.preview_char_limit,
            )
            output, _ = write_conversion_artifacts(
                args.output,
                report,
                private_path=args.private_output,
                private_records=private_records,
                overwrite=args.force,
                repository_root=Path(__file__).resolve().parents[3],
            )
        print(
            json.dumps(
                {
                    "ok": True,
                    "dataset_id": report.dataset_id,
                    "output": str(output),
                    "summary": report.summary.model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (DatasetLoadFatalError, ValidationError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2


def _run_pseudonymize(args: argparse.Namespace) -> int:
    """Keep key paths, source values and Pydantic errors out of CLI output."""

    try:
        repository_root = Path(__file__).resolve().parents[3]
        loaded = load_private_converted_jsonl(args.input)
        if loaded.metadata is None:
            raise ValueError("private artifact has no valid metadata")
        if loaded.metadata.dataset_id != args.dataset_id:
            raise ValueError("dataset id does not match private artifact")
        ontology = load_ontology(args.ontology)
        profile = load_pseudonymization_profile(args.profile, ontology)
        if not ontology.accepts_version(loaded.metadata.ontology_version):
            raise ValueError("ontology version does not match private artifact")
        key = read_secret_key(args.key_file, repository_root)
        run = pseudonymize_records(
            loaded.records, profile, ontology, key, args.dataset_id, args.key_id
        )
        report = build_pseudonym_summary(
            run,
            dataset_id=args.dataset_id,
            dataset_fingerprint=loaded.metadata.dataset_content_sha256,
            input_artifact_sha256=loaded.content_sha256,
            profile=profile,
            key_id=args.key_id,
            invalid_input_count=len(loaded.issues),
        )
        manifest = write_pseudonym_bundle(
            args.output_dir,
            run.records,
            report,
            [
                *run.review_items,
                *[
                    PseudonymReviewItem(
                        input_line=issue.line_number,
                        reason_codes=[
                            PseudonymReasonCode.INPUT_SCHEMA_VERSION_MISMATCH
                            if issue.code.value == "INPUT_SCHEMA_VERSION_MISMATCH"
                            else PseudonymReasonCode.PSEUDONYMIZATION_ERROR
                        ],
                    )
                    for issue in loaded.issues
                ],
            ],
            input_path=args.input,
            key_path=args.key_file,
            overwrite=args.force,
            repository_root=repository_root,
        )
        print(json.dumps({
            "ok": True,
            "dataset_id": report.dataset_id,
            "manifest": str(manifest),
            "input_records": report.input_record_count,
            "output_records": report.output_record_count,
            "review_records": report.review_record_count,
            "invalid_input_lines": len(loaded.issues),
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception:
        print(json.dumps({"ok": False, "error": "pseudonymization failed"}), file=sys.stderr)
        return 2


def _reject_same_input_and_output(input_path: Path, output_path: Path) -> None:
    _reject_colliding_paths(input_path, output_path)


def _reject_colliding_paths(*paths: Path | None) -> None:
    present = [path for path in paths if path is not None]
    for index, first in enumerate(present):
        for second in present[index + 1 :]:
            if first.resolve() == second.resolve():
                raise ValueError("input and output paths must be different files")
            if first.exists() and second.exists() and os.path.samefile(first, second):
                raise ValueError("input and output paths must be different files")
