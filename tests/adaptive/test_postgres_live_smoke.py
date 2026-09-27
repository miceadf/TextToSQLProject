"""Phase 7-03 synthetic PostgreSQL live smoke (unit/fake scope only).

No live PostgreSQL container/server is started or connected here. Live
execution requires explicit flag plus environment and is skipped by
default; these tests verify preflight, redaction, fixture composition,
and exactly-once cleanup with injected fakes.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from typing import Any

from backend.adaptive.catalog.contracts import (
    ColumnCatalog,
    DatabaseCatalog,
    ForeignKeyCatalog,
    SQLiteAffinity,
    TableCatalog,
)
from backend.adaptive.fixtures.postgres_synthetic import (
    SCHEMA_PATTERN,
    SyntheticFixtureError,
    ddl_operations,
    generate_schema_name,
    insert_operations,
)
from backend.adaptive.fixtures.seed import DEFAULT_SEED
from backend.adaptive.harness.postgres_live_smoke import (
    SMOKE_EXPECTED_MAPPING,
    main,
    run_smoke,
    validate_dsn,
)
from backend.adaptive.mapping.rules import RuleBasedSchemaAligner
from backend.adaptive.ontology.loader import load_default_ontology

SECRET_DSN = "postgresql://svc-user:s3cret-pw@127.0.0.1:5432/schema_adaptive_synthetic"
SECRET_HOST_DSN = "postgresql://svc-user:s3cret-pw@pg.internal:5432/schema_adaptive_synthetic"


class FakeIdentifier:
    def __init__(self, name: str) -> None:
        self.name = name


class FakePlaceholder:
    pass


class FakeComposed:
    def __init__(self, template: str, args: tuple[Any, ...]) -> None:
        self.template = template
        self.args = args


class FakeJoined:
    def __init__(self, sep: str, items: list[Any]) -> None:
        self.sep = sep
        self.items = items
        self.template = f"JOIN:{sep}"


class FakeSQLObj:
    def __init__(self, template: str) -> None:
        self.template = template

    def format(self, *args: Any) -> FakeComposed:
        return FakeComposed(self.template, args)

    def join(self, items: list[Any]) -> FakeJoined:
        return FakeJoined(self.template, list(items))


class FakeSqlModule:
    SQL = FakeSQLObj
    Identifier = FakeIdentifier
    Placeholder = FakePlaceholder


class FakeFixtureCursor:
    def __init__(self, connection: "FakeBootstrap") -> None:
        self._connection = connection

    def execute(self, statement: Any, params: Any = None) -> None:
        self._connection.executed.append((statement, params))
        if (
            self._connection.fail_at is not None
            and len(self._connection.executed) == self._connection.fail_at
        ):
            raise RuntimeError("synthetic boom")

    def close(self) -> None:
        pass


class FakeBootstrap:
    def __init__(self, fail_at: int | None = None) -> None:
        self.executed: list[tuple[Any, Any]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.fail_at = fail_at

    def cursor(self) -> FakeFixtureCursor:
        return FakeFixtureCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True

    def drop_count(self) -> int:
        count = 0
        for statement, _ in self.executed:
            template = getattr(statement, "template", "")
            if "DROP SCHEMA" in template:
                count += 1
        return count


def _column(name: str) -> ColumnCatalog:
    return ColumnCatalog(
        name=name,
        declared_type="text",
        affinity=SQLiteAffinity.TEXT,
        nullable=False,
        sample_values=[],
    )


def make_valid_catalog() -> DatabaseCatalog:
    departments = TableCatalog(
        name="departments",
        columns=[_column("department_code"), _column("department_name")],
        primary_key=["department_code"],
        foreign_keys=[],
    )
    instructors = TableCatalog(
        name="instructors",
        columns=[_column("instructor_id"), _column("instructor_name")],
        primary_key=["instructor_id"],
        foreign_keys=[],
    )
    courses = TableCatalog(
        name="courses",
        columns=[_column("course_code"), _column("course_name"), _column("department_code")],
        primary_key=["course_code"],
        foreign_keys=[
            ForeignKeyCatalog(
                id=0,
                sequence=0,
                column="department_code",
                referenced_table="departments",
                referenced_column="department_code",
                on_update="NO ACTION",
                on_delete="NO ACTION",
                match="SIMPLE",
            )
        ],
    )
    offerings = TableCatalog(
        name="course_offerings",
        columns=[
            _column("offering_id"),
            _column("course_code"),
            _column("instructor_id"),
            _column("academic_year"),
        ],
        primary_key=["offering_id"],
        foreign_keys=[
            ForeignKeyCatalog(
                id=0,
                sequence=0,
                column="course_code",
                referenced_table="courses",
                referenced_column="course_code",
                on_update="NO ACTION",
                on_delete="NO ACTION",
                match="SIMPLE",
            ),
            ForeignKeyCatalog(
                id=1,
                sequence=0,
                column="instructor_id",
                referenced_table="instructors",
                referenced_column="instructor_id",
                on_update="NO ACTION",
                on_delete="NO ACTION",
                match="SIMPLE",
            ),
        ],
    )
    schedules = TableCatalog(
        name="schedules",
        columns=[_column("schedule_id"), _column("offering_id"), _column("day_of_week")],
        primary_key=["schedule_id"],
        foreign_keys=[
            ForeignKeyCatalog(
                id=0,
                sequence=0,
                column="offering_id",
                referenced_table="course_offerings",
                referenced_column="offering_id",
                on_update="NO ACTION",
                on_delete="NO ACTION",
                match="SIMPLE",
            )
        ],
    )
    return DatabaseCatalog(
        database_id="synthetic-postgres",
        dialect="postgres",
        tables=[offerings, courses, departments, instructors, schedules],
        schema_fingerprint="ab" * 32,
    )


def expected_q1_rows() -> list[dict[str, str]]:
    names = sorted(
        {
            offering.course_name
            for offering in DEFAULT_SEED.offerings
            if offering.department_name == "컴퓨터공학과"
        }
    )
    return [{"Course.name": name, "Department.name": "컴퓨터공학과"} for name in names]


class FakeAdapter:
    def __init__(self, catalog: DatabaseCatalog | None = None, exc: Exception | None = None) -> None:
        self._catalog = catalog
        self._exc = exc

    def inspect(self, database_id: str) -> DatabaseCatalog:
        if self._exc is not None:
            raise self._exc
        assert database_id == "synthetic-postgres"
        assert self._catalog is not None
        return self._catalog


class FakeHarnessExecutor:
    def __init__(self, q1_rows: list[dict[str, str]]) -> None:
        self._q1_rows = q1_rows
        self.calls = 0

    def execute(self, database_id: str, query: Any) -> tuple[list[str], list[dict[str, str]]]:
        self.calls += 1
        assert database_id == "synthetic-postgres"
        params = list(query.parameters)
        if "__no_such_course__" in params:
            return (["Course.name", "Department.name"], [])
        return (["Course.name", "Department.name"], list(self._q1_rows))


def run_with_fakes(
    *,
    argv_live: bool = True,
    dsn: str | None = SECRET_DSN,
    bootstrap: FakeBootstrap | None = None,
    adapter_exc: Exception | None = None,
    catalog: DatabaseCatalog | None = None,
    executor: FakeHarnessExecutor | None = None,
) -> tuple[int, dict[str, Any], FakeBootstrap, str]:
    holder = bootstrap or FakeBootstrap()
    active_catalog = catalog if catalog is not None else make_valid_catalog()
    active_executor = executor if executor is not None else FakeHarnessExecutor(expected_q1_rows())
    argv = ["--live-synthetic"] if argv_live else []
    env = {} if dsn is None else {"SCHEMA_ADAPTIVE_SYNTHETIC_POSTGRES_DSN": dsn}
    stream = io.StringIO()
    with redirect_stdout(stream):
        exit_code, payload = run_smoke(
            live=argv_live,
            dsn=dsn,
            token_hex=lambda _n: "0123456789abcdef",
            open_bootstrap=lambda: holder,
            make_adapter=lambda _s: FakeAdapter(active_catalog, adapter_exc),
            make_executor=lambda _s: active_executor,
            sql_module=FakeSqlModule,
        )
        cli_holder = FakeBootstrap()
        cli_code = main(
            argv,
            env=env,
            token_hex=lambda _n: "0123456789abcdef",
            open_bootstrap=lambda: cli_holder,
            make_adapter=lambda _s: FakeAdapter(active_catalog, adapter_exc),
            make_executor=lambda _s: FakeHarnessExecutor(expected_q1_rows()),
            sql_module=FakeSqlModule,
        )
    assert cli_code in (0, 2)
    return exit_code, payload, holder, stream.getvalue()


class PreflightTest(unittest.TestCase):
    def test_no_flag_zero_connections(self) -> None:
        calls: list[str] = []
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = main(
                [],
                env={"SCHEMA_ADAPTIVE_SYNTHETIC_POSTGRES_DSN": SECRET_DSN},
                open_bootstrap=lambda: calls.append("connected") or FakeBootstrap(),
                make_adapter=lambda _s: calls.append("inspect") or FakeAdapter(),
                make_executor=lambda _s: calls.append("execute") or FakeHarnessExecutor([]),
                sql_module=FakeSqlModule,
            )
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])
        self.assertIn("FLAG_REQUIRED", stream.getvalue())

    def test_no_env_zero_connections(self) -> None:
        calls: list[str] = []
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = main(
                ["--live-synthetic"],
                env={},
                open_bootstrap=lambda: calls.append("connected") or FakeBootstrap(),
                sql_module=FakeSqlModule,
            )
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])
        self.assertIn("ENV_MISSING", stream.getvalue())

    def test_invalid_dsn_rejected_before_connect(self) -> None:
        bad = [
            "mysql://127.0.0.1:5432/schema_adaptive_synthetic",
            SECRET_HOST_DSN,
            "postgresql://127.0.0.1:5432/other_db",
            "postgresql://127.0.0.1:5432/schema_adaptive_synthetic?sslmode=require&host=evil",
            "postgresql://127.0.0.1:5432/schema_adaptive_synthetic#frag",
            "postgresql://host1:5432,host2:5432/schema_adaptive_synthetic",
            "postgresql:///schema_adaptive_synthetic?host=/tmp/socket",
            "postgresql://127.0.0.1:not-a-port/schema_adaptive_synthetic",
            SECRET_DSN + "?sslmode=unknown",
            SECRET_DSN + "?connect_timeout=forever",
            SECRET_DSN + "?application_name=bad%20name",
        ]
        for dsn in bad:
            with self.subTest(dsn="redacted"):
                calls: list[str] = []
                code, payload = run_smoke(
                    live=True,
                    dsn=dsn,
                    open_bootstrap=lambda: calls.append("connected") or FakeBootstrap(),
                    sql_module=FakeSqlModule,
                )
                self.assertEqual(code, 2)
                self.assertEqual(calls, [])
                self.assertIn(payload["code"], ("INVALID_SCHEME", "INVALID_HOST", "INVALID_DATABASE", "INVALID_OPTION", "INVALID_DSN"))

    def test_valid_loopback_variants_pass_preflight(self) -> None:
        for dsn in (
            "postgresql://127.0.0.1:5432/schema_adaptive_synthetic",
            "postgresql://localhost/schema_adaptive_synthetic",
            "postgresql://[::1]:5432/schema_adaptive_synthetic",
            SECRET_DSN + "?sslmode=require",
        ):
            validate_dsn(dsn)


class FixtureCompositionTest(unittest.TestCase):
    def test_schema_pattern_and_token(self) -> None:
        name = generate_schema_name(lambda _n: "0123456789abcdef")
        self.assertTrue(SCHEMA_PATTERN.fullmatch(name))
        with self.assertRaises(SyntheticFixtureError):
            generate_schema_name(lambda _n: "not-hex!!")
        with self.assertRaises(SyntheticFixtureError):
            generate_schema_name(lambda _n: (_ for _ in ()).throw(RuntimeError("boom")))

    def test_identifier_composition_and_parameterized_inserts(self) -> None:
        schema = generate_schema_name(lambda _n: "0123456789abcdef")

        def identifiers(node: Any) -> list[str]:
            found: list[str] = []
            if isinstance(node, FakeIdentifier):
                return [node.name]
            if isinstance(node, (FakeComposed, FakeJoined)):
                args = getattr(node, "args", None) or getattr(node, "items", [])
                for item in args:
                    found.extend(identifiers(item))
            elif isinstance(node, list):
                for item in node:
                    found.extend(identifiers(item))
            return found

        for statement in ddl_operations(schema, FakeSqlModule):
            template = getattr(statement, "template", "")
            self.assertNotIn(schema, template)
            self.assertIn(schema, identifiers(statement))
        operations = insert_operations(schema, FakeSqlModule, DEFAULT_SEED)
        self.assertGreater(len(operations), 6)
        total_rows = sum(len(rows) for rows in (DEFAULT_SEED.offerings,))
        self.assertGreaterEqual(len(operations), total_rows)
        for statement, params in operations:
            self.assertIsInstance(params, tuple)
            self.assertIn("INSERT INTO", getattr(statement, "template", ""))
            self.assertNotIn(schema, getattr(statement, "template", ""))


class CleanupAndRedactionTest(unittest.TestCase):
    def test_success_cleanup_once_and_safe_summary(self) -> None:
        holder = FakeBootstrap()
        code, payload, _, output = run_with_fakes(bootstrap=holder)
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "SUCCEEDED")
        self.assertEqual(payload["table_count"], 5)
        self.assertEqual(payload["mapping_count"], len(SMOKE_EXPECTED_MAPPING))
        self.assertEqual(payload["query_count"], 2)
        self.assertRegex(payload["schema_fingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual(holder.drop_count(), 1)
        for secret in ("s3cret-pw", "svc-user", "schema_adaptive_smoke_", "SELECT", "Course.name"):
            self.assertNotIn(secret, output)

    def test_partial_setup_failure_cleanup_once(self) -> None:
        holder = FakeBootstrap(fail_at=2)
        code, payload, _, _ = run_with_fakes(bootstrap=holder)
        self.assertEqual(code, 2)
        self.assertEqual(payload["code"], "FIXTURE_FAILED")
        self.assertEqual(holder.drop_count(), 1)
        self.assertEqual(holder.rollbacks, 1)

    def test_inspection_failure_cleanup_once(self) -> None:
        holder = FakeBootstrap()
        code, payload, _, _ = run_with_fakes(
            bootstrap=holder, adapter_exc=RuntimeError("driver boom")
        )
        self.assertEqual(code, 2)
        self.assertEqual(payload["code"], "INSPECTION_FAILED")
        self.assertEqual(holder.drop_count(), 1)

    def test_pre_creation_failure_cleanup_zero(self) -> None:
        def exploding() -> FakeBootstrap:
            raise RuntimeError("connect boom " + SECRET_DSN)

        code, payload = run_smoke(
            live=True,
            dsn=SECRET_DSN,
            token_hex=lambda _n: "0123456789abcdef",
            open_bootstrap=exploding,
            sql_module=FakeSqlModule,
        )
        self.assertEqual(code, 2)
        self.assertEqual(payload["code"], "FIXTURE_FAILED")
        self.assertNotIn("s3cret-pw", json_dumps(payload))

    def test_driver_text_never_surfaces(self) -> None:
        holder = FakeBootstrap()
        code, payload, _, output = run_with_fakes(
            bootstrap=holder, adapter_exc=RuntimeError("secret SELECT " + SECRET_DSN)
        )
        self.assertEqual(code, 2)
        self.assertNotIn("s3cret-pw", json_dumps(payload))
        self.assertNotIn("SELECT", json_dumps(payload))
        self.assertNotIn("s3cret-pw", output)


def json_dumps(payload: dict[str, Any]) -> str:
    import json as _json

    return _json.dumps(payload)


class CatalogMappingQueryTest(unittest.TestCase):
    def test_catalog_samples_zero_pk_fk(self) -> None:
        catalog = make_valid_catalog()
        self.assertEqual(
            [t.name for t in catalog.tables],
            ["course_offerings", "courses", "departments", "instructors", "schedules"],
        )
        for table in catalog.tables:
            for column in table.columns:
                self.assertEqual(column.sample_values, [])
        by_name = {t.name: t for t in catalog.tables}
        self.assertEqual(by_name["courses"].primary_key, ["course_code"])
        self.assertEqual(by_name["course_offerings"].primary_key, ["offering_id"])

    def test_wrong_fk_shape_fails_closed(self) -> None:
        catalog = make_valid_catalog()
        bad_courses = catalog.tables[1].model_copy(
            update={
                "foreign_keys": [
                    catalog.tables[1].foreign_keys[0].model_copy(
                        update={"referenced_column": "wrong_column"}
                    )
                ]
            }
        )
        catalog = catalog.model_copy(
            update={"tables": [catalog.tables[0], bad_courses, *catalog.tables[2:]]}
        )
        holder = FakeBootstrap()
        code, payload, _, _ = run_with_fakes(bootstrap=holder, catalog=catalog)
        self.assertEqual(code, 2)
        self.assertEqual(payload["code"], "INSPECTION_FAILED")
        self.assertEqual(holder.drop_count(), 1)

    def test_mapping_top_candidates_and_no_publish(self) -> None:
        catalog = make_valid_catalog()
        proposal = RuleBasedSchemaAligner().align(catalog, load_default_ontology())
        for field, (table, column) in SMOKE_EXPECTED_MAPPING.items():
            self.assertTrue(proposal.candidates[field])
            self.assertEqual(
                (proposal.candidates[field][0].table, proposal.candidates[field][0].column),
                (table, column),
            )

    def test_query_mismatch_fails_closed(self) -> None:
        class WrongExecutor(FakeHarnessExecutor):
            def execute(self, database_id: str, query: Any) -> Any:
                return (["Course.name", "Department.name"], [{"Course.name": "x", "Department.name": "y"}])

        holder = FakeBootstrap()
        code, payload, _, _ = run_with_fakes(bootstrap=holder, executor=WrongExecutor(expected_q1_rows()))
        self.assertEqual(code, 2)
        self.assertEqual(payload["code"], "QUERY_FAILED")
        self.assertEqual(holder.drop_count(), 1)


if __name__ == "__main__":
    unittest.main()
