from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from typing import Any

from backend.adaptive.catalog import (
    CapabilityError,
    CapabilityErrorCode,
    PostgresAdapter,
    PostgresConnectionConfig,
    SQLiteAdapter,
    SQLiteAffinity,
    get_capability,
    postgres_affinity,
    postgres_capability,
    sqlite_capability,
)
from backend.adaptive.catalog.errors import (
    CatalogErrorCode,
    SchemaInspectionError,
)
from backend.adaptive.catalog.postgres import (
    PG_COLUMNS_QUERY,
    PG_FK_QUERY,
    PG_PK_QUERY,
    PG_TABLES_QUERY,
)
from backend.adaptive.mapping.rules import RuleBasedSchemaAligner
from backend.adaptive.ontology.loader import load_default_ontology
from backend.adaptive.relations.physical import build_physical_relation_graph

SECRET_DSN = "postgresql://svc-user:s3cret-pw@pg.internal:5432/cnu?sslmode=require"


def make_config(**overrides: Any) -> PostgresConnectionConfig:
    params: dict[str, Any] = {"dsn": SECRET_DSN}
    params.update(overrides)
    return PostgresConnectionConfig(**params)


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self._connection = connection
        self.executed: list[tuple[str, Any]] = connection.executed
        self.closed = False

    def execute(self, query: str, params: Any = None) -> None:
        if self._connection.fail_queries:
            raise RuntimeError("boom SELECT * FROM secrets -- " + SECRET_DSN)
        self.executed.append((query, params))
        self._connection.last_query = query
        self._connection.last_params = params

    def fetchall(self) -> list[dict[str, Any]]:
        query = self._connection.last_query
        params = self._connection.last_params
        if query == PG_TABLES_QUERY:
            assert params == ("public",), params
            return [
                {"table_name": name}
                for name in self._connection.tables_in_disorder
            ]
        if query == PG_COLUMNS_QUERY:
            assert params is not None and params[0] == "public", params
            return self._connection.columns[params[1]]
        if query == PG_PK_QUERY:
            return self._connection.pk_rows[params[1]]
        if query == PG_FK_QUERY:
            return self._connection.fk_rows[params[1]]
        return []

    def close(self) -> None:
        self.closed = True
        self._connection.cursor_closed = True
        if self._connection.fail_cleanup:
            raise RuntimeError("cursor cleanup failure " + SECRET_DSN)


