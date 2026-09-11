from __future__ import annotations

import unittest

from backend.adaptive.dataset.contracts import DatasetRecord
from backend.adaptive.dataset.sql_classifier import classify_ir_compatibility
from backend.adaptive.dataset.sql_contracts import (
    IrCompatibilityStatus,
    IrReasonCode,
    SqlAnalysisIssueCode,
)
from backend.adaptive.dataset.sql_features import extract_sql_features
from backend.adaptive.dataset.sql_parser import parse_and_canonicalize


def _record(sql: str, *, line: int = 1) -> DatasetRecord:
    return DatasetRecord(
        source_line=line,
        instruction="synthetic",
        question="synthetic question",
        sql=sql,
    )


def _parsed(sql: str):
    parsed, issue = parse_and_canonicalize(_record(sql))
    if issue is not None:
        raise AssertionError(f"unexpected parse issue: {issue}")
    if parsed is None:
        raise AssertionError("parser returned neither a result nor an issue")
    return parsed


def _classified(sql: str):
    parsed = _parsed(sql)
    features = extract_sql_features(parsed.expression)
    return classify_ir_compatibility(parsed.expression, features)


class SqlParserCanonicalizerTest(unittest.TestCase):
    def test_canonical_hash_ignores_formatting_and_comments(self) -> None:
        compact = _parsed("select name from courses where credits>=3")
        formatted = _parsed(
            "-- comment with a value 999\n"
            "SELECT name\nFROM courses\nWHERE credits >= 3"
        )

        self.assertEqual(compact.canonical_sql, formatted.canonical_sql)
        self.assertEqual(compact.canonical_hash, formatted.canonical_hash)
        self.assertEqual(len(compact.canonical_hash), 64)
        self.assertEqual(len(compact.shape_hash), 64)

    def test_literal_values_change_canonical_hash_but_not_shape_hash(self) -> None:
        first = _parsed("SELECT name FROM courses WHERE credits = 3")
        second = _parsed("SELECT name FROM courses WHERE credits = 4")

        self.assertNotEqual(first.canonical_hash, second.canonical_hash)
        self.assertEqual(first.shape_hash, second.shape_hash)

    def test_shape_hash_preserves_literal_types(self) -> None:
        hashes = {
            _parsed(sql).shape_hash
            for sql in (
                "SELECT name FROM courses WHERE value = '1'",
                "SELECT name FROM courses WHERE value = 1",
                "SELECT name FROM courses WHERE value = 1.0",
                "SELECT name FROM courses WHERE value = TRUE",
                "SELECT name FROM courses WHERE value IS NULL",
            )
        }

        self.assertEqual(len(hashes), 5)

    def test_identifiers_aliases_quotes_and_order_are_not_over_normalized(self) -> None:
        variants = (
            'SELECT "Name" FROM courses WHERE a = 1 AND b = 2',
            "SELECT name FROM courses WHERE a = 1 AND b = 2",
            "SELECT name AS label FROM courses WHERE a = 1 AND b = 2",
            "SELECT name FROM courses WHERE b = 2 AND a = 1",
            "SELECT other_name FROM courses WHERE a = 1 AND b = 2",
        )

        self.assertEqual(len({_parsed(sql).canonical_hash for sql in variants}), 5)

    def test_parser_and_downstream_analysis_do_not_mutate_source_or_ast(self) -> None:
        record = _record(" SELECT name FROM courses WHERE credits = 3 ")
        raw_before = record.sql
        parsed, issue = parse_and_canonicalize(record)
        self.assertIsNone(issue)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        ast_before = parsed.expression.dump()

        features = extract_sql_features(parsed.expression)
        classify_ir_compatibility(parsed.expression, features)

        self.assertEqual(record.sql, raw_before)
        self.assertEqual(parsed.expression.dump(), ast_before)

    def test_parse_failures_are_structured_and_do_not_echo_input(self) -> None:
        cases = (
            ("", SqlAnalysisIssueCode.EMPTY_SQL),
            (" \t\r\n", SqlAnalysisIssueCode.WHITESPACE_ONLY_SQL),
            ("SELECT 'private-value' FROM", SqlAnalysisIssueCode.PARSE_ERROR),
            ("SELECT 1; SELECT 2", SqlAnalysisIssueCode.MULTIPLE_STATEMENTS),
            (
                "INSERT INTO courses VALUES ('private-value')",
                SqlAnalysisIssueCode.NON_SELECT_STATEMENT,
            ),
            (
                "UPDATE courses SET name = 'private-value'",
                SqlAnalysisIssueCode.NON_SELECT_STATEMENT,
            ),
            ("DELETE FROM courses", SqlAnalysisIssueCode.NON_SELECT_STATEMENT),
            ("CREATE TABLE secret_name (value TEXT)", SqlAnalysisIssueCode.NON_SELECT_STATEMENT),
            ("BEGIN", SqlAnalysisIssueCode.NON_SELECT_STATEMENT),
            ("TRUNCATE TABLE courses", SqlAnalysisIssueCode.NON_SELECT_STATEMENT),
            ("PRAGMA query_only", SqlAnalysisIssueCode.NON_SELECT_STATEMENT),
            (
                "ATTACH DATABASE 'private-value' AS private_db",
                SqlAnalysisIssueCode.PARSE_ERROR,
            ),
        )

        for line, (sql, expected_code) in enumerate(cases, start=7):
            with self.subTest(code=expected_code):
                parsed, issue = parse_and_canonicalize(_record(sql, line=line))
                self.assertIsNone(parsed)
                self.assertIsNotNone(issue)
                assert issue is not None
                self.assertEqual(issue.code, expected_code)
                self.assertEqual(issue.line_number, line)
                self.assertNotIn("private-value", issue.message)


