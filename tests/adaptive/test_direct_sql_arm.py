"""Phase 5-18: evaluation-only direct-SQL arm and paired arm comparison."""

from __future__ import annotations

import ast
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from backend.adaptive.catalog.contracts import DatabaseCatalog
from backend.adaptive.compiler import SchemaAdaptiveSQLiteCompiler
from backend.adaptive.evaluation.arm_comparison import ArmComparisonError, compare_arms
from backend.adaptive.evaluation.direct_sql import (
    DIRECT_SQL_TEMPLATE_SHA256,
    DirectSqlOutcome,
    DirectSqlProviderError,
    DirectSqlRejected,
    DirectSqlRequest,
    FakeDirectSqlModel,
    build_direct_sql_prompt,
    check_direct_sql,
    extract_sql,
    gold_row_multiset,
    normalize_result_value,
    positional_row_multiset,
    run_direct_sql_evaluation,
)
from backend.adaptive.evaluation.direct_sql_ollama import OllamaDirectSqlModel
from backend.adaptive.evaluation.loader import load_evaluation_set
from backend.adaptive.evaluation.run_contracts import EvaluationRunConfig
from backend.adaptive.evaluation.runner import _prepare_fixture_contexts, run_evaluation
from backend.adaptive.fixtures import build_fixture_bundle
from backend.adaptive.fixtures.contracts import FixtureVariant
from backend.adaptive.fixtures.seed import DEFAULT_SEED
from backend.adaptive.harness.model import FakeModel
from backend.adaptive.mapping import to_harness_mapping
from backend.adaptive.ontology.loader import load_default_ontology
from backend.adaptive.api.ollama_startup import parse_ollama_startup_config

REPO = Path(__file__).resolve().parents[2]
GOLD_PATH = REPO / "backend" / "adaptive" / "evaluation" / "data" / "fixture_v1_cases.yaml"
VARIANTS = ["flat", "normalized", "legacy"]
SECRET = "p518secret"


@pytest.fixture(scope="module")
def ontology() -> Any:
    return load_default_ontology()


@pytest.fixture(scope="module")
def gold(ontology: Any) -> Any:
    return load_evaluation_set(GOLD_PATH, ontology=ontology)


@pytest.fixture(scope="module")
def bundle(tmp_path_factory: Any, ontology: Any) -> Any:
    return build_fixture_bundle(tmp_path_factory.mktemp("p518") / "databases", ontology=ontology)


@pytest.fixture(scope="module")
def contexts(bundle: Any, ontology: Any) -> Any:
    prepared, _metadata = _prepare_fixture_contexts(bundle, DEFAULT_SEED, ontology, None)
    return prepared


def _config(run_id: str, *, oracle: bool, label: str = "synthetic-label") -> EvaluationRunConfig:
    return EvaluationRunConfig(
        model_run_id=run_id, model_label=label, variants=VARIANTS, oracle_smoke=oracle
    )


def _literal(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _inline(sql: str, parameters: list[Any]) -> str:
    parts = sql.split("?")
    assert len(parts) == len(parameters) + 1
    out = parts[0]
    for value, rest in zip(parameters, parts[1:]):
        out += _literal(value) + rest
    return out


def _oracle_sql(gold: Any, contexts: Any) -> dict[tuple[str, str], str]:
    responses: dict[tuple[str, str], str] = {}
    for variant in contexts:
        artifact, graph, mapping = contexts[variant]
        compiler = SchemaAdaptiveSQLiteCompiler(graph, mapping)
        for case in gold.cases:
            compiled = compiler.compile(case.gold_query_ir, to_harness_mapping(mapping))
            responses[(artifact.database_id, case.question_ko)] = (
                "```sql\n" + _inline(compiled.sql, list(compiled.parameters)) + ";\n```"
            )
    return responses


# --------------------------------------------------------------------------
# prompt


def test_prompt_is_deterministic_and_schema_only(contexts: Any) -> None:
    artifact = contexts[FixtureVariant.LEGACY][0]
    catalog = DatabaseCatalog.model_validate_json(artifact.catalog_path.read_text(encoding="utf-8"))
    question = "질문 {schema} {question} 테스트"
    first = build_direct_sql_prompt(catalog, question)
    assert first == build_direct_sql_prompt(catalog, question)
    for table in catalog.tables:
        assert f"{table.name}(" in first
        for column in table.columns:
            assert column.name in first
    assert "Question: 질문 {schema} {question} 테스트\nSQL:" in first
    assert first.startswith("You are a strict SQL generator for a SQLite database.")
    assert len(DIRECT_SQL_TEMPLATE_SHA256) == 64
    for marker in ("v_course_info", "LIKE '%", "전공"):
        assert marker not in first


def test_prompt_contains_foreign_keys(contexts: Any) -> None:
    artifact = contexts[FixtureVariant.NORMALIZED][0]
    catalog = DatabaseCatalog.model_validate_json(artifact.catalog_path.read_text(encoding="utf-8"))
    prompt = build_direct_sql_prompt(catalog, "q")
    assert "FOREIGN KEY (" in prompt and "REFERENCES" in prompt


# --------------------------------------------------------------------------
# extraction


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("SELECT 1", "SELECT 1"),
        ("  SELECT 1;  ", "SELECT 1"),
        ("SQL: SELECT 1;", "SELECT 1"),
        ("```sql\nSELECT 1;\n```", "SELECT 1"),
        ("Here:\n```\nSELECT a FROM t\n```\nthanks", "SELECT a FROM t"),
        ("SELECT 1;;", "SELECT 1"),
    ],
)
def test_extract_accepts(raw: str, expected: str) -> None:
    assert extract_sql(raw) == expected