class FakeConnection:
    def __init__(self) -> None:
        self.server_version = 140005
        self.executed: list[tuple[str, Any]] = []
        self.last_query = ""
        self.last_params: Any = None
        self.closed = False
        self.rolled_back = False
        self.cursor_closed = False
        self.fail_queries = False
        self.fail_cleanup = False
        # Deliberately out of order to prove deterministic sorting.
        self.tables_in_disorder = ["enrollments", "departments", "courses"]
        self.columns: dict[str, list[dict[str, Any]]] = {
            "departments": [
                {
                    "column_name": "department_id",
                    "declared_type": "integer",
                    "nullable": False,
                    "default_sql": None,
                    "generated_kind": "",
                    "identity_kind": "",
                },
                {
                    "column_name": "name",
                    "declared_type": "text",
                    "nullable": False,
                    "default_sql": None,
                    "generated_kind": "",
                    "identity_kind": "",
                },
            ],
            "courses": [
                {
                    "column_name": "course_code",
                    "declared_type": "character varying(32)",
                    "nullable": False,
                    "default_sql": None,
                    "generated_kind": "",
                    "identity_kind": "",
                },
                {
                    "column_name": "title",
                    "declared_type": "character varying(200)",
                    "nullable": False,
                    "default_sql": None,
                    "generated_kind": "",
                    "identity_kind": "",
                },
                {
                    "column_name": "credits",
                    "declared_type": "numeric(4,1)",
                    "nullable": True,
                    "default_sql": "3.0",
                    "generated_kind": "",
                    "identity_kind": "",
                },
                {
                    "column_name": "department_id",
                    "declared_type": "integer",
                    "nullable": True,
                    "default_sql": None,
                    "generated_kind": "",
                    "identity_kind": "",
                },
                {
                    "column_name": "is_active",
                    "declared_type": "boolean",
                    "nullable": False,
                    "default_sql": "true",
                    "generated_kind": "",
                    "identity_kind": "",
                },
                {
                    "column_name": "score",
                    "declared_type": "double precision",
                    "nullable": True,
                    "default_sql": None,
                    "generated_kind": "",
                    "identity_kind": "",
                },
                {
                    "column_name": "avatar",
                    "declared_type": "bytea",
                    "nullable": True,
                    "default_sql": None,
                    "generated_kind": "",
                    "identity_kind": "",
                },
                {
                    "column_name": "ext_id",
                    "declared_type": "uuid",
                    "nullable": True,
                    "default_sql": None,
                    "generated_kind": "",
                    "identity_kind": "",
                },
                {
                    "column_name": "meta",
                    "declared_type": "jsonb",
                    "nullable": True,
                    "default_sql": None,
                    "generated_kind": "",
                    "identity_kind": "",
                },
                {
                    "column_name": "created_at",
                    "declared_type": "timestamp with time zone",
                    "nullable": True,
                    "default_sql": "now()",
                    "generated_kind": "",
                    "identity_kind": "",
                },
                {
                    "column_name": "title_length",
                    "declared_type": "integer",
                    "nullable": True,
                    "default_sql": None,
                    "generated_kind": "s",
                    "identity_kind": "",
                },
                {
                    "column_name": "enrollment_id",
                    "declared_type": "integer",
                    "nullable": False,
                    "default_sql": None,
                    "generated_kind": "",
                    "identity_kind": "d",
                },
            ],
            "enrollments": [
                {
                    "column_name": "student_id",
                    "declared_type": "integer",
                    "nullable": False,
                    "default_sql": None,
                    "generated_kind": "",
                    "identity_kind": "",
                },
                {
                    "column_name": "course_code",
                    "declared_type": "text",
                    "nullable": False,
                    "default_sql": None,
                    "generated_kind": "",
                    "identity_kind": "",
                },
                {
                    "column_name": "semester",
                    "declared_type": "text",
                    "nullable": False,
                    "default_sql": None,
                    "generated_kind": "",
                    "identity_kind": "",
                },
            ],
        }
        self.pk_rows: dict[str, list[dict[str, Any]]] = {
            "departments": [{"column_name": "department_id", "position": 1}],
            "courses": [{"column_name": "course_code", "position": 1}],
            "enrollments": [
                {"column_name": "student_id", "position": 1},
                {"column_name": "course_code", "position": 2},
                {"column_name": "semester", "position": 3},
            ],
        }
        self.fk_rows: dict[str, list[dict[str, Any]]] = {
            "departments": [],
            "courses": [
                {
                    "constraint_name": "courses_department_fk",
                    "column_name": "department_id",
                    "referenced_table": "departments",
                    "referenced_column": "department_id",
                    "update_action": "c",
                    "delete_action": "n",
                    "match_type": "s",
                    "position": 1,
                }
            ],
            "enrollments": [
                {
                    "constraint_name": "enroll_offering_fk",
                    "column_name": "semester",
                    "referenced_table": "departments",
                    "referenced_column": "name",
                    "update_action": "a",
                    "delete_action": "a",
                    "match_type": "s",
                    "position": 2,
                },
                {
                    "constraint_name": "enroll_offering_fk",
                    "column_name": "course_code",
                    "referenced_table": "departments",
                    "referenced_column": "department_id",
                    "update_action": "a",
                    "delete_action": "a",
                    "match_type": "s",
                    "position": 1,
                },
            ],
        }

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def rollback(self) -> None:
        self.rolled_back = True
        if self.fail_cleanup:
            raise RuntimeError("rollback failure " + SECRET_DSN)

    def close(self) -> None:
        self.closed = True
        if self.fail_cleanup:
            raise RuntimeError("close failure " + SECRET_DSN)