class SqlFeatureExtractionTest(unittest.TestCase):
    def test_extracts_references_projections_predicates_and_literals(self) -> None:
        parsed = _parsed(
            "SELECT c.name AS course_name, c.capacity - c.enrolled "
            "FROM courses AS c "
            "WHERE c.credits >= 3 AND c.active = TRUE AND c.note IS NULL"
        )
        before = parsed.expression.dump()

        features = extract_sql_features(parsed.expression)

        self.assertEqual(features.statement_kind, "SELECT")
        self.assertEqual(features.root_kind, "SELECT")
        self.assertEqual(features.table_references, ["courses"])
        self.assertEqual(features.table_reference_count, 1)
        self.assertEqual(features.projection_count, 2)
        self.assertEqual(features.projection_alias_count, 1)
        self.assertEqual(features.expression_projection_count, 1)
        self.assertTrue(features.has_where)
        self.assertGreaterEqual(features.predicate_count, 3)
        self.assertEqual(sum(features.arithmetic_operators.values()), 1)
        self.assertGreaterEqual(sum(features.comparison_operators.values()), 3)
        self.assertEqual(features.literal_types.get("INTEGER"), 1)
        self.assertEqual(features.literal_types.get("BOOLEAN"), 1)
        self.assertEqual(features.literal_types.get("NULL"), 1)
        self.assertEqual(parsed.expression.dump(), before)

    def test_counts_aggregate_and_general_functions_without_double_counting(self) -> None:
        features = extract_sql_features(
            _parsed(
                "SELECT COUNT(id), LOWER(name), COALESCE(code, 'none') "
                "FROM courses"
            ).expression
        )

        self.assertEqual(sum(features.aggregate_functions.values()), 1)
        self.assertEqual(sum(features.functions.values()), 2)

    def test_extracts_query_clauses_and_advanced_structures(self) -> None:
        features = extract_sql_features(
            _parsed(
                "WITH ranked AS ("
                "SELECT c.id, ROW_NUMBER() OVER (ORDER BY c.id) AS rn "
                "FROM courses c JOIN departments d ON c.department_id = d.id"
                ") SELECT DISTINCT id FROM ranked "
                "WHERE id IN (SELECT course_id FROM registrations) "
                "GROUP BY id HAVING COUNT(*) > 0 ORDER BY id LIMIT 10 OFFSET 2"
            ).expression
        )

        self.assertTrue(features.distinct)
        self.assertTrue(features.group_by)
        self.assertTrue(features.having)
        self.assertTrue(features.order_by)
        self.assertTrue(features.limit)
        self.assertTrue(features.offset)
        self.assertEqual(features.join_count, 1)
        self.assertGreaterEqual(features.subquery_count, 1)
        self.assertEqual(features.cte_count, 1)
        self.assertEqual(features.window_function_count, 1)

    def test_counts_set_operations_wildcards_placeholders_and_operators(self) -> None:
        set_features = extract_sql_features(
            _parsed(
                "SELECT id FROM a UNION SELECT id FROM b ORDER BY id LIMIT 5 OFFSET 1"
            ).expression
        )
        predicate_features = extract_sql_features(
            _parsed(
                "SELECT * FROM courses WHERE NOT ("
                "a != ? OR b < 2 OR c <= 3 OR d > 4 OR e >= 5 "
                "OR f LIKE '%x%' OR g IN (1, 2) OR h BETWEEN 1 AND 3 OR i IS NULL)"
            ).expression
        )

        self.assertEqual(set_features.set_operation_count, 1)
        self.assertTrue(set_features.order_by)
        self.assertTrue(set_features.limit)
        self.assertTrue(set_features.offset)
        self.assertEqual(predicate_features.wildcard_count, 1)
        self.assertEqual(predicate_features.placeholder_count, 1)
        self.assertGreaterEqual(sum(predicate_features.boolean_operators.values()), 8)
        self.assertGreaterEqual(sum(predicate_features.comparison_operators.values()), 9)

    def test_is_not_null_counts_semantic_negation(self) -> None:
        parsed = _parsed("SELECT id FROM courses WHERE archived_at IS NOT NULL")
        features = extract_sql_features(parsed.expression)
        compatibility = classify_ir_compatibility(parsed.expression, features)

        self.assertEqual(features.boolean_operators.get("NOT"), 1)
        self.assertIn(IrReasonCode.IS_NULL_PREDICATE, compatibility.reason_codes)
        self.assertIn(IrReasonCode.NOT_PREDICATE, compatibility.reason_codes)


