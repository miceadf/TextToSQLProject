from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from backend.adaptive.dataset.cli import main
from backend.adaptive.dataset.conversion_contracts import ConversionStatus
from backend.adaptive.dataset.conversion_report import (
    build_query_ir_conversion_report,
)
from backend.adaptive.dataset.loader import load_jsonl_dataset
from backend.adaptive.dataset.source_profile import load_dataset_schema_profile
from backend.adaptive.dataset.storage import write_conversion_artifacts
from backend.adaptive.ontology.loader import default_ontology_path, load_default_ontology


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_PROFILE_PATH = (
    _REPOSITORY_ROOT
    / "backend"
    / "adaptive"
    / "dataset"
    / "profiles"
    / "cnu-courses-v1.yaml"
)


def _jsonl(*rows: dict[str, str]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def _row(question: str, sql: str) -> dict[str, str]:
    return {"instruction": "private instruction", "input": question, "output": sql}


class QueryIrConversionReportTests(unittest.TestCase):
    def _build(self, directory: Path, *rows: dict[str, str]):
        source = directory / "dataset.csv"
        source.write_text(_jsonl(*rows), encoding="utf-8")
        ontology = load_default_ontology()
        profile = load_dataset_schema_profile(_PROFILE_PATH, ontology)
        return source, build_query_ir_conversion_report(
            load_jsonl_dataset(source),
            "synthetic-v1",
            profile,
            ontology,
        )

    def test_public_report_is_private_and_conflicts_block_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source, (report, private) = self._build(
                Path(temp),
                _row(
                    "SECRET CONFLICT",
                    "SELECT subject_name FROM courses WHERE course_year = 2024",
                ),
                _row(
                    "SECRET CONFLICT",
                    "SELECT subject_code FROM courses WHERE course_year = 2024",
                ),
                _row(
                    "SECRET SAFE",
                    "SELECT subject_name FROM courses WHERE subject_code = 'SECRET_LITERAL'",
                ),
            )

        self.assertEqual(report.summary.valid_dataset_records, 3)
        self.assertEqual(report.summary.question_conflict_record_count, 2)
        self.assertEqual(
            report.summary.status_counts[ConversionStatus.REVIEW_REQUIRED.value],
            2,
        )
        self.assertEqual(len(report.review_items), 2)
        self.assertEqual(len(private), 1)
        public_json = report.model_dump_json()
        self.assertNotIn("SECRET CONFLICT", public_json)
        self.assertNotIn("SECRET SAFE", public_json)
        self.assertNotIn("SECRET_LITERAL", public_json)
        self.assertNotIn(str(source.resolve()), public_json)

    def test_report_is_deterministic_and_preview_redacts_literals(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = directory / "dataset.csv"
            source.write_text(
                _jsonl(
                    _row(
                        "SECRET QUESTION",
                        "SELECT subject_name FROM courses WHERE subject_code = 'SECRET_LITERAL'",
                    )
                ),
                encoding="utf-8",
            )
            loaded = load_jsonl_dataset(source)
            ontology = load_default_ontology()
            profile = load_dataset_schema_profile(_PROFILE_PATH, ontology)
            first, _ = build_query_ir_conversion_report(
                loaded,
                "synthetic-v1",
                profile,
                ontology,
                include_previews=True,
                preview_record_limit=1,
                preview_char_limit=80,
            )
            second, _ = build_query_ir_conversion_report(
                loaded,
                "synthetic-v1",
                profile,
                ontology,
                include_previews=True,
                preview_record_limit=1,
                preview_char_limit=80,
            )

        self.assertEqual(first, second)
        self.assertNotIn("SECRET_LITERAL", first.records[0].preview or "")
        self.assertLessEqual(len(first.records[0].preview or ""), 80)

    def test_preview_suppresses_ambiguous_quoted_identifier_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = directory / "dataset.csv"
            source.write_text(
                _jsonl(
                    _row(
                        "question",
                        'SELECT subject_name FROM courses WHERE dept_name = "SECRET-DEPT"',
                    )
                ),
                encoding="utf-8",
            )
            ontology = load_default_ontology()
            profile = load_dataset_schema_profile(_PROFILE_PATH, ontology)
            report, _ = build_query_ir_conversion_report(
                load_jsonl_dataset(source),
                "synthetic-v1",
                profile,
                ontology,
                include_previews=True,
            )

        self.assertIsNone(report.records[0].preview)
        self.assertNotIn("SECRET-DEPT", report.model_dump_json())

    def test_preview_suppresses_quoted_identifiers_in_unsupported_expressions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = directory / "dataset.csv"
            source.write_text(
                _jsonl(
                    _row(
                        "question",
                        'SELECT "SECRET-PROJECTION" || subject_name FROM courses',
                    )
                ),
                encoding="utf-8",
            )
            ontology = load_default_ontology()
            profile = load_dataset_schema_profile(_PROFILE_PATH, ontology)
            report, _ = build_query_ir_conversion_report(
                load_jsonl_dataset(source),
                "synthetic-v1",
                profile,
                ontology,
                include_previews=True,
            )

        self.assertIsNone(report.records[0].preview)
        self.assertNotIn("SECRET-PROJECTION", report.model_dump_json())

    def test_artifact_writer_requires_private_ignored_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, (report, private) = self._build(
                root,
                _row("question", "SELECT subject_name FROM courses"),
            )
            with self.assertRaisesRegex(ValueError, "private output"):
                write_conversion_artifacts(
                    root / "report.json",
                    report,
                    private_path=root / "tracked" / "private.jsonl",
                    private_records=private,
                    repository_root=root,
                )

            report_path, private_path = write_conversion_artifacts(
                root / ".schema-adaptive" / "reports" / "report.json",
                report,
                private_path=root / ".schema-adaptive" / "private" / "records.jsonl",
                private_records=private,
                repository_root=root,
            )
            self.assertTrue(report_path.exists())
            self.assertIsNotNone(private_path)
            payload = json.loads((private_path or Path()).read_text(encoding="utf-8"))
            self.assertEqual(payload["source_line"], 1)
            self.assertIn("duplicate_provenance", payload)
            self.assertEqual(len(payload["canonical_hash"]), 64)
            with self.assertRaises(FileExistsError):
                write_conversion_artifacts(
                    report_path,
                    report,
                    private_path=private_path,
                    private_records=private,
                    repository_root=root,
                )

    def test_private_write_requires_repository_root_and_force_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, (report, private) = self._build(
                root,
                _row("question", "SELECT subject_name FROM courses"),
            )
            report_path = root / ".schema-adaptive" / "report.json"
            private_path = root / ".schema-adaptive" / "private.jsonl"
            with self.assertRaisesRegex(ValueError, "repository_root"):
                write_conversion_artifacts(
                    report_path,
                    report,
                    private_path=private_path,
                    private_records=private,
                )

            report_path.parent.mkdir(parents=True)
            report_path.write_bytes(b"old-public")
            private_path.write_bytes(b"old-private")
            with patch(
                "backend.adaptive.dataset.storage._write_report",
                side_effect=OSError("injected public write failure"),
            ):
                with self.assertRaises(OSError):
                    write_conversion_artifacts(
                        report_path,
                        report,
                        private_path=private_path,
                        private_records=private,
                        overwrite=True,
                        repository_root=root,
                    )
            self.assertEqual(report_path.read_bytes(), b"old-public")
            self.assertEqual(private_path.read_bytes(), b"old-private")

    def test_convert_cli_writes_public_report_without_changing_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = directory / "dataset.csv"
            source.write_text(
                _jsonl(_row("question", "SELECT subject_name FROM courses")),
                encoding="utf-8",
            )
            original = source.read_bytes()
            output = directory / "report.json"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = main(
                    [
                        "convert-ir",
                        "--input",
                        str(source),
                        "--dataset-id",
                        "synthetic-v1",
                        "--profile",
                        str(_PROFILE_PATH),
                        "--ontology",
                        str(default_ontology_path()),
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(result, 0, stderr.getvalue())
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8"))["schema_version"],
                "query-ir-conversion-report-v1",
            )
            self.assertTrue(json.loads(stdout.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