def round_trip_adapter(connection: FakeConnection) -> tuple[PostgresAdapter, Any]:
    holder: dict[str, FakeConnection] = {"current": connection}

    def connector(_config: PostgresConnectionConfig) -> FakeConnection:
        return holder["current"]

    adapter = PostgresAdapter({"pg-db": make_config()}, connector=connector)
    catalog = adapter.inspect("pg-db")
    return adapter, catalog


class CapabilityContractTest(unittest.TestCase):
    def test_exact_contract_values(self) -> None:
        sqlite = sqlite_capability()
        postgres = postgres_capability()
        self.assertTrue(sqlite.model_config.get("frozen"))
        self.assertEqual(sqlite.schema_version, "dbms-capability-v1")
        self.assertEqual(
            (sqlite.adapter, sqlite.dialect), ("sqlite", "sqlite")
        )
        self.assertEqual(
            (postgres.adapter, postgres.dialect), ("postgres", "postgres")
        )
        self.assertTrue(sqlite.supports_schema_inspection)
        self.assertTrue(sqlite.supports_compiler)
        self.assertTrue(sqlite.supports_policy)
        self.assertTrue(sqlite.supports_executor)
        self.assertTrue(postgres.supports_schema_inspection)
        self.assertTrue(postgres.supports_compiler)
        self.assertTrue(postgres.supports_policy)
        self.assertTrue(postgres.supports_executor)
        for capability in (sqlite, postgres):
            self.assertEqual(
                sorted(capability.supported_query_ir_features),
                ["filters", "joins", "limit", "select"],
            )
            self.assertEqual(
                sorted(capability.unsupported_query_ir_features),
                ["aggregate", "cte", "group_by", "order_by", "subquery", "write"],
            )
            self.assertTrue(capability.read_only)
            self.assertTrue(capability.parameterized_only)
            self.assertEqual(capability.max_rows, 200)
            self.assertEqual(capability.max_timeout_seconds, 2.0)

    def test_get_capability_is_deterministic(self) -> None:
        self.assertEqual(get_capability("sqlite"), sqlite_capability())
        self.assertEqual(get_capability("postgres"), postgres_capability())

    def test_unknown_dialect_fails_closed(self) -> None:
        for bad in ("oracle", "mysql", "POSTGRES", "", "sqlite ", "pg"):
            with self.assertRaises(CapabilityError) as raised:
                get_capability(bad)
            self.assertEqual(raised.exception.code, CapabilityErrorCode.UNKNOWN_DIALECT)
            self.assertEqual(raised.exception.message, "Unknown database adapter")

    def test_capability_is_frozen_versioned(self) -> None:
        capability = postgres_capability()
        with self.assertRaises(Exception):
            capability.max_rows = 5  # type: ignore[misc]
        self.assertIsInstance(capability.supported_query_ir_features, tuple)
        with self.assertRaises(AttributeError):
            capability.supported_query_ir_features.append("write")  # type: ignore[attr-defined]
        dumped = capability.model_dump(mode="json")
        self.assertEqual(dumped["schema_version"], "dbms-capability-v1")