class SqlIrCompatibilityClassifierTest(unittest.TestCase):
    def test_simple_select_and_supported_filters_are_current_ir_candidates(self) -> None:
        compatibility = _classified(
            "SELECT name, credits FROM courses "
            "WHERE name LIKE '%database%' AND credits >= 3 LIMIT 20"
        )

        self.assertEqual(
            compatibility.status,
            IrCompatibilityStatus.CURRENT_IR_CANDIDATE,
        )
        self.assertEqual(compatibility.reason_codes, [])

    def test_ir_extension_required_features_preserve_all_reasons_in_enum_order(self) -> None:
        compatibility = _classified(
            "SELECT DISTINCT department, COUNT(*) "
            "FROM courses WHERE credits = 3 OR enrolled > 10 "
            "GROUP BY department HAVING COUNT(*) > 1 "
            "ORDER BY department OFFSET 2"
        )

        self.assertEqual(
            compatibility.status,
            IrCompatibilityStatus.IR_EXTENSION_REQUIRED,
        )
        reasons = compatibility.reason_codes
        self.assertIn(IrReasonCode.AGGREGATE, reasons)
        self.assertIn(IrReasonCode.DISTINCT, reasons)
        self.assertIn(IrReasonCode.GROUP_BY, reasons)
        self.assertIn(IrReasonCode.HAVING, reasons)
        self.assertIn(IrReasonCode.ORDER_BY, reasons)
        self.assertIn(IrReasonCode.OFFSET, reasons)
        self.assertIn(IrReasonCode.OR_PREDICATE, reasons)
        self.assertEqual(reasons, sorted(reasons, key=list(IrReasonCode).index))

    def test_projection_and_like_ambiguities_require_manual_review(self) -> None:
        cases = (
            (
                "SELECT * FROM courses",
                IrReasonCode.WILDCARD_PROJECTION,
                IrCompatibilityStatus.MANUAL_REVIEW,
            ),
            (
                "SELECT capacity - enrolled FROM courses",
                IrReasonCode.EXPRESSION_PROJECTION,
                IrCompatibilityStatus.IR_EXTENSION_REQUIRED,
            ),
            (
                "SELECT LOWER(name) FROM courses",
                IrReasonCode.FUNCTION_PROJECTION,
                IrCompatibilityStatus.MANUAL_REVIEW,
            ),
            (
                "SELECT name AS label FROM courses",
                IrReasonCode.RESULT_ALIAS,
                IrCompatibilityStatus.MANUAL_REVIEW,
            ),
            (
                "SELECT name FROM courses WHERE name LIKE 'database%'",
                IrReasonCode.LIKE_PREFIX_PATTERN,
                IrCompatibilityStatus.MANUAL_REVIEW,
            ),
            (
                "SELECT name FROM courses WHERE name LIKE '%database'",
                IrReasonCode.LIKE_SUFFIX_PATTERN,
                IrCompatibilityStatus.MANUAL_REVIEW,
            ),
            (
                "SELECT name FROM courses WHERE name LIKE 'database'",
                IrReasonCode.LIKE_EXACT_PATTERN,
                IrCompatibilityStatus.MANUAL_REVIEW,
            ),
            (
                "SELECT name FROM courses WHERE name LIKE '%data_base%'",
                IrReasonCode.LIKE_COMPLEX_PATTERN,
                IrCompatibilityStatus.MANUAL_REVIEW,
            ),
            (
                "SELECT name FROM courses WHERE credits = other_credits",
                IrReasonCode.NON_LITERAL_FILTER_VALUE,
                IrCompatibilityStatus.MANUAL_REVIEW,
            ),
            (
                "SELECT name FROM courses LIMIT 201",
                IrReasonCode.LIMIT_OUT_OF_RANGE,
                IrCompatibilityStatus.MANUAL_REVIEW,
            ),
        )

        for sql, reason, expected_status in cases:
            with self.subTest(reason=reason):
                compatibility = _classified(sql)
                self.assertEqual(compatibility.status, expected_status)
                self.assertIn(reason, compatibility.reason_codes)

    def test_additional_predicates_and_joins_require_ir_extension(self) -> None:
        compatibility = _classified(
            "SELECT c.name FROM courses c JOIN departments d ON c.department_id = d.id "
            "WHERE NOT c.id IN (1, 2) AND c.credits BETWEEN 1 AND 3 "
            "AND c.note IS NULL"
        )

        self.assertEqual(
            compatibility.status,
            IrCompatibilityStatus.IR_EXTENSION_REQUIRED,
        )
        for reason in (
            IrReasonCode.JOIN,
            IrReasonCode.MULTIPLE_TABLES,
            IrReasonCode.NOT_PREDICATE,
            IrReasonCode.IN_PREDICATE,
            IrReasonCode.BETWEEN_PREDICATE,
            IrReasonCode.IS_NULL_PREDICATE,
        ):
            self.assertIn(reason, compatibility.reason_codes)

    def test_unsafe_or_out_of_scope_structures_are_not_candidates(self) -> None:
        cases = (
            ("WITH x AS (SELECT id FROM courses) SELECT id FROM x", IrReasonCode.CTE),
            (
                "SELECT id FROM courses WHERE id IN (SELECT id FROM archived)",
                IrReasonCode.SUBQUERY,
            ),
            (
                "SELECT ROW_NUMBER() OVER (ORDER BY id) FROM courses",
                IrReasonCode.WINDOW_FUNCTION,
            ),
            (
                "SELECT id FROM current_courses UNION SELECT id FROM archived",
                IrReasonCode.SET_OPERATION,
            ),
        )

        for sql, reason in cases:
            with self.subTest(reason=reason):
                compatibility = _classified(sql)
                self.assertEqual(
                    compatibility.status,
                    IrCompatibilityStatus.UNSUPPORTED_OR_UNSAFE,
                )

    def test_write_capable_select_clauses_are_unsafe(self) -> None:
        cases = (
            (
                "SELECT id FROM courses FOR UPDATE",
                IrReasonCode.LOCKING_CLAUSE,
            ),
            (
                "SELECT id INTO archived_courses FROM courses",
                IrReasonCode.SELECT_INTO,
            ),
        )
        for sql, reason in cases:
            with self.subTest(reason=reason):
                compatibility = _classified(sql)
                self.assertEqual(
                    compatibility.status,
                    IrCompatibilityStatus.UNSUPPORTED_OR_UNSAFE,
                )
                self.assertIn(reason, compatibility.reason_codes)
                self.assertIn(reason, compatibility.reason_codes)

    def test_reason_order_is_deterministic_across_repeated_analysis(self) -> None:
        sql = (
            "SELECT DISTINCT capacity - enrolled AS seats FROM courses "
            "WHERE name LIKE 'db%' OR credits IN (1, 2) "
            "ORDER BY seats OFFSET 1"
        )

        results = [_classified(sql) for _ in range(3)]

        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])


if __name__ == "__main__":
    unittest.main()
