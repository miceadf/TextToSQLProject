"""Phase 7-02 PostgreSQL QueryIR runtime (synthetic fake-connector scope).

No live PostgreSQL server, CNU data, Ollama call, React/HTTP registry,
registration/upload/publish, or legacy backend is used or changed here.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from backend.adaptive.catalog import postgres_capability, sqlite_capability
from backend.adaptive.catalog.postgres import PostgresConnectionConfig
from backend.adaptive.compiler import (
    JoinCapablePostgresCompiler,
    JoinCapableSQLiteCompiler,
    JoinCompilationRequest,
    SchemaAdaptivePostgresCompiler,
)
from backend.adaptive.harness import (
    CompiledQuery,
    DataType,
    ExecutionHarness,
    FakeModel,
    FieldMapping,
    HarnessErrorCode,
    HarnessStage,
    HarnessStatus,
    MappingProfile,
    MappingStatus,
    Ontology,
    OntologyField,
    Operator,
    PostgresReadOnlyExecutor,
    QueryIR,
    SingleTableSQLiteCompiler,
)
from backend.adaptive.harness.errors import HarnessFailure
from backend.adaptive.mapping import PublishedFieldMapping, PublishedMappingProfile
from backend.adaptive.policy import SchemaAdaptivePostgresPolicy
from backend.adaptive.policy.ast import AstReadOnlyPolicy
from backend.adaptive.policy.contracts import AstPolicyContext
from backend.adaptive.relations import (
    JoinColumnPair,
    JoinPlan,
    JoinPlanningResult,
    JoinPlanStatus,
    JoinStep,
    PhysicalRelation,
    PhysicalRelationGraph,
)

SECRET_DSN = "postgresql://svc-user:s3cret-pw@pg.internal:5432/cnu?sslmode=require"


def make_config(**overrides: Any) -> PostgresConnectionConfig:
    params: dict[str, Any] = {"dsn": SECRET_DSN}
    params.update(overrides)
    return PostgresConnectionConfig(**params)


def make_relation(
    relation_id: str,
    source: str,
    target: str,
    pairs: list[tuple[str, str]] | None = None,
) -> PhysicalRelation:
    return PhysicalRelation(
        relation_id=relation_id,
        source_table=source,
        target_table=target,
        column_pairs=[
            JoinColumnPair(source_column=a, target_column=b)
            for a, b in (pairs or [("parent_id", "id")])
        ],
        on_update="NO ACTION",
        on_delete="NO ACTION",
        match="NONE",
    )


def make_profile(fields: dict[str, tuple[str, str]]) -> PublishedMappingProfile:
    return PublishedMappingProfile(
        database_id="pg-db",
        version=1,
        schema_fingerprint="0" * 64,
        ontology_version=1,
        published_by="pg-test",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        fields={
            field: PublishedFieldMapping(table=table, column=column, reviewer="pg-test")
            for field, (table, column) in fields.items()
        },
    )


def resolved(fields: list[str], plan: JoinPlan) -> JoinPlanningResult:
    return JoinPlanningResult(
        status=JoinPlanStatus.RESOLVED,
        semantic_fields=sorted(fields),
        required_tables=plan.required_tables,
        plan=plan,
    )


def compile_postgres(
    query_ir: QueryIR,
    mapping: PublishedMappingProfile,
    plan: JoinPlan,
) -> CompiledQuery:
    return JoinCapablePostgresCompiler().compile(
        JoinCompilationRequest(
            query_ir=query_ir,
            mapping=mapping,
            join_result=resolved(
                sorted({*query_ir.select, *(item.field for item in query_ir.filters)}),
                plan,
            ),
        )
    )


def postgres_context(
    query_ir: QueryIR,
    mapping: PublishedMappingProfile,
    plan: JoinPlan,
) -> AstPolicyContext:
    join_result = resolved(
        sorted({*query_ir.select, *(item.field for item in query_ir.filters)}),
        plan,
    )
    return AstPolicyContext(
        query=JoinCapablePostgresCompiler().compile(
            JoinCompilationRequest(
                query_ir=query_ir, mapping=mapping, join_result=join_result
            )
        ),
        query_ir=query_ir,
        mapping=mapping,
        join_result=join_result,
        dialect="postgres",
    )


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self._connection = connection
        self.closed = False
        self.description: list[tuple[str, ...]] | None = [
            (name,) for name in connection.columns
        ]

    def execute(self, query: str, params: Any = None) -> None:
        behavior = self._connection.execute_behavior
        if behavior is not None:
            raise behavior
        self._connection.executed.append((query, params))

    def fetchmany(self, size: int) -> list[Any]:
        behavior = self._connection.fetch_behavior
        if behavior is not None:
            raise behavior
        return list(self._connection.rows[:size])

    def close(self) -> None:
        self.closed = True
        self._connection.cursor_closed = True
        if self._connection.fail_cleanup:
            raise RuntimeError("cursor cleanup failure " + SECRET_DSN)


class FakeConnection:
    def __init__(self) -> None:
        self.server_version = 140005
        self.executed: list[tuple[str, Any]] = []
        self.closed = False
        self.rolled_back = False
        self.cursor_closed = False
        self.fail_cleanup = False
        self.make_cursor_fail: Exception | None = None
        self.execute_behavior: Exception | None = None
        self.fetch_behavior: Exception | None = None
        self.columns = ["Course.name"]
        self.rows: list[Any] = []
        self.cursor_instance: FakeCursor | None = None

    def cursor(self) -> FakeCursor:
        if self.make_cursor_fail is not None:
            raise self.make_cursor_fail
        self.cursor_instance = FakeCursor(self)
        return self.cursor_instance

    def rollback(self) -> None:
        self.rolled_back = True
        if self.fail_cleanup:
            raise RuntimeError("rollback failure " + SECRET_DSN)

    def close(self) -> None:
        self.closed = True
        if self.fail_cleanup:
            raise RuntimeError("close failure " + SECRET_DSN)


class QueryCanceledError(Exception):
    def __init__(self) -> None:
        super().__init__("canceling statement due to statement timeout")
        self.pgcode = "57014"


class PostgresCompilerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.compiler = JoinCapablePostgresCompiler()

    def test_single_table_exact_sql_and_params(self) -> None:
        plan = JoinPlan(anchor_table="courses", required_tables=["courses"], steps=[])
        query_ir = QueryIR(
            entity="Course",
            select=["Course.name"],
            filters=[{"field": "Course.name", "operator": "equals", "value": "DB"}],
            limit=5,
        )
        compiled = compile_postgres(
            query_ir,
            make_profile({"Course.name": ("courses", "course_name")}),
            plan,
        )
        self.assertEqual(
            compiled.sql,
            'SELECT "t0"."course_name" AS "Course.name" '
            'FROM "courses" AS "t0" '
            'WHERE "t0"."course_name" = %s LIMIT %s',
        )
        self.assertEqual(compiled.parameters, ["DB", 5])
        self.assertEqual(compiled.sql.count("%s"), 2)
        self.assertNotIn("?", compiled.sql)
        self.assertNotIn("DB", compiled.sql)

    def test_multi_table_filter_order_and_contains_binding(self) -> None:
        edge = make_relation("child-parent", "z_children", "a_parents")
        plan = JoinPlan(
            anchor_table="a_parents",
            required_tables=["a_parents", "z_children"],
            steps=[
                JoinStep(
                    relation=edge,
                    traversal_from="a_parents",
                    traversal_to="z_children",
                )
            ],
        )
        query_ir = QueryIR(
            entity="Parent",
            select=["Parent.name", "Child.name"],
            filters=[
                {"field": "Parent.name", "operator": "equals", "value": "A"},
                {"field": "Child.name", "operator": "contains", "value": "자료"},
            ],
            limit=20,
        )
        compiled = compile_postgres(
            query_ir,
            make_profile(
                {
                    "Parent.name": ("a_parents", "name"),
                    "Child.name": ("z_children", "name"),
                }
            ),
            plan,
        )
        self.assertEqual(
            compiled.sql,
            'SELECT "t0"."name" AS "Parent.name", '
            '"t1"."name" AS "Child.name" '
            'FROM "a_parents" AS "t0" '
            'JOIN "z_children" AS "t1" '
            'ON "t1"."parent_id" = "t0"."id" '
            'WHERE "t0"."name" = %s AND "t1"."name" LIKE %s LIMIT %s',
        )
        self.assertEqual(compiled.parameters, ["A", "%자료%", 20])
        self.assertEqual(compiled.sql.count("%s"), 3)

    def test_reverse_join_and_composite_predicates(self) -> None:
        edge = make_relation(
            "composite", "children", "parents", [("parent_a", "id_a"), ("parent_b", "id_b")]
        )
        plan = JoinPlan(
            anchor_table="parents",
            required_tables=["children", "parents"],
            steps=[
                JoinStep(
                    relation=edge,
                    traversal_from="parents",
                    traversal_to="children",
                )
            ],
        )
        compiled = compile_postgres(
            QueryIR(entity="Item", select=["Parent.name", "Child.value"], limit=9),
            make_profile(
                {
                    "Parent.name": ("parents", "name"),
                    "Child.value": ("children", "value"),
                }
            ),
            plan,
        )
        self.assertIn(
            'ON "t1"."parent_a" = "t0"."id_a" '
            'AND "t1"."parent_b" = "t0"."id_b"',
            compiled.sql,
        )
        self.assertTrue(compiled.sql.endswith("LIMIT %s"))

    def test_quoting_matches_sqlite_shape(self) -> None:
        plan = JoinPlan(
            anchor_table='odd" table', required_tables=['odd" table'], steps=[]
        )
        compiled = compile_postgres(
            QueryIR(entity="Course", select=["Course.name"]),
            make_profile({"Course.name": ('odd" table', 'na"me')}),
            plan,
        )
        sqlite_compiled = JoinCapableSQLiteCompiler().compile(
            JoinCompilationRequest(
                query_ir=QueryIR(entity="Course", select=["Course.name"]),
                mapping=make_profile({"Course.name": ('odd" table', 'na"me')}),
                join_result=resolved(["Course.name"], plan),
            )
        )
        self.assertIn('FROM "odd"" table" AS "t0"', compiled.sql)
        self.assertIn('"t0"."na""me" AS "Course.name"', compiled.sql)
        self.assertEqual(
            compiled.sql.replace("%s", "?"),
            sqlite_compiled.sql,
        )

    def test_fail_closed_codes(self) -> None:
        plan = JoinPlan(anchor_table="a", required_tables=["a"], steps=[])
        with self.assertRaises(HarnessFailure) as ambiguous:
            self.compiler.compile(
                JoinCompilationRequest.model_construct(
                    query_ir=QueryIR(entity="I", select=["A.name"]),
                    mapping=make_profile({"A.name": ("a", "name")}),
                    join_result=JoinPlanningResult.model_construct(
                        status=JoinPlanStatus.AMBIGUOUS,
                        semantic_fields=["A.name"],
                        required_tables=["a"],
                        plan=None,
                        alternatives=[],
                    ),
                )
            )
        self.assertEqual(ambiguous.exception.code, HarnessErrorCode.AMBIGUOUS_JOIN_PATH)
        self.assertEqual(ambiguous.exception.stage, HarnessStage.SQL_COMPILED)

        with self.assertRaises(HarnessFailure) as drift:
            self.compiler.compile(
                JoinCompilationRequest(
                    query_ir=QueryIR(entity="I", select=["B.name"]),
                    mapping=make_profile({"B.name": ("b", "name")}),
                    join_result=resolved(["B.name"], plan),
                )
            )
        self.assertEqual(drift.exception.code, HarnessErrorCode.INVALID_JOIN_PLAN)

        self_edge = make_relation("self", "nodes", "nodes")
        self_plan = JoinPlan.model_construct(
            anchor_table="nodes",
            required_tables=["nodes"],
            steps=[
                JoinStep.model_construct(
                    relation=self_edge,
                    traversal_from="nodes",
                    traversal_to="nodes",
                )
            ],
        )
        with self.assertRaises(HarnessFailure) as self_join:
            self.compiler.compile(
                JoinCompilationRequest(
                    query_ir=QueryIR(entity="N", select=["Node.name"]),
                    mapping=make_profile({"Node.name": ("nodes", "name")}),
                    join_result=JoinPlanningResult.model_construct(
                        status=JoinPlanStatus.RESOLVED,
                        semantic_fields=["Node.name"],
                        required_tables=["nodes"],
                        plan=self_plan,
                        alternatives=[],
                    ),
                )
            )
        self.assertEqual(
            self_join.exception.code, HarnessErrorCode.SELF_JOIN_NOT_SUPPORTED
        )


class PostgresAstPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = AstReadOnlyPolicy()
        edge = make_relation("child-parent", "children", "parents")
        self.plan = JoinPlan(
            anchor_table="parents",
            required_tables=["children", "parents"],
            steps=[
                JoinStep(
                    relation=edge,
                    traversal_from="parents",
                    traversal_to="children",
                )
            ],
        )
        self.query_ir = QueryIR(
            entity="Parent",
            select=["Parent.name", "Child.name"],
            filters=[{"field": "Child.name", "operator": "contains", "value": "자료"}],
            limit=20,
        )
        self.mapping = make_profile(
            {"Parent.name": ("parents", "name"), "Child.name": ("children", "name")}
        )
        self.context = postgres_context(self.query_ir, self.mapping, self.plan)

    def test_valid_postgres_sql_accepted_with_anonymous_placeholders(self) -> None:
        self.policy.validate(self.context)
        self.assertIn("%s", self.context.query.sql)
        self.assertNotIn("?", self.context.query.sql)

    def test_sqlite_default_dialect_regression(self) -> None:
        legacy_mapping = MappingProfile(
            database_id="pg-db",
            version=1,
            status=MappingStatus.PUBLISHED,
            fields={
                "Course.name": FieldMapping(table="courses", column="subject_name"),
            },
        )
        published = make_profile({"Course.name": ("courses", "subject_name")})
        single_plan = JoinPlan(
            anchor_table="courses", required_tables=["courses"], steps=[]
        )
        single_ir = QueryIR(entity="Course", select=["Course.name"], limit=5)
        context = AstPolicyContext(
            query=SingleTableSQLiteCompiler().compile(single_ir, legacy_mapping),
            query_ir=single_ir,
            mapping=published,
            join_result=resolved(["Course.name"], single_plan),
        )
        self.assertEqual(context.dialect, "sqlite")
        self.policy.validate(context)

    def assert_rejected(self, sql: str, code: HarnessErrorCode) -> None:
        mutated = self.context.query.model_copy(update={"sql": sql})
        context = self.context.model_copy(update={"query": mutated})
        with self.assertRaises(HarnessFailure) as raised:
            self.policy.validate(context)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(raised.exception.stage, HarnessStage.SQL_VALIDATED)

    def test_write_cte_subquery_function_rejected(self) -> None:
        base = self.context.query.sql
        cases = {
            "delete": 'DELETE FROM "parents" WHERE "name" = %s',
            "insert": 'INSERT INTO "parents" ("name") VALUES (%s)',
            "update": 'UPDATE "parents" SET "name" = %s',
            "cte": f'WITH "x" AS (SELECT 1) {base}',
            "subquery": f'SELECT * FROM ({base}) AS "x" LIMIT %s',
            "function": base.replace('"t0"."name"', 'LOWER("t0"."name")', 1),
            "comment": "/* bypass */ " + base,
        }
        for name, sql in cases.items():
            with self.subTest(name=name):
                self.assert_rejected(sql, HarnessErrorCode.UNSAFE_SQL_NODE)

    def test_multi_statement_rejected(self) -> None:
        self.assert_rejected(
            self.context.query.sql + "; SELECT 1", HarnessErrorCode.MULTIPLE_STATEMENTS
        )

    def test_extra_table_column_literal_and_placeholder_drift(self) -> None:
        base = self.context.query.sql
        self.assert_rejected(
            base.replace('FROM "parents"', 'FROM "secrets"'),
            HarnessErrorCode.UNAUTHORIZED_TABLE,
        )
        self.assert_rejected(
            base.replace('"t0"."name"', '"t0"."secret"', 1),
            HarnessErrorCode.UNAUTHORIZED_COLUMN,
        )
        self.assert_rejected(
            base.replace("LIKE %s", "LIKE '자료'"),
            HarnessErrorCode.INVALID_PLACEHOLDER,
        )
        self.assert_rejected(
            base.replace("LIMIT %s", "LIMIT 20"),
            HarnessErrorCode.INVALID_LIMIT,
        )
        self.assert_rejected(
            base.replace("%s", ":name"),
            HarnessErrorCode.INVALID_PLACEHOLDER,
        )
        mutated = self.context.query.model_copy(
            update={"parameters": ["%자료%"]}
        )
        context = self.context.model_copy(update={"query": mutated})
        with self.assertRaises(HarnessFailure) as raised:
            self.policy.validate(context)
        self.assertEqual(raised.exception.code, HarnessErrorCode.INVALID_PLACEHOLDER)


class PostgresExecutorTest(unittest.TestCase):
    def test_success_tuple_and_mapping_rows(self) -> None:
        connection = FakeConnection()
        connection.columns = ["Course.name", "Course.credits"]
        connection.rows = [("데이터베이스", 3), ("운영체제", 3)]
        executor = PostgresReadOnlyExecutor(
            {"pg-db": make_config()}, connector=lambda _c: connection
        )
        query = CompiledQuery(
            sql='SELECT "t0"."a" AS "Course.name", "t0"."b" AS "Course.credits" '
            'FROM "t" AS "t0" LIMIT %s',
            parameters=[50],
            expected_columns=["Course.name", "Course.credits"],
        )
        columns, rows = executor.execute("pg-db", query)
        self.assertEqual(columns, ["Course.name", "Course.credits"])
        self.assertEqual(
            rows,
            [
                {"Course.name": "데이터베이스", "Course.credits": 3},
                {"Course.name": "운영체제", "Course.credits": 3},
            ],
        )
        user_calls = connection.executed
        self.assertEqual(len(user_calls), 4)
        self.assertEqual(user_calls[0], ("SET TRANSACTION READ ONLY", ()))
        self.assertIn("statement_timeout", user_calls[1][0])
        self.assertIn("search_path", user_calls[2][0])
        self.assertEqual(user_calls[2][1], ("public",))
        self.assertEqual(user_calls[3], (query.sql, (50,)))
        self.assertIsInstance(user_calls[3][1], tuple)
        self.assertTrue(connection.closed)
        self.assertTrue(connection.rolled_back)
        self.assertTrue(connection.cursor_closed)

        mapping_connection = FakeConnection()
        mapping_connection.columns = ["Course.name"]
        mapping_connection.rows = [{"Course.name": "x", "extra": "ignored"}]
        executor2 = PostgresReadOnlyExecutor(
            {"pg-db": make_config()}, connector=lambda _c: mapping_connection
        )
        _, mapping_rows = executor2.execute(
            "pg-db",
            CompiledQuery(
                sql='SELECT "t0"."a" AS "Course.name" FROM "t" AS "t0" LIMIT %s',
                parameters=[5],
                expected_columns=["Course.name"],
            ),
        )
        self.assertEqual(mapping_rows, [{"Course.name": "x"}])

        empty_connection = FakeConnection()
        empty_connection.columns = ["Course.name"]
        empty_connection.rows = []
        executor3 = PostgresReadOnlyExecutor(
            {"pg-db": make_config()}, connector=lambda _c: empty_connection
        )
        columns3, rows3 = executor3.execute(
            "pg-db",
            CompiledQuery(
                sql='SELECT "t0"."a" AS "Course.name" FROM "t" AS "t0" LIMIT %s',
                parameters=[5],
                expected_columns=["Course.name"],
            ),
        )
        self.assertEqual((columns3, rows3), (["Course.name"], []))

    def test_missing_db_does_not_reflect_input(self) -> None:
        executor = PostgresReadOnlyExecutor(
            {"pg-db": make_config()}, connector=lambda _c: FakeConnection()
        )
        with self.assertRaises(HarnessFailure) as raised:
            executor.execute("postgresql://evil:secret@host/db", query=CompiledQuery(sql="SELECT 1", parameters=[], expected_columns=[]))
        self.assertEqual(raised.exception.code, HarnessErrorCode.DATABASE_NOT_FOUND)
        self.assertEqual(raised.exception.stage, HarnessStage.EXECUTED)
        self.assertNotIn("evil", str(raised.exception))
        self.assertNotIn("secret", str(raised.exception))

    def test_server_version_timeout_max_rows_and_config_bounds(self) -> None:
        old = FakeConnection()
        old.server_version = 110022
        executor = PostgresReadOnlyExecutor(
            {"pg-db": make_config()}, connector=lambda _c: old
        )
        with self.assertRaises(HarnessFailure) as raised:
            executor.execute(
                "pg-db",
                CompiledQuery(sql="SELECT 1", parameters=[], expected_columns=[]),
            )
        self.assertEqual(raised.exception.code, HarnessErrorCode.EXECUTION_ERROR)
        self.assertTrue(old.closed)

        with self.assertRaises(ValueError):
            PostgresReadOnlyExecutor(
                {"pg-db": make_config()},
                connector=lambda _c: FakeConnection(),
                timeout_seconds=60.0,
            )
        with self.assertRaises(ValueError):
            PostgresReadOnlyExecutor(
                {"pg-db": make_config()},
                connector=lambda _c: FakeConnection(),
                max_rows=1000,
            )
        with self.assertRaises(ValueError):
            PostgresReadOnlyExecutor({"BAD ID": make_config()})

        big = FakeConnection()
        big.columns = ["Course.name"]
        big.rows = [("r%d" % i,) for i in range(5)]
        bounded = PostgresReadOnlyExecutor(
            {"pg-db": make_config()},
            connector=lambda _c: big,
            max_rows=2,
        )
        with self.assertRaises(HarnessFailure) as too_large:
            bounded.execute(
                "pg-db",
                CompiledQuery(
                    sql='SELECT "t0"."a" AS "Course.name" FROM "t" AS "t0" LIMIT %s',
                    parameters=[10],
                    expected_columns=["Course.name"],
                ),
            )
        self.assertEqual(too_large.exception.code, HarnessErrorCode.RESULT_TOO_LARGE)
        self.assertTrue(big.closed)
        self.assertTrue(big.rolled_back)

        short = FakeConnection()
        short.columns = ["A"]
        short.rows = [("x",)]
        short_executor = PostgresReadOnlyExecutor(
            {"pg-db": make_config()},
            connector=lambda _c: short,
            timeout_seconds=0.1,
        )
        short_executor.execute(
            "pg-db",
            CompiledQuery(sql="SELECT 1", parameters=[], expected_columns=["A"]),
        )
        self.assertEqual(short.executed[1][1], ("100ms",))
        self.assertEqual(short.executed[2][1], ("public",))

        config_limited = FakeConnection()
        config_limited.columns = ["A"]
        config_limited.rows = [("x",)]
        config_executor = PostgresReadOnlyExecutor(
            {"pg-db": make_config(statement_timeout_ms=250)},
            connector=lambda _c: config_limited,
        )
        config_executor.execute(
            "pg-db",
            CompiledQuery(sql="SELECT 1", parameters=[], expected_columns=["A"]),
        )
        self.assertEqual(config_limited.executed[1][1], ("250ms",))
        self.assertEqual(config_limited.executed[2][1], ("public",))

    def test_failures_redacted_with_fixed_code_stage(self) -> None:
        def exploding(_config: PostgresConnectionConfig) -> FakeConnection:
            raise RuntimeError("connect boom " + SECRET_DSN)

        executor = PostgresReadOnlyExecutor(
            {"pg-db": make_config()}, connector=exploding
        )
        with self.assertRaises(HarnessFailure) as raised:
            executor.execute(
                "pg-db", CompiledQuery(sql="SELECT 1", parameters=[], expected_columns=[])
            )
        self.assertEqual(raised.exception.code, HarnessErrorCode.EXECUTION_ERROR)
        self.assertEqual(raised.exception.stage, HarnessStage.EXECUTED)
        self.assertNotIn("s3cret-pw", str(raised.exception))
        self.assertNotIn("boom", str(raised.exception))

        cursor_fail = FakeConnection()
        cursor_fail.make_cursor_fail = RuntimeError("cursor boom " + SECRET_DSN)
        executor2 = PostgresReadOnlyExecutor(
            {"pg-db": make_config()}, connector=lambda _c: cursor_fail
        )
        with self.assertRaises(HarnessFailure) as raised2:
            executor2.execute(
                "pg-db", CompiledQuery(sql="SELECT 1", parameters=[], expected_columns=[])
            )
        self.assertEqual(raised2.exception.code, HarnessErrorCode.EXECUTION_ERROR)
        self.assertNotIn("s3cret-pw", str(raised2.exception))
        self.assertTrue(cursor_fail.closed)

        query_fail = FakeConnection()
        query_fail.execute_behavior = RuntimeError(
            "syntax error at users table " + SECRET_DSN + " SELECT secret"
        )
        executor3 = PostgresReadOnlyExecutor(
            {"pg-db": make_config()}, connector=lambda _c: query_fail
        )
        with self.assertRaises(HarnessFailure) as raised3:
            executor3.execute(
                "pg-db",
                CompiledQuery(
                    sql='SELECT "t0"."a" AS "A" FROM "t" AS "t0" LIMIT %s',
                    parameters=["p0wn"],
                    expected_columns=["A"],
                ),
            )
        self.assertEqual(raised3.exception.code, HarnessErrorCode.EXECUTION_ERROR)
        self.assertNotIn("s3cret-pw", str(raised3.exception))
        self.assertNotIn("p0wn", str(raised3.exception))
        self.assertNotIn("SELECT", str(raised3.exception))
        self.assertTrue(query_fail.closed)
        self.assertTrue(query_fail.rolled_back)

        cancel = FakeConnection()
        cancel.execute_behavior = QueryCanceledError()
        executor4 = PostgresReadOnlyExecutor(
            {"pg-db": make_config()}, connector=lambda _c: cancel
        )
        with self.assertRaises(HarnessFailure) as timeout:
            executor4.execute(
                "pg-db", CompiledQuery(sql="SELECT 1", parameters=[], expected_columns=[])
            )
        self.assertEqual(timeout.exception.code, HarnessErrorCode.EXECUTION_TIMEOUT)
        self.assertEqual(timeout.exception.stage, HarnessStage.EXECUTED)
        self.assertNotIn("s3cret-pw", str(timeout.exception))

        messy = FakeConnection()
        messy.fail_cleanup = True
        messy.columns = ["A"]
        messy.rows = [("x",)]
        executor5 = PostgresReadOnlyExecutor(
            {"pg-db": make_config()}, connector=lambda _c: messy
        )
        columns, rows = executor5.execute(
            "pg-db",
            CompiledQuery(sql="SELECT 1", parameters=[], expected_columns=["A"]),
        )
        self.assertEqual((columns, rows), (["A"], [{"A": "x"}]))
        self.assertTrue(messy.closed)

    def test_external_harness_failures_and_timeout_text_are_redacted(self) -> None:
        malicious = HarnessFailure(
            HarnessErrorCode.EXECUTION_TIMEOUT,
            "SELECT secret " + SECRET_DSN,
            stage=HarnessStage.EXECUTED,
        )
        connector_executor = PostgresReadOnlyExecutor(
            {"pg-db": make_config()}, connector=lambda _c: (_ for _ in ()).throw(malicious)
        )
        with self.assertRaises(HarnessFailure) as connector_failure:
            connector_executor.execute(
                "pg-db",
                CompiledQuery(sql="SELECT 1", parameters=[], expected_columns=[]),
            )
        self.assertEqual(
            connector_failure.exception.code, HarnessErrorCode.EXECUTION_ERROR
        )
        self.assertEqual(
            connector_failure.exception.message, "PostgreSQL query execution failed"
        )

        cursor_connection = FakeConnection()
        cursor_connection.execute_behavior = malicious
        cursor_executor = PostgresReadOnlyExecutor(
            {"pg-db": make_config()}, connector=lambda _c: cursor_connection
        )
        with self.assertRaises(HarnessFailure) as cursor_failure:
            cursor_executor.execute(
                "pg-db",
                CompiledQuery(sql="SELECT 1", parameters=[], expected_columns=[]),
            )
        self.assertEqual(cursor_failure.exception.code, HarnessErrorCode.EXECUTION_ERROR)
        self.assertNotIn("s3cret-pw", str(cursor_failure.exception))

        text_only = FakeConnection()
        text_only.execute_behavior = RuntimeError(
            "canceling statement due to statement timeout " + SECRET_DSN
        )
        text_executor = PostgresReadOnlyExecutor(
            {"pg-db": make_config()}, connector=lambda _c: text_only
        )
        with self.assertRaises(HarnessFailure) as text_failure:
            text_executor.execute(
                "pg-db",
                CompiledQuery(sql="SELECT 1", parameters=[], expected_columns=[]),
            )
        self.assertEqual(text_failure.exception.code, HarnessErrorCode.EXECUTION_ERROR)
        self.assertNotIn("s3cret-pw", str(text_failure.exception))

    def test_malformed_result_shape_fails_closed(self) -> None:
        connection = FakeConnection()
        connection.columns = ["A", "B"]
        connection.rows = [("only-one",)]
        executor = PostgresReadOnlyExecutor(
            {"pg-db": make_config()}, connector=lambda _c: connection
        )
        with self.assertRaises(HarnessFailure) as raised:
            executor.execute(
                "pg-db",
                CompiledQuery(sql="SELECT 1", parameters=[], expected_columns=["A", "B"]),
            )
        self.assertEqual(raised.exception.code, HarnessErrorCode.EXECUTION_ERROR)
        self.assertEqual(raised.exception.message, "PostgreSQL query execution failed")

    def test_production_import_failure_is_fixed_error(self) -> None:
        from backend.adaptive.harness import postgres as pg_module

        saved = sys.modules.get("psycopg2")
        sys.modules["psycopg2"] = None  # type: ignore[assignment]
        try:
            executor = PostgresReadOnlyExecutor(
                {"pg-db": make_config()},
                connector=pg_module._production_connector,
            )
            with self.assertRaises(HarnessFailure) as raised:
                executor.execute(
                    "pg-db",
                    CompiledQuery(sql="SELECT 1", parameters=[], expected_columns=[]),
                )
            self.assertEqual(raised.exception.code, HarnessErrorCode.EXECUTION_ERROR)
            self.assertEqual(
                raised.exception.message, "PostgreSQL query execution failed"
            )
        finally:
            if saved is not None:
                sys.modules["psycopg2"] = saved
            else:
                del sys.modules["psycopg2"]

    def test_production_connector_rounds_connect_timeout_up(self) -> None:
        from backend.adaptive.harness import postgres as pg_module

        captured: dict[str, Any] = {}
        sentinel = object()

        def connect(dsn: str, **kwargs: Any) -> object:
            captured.update({"dsn": dsn, **kwargs})
            return sentinel

        saved = sys.modules.get("psycopg2")
        sys.modules["psycopg2"] = SimpleNamespace(connect=connect)  # type: ignore[assignment]
        try:
            result = pg_module._production_connector(
                make_config(connect_timeout_seconds=1.2)
            )
            self.assertIs(result, sentinel)
            self.assertEqual(captured["connect_timeout"], 2)
            self.assertIsInstance(captured["connect_timeout"], int)
            self.assertNotIn("s3cret-pw", repr(captured["options"]))
        finally:
            if saved is not None:
                sys.modules["psycopg2"] = saved
            else:
                del sys.modules["psycopg2"]


class PostgresHarnessEndToEndTest(unittest.TestCase):
    def _graph_mapping(self) -> tuple[PhysicalRelationGraph, PublishedMappingProfile]:
        graph = PhysicalRelationGraph(
            database_id="pg-db",
            schema_fingerprint="0" * 64,
            tables=["courses", "departments"],
            relations=[
                PhysicalRelation(
                    relation_id="courses:fk:0:departments",
                    source_table="courses",
                    target_table="departments",
                    column_pairs=[
                        JoinColumnPair(
                            source_column="department_id",
                            target_column="department_id",
                        )
                    ],
                    on_update="NO ACTION",
                    on_delete="NO ACTION",
                    match="SIMPLE",
                )
            ],
        )
        published = PublishedMappingProfile(
            database_id="pg-db",
            version=1,
            schema_fingerprint="0" * 64,
            ontology_version=1,
            published_by="pg-test",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            fields={
                "Course.name": PublishedFieldMapping(
                    table="courses", column="course_name", reviewer="pg-test"
                ),
                "Department.name": PublishedFieldMapping(
                    table="departments", column="department_name", reviewer="pg-test"
                ),
            },
        )
        return graph, published

    def _ontology_runtime(self, published: PublishedMappingProfile) -> tuple[Ontology, MappingProfile]:
        from backend.adaptive.mapping import to_harness_mapping

        ontology = Ontology(
            entities={"Course"},
            fields={
                "Course.name": OntologyField(
                    name="Course.name",
                    data_type=DataType.STRING,
                    allowed_operators={Operator.EQUALS},
                ),
                "Department.name": OntologyField(
                    name="Department.name",
                    data_type=DataType.STRING,
                    allowed_operators={Operator.EQUALS},
                ),
            },
        )
        return ontology, to_harness_mapping(published)

    def test_fake_end_to_end_success(self) -> None:
        graph, published = self._graph_mapping()
        ontology, runtime_mapping = self._ontology_runtime(published)
        connection = FakeConnection()
        connection.columns = ["Course.name", "Department.name"]
        connection.rows = [("데이터베이스", "컴퓨터공학과")]
        harness = ExecutionHarness(
            model=FakeModel(
                {
                    "질문": {
                        "entity": "Course",
                        "select": ["Course.name", "Department.name"],
                        "filters": [
                            {
                                "field": "Course.name",
                                "operator": "equals",
                                "value": "데이터베이스",
                            }
                        ],
                        "limit": 10,
                    }
                }
            ),
            compiler=SchemaAdaptivePostgresCompiler(graph, published),
            sql_policy=SchemaAdaptivePostgresPolicy(graph, published),
            executor=PostgresReadOnlyExecutor(
                {"pg-db": make_config()}, connector=lambda _c: connection
            ),
        )
        result = harness.run(
            {"database_id": "pg-db", "question": "질문"}, ontology, runtime_mapping
        )
        self.assertEqual(result.status, HarnessStatus.SUCCEEDED)
        self.assertEqual(result.columns, ["Course.name", "Department.name"])
        self.assertEqual(
            result.rows, [{"Course.name": "데이터베이스", "Department.name": "컴퓨터공학과"}]
        )
        self.assertIn("%s", result.sql or "")
        self.assertNotIn("?", result.sql or "")
        self.assertNotIn("데이터베이스", result.sql or "")

    def test_result_schema_mismatch(self) -> None:
        graph, published = self._graph_mapping()
        ontology, runtime_mapping = self._ontology_runtime(published)
        connection = FakeConnection()
        connection.columns = ["Course.name", "Wrong.column"]
        connection.rows = [("x", "y")]
        harness = ExecutionHarness(
            model=FakeModel(
                {
                    "질문": {
                        "entity": "Course",
                        "select": ["Course.name", "Department.name"],
                        "filters": [],
                        "limit": 5,
                    }
                }
            ),
            compiler=SchemaAdaptivePostgresCompiler(graph, published),
            sql_policy=SchemaAdaptivePostgresPolicy(graph, published),
            executor=PostgresReadOnlyExecutor(
                {"pg-db": make_config()}, connector=lambda _c: connection
            ),
        )
        result = harness.run(
            {"database_id": "pg-db", "question": "질문"}, ontology, runtime_mapping
        )
        self.assertEqual(result.status, HarnessStatus.FAILED)
        self.assertEqual(result.error.code, HarnessErrorCode.RESULT_SCHEMA_MISMATCH)


class PostgresCapabilityTest(unittest.TestCase):
    def test_phase_7_02_ready(self) -> None:
        capability = postgres_capability()
        self.assertTrue(capability.supports_schema_inspection)
        self.assertTrue(capability.supports_compiler)
        self.assertTrue(capability.supports_policy)
        self.assertTrue(capability.supports_executor)
        self.assertEqual(capability.schema_version, "dbms-capability-v1")
        sqlite = sqlite_capability()
        self.assertTrue(sqlite.supports_compiler)
        self.assertTrue(sqlite.supports_policy)
        self.assertTrue(sqlite.supports_executor)


if __name__ == "__main__":
    unittest.main()
