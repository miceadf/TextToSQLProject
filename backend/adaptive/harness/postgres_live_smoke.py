"""Synthetic PostgreSQL live smoke (Phase 7-03, backend/CLI only).

Default execution performs zero provider/DB connections, DDL, or file
writes. Live work requires explicit ``--live-synthetic`` plus the
server-owned ``SCHEMA_ADAPTIVE_SYNTHETIC_POSTGRES_DSN`` environment
variable. All failures use fixed codes; DSN, credentials, hosts, schema
names, SQL, questions, rows, samples, and driver text never appear in
stdout, results, or errors.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlsplit

SMOKE_VERSION = "postgres-live-synthetic-v1"
DSN_ENV = "SCHEMA_ADAPTIVE_SYNTHETIC_POSTGRES_DSN"
SYNTHETIC_DATABASE = "schema_adaptive_synthetic"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
ALLOWED_QUERY_OPTIONS = frozenset({"sslmode", "connect_timeout", "application_name"})
ALLOWED_SSL_MODES = frozenset(
    {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
)
APPLICATION_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
SMOKE_DATABASE_ID = "synthetic-postgres"

EXIT_OK = 0
EXIT_FAIL = 2

_FIXED_CODES = frozenset(
    {
        "FLAG_REQUIRED",
        "ENV_MISSING",
        "INVALID_SCHEME",
        "INVALID_HOST",
        "INVALID_DATABASE",
        "INVALID_OPTION",
        "INVALID_DSN",
        "FIXTURE_FAILED",
        "INSPECTION_FAILED",
        "MAPPING_FAILED",
        "QUERY_FAILED",
    }
)

SMOKE_EXPECTED_MAPPING: dict[str, tuple[str, str]] = {
    "Course.name": ("courses", "course_name"),
    "Department.name": ("departments", "department_name"),
    "Instructor.name": ("instructors", "instructor_name"),
    "Schedule.day": ("schedules", "day_of_week"),
}

_Q1_IR = {
    "entity": "Course",
    "select": ["Course.name", "Department.name"],
    "filters": [
        {"field": "Department.name", "operator": "equals", "value": "Q1_FILTER"},
    ],
    "limit": 50,
}
_Q2_IR = {
    "entity": "Course",
    "select": ["Course.name", "Department.name"],
    "filters": [
        {"field": "Course.name", "operator": "equals", "value": "__no_such_course__"},
    ],
    "limit": 50,
}


class SmokeFailure(Exception):
    def __init__(self, code: str) -> None:
        if code not in _FIXED_CODES:
            code = "INVALID_DSN"
        super().__init__(code)
        self.code = code


def validate_dsn(raw: object) -> None:
    if not isinstance(raw, str) or not 1 <= len(raw) <= 2000:
        raise SmokeFailure("INVALID_DSN")
    try:
        parts = urlsplit(raw)
    except Exception:
        raise SmokeFailure("INVALID_DSN") from None
    if parts.scheme != "postgresql":
        raise SmokeFailure("INVALID_SCHEME")
    if parts.fragment:
        raise SmokeFailure("INVALID_DSN")
    netloc = parts.netloc
    if not netloc or "," in netloc:
        raise SmokeFailure("INVALID_HOST")
    host = parts.hostname
    if host is None:
        raise SmokeFailure("INVALID_HOST")
    if host.lower() not in LOOPBACK_HOSTS:
        raise SmokeFailure("INVALID_HOST")
    try:
        _ = parts.port
    except ValueError:
        raise SmokeFailure("INVALID_HOST") from None
    # Reject Unix-socket style authorities that smuggle a path into netloc.
    hostport = netloc.rsplit("@", 1)[-1]
    if "/" in hostport or "%2f" in hostport.lower():
        raise SmokeFailure("INVALID_HOST")
    if parts.path != f"/{SYNTHETIC_DATABASE}":
        raise SmokeFailure("INVALID_DATABASE")
    try:
        options = parse_qsl(parts.query, keep_blank_values=True)
    except Exception:
        raise SmokeFailure("INVALID_OPTION") from None
    seen: set[str] = set()
    for key, value in options:
        if key not in ALLOWED_QUERY_OPTIONS or not value or key in seen:
            raise SmokeFailure("INVALID_OPTION")
        if key == "sslmode" and value not in ALLOWED_SSL_MODES:
            raise SmokeFailure("INVALID_OPTION")
        if key == "connect_timeout" and (
            not value.isascii()
            or not value.isdecimal()
            or not 1 <= int(value) <= 10
        ):
            raise SmokeFailure("INVALID_OPTION")
        if key == "application_name" and APPLICATION_NAME_PATTERN.fullmatch(value) is None:
            raise SmokeFailure("INVALID_OPTION")
        seen.add(key)


def _safe_payload(status: str, code: str | None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"smoke": SMOKE_VERSION, "status": status}
    if code is not None:
        payload["code"] = code
    if extra:
        payload.update(extra)
    return payload


def _expected_q1_rows(seed: Any) -> list[dict[str, str]]:
    names = sorted(
        {
            offering.course_name
            for offering in seed.offerings
            if offering.department_name == "컴퓨터공학과"
        }
    )
    return [
        {"Course.name": name, "Department.name": "컴퓨터공학과"} for name in names
    ]


def run_smoke(
    *,
    live: bool,
    dsn: str | None,
    token_hex: Callable[[int], str] | None = None,
    open_bootstrap: Callable[[], Any] | None = None,
    make_adapter: Callable[[str], Any] | None = None,
    make_executor: Callable[[str], Any] | None = None,
    load_ontology_fn: Callable[[], Any] | None = None,
    sql_module: Any | None = None,
) -> tuple[int, dict[str, Any]]:
    if not live:
        return EXIT_FAIL, _safe_payload("NOT_RUN", "FLAG_REQUIRED")
    if not dsn:
        return EXIT_FAIL, _safe_payload("NOT_RUN", "ENV_MISSING")
    try:
        validate_dsn(dsn)
    except SmokeFailure as exc:
        return EXIT_FAIL, _safe_payload("CONFIG_ERROR", exc.code)

    from backend.adaptive.fixtures.postgres_synthetic import (
        SyntheticFixtureError,
        cleanup_synthetic_schema,
        generate_schema_name,
        setup_synthetic_schema,
    )

    try:
        schema = generate_schema_name(token_hex)
    except SyntheticFixtureError:
        return EXIT_FAIL, _safe_payload("FAILED", "FIXTURE_FAILED")

    bootstrap: Any | None = None
    if open_bootstrap is None:
        validated_dsn = dsn

        def _open() -> Any:
            try:
                import psycopg2  # type: ignore[import-not-found]
            except Exception:
                raise SmokeFailure("FIXTURE_FAILED") from None
            try:
                return psycopg2.connect(validated_dsn)
            except Exception:
                raise SmokeFailure("FIXTURE_FAILED") from None

        open_bootstrap = _open

    try:
        try:
            bootstrap = open_bootstrap()
        except SmokeFailure:
            return EXIT_FAIL, _safe_payload("FAILED", "FIXTURE_FAILED")
        except Exception:
            return EXIT_FAIL, _safe_payload("FAILED", "FIXTURE_FAILED")
    except Exception:
        return EXIT_FAIL, _safe_payload("FAILED", "FIXTURE_FAILED")

    cleanup_attempted = False

    def _cleanup_once() -> bool:
        nonlocal cleanup_attempted
        if cleanup_attempted:
            return False
        cleanup_attempted = True
        try:
            cleanup_synthetic_schema(bootstrap, schema, sql_module=sql_module)
        except Exception:
            return False
        return True

    try:
        try:
            setup_synthetic_schema(
                bootstrap, schema, sql_module=sql_module
            )
        except Exception:
            _cleanup_once()
            return EXIT_FAIL, _safe_payload("FAILED", "FIXTURE_FAILED")

        try:
            from backend.adaptive.catalog.postgres import PostgresConnectionConfig
            from backend.adaptive.catalog.errors import SchemaInspectionError
            from backend.adaptive.fixtures.postgres_synthetic import (
                EXPECTED_FOREIGN_KEYS,
                EXPECTED_PRIMARY_KEYS,
                EXPECTED_TABLES,
            )
            from backend.adaptive.harness.errors import HarnessFailure
            from backend.adaptive.mapping.rules import RuleBasedSchemaAligner
            from backend.adaptive.relations.physical import (
                build_physical_relation_graph,
            )
        except Exception:
            _cleanup_once()
            return EXIT_FAIL, _safe_payload("FAILED", "INSPECTION_FAILED")

        # --- inspect ---
        try:
            if make_adapter is None:
                config = PostgresConnectionConfig(dsn, schema=schema)

                def _default_adapter(_schema: str) -> Any:
                    from backend.adaptive.catalog.postgres import PostgresAdapter

                    return PostgresAdapter({SMOKE_DATABASE_ID: config})

                adapter = _default_adapter(schema)
            else:
                adapter = make_adapter(schema)
            catalog = adapter.inspect(SMOKE_DATABASE_ID)
        except Exception:
            _cleanup_once()
            return EXIT_FAIL, _safe_payload("FAILED", "INSPECTION_FAILED")

        try:
            if catalog.dialect != "postgres":
                raise ValueError("dialect")
            if [t.name for t in catalog.tables] != sorted(EXPECTED_TABLES):
                raise ValueError("tables")
            by_name = {t.name: t for t in catalog.tables}
            for table, pk in EXPECTED_PRIMARY_KEYS.items():
                if by_name[table].primary_key != pk:
                    raise ValueError("pk")
            for table, expected in EXPECTED_FOREIGN_KEYS.items():
                actual = {
                    (fk.column, fk.referenced_table, fk.referenced_column)
                    for fk in by_name[table].foreign_keys
                }
                if actual != expected:
                    raise ValueError("fk")
            for table in catalog.tables:
                for column in table.columns:
                    if column.sample_values:
                        raise ValueError("samples")
            fingerprint = catalog.schema_fingerprint
        except Exception:
            _cleanup_once()
            return EXIT_FAIL, _safe_payload("FAILED", "INSPECTION_FAILED")

        # --- mapping proposal ---
        try:
            loader = load_ontology_fn
            if loader is None:
                from backend.adaptive.ontology.loader import load_default_ontology

                loader = load_default_ontology
            ontology = loader()
            proposal = RuleBasedSchemaAligner().align(catalog, ontology)
            for field, (table, column) in SMOKE_EXPECTED_MAPPING.items():
                candidates = proposal.candidates.get(field, [])
                if not candidates or (candidates[0].table, candidates[0].column) != (
                    table,
                    column,
                ):
                    raise ValueError("mapping")
        except Exception:
            _cleanup_once()
            return EXIT_FAIL, _safe_payload("FAILED", "MAPPING_FAILED")

        # --- compile/policy/execute two fixed QueryIRs ---
        try:
            from datetime import datetime as _dt
            from datetime import timezone as _tz

            from backend.adaptive.compiler.postgres import (
                SchemaAdaptivePostgresCompiler,
            )
            from backend.adaptive.harness import (
                ExecutionHarness,
                FakeModel,
                HarnessStatus,
            )
            from backend.adaptive.harness.contracts import (
                DataType,
                MappingProfile,
                MappingStatus,
                Ontology,
                OntologyField,
                Operator,
            )
            from backend.adaptive.mapping import (
                PublishedFieldMapping,
                PublishedMappingProfile,
                to_harness_mapping,
            )
            from backend.adaptive.policy.postgres import SchemaAdaptivePostgresPolicy
            from backend.adaptive.relations.physical import (
                build_physical_relation_graph as _build_graph,
            )

            # Reuse already-validated graph builder; alias kept for injection clarity.
            _ = _build_graph
            from backend.adaptive.fixtures.seed import DEFAULT_SEED as _SEED

            published = PublishedMappingProfile(
                database_id=SMOKE_DATABASE_ID,
                version=1,
                schema_fingerprint=fingerprint,
                ontology_version=ontology.version,
                published_by="synthetic-smoke",
                published_at=_dt.now(_tz.utc),
                fields={
                    field: PublishedFieldMapping(
                        table=table, column=column, reviewer="synthetic-smoke"
                    )
                    for field, (table, column) in SMOKE_EXPECTED_MAPPING.items()
                },
            )
            graph = build_physical_relation_graph(catalog)
            runtime_ontology = Ontology(
                entities={"Course", "Department"},
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
            runtime_mapping: MappingProfile = to_harness_mapping(published)

            if make_executor is None:

                def _default_executor(_schema: str) -> Any:
                    from backend.adaptive.catalog.postgres import (
                        PostgresConnectionConfig as _Config,
                    )
                    from backend.adaptive.harness.postgres import (
                        PostgresReadOnlyExecutor,
                    )

                    return PostgresReadOnlyExecutor(
                        {SMOKE_DATABASE_ID: _Config(dsn, schema=_schema)}
                    )

                executor = _default_executor(schema)
            else:
                executor = make_executor(schema)

            q1_value = next(
                offering.department_name
                for offering in _SEED.offerings
                if offering.department_code == "CSE"
            )
            q1_ir = dict(_Q1_IR)
            q1_ir["filters"] = [
                {"field": "Department.name", "operator": "equals", "value": q1_value}
            ]
            harness = ExecutionHarness(
                model=FakeModel({"q1": q1_ir, "q2": dict(_Q2_IR)}),
                compiler=SchemaAdaptivePostgresCompiler(graph, published),
                sql_policy=SchemaAdaptivePostgresPolicy(graph, published),
                executor=executor,
            )
            first = harness.run(
                {"database_id": SMOKE_DATABASE_ID, "question": "q1"},
                runtime_ontology,
                runtime_mapping,
            )
            second = harness.run(
                {"database_id": SMOKE_DATABASE_ID, "question": "q2"},
                runtime_ontology,
                runtime_mapping,
            )
            if (
                first.status is not HarnessStatus.SUCCEEDED
                or second.status is not HarnessStatus.SUCCEEDED
            ):
                raise ValueError("query")
            expected_first = _expected_q1_rows(_SEED)
            actual_first = sorted(
                first.rows, key=lambda row: str(row.get("Course.name"))
            )
            if actual_first != expected_first or second.rows != []:
                raise ValueError("query")
            if first.columns != ["Course.name", "Department.name"]:
                raise ValueError("query")
        except SchemaInspectionError:
            _cleanup_once()
            return EXIT_FAIL, _safe_payload("FAILED", "INSPECTION_FAILED")
        except HarnessFailure:
            _cleanup_once()
            return EXIT_FAIL, _safe_payload("FAILED", "QUERY_FAILED")
        except Exception:
            _cleanup_once()
            return EXIT_FAIL, _safe_payload("FAILED", "QUERY_FAILED")

        if not _cleanup_once():
            return EXIT_FAIL, _safe_payload("FAILED", "FIXTURE_FAILED")
        return EXIT_OK, _safe_payload(
            "SUCCEEDED",
            None,
            {
                "table_count": len(catalog.tables),
                "mapping_count": len(published.fields),
                "query_count": 2,
                "schema_fingerprint": fingerprint,
            },
        )
    finally:
        try:
            close = getattr(bootstrap, "close", None)
            if callable(close):
                close()
        except Exception:
            pass


def main(
    argv: list[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    token_hex: Callable[[int], str] | None = None,
    open_bootstrap: Callable[[], Any] | None = None,
    make_adapter: Callable[[str], Any] | None = None,
    make_executor: Callable[[str], Any] | None = None,
    load_ontology_fn: Callable[[], Any] | None = None,
    sql_module: Any | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Synthetic PostgreSQL live smoke (opt-in only)"
    )
    parser.add_argument("--live-synthetic", action="store_true")
    args = parser.parse_args(argv)
    environment = env if env is not None else os.environ
    dsn = environment.get(DSN_ENV)
    exit_code, payload = run_smoke(
        live=bool(args.live_synthetic),
        dsn=dsn,
        token_hex=token_hex,
        open_bootstrap=open_bootstrap,
        make_adapter=make_adapter,
        make_executor=make_executor,
        load_ontology_fn=load_ontology_fn,
        sql_module=sql_module,
    )
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