class PostgresAdapterRoundTripTest(unittest.TestCase):
    def test_catalog_round_trip_order_and_keys(self) -> None:
        connection = FakeConnection()
        _, catalog = round_trip_adapter(connection)

        self.assertEqual(catalog.database_id, "pg-db")
        self.assertEqual(catalog.dialect, "postgres")
        self.assertEqual(
            [table.name for table in catalog.tables],
            ["courses", "departments", "enrollments"],
        )
        courses = next(t for t in catalog.tables if t.name == "courses")
        self.assertEqual(
            [c.name for c in courses.columns],
            [
                "course_code",
                "title",
                "credits",
                "department_id",
                "is_active",
                "score",
                "avatar",
                "ext_id",
                "meta",
                "created_at",
                "title_length",
                "enrollment_id",
            ],
        )
        self.assertEqual(courses.primary_key, ["course_code"])
        enrollments = next(t for t in catalog.tables if t.name == "enrollments")
        self.assertEqual(
            enrollments.primary_key, ["student_id", "course_code", "semester"]
        )
        # Composite FK arrives out of order from the fake but must be sequenced.
        self.assertEqual(
            [(fk.column, fk.sequence, fk.id) for fk in enrollments.foreign_keys],
            [("course_code", 0, 0), ("semester", 1, 0)],
        )
        single = courses.foreign_keys[0]
        self.assertEqual(single.referenced_table, "departments")
        self.assertEqual(single.on_update, "CASCADE")
        self.assertEqual(single.on_delete, "SET NULL")
        self.assertEqual(single.match, "SIMPLE")

    def test_generated_identity_and_affinity(self) -> None:
        _, catalog = round_trip_adapter(FakeConnection())
        courses = next(t for t in catalog.tables if t.name == "courses")
        by_name = {c.name: c for c in courses.columns}
        self.assertTrue(by_name["title_length"].generated)
        self.assertTrue(by_name["enrollment_id"].generated)
        self.assertFalse(by_name["title"].generated)
        self.assertEqual(by_name["course_code"].affinity, SQLiteAffinity.TEXT)
        self.assertEqual(by_name["credits"].affinity, SQLiteAffinity.NUMERIC)
        self.assertEqual(by_name["is_active"].affinity, SQLiteAffinity.INTEGER)
        self.assertEqual(by_name["score"].affinity, SQLiteAffinity.REAL)
        self.assertEqual(by_name["avatar"].affinity, SQLiteAffinity.BLOB)
        self.assertEqual(by_name["ext_id"].affinity, SQLiteAffinity.TEXT)
        self.assertEqual(by_name["meta"].affinity, SQLiteAffinity.TEXT)
        self.assertEqual(by_name["created_at"].affinity, SQLiteAffinity.TEXT)
        self.assertEqual(by_name["credits"].default_sql, "3.0")
        self.assertTrue(by_name["course_code"].primary_key_position > 0)

    def test_postgres_affinity_point_conservative(self) -> None:
        self.assertEqual(postgres_affinity("point"), SQLiteAffinity.TEXT)
        self.assertEqual(postgres_affinity("boolean"), SQLiteAffinity.INTEGER)
        self.assertEqual(postgres_affinity(""), SQLiteAffinity.BLOB)

    def test_parameterized_single_schema_readonly_timeout(self) -> None:
        connection = FakeConnection()
        _, _ = round_trip_adapter(connection)
        queries = [query for query, _ in connection.executed]
        self.assertIn("SET TRANSACTION READ ONLY", queries)
        self.assertTrue(
            any("statement_timeout" in query for query in queries)
        )
        metadata = [
            (query, params)
            for query, params in connection.executed
            if "pg_catalog" in query
        ]
        self.assertEqual(len(metadata), 10)  # 1 tables + 3 tables x 3 queries
        for query, params in metadata:
            self.assertIn("%s", query)
            self.assertIsInstance(params, tuple)
            self.assertEqual(params[0], "public")
            self.assertNotIn("departments", query)
            self.assertNotIn("enrollments", query)
            self.assertNotIn("public", query.replace("n.nspname = %s", ""))
        self.assertIn(PG_TABLES_QUERY, queries)
        self.assertIn(PG_COLUMNS_QUERY, queries)
        self.assertIn(PG_PK_QUERY, queries)
        self.assertIn(PG_FK_QUERY, queries)
        fk_calls = [params for query, params in metadata if query == PG_FK_QUERY]
        self.assertEqual(len(fk_calls), 3)
        self.assertTrue(all(params[2] == "public" for params in fk_calls))
        self.assertIn("rn.nspname = %s", PG_FK_QUERY)
        # relkind filter excludes views/materialized/foreign tables.
        self.assertIn("relkind IN ('r', 'p')", PG_TABLES_QUERY)

    def test_fingerprint_deterministic_without_samples(self) -> None:
        first = round_trip_adapter(FakeConnection())[1]
        second = round_trip_adapter(FakeConnection())[1]
        self.assertEqual(first.schema_fingerprint, second.schema_fingerprint)
        self.assertRegex(first.schema_fingerprint, r"^[0-9a-f]{64}$")
        for table in first.tables:
            for column in table.columns:
                self.assertEqual(column.sample_values, [])

    def test_include_samples_blocked_with_zero_queries(self) -> None:
        calls: list[str] = []

        def connector(_config: PostgresConnectionConfig) -> FakeConnection:
            calls.append("called")
            return FakeConnection()

        adapter = PostgresAdapter({"pg-db": make_config()}, connector=connector)
        with self.assertRaises(SchemaInspectionError) as raised:
            adapter.inspect("pg-db", include_samples=True)
        self.assertEqual(raised.exception.code, CatalogErrorCode.INSPECTION_FAILED)
        self.assertEqual(calls, [])

    def test_unknown_database_id(self) -> None:
        adapter = PostgresAdapter(
            {"pg-db": make_config()}, connector=lambda _c: FakeConnection()
        )
        with self.assertRaises(SchemaInspectionError) as raised:
            adapter.inspect("missing-db")
        self.assertEqual(raised.exception.code, CatalogErrorCode.DATABASE_NOT_FOUND)
        self.assertEqual(
            raised.exception.message,
            "PostgreSQL database is not configured",
        )

        with self.assertRaises(SchemaInspectionError) as reflected:
            adapter.inspect("postgresql://user:password@secret-host/db")
        self.assertNotIn("password", str(reflected.exception))
        self.assertNotIn("secret-host", str(reflected.exception))

    def test_unsupported_server_version_fails_closed(self) -> None:
        connection = FakeConnection()
        connection.server_version = 110022
        adapter = PostgresAdapter(
            {"pg-db": make_config()}, connector=lambda _c: connection
        )
        with self.assertRaises(SchemaInspectionError) as raised:
            adapter.inspect("pg-db")
        self.assertEqual(raised.exception.code, CatalogErrorCode.INSPECTION_FAILED)
        self.assertEqual(
            raised.exception.message, "PostgreSQL schema inspection failed"
        )
        self.assertEqual(connection.executed, [])
        self.assertTrue(connection.closed)

    def test_invalid_config_and_schema_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PostgresConnectionConfig(SECRET_DSN, schema="bad-schema!")
        with self.assertRaises(ValueError):
            PostgresConnectionConfig(SECRET_DSN, schema="public; DROP TABLE x")
        # A syntactically valid non-public schema is still pinned to a
        # single schema via parameters for every metadata query.
        with self.assertRaises(ValueError):
            PostgresAdapter({"BAD ID": make_config()})
        with self.assertRaises(ValueError):
            PostgresConnectionConfig(
                SECRET_DSN, connect_timeout_seconds=60.0
            )
        with self.assertRaises(ValueError):
            PostgresConnectionConfig(SECRET_DSN, statement_timeout_ms=60000)

    def test_connector_query_and_cleanup_failures_redacted(self) -> None:
        def exploding(_config: PostgresConnectionConfig) -> FakeConnection:
            raise RuntimeError("connect boom " + SECRET_DSN)

        adapter = PostgresAdapter({"pg-db": make_config()}, connector=exploding)
        with self.assertRaises(SchemaInspectionError) as raised:
            adapter.inspect("pg-db")
        self.assertEqual(raised.exception.code, CatalogErrorCode.INSPECTION_FAILED)
        self.assertNotIn("s3cret-pw", str(raised.exception))
        self.assertNotIn("boom", str(raised.exception))

        failing = FakeConnection()
        failing.fail_queries = True
        _, adapter2 = None, PostgresAdapter(
            {"pg-db": make_config()}, connector=lambda _c: failing
        )
        with self.assertRaises(SchemaInspectionError) as raised2:
            adapter2.inspect("pg-db")
        self.assertNotIn("s3cret-pw", str(raised2.exception))
        self.assertNotIn("boom", str(raised2.exception))
        self.assertNotIn("SELECT *", str(raised2.exception))
        self.assertTrue(failing.closed)

    def test_cleanup_runs_and_redacts(self) -> None:
        connection = FakeConnection()
        _, _ = round_trip_adapter(connection)
        self.assertTrue(connection.closed)
        self.assertTrue(connection.rolled_back)
        self.assertTrue(connection.cursor_closed)

        messy = FakeConnection()
        messy.fail_cleanup = True
        holder: dict[str, FakeConnection] = {"current": messy}
        adapter = PostgresAdapter(
            {"pg-db": make_config()},
            connector=lambda _c: holder["current"],
        )
        # Cleanup failures must not surface DSN/exception text.
        try:
            adapter.inspect("pg-db")
        except SchemaInspectionError as exc:
            self.assertNotIn("s3cret-pw", str(exc))
        self.assertTrue(messy.closed)

    def test_dsn_never_rendered(self) -> None:
        config = make_config()
        self.assertNotIn("s3cret-pw", repr(config))
        self.assertNotIn("s3cret-pw", str(config))
        self.assertNotIn("svc-user", repr(config))
        connection = FakeConnection()
        _, catalog = round_trip_adapter(connection)
        self.assertNotIn("s3cret-pw", catalog.model_dump_json())
        self.assertNotIn("s3cret-pw", postgres_capability().model_dump_json())

    def test_production_import_failure_is_fixed_error(self) -> None:
        from backend.adaptive.catalog import postgres as pg_module

        saved = sys.modules.get("psycopg2")
        sys.modules["psycopg2"] = None  # type: ignore[assignment]
        try:
            adapter = PostgresAdapter(
                {"pg-db": make_config()},
                connector=pg_module._production_connector,
            )
            with self.assertRaises(SchemaInspectionError) as raised:
                adapter.inspect("pg-db")
            self.assertEqual(
                raised.exception.code, CatalogErrorCode.INSPECTION_FAILED
            )
            self.assertEqual(
                raised.exception.message, "PostgreSQL schema inspection failed"
            )
        finally:
            if saved is not None:
                sys.modules["psycopg2"] = saved
            else:
                del sys.modules["psycopg2"]

    def test_production_connector_rounds_connect_timeout_up(self) -> None:
        from backend.adaptive.catalog import postgres as pg_module

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

    def test_aligner_and_relation_planning_accept_postgres_catalog(self) -> None:
        _, catalog = round_trip_adapter(FakeConnection())
        proposal = RuleBasedSchemaAligner().align(catalog, load_default_ontology())
        self.assertEqual(proposal.database_id, "pg-db")
        self.assertEqual(proposal.schema_fingerprint, catalog.schema_fingerprint)
        graph = build_physical_relation_graph(catalog)
        relation_ids = [relation.relation_id for relation in graph.relations]
        self.assertIn("courses:fk:0:departments", relation_ids)
        self.assertIn("enrollments:fk:0:departments", relation_ids)


class SQLiteRegressionTest(unittest.TestCase):
    def test_sqlite_affinity_serialization_unchanged(self) -> None:
        self.assertEqual(
            [item.value for item in SQLiteAffinity],
            ["INTEGER", "TEXT", "BLOB", "REAL", "NUMERIC"],
        )

    def test_sqlite_adapter_still_inspects(self) -> None:
        import sqlite3
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "regression.sqlite3"
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)"
                )
                connection.commit()
            finally:
                connection.close()
            catalog = SQLiteAdapter({"reg-db": path}).inspect("reg-db")
            self.assertEqual(catalog.dialect, "sqlite")
            self.assertRegex(catalog.schema_fingerprint, r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
