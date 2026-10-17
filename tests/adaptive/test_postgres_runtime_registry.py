"""Phase 7-04 server-owned PostgreSQL runtime registry tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.adaptive.api.clarification_http import START_PATH, SUBMIT_PATH
from backend.adaptive.api.postgres_runtime import (
    DatabaseRoutingCompiler,
    DatabaseRoutingPolicy,
    PostgresRuntimeProfile,
    build_validated_contexts,
)
from backend.adaptive.api.app_factory import (
    SYNTHETIC_PROTOTYPE_DATABASE_ID,
    create_synthetic_clarification_prototype,
)
from backend.adaptive.api.database_registry import (
    CANONICAL_SELECT,
    CATALOG_PATH,
    DATABASES_PATH,
)
from backend.adaptive.catalog.contracts import (
    ColumnCatalog,
    DatabaseCatalog,
    ForeignKeyCatalog,
    SQLiteAffinity,
    TableCatalog,
)
from backend.adaptive.catalog.postgres import PostgresConnectionConfig
from backend.adaptive.harness.contracts import CompiledQuery, HarnessErrorCode
from backend.adaptive.harness.errors import HarnessFailure
from backend.adaptive.mapping.contracts import (
    PublishedFieldMapping,
    PublishedMappingProfile,
)

DATABASE_ID = "synthetic-postgres-runtime"
SECRET_DSN = (
    "postgresql://svc-user:s3cret-pw@127.0.0.1:5432/"
    "schema_adaptive_synthetic"
)
FINGERPRINT = "ab" * 32
TRIGGER = "SYNTH_Q_P616_k9w1 강의 목록 보여줘"

FIELD_MAP = {
    "Course.code": ("courses", "course_code"),
    "Course.name": ("courses", "course_name"),
    "Course.credits": ("courses", "credits"),
    "Course.category": ("courses", "category"),
    "Department.code": ("departments", "department_code"),
    "Department.name": ("departments", "department_name"),
    "Instructor.name": ("instructors", "instructor_name"),
    "CourseOffering.year": ("course_offerings", "academic_year"),
    "CourseOffering.semester": ("course_offerings", "semester"),
    "CourseOffering.section": ("course_offerings", "section"),
    "CourseOffering.capacity": ("course_offerings", "capacity"),
    "CourseOffering.enrolled": ("course_offerings", "enrolled"),
    "Schedule.day": ("schedules", "day_of_week"),
    "Schedule.start_time": ("schedules", "start_time"),
    "Schedule.end_time": ("schedules", "end_time"),
}

SEMANTIC_ROW: dict[str, Any] = {
    "Course.code": "CSE101",
    "Course.name": "자료구조",
    "Course.credits": Decimal("3.0"),
    "Course.category": "전공",
    "Department.code": "CSE",
    "Department.name": "컴퓨터공학과",
    "Instructor.name": "홍길동",
    "CourseOffering.year": 2026,
    "CourseOffering.semester": "1학기",
    "CourseOffering.section": "01",
    "CourseOffering.capacity": 50,
    "CourseOffering.enrolled": 30,
    "Schedule.day": "월",
    "Schedule.start_time": "09:00",
    "Schedule.end_time": "10:30",
}


def _column(name: str) -> ColumnCatalog:
    return ColumnCatalog(
        name=name,
        declared_type="text",
        affinity=SQLiteAffinity.TEXT,
        nullable=False,
        sample_values=[],
    )


def _foreign_key(column: str, table: str, referenced_column: str, key: int) -> ForeignKeyCatalog:
    return ForeignKeyCatalog(
        id=key,
        sequence=0,
        column=column,
        referenced_table=table,
        referenced_column=referenced_column,
        on_update="NO ACTION",
        on_delete="NO ACTION",
        match="SIMPLE",
    )


def make_catalog(
    database_id: str = DATABASE_ID,
    *,
    fingerprint: str = FINGERPRINT,
    dialect: str = "postgres",
) -> DatabaseCatalog:
    return DatabaseCatalog(
        database_id=database_id,
        dialect=dialect,
        schema_fingerprint=fingerprint,
        tables=[
            TableCatalog(
                name="course_offerings",
                columns=[
                    _column(name)
                    for name in (
                        "offering_id",
                        "course_code",
                        "instructor_id",
                        "academic_year",
                        "semester",
                        "section",
                        "capacity",
                        "enrolled",
                    )
                ],
                primary_key=["offering_id"],
                foreign_keys=[
                    _foreign_key("course_code", "courses", "course_code", 0),
                    _foreign_key("instructor_id", "instructors", "instructor_id", 1),
                ],
            ),
            TableCatalog(
                name="courses",
                columns=[
                    _column(name)
                    for name in (
                        "course_code",
                        "course_name",
                        "credits",
                        "category",
                        "department_code",
                    )
                ],
                primary_key=["course_code"],
                foreign_keys=[
                    _foreign_key(
                        "department_code", "departments", "department_code", 0
                    )
                ],
            ),
            TableCatalog(
                name="departments",
                columns=[_column("department_code"), _column("department_name")],
                primary_key=["department_code"],
                foreign_keys=[],
            ),
            TableCatalog(
                name="instructors",
                columns=[_column("instructor_id"), _column("instructor_name")],
                primary_key=["instructor_id"],
                foreign_keys=[],
            ),
            TableCatalog(
                name="schedules",
                columns=[
                    _column(name)
                    for name in (
                        "schedule_id",
                        "offering_id",
                        "day_of_week",
                        "start_time",
                        "end_time",
                    )
                ],
                primary_key=["schedule_id"],
                foreign_keys=[
                    _foreign_key(
                        "offering_id", "course_offerings", "offering_id", 0
                    )
                ],
            ),
        ],
    )


def make_profile(
    database_id: str = DATABASE_ID,
    *,
    fingerprint: str = FINGERPRINT,
    fields: dict[str, tuple[str, str]] | None = None,
    ontology_version: int = 1,
) -> PostgresRuntimeProfile:
    published = PublishedMappingProfile(
        database_id=database_id,
        version=1,
        schema_fingerprint=fingerprint,
        ontology_version=ontology_version,
        published_by="synthetic-test",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        fields={
            semantic: PublishedFieldMapping(
                table=table, column=column, reviewer="synthetic-test"
            )
            for semantic, (table, column) in (fields or FIELD_MAP).items()
        },
    )
    return PostgresRuntimeProfile(
        database_id=database_id,
        label="Synthetic PostgreSQL",
        connection_config=PostgresConnectionConfig(SECRET_DSN, schema="private_schema"),
        published_mapping=published,
    )


class FakeAdapter:
    def __init__(self, catalog: DatabaseCatalog) -> None:
        self.catalog = catalog
        self.calls = 0
        self.drift_on_call: int | None = None
        self.error: Exception | None = None

    def inspect(self, database_id: str, *, include_samples: bool = False) -> DatabaseCatalog:
        assert database_id == self.catalog.database_id
        assert include_samples is False
        self.calls += 1
        if self.error is not None:
            raise self.error
        catalog = self.catalog.model_copy(deep=True)
        if self.drift_on_call == self.calls:
            catalog.schema_fingerprint = "cd" * 32
        return catalog


class FakeExecutor:
    def __init__(self) -> None:
        self.calls = 0
        self.error: Exception | None = None

    def execute(
        self, database_id: str, compiled: CompiledQuery
    ) -> tuple[list[str], list[dict[str, Any]]]:
        assert database_id == DATABASE_ID
        self.calls += 1
        if self.error is not None:
            raise self.error
        columns = list(compiled.expected_columns)
        return columns, [{column: SEMANTIC_ROW[column] for column in columns}]


def make_app() -> tuple[Any, FakeAdapter, FakeExecutor]:
    adapter = FakeAdapter(make_catalog())
    executor = FakeExecutor()
    app = create_synthetic_clarification_prototype(
        postgres_profiles=[make_profile()],
        postgres_adapters={DATABASE_ID: adapter},
        postgres_executors={DATABASE_ID: executor},
    )
    return app, adapter, executor


def test_default_app_has_zero_postgres_profiles_and_connections(monkeypatch) -> None:
    import backend.adaptive.catalog.postgres as catalog_module
    import backend.adaptive.harness.postgres as harness_module

    def explode(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("PostgreSQL dependency must not be constructed")

    monkeypatch.setattr(catalog_module, "PostgresAdapter", explode)
    monkeypatch.setattr(harness_module, "PostgresReadOnlyExecutor", explode)
    app = create_synthetic_clarification_prototype()
    response = TestClient(app).get(DATABASES_PATH)
    assert response.status_code == 200
    assert app.state.synthetic_postgres_database_ids == ()
    assert all(entry["schema"] != "postgres" for entry in response.json()["databases"])


def test_postgres_registry_catalog_and_clarification_routing() -> None:
    app, adapter, executor = make_app()
    client = TestClient(app, raise_server_exceptions=False)
    listed = client.get(DATABASES_PATH)
    assert listed.status_code == 200
    assert {
        "database_id": DATABASE_ID,
        "label": "Synthetic PostgreSQL",
        "schema": "postgres",
    } in listed.json()["databases"]

    catalog = client.get(CATALOG_PATH, params={"database_id": DATABASE_ID})
    assert catalog.status_code == 200
    assert catalog.json()["total"] == 1
    assert catalog.json()["items"][0]["course_name"] == "자료구조"

    started = client.post(
        START_PATH,
        json={"request_id": "phase704", "database_id": DATABASE_ID, "question": TRIGGER},
    )
    assert started.status_code == 200
    option = started.json()["options"][0]
    submitted = client.post(
        SUBMIT_PATH,
        json={
            "session_token": started.json()["session_token"],
            "selection": [option["option_id"]],
        },
    )
    assert submitted.status_code == 200
    assert submitted.json()["execution"]["status"] == "SUCCEEDED"
    assert executor.calls == 2
    assert adapter.calls >= 5


def test_execution_immediate_drift_check_blocks_catalog_query() -> None:
    app, adapter, executor = make_app()
    # startup=1, catalog entry check=2, routing executor pre-execute check=3
    adapter.drift_on_call = 3
    response = TestClient(app, raise_server_exceptions=False).get(
        CATALOG_PATH, params={"database_id": DATABASE_ID}
    )
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "SYNTHETIC_CATALOG_CONTEXT_UNAVAILABLE"}
    }
    assert executor.calls == 0


@pytest.mark.parametrize(
    "profile,catalog",
    [
        (make_profile(fingerprint="ef" * 32), make_catalog()),
        (
            make_profile(
                fields={**FIELD_MAP, "Course.name": ("missing", "course_name")}
            ),
            make_catalog(),
        ),
        (
            make_profile(
                fields={**FIELD_MAP, "Course.name": ("courses", "course_code")}
            ),
            make_catalog(),
        ),
        (make_profile(ontology_version=2), make_catalog()),
    ],
)
def test_invalid_startup_profiles_fail_closed(
    profile: PostgresRuntimeProfile, catalog: DatabaseCatalog
) -> None:
    with pytest.raises(RuntimeError, match="^synthetic prototype unavailable$"):
        create_synthetic_clarification_prototype(
            postgres_profiles=[profile],
            postgres_adapters={DATABASE_ID: FakeAdapter(catalog)},
            postgres_executors={DATABASE_ID: FakeExecutor()},
        )


def test_duplicate_fixed_id_and_http_injection_are_rejected() -> None:
    duplicate = make_profile(SYNTHETIC_PROTOTYPE_DATABASE_ID)
    with pytest.raises(RuntimeError, match="^synthetic prototype unavailable$"):
        create_synthetic_clarification_prototype(
            postgres_profiles=[duplicate],
            postgres_adapters={
                SYNTHETIC_PROTOTYPE_DATABASE_ID: FakeAdapter(
                    make_catalog(SYNTHETIC_PROTOTYPE_DATABASE_ID)
                )
            },
            postgres_executors={SYNTHETIC_PROTOTYPE_DATABASE_ID: FakeExecutor()},
        )

    app, _, _ = make_app()
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get(
        CATALOG_PATH, params={"database_id": DATABASE_ID, "dsn": SECRET_DSN}
    ).status_code == 400
    assert client.post(
        START_PATH,
        json={
            "request_id": "inject",
            "database_id": DATABASE_ID,
            "question": TRIGGER,
            "schema": "private_schema",
        },
    ).status_code == 400


def test_profile_snapshots_config_and_all_public_failures_are_redacted() -> None:
    config = PostgresConnectionConfig(SECRET_DSN, schema="private_schema")
    published = make_profile().published_mapping
    profile = PostgresRuntimeProfile(
        database_id=DATABASE_ID,
        label="Synthetic PostgreSQL",
        connection_config=config,
        published_mapping=published,
    )
    object.__setattr__(config, "_schema", "source-tampered")
    assert profile.connection_config.schema == "private_schema"
    profile_config = profile.connection_config
    object.__setattr__(profile_config, "_schema", "tampered")
    assert profile.connection_config.schema == "private_schema"
    assert "s3cret-pw" not in repr(profile)
    assert "private_schema" not in repr(profile)

    app, adapter, executor = make_app()
    executor.error = RuntimeError(
        "SELECT secret FROM physical_table " + SECRET_DSN
    )
    response = TestClient(app, raise_server_exceptions=False).get(
        CATALOG_PATH, params={"database_id": DATABASE_ID}
    )
    blob = response.text + repr(profile)
    assert response.status_code == 404
    for secret in (
        "s3cret-pw",
        "127.0.0.1",
        "private_schema",
        "physical_table",
        "SELECT",
    ):
        assert secret not in blob

    adapter.error = RuntimeError("driver leaked " + SECRET_DSN)
    failed = TestClient(app, raise_server_exceptions=False).post(
        START_PATH,
        json={"request_id": "redact", "database_id": DATABASE_ID, "question": TRIGGER},
    )
    assert failed.status_code == 503
    assert "s3cret-pw" not in failed.text


def test_router_compiler_and_policy_wrap_untrusted_error_text() -> None:
    profile = make_profile()
    context = build_validated_contexts(
        [profile],
        adapters_by_id={DATABASE_ID: FakeAdapter(make_catalog())},
        executors_by_id={DATABASE_ID: FakeExecutor()},
        expected_ontology_version=1,
    )[DATABASE_ID]
    mapping = context.harness_mapping()

    class ExplodingCompiler:
        def compile(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("SELECT secret " + SECRET_DSN)

    class ExplodingPolicy:
        def validate(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("physical_table " + SECRET_DSN)

    object.__setattr__(context, "_compiler", ExplodingCompiler())
    object.__setattr__(context, "_policy", ExplodingPolicy())
    compiler = DatabaseRoutingCompiler(ExplodingCompiler(), {DATABASE_ID: context})
    policy = DatabaseRoutingPolicy(ExplodingPolicy(), {DATABASE_ID: context})

    with pytest.raises(HarnessFailure) as compiled:
        compiler.compile(object(), mapping)
    assert compiled.value.code is HarnessErrorCode.SQL_COMPILE_ERROR
    assert "s3cret-pw" not in str(compiled.value)
    assert "SELECT" not in str(compiled.value)

    with pytest.raises(HarnessFailure) as validated:
        policy.validate(object(), mapping=mapping)
    assert validated.value.code is HarnessErrorCode.UNSAFE_SQL
    assert "s3cret-pw" not in str(validated.value)
    assert "physical_table" not in str(validated.value)
