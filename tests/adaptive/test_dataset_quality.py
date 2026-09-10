from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
import unicodedata
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from backend.adaptive.dataset.cli import main as dataset_cli
from backend.adaptive.dataset.loader import load_jsonl_dataset
from backend.adaptive.dataset.report import build_quality_report


def _row(instruction: str, question: str, sql: str) -> dict[str, str]:
    return {"instruction": instruction, "input": question, "output": sql}


def _jsonl(*rows: object) -> bytes:
    return (
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
    ).encode("utf-8")


class DatasetQualityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_bytes(self, name: str, content: bytes) -> Path:
        path = self.root / name
        path.write_bytes(content)
        return path

    def load_report(
        self,
        content: bytes,
        *,
        name: str = "synthetic.jsonl",
        include_previews: bool = False,
        preview_group_limit: int = 5,
        preview_char_limit: int = 160,
    ):
        path = self.write_bytes(name, content)
        loaded = load_jsonl_dataset(path)
        return loaded, build_quality_report(
            loaded,
            dataset_id="synthetic-v1",
            include_previews=include_previews,
            preview_group_limit=preview_group_limit,
            preview_char_limit=preview_char_limit,
        )

    def test_loads_jsonl_and_preserves_source_values_and_lines(self) -> None:
        rows = [
            _row(" instruction ", " 질문 1 ", " SELECT 1 "),
            _row("instruction", "질문 2", "SELECT 2"),
        ]
        content = _jsonl(*rows)
        path = self.write_bytes("dataset.csv", content)

        loaded = load_jsonl_dataset(path)

        self.assertEqual(loaded.total_lines, 2)
        self.assertEqual(loaded.source_name, "dataset.csv")
        self.assertEqual(loaded.content_sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual(loaded.issues, [])
        self.assertEqual([record.source_line for record in loaded.records], [1, 2])
        self.assertEqual(loaded.records[0].instruction, rows[0]["instruction"])
        self.assertEqual(loaded.records[0].question, rows[0]["input"])
        self.assertEqual(loaded.records[0].sql, rows[0]["output"])

    def test_accepts_utf8_bom_without_changing_first_record(self) -> None:
        content = b"\xef\xbb\xbf" + _jsonl(_row("지시", "질문", "SELECT 1"))
        path = self.write_bytes("bom.csv", content)

        loaded = load_jsonl_dataset(path)

        self.assertEqual(len(loaded.records), 1)
        self.assertEqual(loaded.records[0].instruction, "지시")
        self.assertEqual(loaded.issues, [])
        self.assertEqual(loaded.content_sha256, hashlib.sha256(content).hexdigest())

    def test_empty_file_produces_empty_versioned_report(self) -> None:
        loaded, report = self.load_report(b"", name="empty.csv")

        self.assertEqual(loaded.total_lines, 0)
        self.assertEqual(loaded.records, [])
        self.assertEqual(loaded.issues, [])
        self.assertEqual(report.schema_version, "dataset-quality-report-v1")
        self.assertEqual(report.tool_version, "dataset-quality-inspector-v1")
        self.assertEqual(report.summary.total_records, 0)
        self.assertEqual(report.summary.valid_records, 0)
        self.assertEqual(report.summary.invalid_records, 0)
        self.assertEqual(report.content_sha256, hashlib.sha256(b"").hexdigest())

    def test_reports_empty_malformed_non_object_and_continues(self) -> None:
        content = b"\n{not-json}\n[]\n" + _jsonl(
            _row("instruction", "valid", "SELECT 1")
        )
        path = self.write_bytes("invalid-lines.jsonl", content)

        loaded = load_jsonl_dataset(path)

        self.assertEqual(loaded.total_lines, 4)
        self.assertEqual([record.source_line for record in loaded.records], [4])
        self.assertEqual(
            [(issue.code.value, issue.line_number, issue.field) for issue in loaded.issues],
            [
                ("EMPTY_LINE", 1, None),
                ("MALFORMED_JSON", 2, None),
                ("NON_OBJECT", 3, None),
            ],
        )

    def test_reports_each_missing_or_invalid_field_with_source_field_name(self) -> None:
        content = _jsonl(
            {"instruction": "i", "output": "SELECT 1"},
            {"instruction": 1, "input": [], "output": None},
            _row("i", "valid", "SELECT 2"),
        )
        path = self.write_bytes("fields.jsonl", content)

        loaded = load_jsonl_dataset(path)

        self.assertEqual([record.source_line for record in loaded.records], [3])
        self.assertEqual(
            [(issue.code.value, issue.line_number, issue.field) for issue in loaded.issues],
            [
                ("MISSING_FIELD", 1, "input"),
                ("INVALID_FIELD_TYPE", 2, "instruction"),
                ("INVALID_FIELD_TYPE", 2, "input"),
                ("INVALID_FIELD_TYPE", 2, "output"),
            ],
        )
        report = build_quality_report(loaded, dataset_id="synthetic-v1")
        self.assertEqual(report.summary.invalid_records, 2)
        self.assertEqual(len(report.issues), 4)

    def test_reports_invalid_utf8_and_continues_at_next_line(self) -> None:
        content = b'\xff\n' + _jsonl(_row("i", "valid", "SELECT 1"))
        path = self.write_bytes("encoding.jsonl", content)

        loaded = load_jsonl_dataset(path)

        self.assertEqual([record.source_line for record in loaded.records], [2])
        self.assertEqual(loaded.issues[0].code.value, "INVALID_UTF8")
        self.assertEqual(loaded.issues[0].line_number, 1)

    def test_reports_lone_surrogate_and_continues_at_next_line(self) -> None:
        invalid = b'{"instruction":"i","input":"\\ud800","output":"SELECT 1"}\n'
        content = invalid + _jsonl(_row("i", "valid", "SELECT 2"))
        path = self.write_bytes("unicode.jsonl", content)

        loaded = load_jsonl_dataset(path)

        self.assertEqual([record.source_line for record in loaded.records], [2])
        self.assertEqual(loaded.issues[0].code.value, "INVALID_UNICODE")
        self.assertEqual(loaded.issues[0].line_number, 1)
        self.assertEqual(loaded.issues[0].field, "input")

    def test_report_distinguishes_exact_question_duplicates_and_conflicts(self) -> None:
        content = _jsonl(
            _row("i", "같은 질문", "SELECT 1"),
            _row("i", "같은 질문", "SELECT 1"),
            _row("다른 지시", "같은 질문", "SELECT 1"),
            _row("i", "같은 질문", "SELECT 2"),
            _row("i", "별도 질문", "SELECT 3"),
        )

        _, report = self.load_report(content)

        self.assertEqual(report.summary.total_records, 5)
        self.assertEqual(report.summary.valid_records, 5)
        self.assertEqual(report.summary.invalid_records, 0)
        self.assertEqual(report.summary.unique_instruction_count, 2)
        self.assertEqual(report.summary.unique_question_count, 2)
        self.assertEqual(report.summary.unique_sql_count, 3)
        self.assertEqual(report.summary.repeated_question_group_count, 1)
        self.assertEqual(report.summary.exact_duplicate_group_count, 1)
        self.assertEqual(report.summary.question_sql_duplicate_group_count, 1)
        self.assertEqual(report.summary.question_conflict_group_count, 1)
        self.assertEqual(report.exact_duplicate_groups[0].line_numbers, [1, 2])
        self.assertEqual(
            report.question_sql_duplicate_groups[0].line_numbers,
            [1, 2, 3],
        )
        self.assertEqual(report.question_conflict_groups[0].line_numbers, [1, 2, 3, 4])
        self.assertEqual(
            [
                variant.line_numbers
                for variant in report.question_conflict_groups[0].sql_variants
            ],
            [[1, 2, 3], [4]],
        )

    def test_normalization_is_nfc_newline_and_boundary_trim_only(self) -> None:
        decomposed = unicodedata.normalize("NFD", "질문")
        content = _jsonl(
            _row("i", f"  {decomposed}\r\n", " SELECT  1\r\n"),
            _row("i", "질문\n", "SELECT  1"),
            _row("i", "질문", "select  1"),
            _row("i", "질문", "SELECT 1"),
        )

        _, report = self.load_report(content)

        self.assertEqual(report.summary.unique_question_count, 1)
        self.assertEqual(report.summary.unique_sql_count, 3)
        self.assertEqual(report.summary.question_conflict_group_count, 1)
        self.assertEqual(report.summary.exact_duplicate_group_count, 0)
        self.assertEqual(
            [change.line_number for change in report.normalization_changes],
            sorted(change.line_number for change in report.normalization_changes),
        )
        self.assertTrue(
            any(change.field == "question" for change in report.normalization_changes)
        )
        self.assertTrue(
            any(change.field == "sql" for change in report.normalization_changes)
        )

    def test_invalid_rows_are_counted_and_issues_are_in_deterministic_order(self) -> None:
        content = b"\n{bad}\n" + _jsonl(
            {"instruction": "i", "output": "SELECT 1"},
            _row("i", "valid", "SELECT 2"),
        )

        _, report = self.load_report(content)

        self.assertEqual(report.summary.total_records, 4)
        self.assertEqual(report.summary.valid_records, 1)
        self.assertEqual(report.summary.invalid_records, 3)
        self.assertEqual(
            [(issue.line_number, issue.code.value, issue.field) for issue in report.issues],
            [
                (1, "EMPTY_LINE", None),
                (2, "MALFORMED_JSON", None),
                (3, "MISSING_FIELD", "input"),
            ],
        )

    def test_same_input_has_same_fingerprint_and_report_core(self) -> None:
        content = _jsonl(
            _row("i", "q2", "SELECT 2"),
            _row("i", "q1", "SELECT 1"),
            _row("i", "q1", "SELECT 1"),
        )
        first, report_a = self.load_report(content, name="first.csv")
        second, report_b = self.load_report(content, name="second.csv")

        self.assertEqual(first.content_sha256, second.content_sha256)
        core_a = report_a.model_dump(mode="json", exclude={"source_name"})
        core_b = report_b.model_dump(mode="json", exclude={"source_name"})
        self.assertEqual(core_a, core_b)
        self.assertEqual(
            [group.line_numbers for group in report_a.exact_duplicate_groups],
            sorted(group.line_numbers for group in report_a.exact_duplicate_groups),
        )

    def test_previews_are_off_by_default_and_limited_when_enabled(self) -> None:
        long_question = "가" * 40
        long_sql = "SELECT '" + "나" * 40 + "'"
        content = _jsonl(
            _row("i", long_question, long_sql),
            _row("i", long_question, long_sql),
            _row("i", "두번째", "SELECT 2"),
            _row("i", "두번째", "SELECT 2"),
        )
        _, private = self.load_report(content)
        private_json = json.dumps(private.model_dump(mode="json"), ensure_ascii=False)
        self.assertNotIn(long_question, private_json)
        self.assertNotIn(long_sql, private_json)

        _, preview = self.load_report(
            content,
            include_previews=True,
            preview_group_limit=1,
            preview_char_limit=12,
        )
        all_groups = [
            *preview.question_conflict_groups,
            *preview.question_sql_duplicate_groups,
            *preview.exact_duplicate_groups,
        ]
        previewed = [group for group in all_groups if group.question_preview is not None]
        self.assertEqual(len(previewed), 1)
        self.assertLessEqual(len(previewed[0].question_preview), 12)
        if hasattr(previewed[0], "sql_preview"):
            self.assertLessEqual(len(previewed[0].sql_preview), 12)

    def test_conflict_sql_previews_have_a_separate_variant_limit(self) -> None:
        content = _jsonl(
            *[_row("i", "같은 질문", f"SELECT {index}") for index in range(5)]
        )
        _, report = self.load_report(
            content,
            include_previews=True,
            preview_group_limit=1,
            preview_char_limit=20,
        )

        variants = report.question_conflict_groups[0].sql_variants
        self.assertEqual(sum(item.sql_preview is not None for item in variants), 3)

    def test_cli_writes_json_report_without_modifying_input(self) -> None:
        content = _jsonl(
            _row("instruction", "question", "SELECT 1"),
            _row("instruction", "question", "SELECT 1"),
        )
        input_path = self.write_bytes("dataset.csv", content)
        output_path = self.root / "reports" / "quality.json"
        before = input_path.read_bytes()

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = dataset_cli(
                [
                    "inspect",
                    "--input",
                    str(input_path),
                    "--dataset-id",
                    "synthetic-v1",
                    "--output",
                    str(output_path),
                ]
            )

        self.assertEqual(exit_code, 0, stderr.getvalue())
        self.assertEqual(input_path.read_bytes(), before)
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["dataset_id"], "synthetic-v1")
        self.assertEqual(payload["source_name"], "dataset.csv")
        self.assertNotIn(str(input_path.resolve()), output_path.read_text(encoding="utf-8"))
        cli_payload = json.loads(stdout.getvalue())
        self.assertTrue(cli_payload["ok"])
        self.assertEqual(cli_payload["dataset_id"], "synthetic-v1")

    def test_cli_refuses_overwrite_without_force_and_replaces_with_force(self) -> None:
        first = _jsonl(_row("i", "first", "SELECT 1"))
        second = _jsonl(_row("i", "second", "SELECT 2"))
        first_path = self.write_bytes("first.csv", first)
        second_path = self.write_bytes("second.csv", second)
        output_path = self.root / "quality.json"

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(
                dataset_cli(
                    [
                        "inspect",
                        "--input",
                        str(first_path),
                        "--dataset-id",
                        "synthetic-v1",
                        "--output",
                        str(output_path),
                    ]
                ),
                0,
            )
        original_report = output_path.read_bytes()

        stderr = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            self.assertEqual(
                dataset_cli(
                    [
                        "inspect",
                        "--input",
                        str(second_path),
                        "--dataset-id",
                        "synthetic-v2",
                        "--output",
                        str(output_path),
                    ]
                ),
                2,
            )
        self.assertEqual(output_path.read_bytes(), original_report)
        self.assertFalse(json.loads(stderr.getvalue())["ok"])

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(
                dataset_cli(
                    [
                        "inspect",
                        "--input",
                        str(second_path),
                        "--dataset-id",
                        "synthetic-v2",
                        "--output",
                        str(output_path),
                        "--force",
                    ]
                ),
                0,
            )
        replaced = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(replaced["dataset_id"], "synthetic-v2")
        self.assertEqual(replaced["content_sha256"], hashlib.sha256(second).hexdigest())

    def test_cli_never_overwrites_input_or_its_hardlink(self) -> None:
        content = _jsonl(_row("i", "question", "SELECT 1"))
        input_path = self.write_bytes("dataset.csv", content)

        for output_path in (input_path, self.root / "dataset-hardlink.csv"):
            if output_path != input_path:
                try:
                    os.link(input_path, output_path)
                except OSError as exc:
                    self.skipTest(f"hard links unavailable: {exc}")
            with self.subTest(output=output_path.name):
                stderr = io.StringIO()
                with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                    exit_code = dataset_cli(
                        [
                            "inspect",
                            "--input",
                            str(input_path),
                            "--dataset-id",
                            "synthetic-v1",
                            "--output",
                            str(output_path),
                            "--force",
                        ]
                    )
                self.assertEqual(exit_code, 2)
                self.assertEqual(input_path.read_bytes(), content)
                self.assertFalse(json.loads(stderr.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
