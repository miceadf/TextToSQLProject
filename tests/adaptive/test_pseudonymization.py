from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from backend.adaptive.dataset.cli import main
from backend.adaptive.dataset.conversion_contracts import (
    ConversionStatus,
    DuplicateProvenance,
    LimitProvenance,
    PrivateConvertedRecord,
)
from backend.adaptive.dataset.pseudonym_contracts import (
    PseudonymPolicy,
    PseudonymReasonCode,
)
from backend.adaptive.dataset.pseudonym_loader import load_private_converted_jsonl
from backend.adaptive.dataset.pseudonym_profile import (
    load_pseudonymization_profile,
    read_secret_key,
)
from backend.adaptive.dataset.pseudonym_report import build_pseudonym_summary
from backend.adaptive.dataset.pseudonym_token import generate_token_map, lineage_token
from backend.adaptive.dataset.pseudonym_transform import pseudonymize_records
from backend.adaptive.harness.contracts import FilterIR, QueryIR
from backend.adaptive.ontology.contracts import Operator
from backend.adaptive.ontology.loader import load_default_ontology


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "backend/adaptive/dataset/profiles/cnu-pseudonymization-v1.yaml"


def _record(
    line: int,
    *,
    name: str = "Kim",
    question: str = "Kim 교수 과목 보여줘",
    instruction: str = "질문을 해석하세요",
) -> PrivateConvertedRecord:
    return PrivateConvertedRecord(
        dataset_id="synthetic-v1",
        dataset_content_sha256="a" * 64,
        profile_id="source-profile-v1",
        profile_version=1,
        ontology_version=1,
        source_line=line,
        canonical_hash="b" * 64,
        shape_hash="c" * 64,
        duplicate_provenance=DuplicateProvenance(),
        instruction=instruction,
        question=question,
        query_ir=QueryIR(
            entity="CourseOffering",
            select=["Course.name"],
            filters=[FilterIR(field="Instructor.name", operator=Operator.EQUALS, value=name)],
            limit=50,
        ),
        conversion_status=ConversionStatus.CONVERTED_WITH_EXECUTION_POLICY,
        limit_provenance=LimitProvenance.EXECUTION_POLICY_DEFAULT,
    )


class PseudonymizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ontology = load_default_ontology()
        cls.profile = load_pseudonymization_profile(PROFILE, cls.ontology)

    def setUp(self) -> None:
        self.key = os.urandom(32)

    def _run(self, records: list[PrivateConvertedRecord]):
        return pseudonymize_records(
            records, self.profile, self.ontology, self.key, "synthetic-v1", "test-key"
        )

    def test_profile_covers_initial_ten_fields(self) -> None:
        self.assertEqual(len(self.profile.field_policies), 10)
        self.assertEqual(
            sum(item.policy.value == "PSEUDONYMIZE" for item in self.profile.field_policies), 4
        )

    def test_token_is_deterministic_domain_separated_and_order_independent(self) -> None:
        pairs = {("instructor-name", "Kim"), ("course-name", "Kim"), ("instructor-name", "Lee")}
        first = generate_token_map(pairs, self.key, self.profile)
        second = generate_token_map(set(reversed(sorted(pairs))), self.key, self.profile)
        self.assertEqual(first, second)
        self.assertNotEqual(first[("instructor-name", "Kim")], first[("course-name", "Kim")])
        self.assertNotEqual(
            first[("instructor-name", "Kim")],
            generate_token_map(pairs, os.urandom(32), self.profile)[("instructor-name", "Kim")],
        )
        self.assertTrue(first[("instructor-name", "Kim")].startswith("<INSTRUCTOR_NAME_"))
        self.assertNotIn("Kim", lineage_token("record", "Kim", self.key, self.profile))

    def test_git_tracked_key_hardlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            tracked = root / "tracked-key.bin"
            tracked.write_bytes(self.key)
            subprocess.run(["git", "add", "tracked-key.bin"], cwd=root, check=True)
            alias = root / "alias-key.bin"
            try:
                os.link(tracked, alias)
            except OSError:
                self.skipTest("hardlinks unavailable")
            with self.assertRaisesRegex(ValueError, "Git-tracked"):
                read_secret_key(alias, root)

    def test_forced_truncated_hmac_collision_extends_all_members(self) -> None:
        def digest(_domain, _namespace, value, _key, _profile):
            return "A" * 12 + ("1" if value == "Kim" else "2") + "B" * 51

        with patch("backend.adaptive.dataset.pseudonym_token._digest", side_effect=digest):
            tokens = generate_token_map(
                {("instructor-name", "Kim"), ("instructor-name", "Lee")},
                self.key,
                self.profile,
            )
        self.assertEqual(len(set(tokens.values())), 2)
        self.assertTrue(all(len(token.rsplit("_", 1)[-1]) == 15 for token in tokens.values()))

    def test_question_instruction_and_ir_change_together(self) -> None:
        raw = _record(1, question="Kim 교수 Kim", instruction="Kim 검색")
        run = self._run([raw])
        self.assertEqual(len(run.records), 1)
        out = run.records[0]
        token = out.query_ir.filters[0].value
        self.assertEqual(out.question, f"{token} 교수 {token}")
        self.assertEqual(out.instruction, f"{token} 검색")
        self.assertEqual(out.query_ir.select, raw.query_ir.select)
        self.assertEqual(out.query_ir.limit, raw.query_ir.limit)
        self.assertEqual(raw.question, "Kim 교수 Kim")
        self.assertEqual(raw.query_ir.filters[0].value, "Kim")
        self.assertNotIn("Kim", out.model_dump_json())
        self.assertNotIn("source_line", type(out).model_fields)
        self.assertNotIn("canonical_hash", type(out).model_fields)

    def test_missing_text_match_goes_to_review_without_partial_record(self) -> None:
        run = self._run([_record(1, question="다른 교수 과목")])
        self.assertEqual(len(run.records), 0)
        self.assertIn(
            PseudonymReasonCode.SENSITIVE_VALUE_NOT_FOUND_IN_TEXT,
            run.review_items[0].reason_codes,
        )

    def test_ascii_identifier_boundary(self) -> None:
        raw = _record(1, name="CS101", question="XCS101Y 과목")
        run = self._run([raw])
        self.assertEqual(len(run.records), 0)
        self.assertIn(
            PseudonymReasonCode.SENSITIVE_VALUE_NOT_FOUND_IN_TEXT,
            run.review_items[0].reason_codes,
        )
        hyphenated = self._run([_record(2, name="CS-101", question="XCS-101Y 과목")])
        self.assertEqual(len(hyphenated.records), 0)
        self.assertIn(
            PseudonymReasonCode.SENSITIVE_VALUE_NOT_FOUND_IN_TEXT,
            hyphenated.review_items[0].reason_codes,
        )

    def test_same_surface_in_two_namespaces_is_reviewed(self) -> None:
        first = _record(1)
        second = _record(2)
        second.query_ir.filters[0].field = "Course.name"
        run = self._run([first, second])
        self.assertEqual(len(run.records), 0)
        self.assertTrue(all(
            PseudonymReasonCode.AMBIGUOUS_SURFACE_NAMESPACE in item.reason_codes
            for item in run.review_items
        ))

    def test_nfc_collision_and_korean_overlap_are_reviewed(self) -> None:
        nfc = self._run([
            _record(1, name="é", question="é 교수"),
            _record(2, name="e\u0301", question="e\u0301 교수"),
        ])
        self.assertEqual(len(nfc.records), 0)
        self.assertTrue(all(
            PseudonymReasonCode.NORMALIZATION_COLLISION in item.reason_codes
            for item in nfc.review_items
        ))
        overlap = self._run([
            _record(3, name="컴퓨터공학", question="컴퓨터공학 과목"),
            _record(4, name="컴퓨터", question="컴퓨터 과목"),
        ])
        self.assertEqual(len(overlap.records), 1)
        self.assertTrue(any(
            PseudonymReasonCode.OVERLAPPING_TEXT_MATCH in item.reason_codes
            for item in overlap.review_items
        ))

    def test_preserve_review_and_non_string_sensitive_values(self) -> None:
        preserved = _record(1, name="전공", question="전공 강의")
        preserved.query_ir.filters[0].field = "Course.category"
        preserved_run = self._run([preserved])
        self.assertEqual(len(preserved_run.records), 1)
        self.assertEqual(preserved_run.records[0].query_ir.filters[0].value, "전공")

        policy = self.profile.model_copy(deep=True)
        next(item for item in policy.field_policies if item.field == "Course.category").policy = PseudonymPolicy.REVIEW
        review_run = pseudonymize_records(
            [preserved], policy, self.ontology, self.key, "synthetic-v1", "test-key"
        )
        self.assertEqual(len(review_run.records), 0)
        self.assertIn(PseudonymReasonCode.FIELD_REQUIRES_REVIEW, review_run.review_items[0].reason_codes)

        non_string = _record(2, question="Kim 교수")
        non_string.query_ir.filters[0].value = 12
        non_string_run = self._run([non_string])
        self.assertEqual(len(non_string_run.records), 0)
        self.assertIn(
            PseudonymReasonCode.NON_STRING_SENSITIVE_VALUE,
            non_string_run.review_items[0].reason_codes,
        )

    def test_duplicate_lineage_preserved_but_conflict_blocked(self) -> None:
        first = _record(1)
        second = _record(2)
        for record in (first, second):
            record.duplicate_provenance.question_sql_duplicate_group_id = "private-group"
        run = self._run([first, second])
        self.assertEqual(len(run.records), 2)
        self.assertEqual(
            run.records[0].question_sql_duplicate_lineage,
            run.records[1].question_sql_duplicate_lineage,
        )
        self.assertNotEqual(run.records[0].record_id, run.records[1].record_id)
        second.duplicate_provenance.question_conflict_group_id = "conflict"
        blocked = self._run([first, second])
        self.assertEqual(len(blocked.records), 1)
        self.assertIn(
            PseudonymReasonCode.QUESTION_SQL_CONFLICT_PRESENT,
            blocked.review_items[0].reason_codes,
        )

    def test_global_lexicon_does_not_silently_rewrite_unrelated_question(self) -> None:
        first = _record(1, name="Kim", question="Kim 교수")
        second = _record(2, name="Lee", question="Lee 교수와 Kim 교수")
        run = self._run([first, second])
        self.assertEqual(len(run.records), 1)
        self.assertEqual(len(run.review_items), 1)
        self.assertIn(PseudonymReasonCode.QUESTION_IR_TOKEN_MISMATCH, run.review_items[0].reason_codes)

    def test_order_independent_output_and_public_report_has_no_private_text(self) -> None:
        records = [_record(1, name="Kim"), _record(2, name="Lee", question="Lee 교수")]
        first = self._run(records)
        second = self._run(list(reversed(records)))
        self.assertEqual(first.records, second.records)
        report = build_pseudonym_summary(
            first,
            dataset_id="synthetic-v1",
            dataset_fingerprint="a" * 64,
            input_artifact_sha256="d" * 64,
            profile=self.profile,
            key_id="test-key",
        )
        public = report.model_dump_json()
        self.assertNotIn("Kim", public)
        self.assertNotIn("Lee", public)
        self.assertNotIn("source_line", public)
        self.assertEqual(report.output_record_count, 2)
        self.assertEqual(report.known_value_leak_count, 0)

    def test_private_loader_recovers_after_malformed_line_without_exposing_value(self) -> None:
        record = _record(1)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "private.jsonl"
            source.write_text(
                "{SECRET malformed\n"
                + json.dumps(record.model_dump(mode="json"), ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )
            original = source.read_bytes()
            loaded = load_private_converted_jsonl(source)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(len(loaded.records), 1)
            self.assertEqual(loaded.issues[0].line_number, 1)
            self.assertNotIn("SECRET", loaded.issues[0].message)
            self.assertEqual(loaded.content_sha256, hashlib.sha256(original).hexdigest())

    def test_private_loader_rejects_coerced_types(self) -> None:
        raw = _record(1).model_dump(mode="json")
        raw["source_line"] = "1"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "private.jsonl"
            source.write_text(json.dumps(raw) + "\n", encoding="utf-8")
            loaded = load_private_converted_jsonl(source)
            self.assertEqual(len(loaded.records), 0)
            self.assertEqual(loaded.issues[0].code.value, "INVALID_RECORD")

    def test_private_loader_bom_non_object_and_invalid_utf8(self) -> None:
        raw = _record(1).model_dump(mode="json")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "private.jsonl"
            source.write_bytes(
                b"\xef\xbb\xbf"
                + json.dumps(raw, ensure_ascii=False).encode("utf-8")
                + b"\n[]\n\xff\n"
            )
            loaded = load_private_converted_jsonl(source)
            self.assertEqual(len(loaded.records), 1)
            self.assertEqual([issue.code.value for issue in loaded.issues], ["NON_OBJECT", "INVALID_UTF8"])

    def test_cli_keeps_malformed_input_line_in_private_review(self) -> None:
        record = _record(1)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "private.jsonl"
            source.write_text(
                "{PRIVATE BAD JSON\n"
                + json.dumps(record.model_dump(mode="json"), ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )
            key_file = folder / "key.bin"
            key_file.write_bytes(os.urandom(32))
            captured = io.StringIO()
            with patch("backend.adaptive.dataset.cli.write_pseudonym_bundle") as writer:
                writer.return_value = folder / "manifest.json"
                with redirect_stdout(captured):
                    code = main([
                        "pseudonymize-ir", "--input", str(source),
                        "--dataset-id", "synthetic-v1", "--profile", str(PROFILE),
                        "--ontology", str(ROOT / "backend/adaptive/ontology/course-domain.yaml"),
                        "--key-file", str(key_file), "--key-id", "test-key",
                        "--output-dir", str(folder / "output"),
                    ])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(captured.getvalue())["invalid_input_lines"], 1)
            review = writer.call_args.args[3]
            self.assertEqual(len(review), 1)
            self.assertEqual(review[0].input_line, 1)
            self.assertIsNone(review[0].source_line)


if __name__ == "__main__":
    unittest.main()