@pytest.mark.parametrize(
    "raw,outcome",
    [
        (None, DirectSqlOutcome.NON_STRING_OUTPUT),
        ({"sql": "SELECT 1"}, DirectSqlOutcome.NON_STRING_OUTPUT),
        ("   ", DirectSqlOutcome.EMPTY_OUTPUT),
        (";", DirectSqlOutcome.EMPTY_OUTPUT),
        ("UNKNOWN", DirectSqlOutcome.ABSTAIN),
        ("SQL: unknown;", DirectSqlOutcome.ABSTAIN),
        ("```sql\nSELECT 1\n```\n```sql\nSELECT 2\n```", DirectSqlOutcome.AMBIGUOUS_OUTPUT),
    ],
)
def test_extract_rejects(raw: Any, outcome: DirectSqlOutcome) -> None:
    with pytest.raises(DirectSqlRejected) as info:
        extract_sql(raw)
    assert info.value.outcome is outcome


# --------------------------------------------------------------------------
# policy

TABLES = frozenset({"lecture_info", "courses"})


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT sbjt_nm FROM lecture_info",
        "SELECT a.sbjt_nm FROM lecture_info AS a JOIN courses AS c ON a.x = c.y",
        "WITH t AS (SELECT sbjt_nm FROM lecture_info) SELECT * FROM t",
        "SELECT sbjt_nm FROM lecture_info WHERE x IN (SELECT y FROM courses)",
        "SELECT x FROM lecture_info UNION SELECT y FROM courses",
        "SELECT COUNT(*) FROM LECTURE_INFO",
    ],
)
def test_policy_allows_read_only_select(sql: str) -> None:
    check_direct_sql(sql, TABLES)


@pytest.mark.parametrize(
    "sql,outcome",
    [
        ("SELEC x FRM", DirectSqlOutcome.SQL_PARSE_ERROR),
        ("SELECT 1; SELECT 2", DirectSqlOutcome.SQL_MULTIPLE_STATEMENTS),
        ("DELETE FROM lecture_info", DirectSqlOutcome.SQL_NOT_SELECT),
        ("UPDATE lecture_info SET x = 1", DirectSqlOutcome.SQL_NOT_SELECT),
        ("DROP TABLE lecture_info", DirectSqlOutcome.SQL_NOT_SELECT),
        ("PRAGMA table_info(lecture_info)", DirectSqlOutcome.SQL_NOT_SELECT),
        ("ATTACH DATABASE 'x' AS y", DirectSqlOutcome.SQL_NOT_SELECT),
        ("VACUUM", DirectSqlOutcome.SQL_NOT_SELECT),
        ("SELECT * INTO t2 FROM lecture_info", DirectSqlOutcome.SQL_FORBIDDEN_NODE),
        ("SELECT * FROM sqlite_master", DirectSqlOutcome.SQL_UNKNOWN_TABLE),
        ("SELECT * FROM main.lecture_info", DirectSqlOutcome.SQL_UNKNOWN_TABLE),
        ("SELECT * FROM v_course_info", DirectSqlOutcome.SQL_UNKNOWN_TABLE),
        ("SELECT * FROM lecture_info JOIN other ON 1 = 1", DirectSqlOutcome.SQL_UNKNOWN_TABLE),
        (
            "SELECT name FROM sqlite_master WHERE EXISTS "
            "(WITH sqlite_master AS (SELECT 1) SELECT 1 FROM sqlite_master)",
            DirectSqlOutcome.SQL_UNKNOWN_TABLE,
        ),
        (
            "SELECT * FROM hidden WHERE EXISTS (WITH hidden AS (SELECT 1) SELECT 1 FROM hidden)",
            DirectSqlOutcome.SQL_UNKNOWN_TABLE,
        ),
        ("WITH sqlite_temp AS (SELECT 1) SELECT * FROM sqlite_temp", DirectSqlOutcome.SQL_UNKNOWN_TABLE),
    ],
)
def test_policy_rejects(sql: str, outcome: DirectSqlOutcome) -> None:
    with pytest.raises(DirectSqlRejected) as info:
        check_direct_sql(sql, TABLES)
    assert info.value.outcome is outcome


