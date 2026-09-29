"""Phase 5-23: extended schema-variation fixture suite, synthetic only."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

import backend.adaptive.api.ollama_startup as startup
from backend.adaptive.catalog import SQLiteAdapter
from backend.adaptive.evaluation import arm_live_cli as cli
from backend.adaptive.evaluation.arm_comparison import (
    ArmComparisonError,
    compare_arms,
    schema_consistency,
)
from backend.adaptive.evaluation.direct_sql import FakeDirectSqlModel, run_direct_sql_evaluation
from backend.adaptive.evaluation.fixture_mapping import fixture_published_mapping
from backend.adaptive.evaluation.loader import load_evaluation_set
from backend.adaptive.evaluation.run_contracts import EvaluationRunConfig
from backend.adaptive.evaluation.run_report import _case_consistency
from backend.adaptive.evaluation.runner import _prepare_fixture_contexts, run_evaluation
from backend.adaptive.fixtures import (
    CORE_VARIANTS,
    EXTENDED_VARIANTS,
    FIXTURE_SUITES,
    FixtureBundle,
    FixtureVariant,
    build_coded_database,
    build_fixture_bundle,
    build_hybrid_database,
)
from backend.adaptive.fixtures.seed import DEFAULT_SEED
from backend.adaptive.harness.model import FakeModel
from backend.adaptive.ontology.loader import load_default_ontology
from tests.adaptive.test_arm_live_cli import TAG, _run, oracle_transport  # noqa: F401
from tests.adaptive.test_direct_sql_arm import _oracle_sql

EXTENDED = [variant.value for variant in EXTENDED_VARIANTS]

CODED_ROWS = """
SELECT s.sbjt_cd, s.sbjt_nm, s.pnt, s.isu_gb, d.dept_cd, d.dept_nm, p.prof_nm,
       l.yyyy, l.hakgi, l.bunban, l.limit_cnt, l.aply_cnt, t.yoil, t.st_tm, t.ed_tm
FROM tb_lect l
JOIN tb_sbjt s ON s.sbjt_cd = l.sbjt_cd
JOIN tb_dept d ON d.dept_cd = s.dept_cd
JOIN tb_prof p ON p.prof_no = l.prof_no
JOIN tb_lect_tm t ON t.lect_no = l.lect_no
ORDER BY l.yyyy, l.hakgi, s.sbjt_cd, l.bunban
"""

HYBRID_ROWS = """
SELECT s.subject_code, s.subject_title, s.credit_hours, s.course_type, c.dept_code,
       c.dept_name, c.professor, c.term_year, c.term, c.section_no, c.seat_limit,
       c.seats_taken, c.weekday, c.time_from, c.time_to
