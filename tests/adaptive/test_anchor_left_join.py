"""Phase 7-17 anchored LEFT JOIN and the legacy base-table profile."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from backend.adaptive.api import legacy_view_profile as lvp
from backend.adaptive.api.chat_query import answer_chat_query
from backend.adaptive.api.postgres_runtime import (
    PostgresRuntimeError,
    PostgresRuntimeProfile,
    build_validated_contexts,
)
from backend.adaptive.api.postgres_startup_profile import (
    PostgresStartupProfileError,
    load_postgres_startup_profiles,
)
from backend.adaptive.catalog.contracts import (
    ColumnCatalog,
    DatabaseCatalog,
    ForeignKeyCatalog,
    SQLiteAffinity,
    TableCatalog,
)
from backend.adaptive.catalog.postgres import PostgresConnectionConfig
from backend.adaptive.compiler.postgres import SchemaAdaptivePostgresCompiler
from backend.adaptive.evaluation import legacy_chat_compare as lcc
from backend.adaptive.harness.contracts import CompiledQuery, QueryIR
from backend.adaptive.harness.errors import HarnessFailure
from backend.adaptive.mapping.contracts import PublishedFieldMapping, PublishedMappingProfile
from backend.adaptive.policy.postgres import SchemaAdaptivePostgresPolicy
from backend.adaptive.relations.contracts import JoinPlanStatus
from backend.adaptive.relations.errors import RelationGraphError
from backend.adaptive.relations.physical import build_physical_relation_graph
from backend.adaptive.relations.planner import plan_join_path

DATABASE_ID = lvp.BASE_DATABASE_ID
ANCHOR = lvp.BASE_ANCHOR_TABLE
SECRET_DSN = "postgresql://svc-user:s3cret-pw-717@127.0.0.1:5432/legacy717"
FINGERPRINT = "cd" * 32

VARCHAR = "character varying(50)"
TIME = "time without time zone"

# Shape of the team-built legacy DB: offerings point at subjects and
# departments; schedules point at offerings. The required-subject and
# prerequisite tables add alternative paths between the same tables.
TABLES: dict[str, dict[str, Any]] = {
    "cnu_courses": {
        "columns": {
            "course_year": "integer", "subject_code": VARCHAR, "section": VARCHAR,
            "capacity": "integer", "enrolled": "integer", "professor": VARCHAR,
            "dept_code": VARCHAR,
        },
        "pk": ["subject_code", "section"],
        "fks": [(0, "dept_code", "department", "dept_code"), (1, "subject_code", "subject", "subject_code")],
    },
    "course_prerequisite": {
        "columns": {"subject_code": VARCHAR, "section": VARCHAR, "prereq_subject_code": VARCHAR},
        "pk": ["subject_code", "section", "prereq_subject_code"],
        "fks": [
            (0, "subject_code", "cnu_courses", "subject_code"),
            (0, "section", "cnu_courses", "section"),
            (1, "prereq_subject_code", "subject", "subject_code"),
        ],
    },
    "course_schedule": {
        "columns": {
            "subject_code": VARCHAR, "section": VARCHAR, "day_of_week": VARCHAR,
            "start_time": TIME, "end_time": TIME, "schedule_id": "bigint",
        },
        "pk": ["schedule_id"],
        "fks": [(0, "subject_code", "cnu_courses", "subject_code"), (0, "section", "cnu_courses", "section")],
    },
    "department": {
        "columns": {"dept_code": VARCHAR, "dept_name": VARCHAR},
        "pk": ["dept_code"],
        "fks": [],
    },
    "required_subject": {
        "columns": {"dept_code": VARCHAR, "student_id_prefix": VARCHAR, "subject_code": VARCHAR},
        "pk": ["dept_code", "student_id_prefix", "subject_code"],
        "fks": [(0, "dept_code", "department", "dept_code"), (1, "subject_code", "subject", "subject_code")],
    },
    "subject": {
        "columns": {
            "subject_code": VARCHAR, "subject_name": VARCHAR, "target_year": "integer",
            "category": VARCHAR, "credit_hours": "character varying(20)",
        },
        "pk": ["subject_code"],
        "fks": [],
    },
}


def make_catalog(tables: dict[str, dict[str, Any]] | None = None) -> DatabaseCatalog:
    source = tables or TABLES
    return DatabaseCatalog(
        database_id=DATABASE_ID,
        dialect="postgres",
        schema_fingerprint=FINGERPRINT,
        tables=[
            TableCatalog(
                name=name,
                columns=[
                    ColumnCatalog(
                        name=column,
                        declared_type=declared,
                        affinity=SQLiteAffinity.TEXT,
                        nullable=True,
                        sample_values=[],
                    )
                    for column, declared in spec["columns"].items()
                ],
                primary_key=list(spec["pk"]),
                foreign_keys=[
                    ForeignKeyCatalog(
                        id=fk_id,
                        sequence=sum(1 for other in spec["fks"][:index] if other[0] == fk_id),
                        column=column,
                        referenced_table=target,
                        referenced_column=target_column,
                        on_update="NO ACTION",
                        on_delete="NO ACTION",
                        match="NONE",
                    )
                    for index, (fk_id, column, target, target_column) in enumerate(spec["fks"])
                ],
            )
            for name, spec in sorted(source.items())
        ],
    )


def make_published() -> PublishedMappingProfile:
    return PublishedMappingProfile(
        database_id=DATABASE_ID,
        version=1,
        schema_fingerprint=FINGERPRINT,
        ontology_version=lvp.ONTOLOGY_VERSION,
        published_by="synthetic-test",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        fields={
            semantic: PublishedFieldMapping(table=table, column=column, reviewer="synthetic-test")
            for semantic, (table, column, _) in lvp.BASE_FIELDS.items()
        },
    )


def make_profile(anchor: str | None = ANCHOR) -> PostgresRuntimeProfile:
    return PostgresRuntimeProfile(
        database_id=DATABASE_ID,
        label=lvp.BASE_LABEL,
        connection_config=PostgresConnectionConfig(SECRET_DSN),
        published_mapping=make_published(),
        field_transforms=dict(lvp.FIELD_TRANSFORMS),
        unsupported_fields=lvp.UNSUPPORTED_FIELDS,
        anchor_table=anchor,
    )


class FakeAdapter:
    def __init__(self, catalog: DatabaseCatalog | None = None) -> None:
        self.catalog = catalog or make_catalog()

    def inspect(self, database_id: str, *, include_samples: bool = False) -> DatabaseCatalog:
        assert database_id == DATABASE_ID and include_samples is False
        return self.catalog.model_copy(deep=True)


class FakeExecutor:
    def __init__(self) -> None:
        self.queries: list[CompiledQuery] = []

    def execute(self, database_id: str, compiled: CompiledQuery) -> tuple[list[str], list[dict[str, Any]]]:
        self.queries.append(compiled)
        return list(compiled.expected_columns), []


def make_context(profile: PostgresRuntimeProfile | None = None, *, executor: Any = None, catalog=None):
    return build_validated_contexts(
        [profile or make_profile()],
        adapters_by_id={DATABASE_ID: FakeAdapter(catalog)},
        executors_by_id={DATABASE_ID: executor or FakeExecutor()},
        expected_ontology_version=lvp.ONTOLOGY_VERSION,
    )[DATABASE_ID]


def query_ir(select: list[str], filters: list[dict[str, Any]] | None = None) -> QueryIR:
    return QueryIR.model_validate(
        {"entity": "CourseOffering", "select": select, "filters": filters or [], "limit": 50}
    )


def compile_and_validate(context: Any, ir: QueryIR) -> CompiledQuery:
    mapping = context.harness_mapping()
    compiled = context.compiler().compile(ir, mapping)
    context.policy().validate(compiled, query_ir=ir, mapping=mapping)
    return compiled


def validate(context: Any, ir: QueryIR, sql: str, parameters: list[Any]) -> None:
    context.policy().validate(
        CompiledQuery(sql=sql, parameters=parameters, expected_columns=ir.select),
        query_ir=ir,
        mapping=context.harness_mapping(),
    )


GRAPH = build_physical_relation_graph(make_catalog())

# --- planner -----------------------------------------------------------------


def test_anchor_resolves_an_otherwise_ambiguous_path() -> None:
    fields = ["Course.name", "Department.name"]
    # Subject and department connect through offerings or required subjects.
    assert plan_join_path(GRAPH, make_published(), fields).status is JoinPlanStatus.AMBIGUOUS
    result = plan_join_path(GRAPH, make_published(), fields, anchor_table=ANCHOR)
    assert result.status is JoinPlanStatus.RESOLVED
    assert result.plan.anchor_table == ANCHOR
    assert result.required_tables == ["cnu_courses", "department", "subject"]


def test_anchor_is_always_required_and_unknown_anchor_fails() -> None:
    result = plan_join_path(GRAPH, make_published(), ["Course.name"], anchor_table=ANCHOR)
    assert result.required_tables == ["cnu_courses", "subject"]
    assert [step.traversal_to for step in result.plan.steps] == ["subject"]
    alone = plan_join_path(GRAPH, make_published(), ["CourseOffering.section"], anchor_table=ANCHOR)
    assert alone.plan.steps == [] and alone.required_tables == [ANCHOR]
    with pytest.raises(RelationGraphError):
        plan_join_path(GRAPH, make_published(), ["Course.name"], anchor_table="missing_table")


# --- compiler and AST policy -------------------------------------------------

CARD_SQL = (
    'SELECT "t1"."subject_name" AS "Course.name", "t2"."day_of_week" AS "Schedule.day" '
    'FROM "cnu_courses" AS "t0" '
    'LEFT JOIN "subject" AS "t1" ON "t0"."subject_code" = "t1"."subject_code" '
    'LEFT JOIN "course_schedule" AS "t2" ON "t2"."subject_code" = "t0"."subject_code" '
    'AND "t2"."section" = "t0"."section" '
    'WHERE "t2"."day_of_week" = %s LIMIT %s'
)


def test_anchored_compile_uses_left_joins_and_passes_policy() -> None:
    context = make_context()
    ir = query_ir(["Course.name", "Schedule.day"], [{"field": "Schedule.day", "operator": "equals", "value": "월요일"}])
    compiled = compile_and_validate(context, ir)
    assert compiled.sql == CARD_SQL
    assert compiled.parameters == ["월", 50]


def test_single_table_question_still_starts_from_the_anchor() -> None:
    context = make_context()
    compiled = compile_and_validate(context, query_ir(["Course.name"]))
    assert compiled.sql == (
        'SELECT "t1"."subject_name" AS "Course.name" FROM "cnu_courses" AS "t0" '
        'LEFT JOIN "subject" AS "t1" ON "t0"."subject_code" = "t1"."subject_code" LIMIT %s'
    )
    offering = compile_and_validate(context, query_ir(["CourseOffering.section"]))
    assert offering.sql == 'SELECT "t0"."section" AS "CourseOffering.section" FROM "cnu_courses" AS "t0" LIMIT %s'


@pytest.mark.parametrize(
    "mutate",
    [
        lambda sql: sql.replace("LEFT JOIN", "JOIN", 1),
        lambda sql: sql.replace("LEFT JOIN", "INNER JOIN"),
        lambda sql: sql.replace("LEFT JOIN", "LEFT OUTER JOIN", 1),
        lambda sql: sql.replace("LEFT JOIN", "RIGHT JOIN", 1),
        lambda sql: sql.replace("LEFT JOIN", "FULL JOIN", 1),
        lambda sql: sql.replace("LEFT JOIN", "NATURAL LEFT JOIN", 1),
        # another table as the FROM anchor
        lambda sql: sql.replace('FROM "cnu_courses" AS "t0" LEFT JOIN "subject" AS "t1"',
                                'FROM "subject" AS "t0" LEFT JOIN "cnu_courses" AS "t1"'),
    ],
)
def test_policy_accepts_only_plain_left_joins_from_the_anchor(mutate: Any) -> None:
    context = make_context()
    ir = query_ir(["Course.name", "Schedule.day"], [{"field": "Schedule.day", "operator": "equals", "value": "월"}])
    validate(context, ir, CARD_SQL, ["월", 50])
    mutated = mutate(CARD_SQL)
    assert mutated != CARD_SQL
    with pytest.raises(HarnessFailure):
        validate(context, ir, mutated, ["월", 50])


def test_without_an_anchor_joins_stay_inner_and_left_is_rejected() -> None:
    published = make_published()
    compiler = SchemaAdaptivePostgresCompiler(GRAPH, published, field_transforms=lvp.FIELD_TRANSFORMS)
    policy = SchemaAdaptivePostgresPolicy(GRAPH, published, field_transforms=lvp.FIELD_TRANSFORMS)
    mapping = make_context().harness_mapping()
    ir = query_ir(["Course.name", "Schedule.day"])
    compiled = compiler.compile(ir, mapping)
    assert " LEFT " not in compiled.sql and compiled.sql.count(" JOIN ") == 2
    policy.validate(compiled, query_ir=ir, mapping=mapping)
    left = compiled.model_copy(update={"sql": compiled.sql.replace(" JOIN ", " LEFT JOIN ")})
    with pytest.raises(HarnessFailure):
        policy.validate(left, query_ir=ir, mapping=mapping)
    # The anchored policy in turn rejects the inner-join shape.
    with pytest.raises(HarnessFailure):
        make_context().policy().validate(compiled, query_ir=ir, mapping=mapping)


def test_compiler_and_policy_reject_an_unmapped_anchor() -> None:
    with pytest.raises(ValueError):
        SchemaAdaptivePostgresCompiler(GRAPH, make_published(), anchor_table="required_subject")
    with pytest.raises(ValueError):
        SchemaAdaptivePostgresPolicy(GRAPH, make_published(), anchor_table="required_subject")


# --- runtime startup ---------------------------------------------------------


@pytest.mark.parametrize("anchor", ["required_subject", "missing_table", " cnu_courses", ""])
def test_runtime_rejects_an_anchor_outside_the_mapping(anchor: str) -> None:
    with pytest.raises(PostgresRuntimeError) as caught:
        make_context(make_profile(anchor))
    assert "s3cret" not in repr(caught.value)


def test_runtime_rejects_an_anchor_with_ambiguous_paths() -> None:
    # From the department table, department + subject connect through
    # offerings or through required subjects: two equally short plans.
    with pytest.raises(PostgresRuntimeError):
        make_context(make_profile("department"))


def test_runtime_rejects_a_disconnected_anchor() -> None:
    tables = {name: dict(spec) for name, spec in TABLES.items()}
    tables["course_schedule"] = {**TABLES["course_schedule"], "fks": []}
    with pytest.raises(PostgresRuntimeError):
        make_context(catalog=make_catalog(tables))


def test_context_exposes_anchor_and_chat_answers_through_left_joins() -> None:
    executor = FakeExecutor()
    context = make_context(executor=executor)
    assert context.anchor_table() == ANCHOR

    class Model:
        def generate_ir(self, *_args: Any) -> dict[str, Any]:
            return {
                "entity": "Course",
                "select": ["Course.name"],
                "filters": [{"field": "Department.name", "operator": "equals", "value": "수학과"}],
                "limit": 50,
            }

    answer = answer_chat_query(
        database_id=DATABASE_ID, question="수학과 강의", pg_contexts={DATABASE_ID: context}, model=Model()
    )
    assert answer["status"] == "SUCCEEDED"
    [compiled] = executor.queries
    assert compiled.sql.count(" LEFT JOIN ") == 3 and ' JOIN "' in compiled.sql
    assert 'FROM "cnu_courses" AS "t0"' in compiled.sql and " INNER " not in compiled.sql
    assert compiled.parameters == ["%수학과%", 200]
    assert "cnu_courses" not in json.dumps(answer, ensure_ascii=False)


# --- private profile v3 ------------------------------------------------------


@pytest.fixture()
def repository(tmp_path: Path) -> Path:
    (tmp_path / ".gitignore").write_text(".schema-adaptive/\n", encoding="utf-8")
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def _v3(profile_bytes: bytes) -> dict[str, Any]:
    """The base profile in the Phase 7-17 v3 shape (no value dictionaries)."""
    profile = json.loads(profile_bytes)
    del profile["value_dictionaries"]
    return {**profile, "version": "postgres-runtime-profile-v3"}


def _write_pair(repository: Path, profile: dict[str, Any], mapping_bytes: bytes) -> None:
    directory = repository / lvp.BASE_PROFILE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "mapping.json").write_bytes(mapping_bytes)
    (directory / "profile.json").write_text(json.dumps(profile), encoding="utf-8")


def test_v3_profile_round_trips_the_anchor(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile_bytes, mapping_bytes = lvp.build_documents(
        FINGERPRINT, datetime(2026, 1, 1, tzinfo=timezone.utc), lvp.BASE_SPEC
    )
    profile = _v3(profile_bytes)
    assert profile["anchor_table"] == ANCHOR and profile["include_views"] is False
    _write_pair(repository, profile, mapping_bytes)
    monkeypatch.setenv(lvp.BASE_DSN_ENV, SECRET_DSN)
    [loaded] = load_postgres_startup_profiles(repository_root=repository, database_ids=[DATABASE_ID])
    assert loaded.anchor_table == ANCHOR
    assert loaded.connection_config.include_views is False
    assert make_context(loaded).anchor_table() == ANCHOR


@pytest.mark.parametrize(
    "change",
    [
        {"version": "postgres-runtime-profile-v2"},  # v2 has no anchor key
        {"anchor_table": "Bad-Name"},
        {"anchor_table": ""},
        {"anchor_table": 7},
        {"anchor_table": None},
    ],
)
def test_v3_profile_is_strict(repository: Path, monkeypatch: pytest.MonkeyPatch, change: dict[str, Any]) -> None:
    profile_bytes, mapping_bytes = lvp.build_documents(
        FINGERPRINT, datetime(2026, 1, 1, tzinfo=timezone.utc), lvp.BASE_SPEC
    )
    _write_pair(repository, {**_v3(profile_bytes), **change}, mapping_bytes)
    monkeypatch.setenv(lvp.BASE_DSN_ENV, SECRET_DSN)
    with pytest.raises(PostgresStartupProfileError) as caught:
        load_postgres_startup_profiles(repository_root=repository, database_ids=[DATABASE_ID])
    assert "s3cret" not in str(caught.value)


def test_v3_profile_requires_the_anchor_key(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile_bytes, mapping_bytes = lvp.build_documents(
        FINGERPRINT, datetime(2026, 1, 1, tzinfo=timezone.utc), lvp.BASE_SPEC
    )
    profile = _v3(profile_bytes)
    del profile["anchor_table"]
    profile["mapping_sha256"] = hashlib.sha256(mapping_bytes).hexdigest()
    _write_pair(repository, profile, mapping_bytes)
    monkeypatch.setenv(lvp.BASE_DSN_ENV, SECRET_DSN)
    with pytest.raises(PostgresStartupProfileError):
        load_postgres_startup_profiles(repository_root=repository, database_ids=[DATABASE_ID])


def test_view_documents_are_unchanged_by_the_spec_refactor() -> None:
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert lvp.build_documents(FINGERPRINT, at) == lvp.build_documents(FINGERPRINT, at, lvp.VIEW_SPEC)
    profile = json.loads(lvp.build_documents(FINGERPRINT, at)[0])
    # Phase 8-06: the view profile is v4 with no anchor.
    assert profile["version"] == "postgres-runtime-profile-v4" and profile["anchor_table"] is None


# --- base registration CLI pieces ---------------------------------------------


def test_write_base_profile_verifies_tables_and_columns(repository: Path) -> None:
    catalog = make_catalog()
    assert lvp.write_profile(repository, catalog, replace=False, spec=lvp.BASE_SPEC) == "WRITTEN"
    assert lvp.write_profile(repository, catalog, replace=False, spec=lvp.BASE_SPEC) == "UNCHANGED"
    assert not (repository / lvp.PROFILE_DIR).exists()
    missing = {name: spec for name, spec in TABLES.items() if name != "course_schedule"}
    with pytest.raises(lvp.LegacyViewProfileError) as caught:
        lvp.write_profile(repository, make_catalog(missing), replace=True, spec=lvp.BASE_SPEC)
    assert caught.value.code == "TABLE_MISSING"
    wrong = {name: dict(spec) for name, spec in TABLES.items()}
    wrong["course_schedule"] = {
        **TABLES["course_schedule"],
        "columns": {**TABLES["course_schedule"]["columns"], "start_time": "text"},
    }
    with pytest.raises(lvp.LegacyViewProfileError) as caught:
        lvp.write_profile(repository, make_catalog(wrong), replace=True, spec=lvp.BASE_SPEC)
    assert caught.value.code == "COLUMN_MISMATCH"


def test_base_smoke_runs_through_the_anchored_runtime() -> None:
    executor = FakeExecutor()
    results = lvp.run_smoke(make_context(executor=executor), lvp.BASE_SPEC)
    assert [item["status"] for item in results] == ["OK"] * len(lvp.SMOKE_QUERIES)
    assert len(executor.queries) == len(lvp.SMOKE_QUERIES) - 1
    assert all('FROM "cnu_courses" AS "t0"' in query.sql for query in executor.queries)


def test_cli_target_selects_the_base_dsn_variable(capsys: pytest.CaptureFixture[str]) -> None:
    assert lvp.main(["--check", "--target", "base"], environ={lvp.DSN_ENV: SECRET_DSN}) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "ENV_MISSING"
    assert lvp.main(["--smoke", "--target", "other"], environ={}) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "INVALID_ARGUMENTS"


# --- comparison tool -----------------------------------------------------------


def test_compare_tool_can_target_the_base_profile() -> None:
    sent: list[dict[str, Any]] = []

    def post(url: str, payload: dict[str, Any], _timeout: float) -> tuple[int, Any]:
        sent.append(payload)
        return 200, {"status": "NOT_UNDERSTOOD", "failure_code": "X"}

    record = lcc.ask_queryir(post, "q", DATABASE_ID)
    assert sent == [{"database_id": DATABASE_ID, "query": "q"}]
    assert record["answered"] is False
    assert lcc.QUERYIR_DATABASES == ("legacy-view", "legacy-base")


def test_compare_tool_reads_the_reference_dsn_of_the_chosen_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Label already used: the run stops right after the DSN check, offline.
    (tmp_path / lcc.REPORT_DIR).mkdir(parents=True)
    (tmp_path / lcc.REPORT_DIR / "p717-unit.json").write_text("{}", encoding="utf-8")
    argv = ["--live", "--label", "p717-unit", "--queryir-database", DATABASE_ID,
            "--repository-root", str(tmp_path)]
    monkeypatch.setenv(lvp.DSN_ENV, SECRET_DSN)
    monkeypatch.delenv(lvp.BASE_DSN_ENV, raising=False)
    assert lcc.main(argv) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "ENV_MISSING"
    monkeypatch.delenv(lvp.DSN_ENV, raising=False)
    monkeypatch.setenv(lvp.BASE_DSN_ENV, SECRET_DSN)
    assert lcc.main(argv) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["code"] == "LABEL_EXISTS" and "s3cret" not in output


@pytest.mark.parametrize("spec", [lvp.BASE_SPEC, lvp.VIEW_SPEC])
def test_registration_rejects_timestamp_where_the_transform_needs_time(
    repository: Path, spec: lvp.LegacyProfileSpec
) -> None:
    if spec is lvp.BASE_SPEC:
        tables = {name: dict(item) for name, item in TABLES.items()}
        tables["course_schedule"] = {
            **TABLES["course_schedule"],
            "columns": {**TABLES["course_schedule"]["columns"], "start_time": "timestamp without time zone"},
        }
        catalog = make_catalog(tables)
    else:
        from tests.adaptive.test_legacy_view_partial_mapping import VIEW_COLUMNS
        from tests.adaptive.test_legacy_view_partial_mapping import make_catalog as make_view_catalog

        catalog = make_view_catalog(columns={**VIEW_COLUMNS, "start_time": "timestamp without time zone"})
    with pytest.raises(lvp.LegacyViewProfileError) as caught:
        lvp.write_profile(repository, catalog, replace=False, spec=spec)
    assert caught.value.code == spec.mismatch_code
    assert not (repository / spec.profile_dir).exists()