# --------------------------------------------------------------------------
# comparison


def test_normalization_and_multiset_rules() -> None:
    assert normalize_result_value(3) == normalize_result_value(3.0) == normalize_result_value(Decimal("3.00")) == "V:3"
    assert normalize_result_value(100) == "V:100"
    assert normalize_result_value(0.5) == "V:0.5"
    assert normalize_result_value(None) == "N:"
    assert normalize_result_value("NULL") == "V:NULL"
    assert positional_row_multiset([[None]]) != positional_row_multiset([["NULL"]])
    assert normalize_result_value(True) == "V:1"
    assert normalize_result_value("01") == "V:01"
    with pytest.raises(Exception):
        normalize_result_value(b"x")
    with pytest.raises(Exception):
        normalize_result_value(float("nan"))
    rows = [["a", 1], ["b", 2], ["a", 1]]
    swapped = [[2, "b"], [1, "a"], [1, "a"]]
    assert positional_row_multiset(rows) == positional_row_multiset(swapped)
    assert positional_row_multiset(rows) != positional_row_multiset(rows[:2])
    assert positional_row_multiset(rows) == Counter({("V:1", "V:a"): 2, ("V:2", "V:b"): 1})


# --------------------------------------------------------------------------
# runner


def test_oracle_sql_arm_is_self_consistent_and_pairs_with_ir_oracle(
    gold: Any, bundle: Any, contexts: Any, ontology: Any
) -> None:
    sql_report = run_direct_sql_evaluation(
        gold, FakeDirectSqlModel(_oracle_sql(gold, contexts)), _config("oracle-sql", oracle=True),
        bundle, ontology=ontology,
    )
    assert sql_report.totals.scheduled_attempts == 120
    assert sql_report.totals.execution_correct.count == 120, Counter(
        item.outcome.value for item in sql_report.attempts
    )
    assert sql_report.totals.valid_sql.count == 120
    assert "ORACLE_SMOKE_NOT_MODEL_ACCURACY" in sql_report.warnings
    assert "GOLD_NEEDS_HUMAN_REVIEW" in sql_report.warnings
    assert sql_report.prompt_template_sha256 == DIRECT_SQL_TEMPLATE_SHA256

    ir_report = run_evaluation(
        gold,
        FakeModel({case.question_ko: case.gold_query_ir.model_dump(mode="json") for case in gold.cases}),
        _config("oracle-ir", oracle=True),
        bundle,
        ontology=ontology,
    )
    comparison = compare_arms(ir_report, sql_report)
    assert comparison.totals.denominator == 120
    assert comparison.totals.both_correct == 120
    assert comparison.totals.ir_only == comparison.totals.sql_only == comparison.totals.neither == 0
    assert set(comparison.by_variant) == set(VARIANTS)
    assert comparison.same_model_label is True
    assert comparison.oracle_smoke is True
    assert "PAIRED_COUNTS_ONLY_NO_SIGNIFICANCE_CLAIM" in comparison.warnings