FROM class_sections c
JOIN subjects s ON s.subject_code = c.subject_code
ORDER BY c.term_year, c.term, s.subject_code, c.section_no
"""


@pytest.fixture(scope="module")
def ontology() -> Any:
    return load_default_ontology()


@pytest.fixture(scope="module")
def gold(ontology: Any) -> Any:
    return load_evaluation_set(cli.DEVELOPMENT_SET_PATH, ontology=ontology)


@pytest.fixture(scope="module")
def extended_bundle(tmp_path_factory: Any, ontology: Any) -> Any:
    return build_fixture_bundle(
        tmp_path_factory.mktemp("p523") / "databases", ontology=ontology, variants=EXTENDED_VARIANTS
    )


def _config(run_id: str, variants: list[str]) -> EvaluationRunConfig:
    return EvaluationRunConfig(
        model_run_id=run_id, model_label="synthetic-label", variants=variants, oracle_smoke=True
    )


# --------------------------------------------------------------------------
# builders and suites


@pytest.mark.parametrize(
    "builder,query,tables",
    [(build_coded_database, CODED_ROWS, 5), (build_hybrid_database, HYBRID_ROWS, 2)],
)
def test_new_builders_hold_the_same_logical_rows(
    tmp_path: Path, builder: Any, query: str, tables: int
) -> None:
    path = builder(tmp_path / "variant.sqlite3")
    with sqlite3.connect(path) as connection:
        rows = [list(row) for row in connection.execute(query)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    expected = [list(row.values()) for row in DEFAULT_SEED.canonical_rows()]
    assert rows == expected
    catalog = SQLiteAdapter({"probe": path}).inspect("probe")
    assert len(catalog.tables) == tables
    assert any(table.foreign_keys for table in catalog.tables)


def test_default_bundle_stays_core_and_extended_adds_two(tmp_path: Path, extended_bundle: Any) -> None:
    core = build_fixture_bundle(tmp_path / "core")
    assert list(core.artifacts) == list(CORE_VARIANTS)
    assert sorted(path.name for path in next((tmp_path / "core" / "generations").iterdir()).iterdir()) == sorted(
        f"{variant.value}.{suffix}"
        for variant in CORE_VARIANTS
        for suffix in ("sqlite3", "catalog.json", "proposal.json")
    )
    assert list(extended_bundle.artifacts) == list(EXTENDED_VARIANTS)
    assert extended_bundle.artifacts[FixtureVariant.CODED].database_id == "fixture-coded-v1"
    assert extended_bundle.artifacts[FixtureVariant.HYBRID].database_id == "fixture-hybrid-v1"
    assert set(FIXTURE_SUITES) == {"core-v1", "extended-v1"}


@pytest.mark.parametrize(
    "variants",
    [
        (FixtureVariant.FLAT,),
        (FixtureVariant.NORMALIZED, FixtureVariant.FLAT, FixtureVariant.LEGACY),
        (*CORE_VARIANTS, FixtureVariant.CODED),
        (*CORE_VARIANTS, FixtureVariant.HYBRID, FixtureVariant.CODED),
        (),
    ],
)
def test_bundle_rejects_non_suite_variant_sets(tmp_path: Path, variants: tuple[Any, ...]) -> None:
    with pytest.raises(ValueError):
        build_fixture_bundle(tmp_path / "bad", variants=variants)
    assert not (tmp_path / "bad").exists()


def test_bundle_manifest_rejects_non_suite_artifacts(extended_bundle: Any) -> None:
    payload = extended_bundle.model_dump(mode="json")
    del payload["artifacts"]["hybrid"]
    with pytest.raises(ValueError):
        FixtureBundle.model_validate(payload)


# --------------------------------------------------------------------------
# explicit mappings


def test_new_variant_mappings_match_their_catalogs(extended_bundle: Any, ontology: Any) -> None:
    catalogs = {
        variant: SQLiteAdapter({artifact.database_id: artifact.database_path}).inspect(artifact.database_id)
        for variant, artifact in extended_bundle.artifacts.items()
    }
    for variant in (FixtureVariant.CODED, FixtureVariant.HYBRID):
        mapping = fixture_published_mapping(variant, catalogs[variant], ontology_version=ontology.version)
        assert mapping.database_id == f"fixture-{variant.value}-v1"
        assert len(mapping.fields) == 15
    with pytest.raises(ValueError):
        fixture_published_mapping(FixtureVariant.CODED, catalogs[FixtureVariant.HYBRID])
    with pytest.raises(ValueError):
        fixture_published_mapping(FixtureVariant.HYBRID, catalogs[FixtureVariant.NORMALIZED])


# --------------------------------------------------------------------------
# runners


@pytest.fixture(scope="module")
def extended_reports(gold: Any, ontology: Any, extended_bundle: Any) -> tuple[Any, Any]:
    ir_model = FakeModel({case.question_ko: case.gold_query_ir.model_dump(mode="json") for case in gold.cases})
    ir = run_evaluation(gold, ir_model, _config("p523-ir", EXTENDED), extended_bundle, ontology=ontology)
    contexts, _ = _prepare_fixture_contexts(extended_bundle, DEFAULT_SEED, ontology, None)
    sql_model = FakeDirectSqlModel(_oracle_sql(gold, contexts))
    sql = run_direct_sql_evaluation(
        gold, sql_model, _config("p523-sql", EXTENDED), extended_bundle, ontology=ontology
    )
    return ir, sql


def test_oracle_smoke_is_complete_on_all_five_variants(extended_reports: tuple[Any, Any]) -> None:
    ir, sql = extended_reports
    assert (ir.totals.semantic_correct.count, ir.totals.semantic_correct.denominator) == (200, 200)
    assert (ir.cross_schema_consistent.count, ir.cross_schema_consistent.denominator) == (40, 40)
    assert (sql.totals.execution_correct.count, sql.totals.execution_correct.denominator) == (200, 200)
    assert [item.variant for item in ir.fixture_metadata] == EXTENDED
    comparison = compare_arms(ir, sql)
    assert comparison.totals.both_correct == 200
    assert list(comparison.by_variant) == EXTENDED


def test_schema_consistency_counts(extended_reports: tuple[Any, Any]) -> None:
    ir, sql = extended_reports
    report = schema_consistency(ir, sql)
    assert report.variants == EXTENDED
    assert report.oracle_smoke is True
    for arm in (report.ir, report.sql):
        assert arm.case_denominator == 40
        assert arm.by_variant_correct == {name: 40 for name in EXTENDED}
        assert arm.spread == 0
        assert arm.all_variants_correct == 40

    wrong = {("P2-001", "coded"), ("P2-001", "hybrid"), ("P2-002", "hybrid")}
    attempts = [
        attempt.model_copy(update={"correct": False})
        if (attempt.case_id, attempt.variant.value) in wrong else attempt
        for attempt in sql.attempts
    ]
    changed = schema_consistency(ir, sql.model_copy(update={"attempts": attempts}))
    assert changed.sql.by_variant_correct["hybrid"] == 38
    assert changed.sql.by_variant_correct["coded"] == 39
    assert changed.sql.spread == 2
    assert changed.sql.all_variants_correct == 38
    assert changed.ir.all_variants_correct == 40


def test_schema_consistency_refuses_unpaired_reports(extended_reports: tuple[Any, Any]) -> None:
    ir, sql = extended_reports
    with pytest.raises(ArmComparisonError):
        schema_consistency(ir, sql.model_copy(update={"seed_fingerprint": "0" * 64}))


def test_configured_variant_must_exist_in_bundle(tmp_path: Path, gold: Any, ontology: Any) -> None:
    core = build_fixture_bundle(tmp_path / "core", ontology=ontology)
    model = FakeModel({case.question_ko: case.gold_query_ir.model_dump(mode="json") for case in gold.cases})
    with pytest.raises(ValueError, match="configured variant missing"):
        run_evaluation(gold, model, _config("p523-miss", ["flat", "coded"]), core, ontology=ontology)
    with pytest.raises(ValueError, match="configured variant missing"):
        run_direct_sql_evaluation(
            gold, FakeDirectSqlModel({}), _config("p523-miss", ["hybrid"]), core, ontology=ontology
        )


def test_run_config_accepts_five_unique_variants_only() -> None:
    assert _config("ok", EXTENDED).variants == EXTENDED
    for bad in (EXTENDED + ["flat"], ["coded", "coded"], ["unknown"]):
        with pytest.raises(ValueError):
            _config("bad", bad)


def test_case_consistency_needs_three_or_more_attempts(extended_reports: tuple[Any, Any]) -> None:
    ir, _sql = extended_reports
    five = [attempt for attempt in ir.attempts if attempt.case_id == "P2-001"]
    assert len(five) == 5
    assert _case_consistency("P2-001", five).reason_code == "CONSISTENT"
    assert _case_consistency("P2-001", five[:2]).reason_code == "INSUFFICIENT_VARIANTS"


# --------------------------------------------------------------------------
# CLI


def test_check_mode_lists_extended_variants(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _c: (_ for _ in ()).throw(AssertionError()))
    code, payload, _ = _run(
        ["--check", "--ollama-model-tag", TAG, "--fixture-suite", "extended-v1",
         "--repository-root", str(tmp_path), "--output-name", "run-x"],
        capsys,
    )
    assert code == 0
    assert payload == {"status": "CHECKED", "cases": 40, "variants": EXTENDED, "model_calls": 0}


@pytest.mark.parametrize(
    "extra,run_id",
    [
        ([], "p523-ext-ir-synthetic-p519-latest.ext"),
        (["--ollama-request-format", "alpaca-raw-no-system-v1", "--ir-extract", "single-fence-v1"],
         "p523-ext-nosys-fence-ir-synthetic-p519-latest.nosys.fence.ext"),
    ],
)
def test_extended_live_run_with_oracle_transport(
    tmp_path: Path, monkeypatch: Any, capsys: Any, oracle_transport: Any, extra: list[str], run_id: str  # noqa: F811
) -> None:
    transport = oracle_transport()
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _config: transport)
    code, payload, _ = _run(
        ["--live-synthetic", "--ollama-model-tag", TAG, "--fixture-suite", "extended-v1", *extra,
         "--repository-root", str(tmp_path), "--output-name", "run-e"],
        capsys,
    )
    assert code == 0, payload
    assert transport.calls == 400
    assert payload["fixture_suite"] == "extended-v1"
    assert payload["model_label"] == run_id.split("-ir-", 1)[1]
    assert payload["ir_arm"]["semantic_correct"] == {"count": 200, "denominator": 200}
    assert payload["sql_arm"]["execution_correct"] == {"count": 200, "denominator": 200}
    assert list(payload["paired_by_variant"]) == EXTENDED
    for arm in ("ir", "sql"):
        assert payload["consistency"][arm]["spread"] == 0
        assert payload["consistency"][arm]["all_variants_correct"] == 40
    out_dir = tmp_path / ".schema-adaptive" / "reports" / "run-e"
    comparison = json.loads((out_dir / "comparison.json").read_text(encoding="utf-8"))
    assert comparison["ir_model_run_id"] == run_id
    consistency = json.loads((out_dir / "consistency.json").read_text(encoding="utf-8"))
    assert consistency["schema_version"] == "schema-consistency-v1"


def test_core_suite_keeps_previous_identity(
    tmp_path: Path, monkeypatch: Any, capsys: Any, oracle_transport: Any  # noqa: F811
) -> None:
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _config: oracle_transport())
    code, payload, _ = _run(
        ["--live-synthetic", "--ollama-model-tag", TAG, "--fixture-suite", "core-v1",
         "--repository-root", str(tmp_path), "--output-name", "run-c"],
        capsys,
    )
    assert code == 0
    assert payload["fixture_suite"] == "core-v1"
    assert payload["model_label"] == "synthetic-p519-latest"
    comparison = json.loads(
        (tmp_path / ".schema-adaptive" / "reports" / "run-c" / "comparison.json").read_text(encoding="utf-8")
    )
    assert comparison["ir_model_run_id"] == "p519-ir-synthetic-p519-latest"
    assert comparison["variants"] == ["flat", "normalized", "legacy"]


@pytest.mark.parametrize(
    "extra",
    [
        ["--fixture-suite", ""],
        ["--fixture-suite", "extended"],
        ["--fixture-suite", "core-v1", "--fixture-suite", "core-v1"],
    ],
)
def test_invalid_fixture_suite_is_config_error(tmp_path: Path, monkeypatch: Any, capsys: Any, extra: list[str]) -> None:
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _c: (_ for _ in ()).throw(AssertionError()))
    try:
        code = cli.main(
            ["--live-synthetic", "--ollama-model-tag", TAG, "--repository-root", str(tmp_path),
             "--output-name", "run-i", *extra]
        )
    except SystemExit as exc:
        code = exc.code
    capsys.readouterr()
    assert code == 2
    assert not (tmp_path / ".schema-adaptive").exists()


def test_runner_rechecks_bundle_suite_after_mutation(tmp_path: Path, ontology: Any) -> None:
    bundle = build_fixture_bundle(tmp_path / "core", ontology=ontology)
    del bundle.artifacts[FixtureVariant.LEGACY]
    with pytest.raises(ValueError, match="variant drift"):
        _prepare_fixture_contexts(bundle, DEFAULT_SEED, ontology, None)


@pytest.mark.parametrize("variants", [["flat"], ["flat", "normalized"], ["flat", "normalized", "coded"]])
def test_schema_consistency_requires_a_whole_suite(
    tmp_path: Path, gold: Any, ontology: Any, extended_bundle: Any, variants: list[str]
) -> None:
    ir_model = FakeModel({case.question_ko: case.gold_query_ir.model_dump(mode="json") for case in gold.cases})
    ir = run_evaluation(gold, ir_model, _config("p523-sub-ir", variants), extended_bundle, ontology=ontology)
    contexts, _ = _prepare_fixture_contexts(extended_bundle, DEFAULT_SEED, ontology, None)
    sql = run_direct_sql_evaluation(
        gold, FakeDirectSqlModel(_oracle_sql(gold, contexts)), _config("p523-sub-sql", variants),
        extended_bundle, ontology=ontology,
    )
    compare_arms(ir, sql)
    with pytest.raises(ArmComparisonError):
        schema_consistency(ir, sql)
