from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from pydantic import ValidationError

from .loader import DatasetLoadFatalError, load_jsonl_dataset
from .report import build_quality_report
from .storage import write_quality_report


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _reject_same_input_and_output(args.input, args.output)
        loaded = load_jsonl_dataset(args.input)
        report = build_quality_report(
            loaded,
            dataset_id=args.dataset_id,
            include_previews=args.include_previews,
            preview_group_limit=args.preview_group_limit,
            preview_char_limit=args.preview_char_limit,
            preview_variant_limit=args.preview_variant_limit,
        )
        output = write_quality_report(args.output, report, overwrite=args.force)
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


def _reject_same_input_and_output(input_path: Path, output_path: Path) -> None:
    if input_path.resolve() == output_path.resolve():
        raise ValueError("input and output must be different files")
    if input_path.exists() and output_path.exists() and os.path.samefile(
        input_path,
        output_path,
    ):
        raise ValueError("input and output must be different files")