def test_failures_stay_in_denominator_with_fixed_codes(
    gold: Any, bundle: Any, contexts: Any, ontology: Any
) -> None:
    oracle = _oracle_sql(gold, contexts)
    first = gold.cases[0]
    flat_db = contexts[FixtureVariant.FLAT][0].database_id
    norm_db = contexts[FixtureVariant.NORMALIZED][0].database_id
    legacy_db = contexts[FixtureVariant.LEGACY][0].database_id
    responses: dict[tuple[str, str], Any] = dict(oracle)
    responses[(flat_db, first.question_ko)] = DirectSqlProviderError(DirectSqlOutcome.PROVIDER_TIMEOUT)
    responses[(norm_db, first.question_ko)] = f"DELETE FROM x -- {SECRET}"
    responses[(legacy_db, first.question_ko)] = "UNKNOWN"
    second = gold.cases[1]
    responses[(flat_db, second.question_ko)] = RuntimeError(SECRET)
    responses[(norm_db, second.question_ko)] = "SELECT 1, 2, 3, 4, 5, 6, 7"
    responses[(legacy_db, second.question_ko)] = "SELECT sbjt_nm FROM lecture_info WHERE 1 = 0"
    report = run_direct_sql_evaluation(
        gold, FakeDirectSqlModel(responses), _config("mixed", oracle=False), bundle, ontology=ontology
    )
    by_key = {(a.case_id, a.variant.value): a for a in report.attempts}
    assert by_key[(first.case_id, "flat")].outcome is DirectSqlOutcome.PROVIDER_TIMEOUT
    assert by_key[(first.case_id, "normalized")].outcome is DirectSqlOutcome.SQL_NOT_SELECT
    assert by_key[(first.case_id, "normalized")].sql_fingerprint is not None
    assert by_key[(first.case_id, "legacy")].outcome is DirectSqlOutcome.ABSTAIN
    assert by_key[(second.case_id, "flat")].outcome is DirectSqlOutcome.PROVIDER_ERROR
    assert by_key[(second.case_id, "normalized")].outcome is DirectSqlOutcome.COLUMN_COUNT_MISMATCH
    legacy_second = by_key[(second.case_id, "legacy")]
    assert legacy_second.execution_success is True
    assert legacy_second.outcome in {DirectSqlOutcome.INCORRECT_RESULT, DirectSqlOutcome.COLUMN_COUNT_MISMATCH}
    assert report.totals.scheduled_attempts == 120
    assert report.totals.execution_correct.count == 114
    assert report.totals.abstain.count == 1
    dumped = report.model_dump_json()
    for marker in (SECRET, "DELETE FROM", "SELECT 1, 2", "sbjt_nm", "lecture_info", first.question_ko):
        assert marker not in dumped


def test_executor_backstop_denies_non_catalog_reads(bundle: Any, contexts: Any) -> None:
    from backend.adaptive.evaluation.direct_sql import _CatalogBoundExecutor
    from backend.adaptive.harness.contracts import CompiledQuery
    from backend.adaptive.harness.errors import HarnessFailure

    artifact = contexts[FixtureVariant.LEGACY][0]
    executor = _CatalogBoundExecutor({artifact.database_id: artifact.database_path}, frozenset({"lecture_info"}))
    columns, rows = executor.execute(
        artifact.database_id, CompiledQuery(sql="SELECT sbjt_nm FROM lecture_info", parameters=[], expected_columns=[])
    )
    assert columns == ["sbjt_nm"] and rows
    with pytest.raises(HarnessFailure):
        executor.execute(
            artifact.database_id, CompiledQuery(sql="SELECT name FROM sqlite_master", parameters=[], expected_columns=[])
        )


def test_duplicate_result_columns_are_not_comparable(
    gold: Any, bundle: Any, contexts: Any, ontology: Any
) -> None:
    oracle = _oracle_sql(gold, contexts)
    case = gold.cases[0]
    legacy_db = contexts[FixtureVariant.LEGACY][0].database_id
    oracle[(legacy_db, case.question_ko)] = "SELECT sbjt_nm AS a, bunban AS a FROM lecture_info"
    report = run_direct_sql_evaluation(
        gold, FakeDirectSqlModel(oracle), _config("dup", oracle=False), bundle, ontology=ontology
    )
    attempt = next(a for a in report.attempts if a.case_id == case.case_id and a.variant.value == "legacy")
    assert attempt.outcome is DirectSqlOutcome.RESULT_DUPLICATE_COLUMNS
    assert attempt.correct is False


# --------------------------------------------------------------------------
# paired comparison guards


