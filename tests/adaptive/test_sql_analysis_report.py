from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from backend.adaptive.dataset.cli import main
from backend.adaptive.dataset.loader import load_jsonl_dataset
from backend.adaptive.dataset.sql_contracts import IrCompatibilityStatus
from backend.adaptive.dataset.sql_report import build_sql_analysis_report


class SqlAnalysisReportTests(unittest.TestCase):
    def _write_dataset(self, directory: Path) -> Path:
        source = directory / "dataset.csv"
        lines = [
            {
                "instruction": "private instruction",
                "input": "SECRET QUESTION",
                "output": (
                    "SELECT name FROM courses "
                    "WHERE department = 'SECRET_LITERAL' AND credits = 3"
                ),
            },
            {
                "instruction": "private instruction",
                "input": "broken question",
                "output": "SELECT 'SECRET_PARSE_LITERAL",
            },
        ]
        source.write_text(
            "".join(json.dumps(line) + "\n" for line in lines),
            encoding="utf-8",
        )
        return source

    def test_report_is_deterministic_and_private_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = self._write_dataset(Path(temp))
            loaded = load_jsonl_dataset(source)
            first = build_sql_analysis_report(loaded, "synthetic-v1")
            second = build_sql_analysis_report(loaded, "synthetic-v1")

        self.assertEqual(first, second)
        self.assertEqual(first.dialect, "postgres")
        self.assertEqual(first.sqlglot_version, "30.18.0")
        self.assertEqual(first.summary.analyzed_sql_records, 1)
        self.assertEqual(first.summary.sql_issue_records, 1)
        self.assertEqual(
            first.summary.compatibility_counts,
            {
                IrCompatibilityStatus.CURRENT_IR_CANDIDATE.value: 1,
                IrCompatibilityStatus.IR_EXTENSION_REQUIRED.value: 0,
                IrCompatibilityStatus.MANUAL_REVIEW.value: 0,
                IrCompatibilityStatus.UNSUPPORTED_OR_UNSAFE.value: 1,
            },
        )
        serialized = first.model_dump_json()
        self.assertNotIn("SECRET QUESTION", serialized)
        self.assertNotIn("SECRET_LITERAL", serialized)
        self.assertNotIn("SECRET_PARSE_LITERAL", serialized)
        self.assertTrue(all(record.preview is None for record in first.records))
        candidate_group = first.summary.compatibility_groups[
            IrCompatibilityStatus.CURRENT_IR_CANDIDATE.value
        ]
        unsafe_group = first.summary.compatibility_groups[
            IrCompatibilityStatus.UNSUPPORTED_OR_UNSAFE.value
        ]
        self.assertEqual(candidate_group.record_count, 1)
        self.assertEqual(unsafe_group.record_count, 1)
        self.assertEqual(unsafe_group.reason_code_counts, {"PARSE_FAILED": 1})

    def test_preview_is_literal_redacted_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            loaded = load_jsonl_dataset(self._write_dataset(Path(temp)))
            report = build_sql_analysis_report(
                loaded,
                "synthetic-v1",
                include_previews=True,
                preview_record_limit=1,
                preview_char_limit=40,
            )

        preview = report.records[0].preview
        self.assertIsNotNone(preview)
        self.assertLessEqual(len(preview or ""), 40)
        self.assertNotIn("SECRET_LITERAL", preview or "")

    def test_cli_writes_report_without_modifying_input_or_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = self._write_dataset(directory)
            original = source.read_bytes()
            output = directory / "report.json"
            args = [
                "analyze-sql",
                "--input",
                str(source),
                "--dataset-id",
                "synthetic-v1",
                "--output",
                str(output),
            ]
            self.assertEqual(main(args), 0)
            self.assertEqual(source.read_bytes(), original)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], "sql-analysis-report-v1")
            self.assertEqual(main(args), 2)
            self.assertEqual(main([*args, "--force"]), 0)

    def test_cli_rejects_same_path_and_hardlink_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = self._write_dataset(directory)
            common = [
                "analyze-sql",
                "--input",
                str(source),
                "--dataset-id",
                "synthetic-v1",
            ]
            self.assertEqual(main([*common, "--output", str(source), "--force"]), 2)

            linked = directory / "linked.json"
            try:
                os.link(source, linked)
            except OSError as exc:  # pragma: no cover - filesystem capability
                self.skipTest(f"hardlinks are unavailable: {exc}")
            self.assertEqual(main([*common, "--output", str(linked), "--force"]), 2)


if __name__ == "__main__":
    unittest.main()