def test_comparison_refuses_mismatched_reports(
    gold: Any, bundle: Any, contexts: Any, ontology: Any
) -> None:
    sql_report = run_direct_sql_evaluation(
        gold, FakeDirectSqlModel(_oracle_sql(gold, contexts)), _config("a", oracle=True), bundle, ontology=ontology
    )
    ir_report = run_evaluation(
        gold,
        FakeModel({case.question_ko: case.gold_query_ir.model_dump(mode="json") for case in gold.cases}),
        _config("b", oracle=True, label="other-label"),
        bundle,
        ontology=ontology,
    )
    assert compare_arms(ir_report, sql_report).same_model_label is False
    for mutate in (
        lambda r: r.model_copy(update={"config": r.config.model_copy(update={"oracle_smoke": False})}),
        lambda r: r.model_copy(update={"attempts": [
            a.model_copy(update={"request_id": a.request_id + "x"}) if i == 0 else a
            for i, a in enumerate(r.attempts)
        ]}),
        lambda r: r.model_copy(update={"gold_set_fingerprint": "0" * 64}),
        lambda r: r.model_copy(update={"benchmark_version": r.benchmark_version + 1}),
        lambda r: r.model_copy(update={"attempts": r.attempts[:-1]}),
        lambda r: r.model_copy(update={"attempts": [*r.attempts, r.attempts[0]]}),
        lambda r: r.model_copy(update={
            "fixture_metadata": [m.model_copy(update={"catalog_fingerprint": "1" * 64}) for m in r.fixture_metadata]
        }),
    ):
        with pytest.raises(ArmComparisonError) as info:
            compare_arms(ir_report, mutate(sql_report))
        assert str(info.value) == "ARM_COMPARISON_MISMATCH"
    # The same truncated subset on both sides is still refused.
    keep = {(a.case_id, a.variant.value) for a in sql_report.attempts[:3]}
    ir_cut = ir_report.model_copy(update={"attempts": [a for a in ir_report.attempts if (a.case_id, str(getattr(a.variant, "value", a.variant))) in keep]})
    sql_cut = sql_report.model_copy(update={"attempts": [a for a in sql_report.attempts if (a.case_id, a.variant.value) in keep]})
    with pytest.raises(ArmComparisonError):
        compare_arms(ir_cut, sql_cut)


# --------------------------------------------------------------------------
# Ollama SQL provider (synthetic transport)


class _Transport:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, dict[str, Any], float]] = []

    def __call__(self, url: str, payload: dict[str, Any], timeout: float) -> Any:
        self.calls.append((url, payload, timeout))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _request() -> DirectSqlRequest:
    return DirectSqlRequest(request_id="r1", database_id="db", question="q", prompt="PROMPT-518")


def test_ollama_sql_provider_single_call_and_payload() -> None:
    transport = _Transport({"response": "SELECT 1", "done": True})
    config = parse_ollama_startup_config("synthetic-518:latest", None, "45")
    model = OllamaDirectSqlModel(config, transport=transport)
    assert model.generate_sql(_request()) == "SELECT 1"
    assert len(transport.calls) == 1
    url, payload, timeout = transport.calls[0]
    assert url == "http://127.0.0.1:11434/api/generate"
    assert payload == {
        "model": "synthetic-518:latest",
        "prompt": "PROMPT-518",
        "stream": False,
        "options": {"temperature": 0},
    }
    assert timeout == 45.0
    assert "127.0.0.1" not in repr(model) and "synthetic-518" not in repr(model)


@pytest.mark.parametrize(
    "outcome,expected",
    [
        (TimeoutError(SECRET), DirectSqlOutcome.PROVIDER_TIMEOUT),
        (RuntimeError(SECRET), DirectSqlOutcome.PROVIDER_ERROR),
        ({"error": SECRET}, DirectSqlOutcome.PROVIDER_ERROR),
        ({"response": 5}, DirectSqlOutcome.PROVIDER_ERROR),
        ("not-a-dict", DirectSqlOutcome.PROVIDER_ERROR),
    ],
)
def test_ollama_sql_provider_errors_are_fixed(outcome: Any, expected: DirectSqlOutcome) -> None:
    config = parse_ollama_startup_config("synthetic-518:latest")
    transport = _Transport(outcome)
    with pytest.raises(DirectSqlProviderError) as info:
        OllamaDirectSqlModel(config, transport=transport).generate_sql(_request())
    assert info.value.outcome is expected
    assert SECRET not in str(info.value) and info.value.__cause__ is None
    assert len(transport.calls) == 1


# --------------------------------------------------------------------------
# runtime isolation


def test_runtime_modules_do_not_import_direct_sql() -> None:
    for folder in ("api", "harness", "compiler", "policy", "registration", "mapping", "catalog"):
        for path in (REPO / "backend" / "adaptive" / folder).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    names = [node.module or "", *(alias.name for alias in node.names)]
                    for name in names:
                        assert "direct_sql" not in name, path
                        assert "arm_comparison" not in name, path
                        assert "ir_extract" not in name, path
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for banned in ("direct_sql", "arm_comparison", "ir_extract"):
                            assert banned not in alias.name, path


def test_ollama_sql_provider_uses_patchable_transport_factory(monkeypatch: Any) -> None:
    import backend.adaptive.api.ollama_startup as startup

    transport = _Transport({"response": "SELECT 1"})
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _config: transport)
    model = OllamaDirectSqlModel(parse_ollama_startup_config("synthetic-518:latest"))
    assert model.generate_sql(_request()) == "SELECT 1"
    assert len(transport.calls) == 1
