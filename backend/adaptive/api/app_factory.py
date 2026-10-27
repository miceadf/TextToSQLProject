"""Assemble the adaptive FastAPI app for the local development server.

The default dependencies use synthetic SQLite databases. The app also
assembles database registration, mapping review/publication, the course
catalog, clarification, and QueryIR chat routes for published PostgreSQL
databases. Explicit CLI options can add server-owned PostgreSQL profiles
or a loopback-only Ollama QueryIR provider; neither is contacted at startup.
The CLI serves only on local loopback.
"""

from __future__ import annotations

import argparse
import atexit
import copy
import tempfile
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from ..catalog.sqlite import SQLiteAdapter
from ..fixtures.flat import build_flat_database
from ..fixtures.legacy import build_legacy_database
from ..fixtures.seed import DEFAULT_SEED
from ..harness.ambiguity_detector import AMBIGUITY_DETECTOR_VERSION
from ..harness.clarification_session import ClarificationSessionCoordinator
from ..harness.compiler import SingleTableSQLiteCompiler
from ..harness.contracts import (
    DataType,
    FieldMapping,
    HarnessErrorCode,
    HarnessStage,
    MappingProfile,
    MappingStatus,
    Ontology,
    OntologyField,
    Operator,
)
from ..harness.errors import HarnessFailure
from ..harness.executor import SQLiteReadOnlyExecutor
from ..harness.policy import ConservativeSqlPolicy
from .admin_test_queries import register_admin_test_query_routes
from .chat_query import register_chat_query_routes
from .clarification_http import create_clarification_http_app
from .registration_upload import register_sqlite_upload_routes
from .review_publish import register_mapping_review_routes
from .database_registry import (
    SyntheticDatabaseRegistry,
    register_synthetic_database_routes,
)


SYNTHETIC_CLARIFICATION_PROTOTYPE_VERSION = "synthetic-clarification-prototype-v1"
SYNTHETIC_PROTOTYPE_DATABASE_ID = "synth-clarification-prototype"
SYNTHETIC_LEGACY_DATABASE_ID = "synth-clarification-legacy"

SYNTHETIC_FLAT_LABEL = "Synthetic flat (2026 seed)"
SYNTHETIC_LEGACY_LABEL = "Synthetic legacy (2026 seed)"

_FIXED_HOST = "127.0.0.1"
_DEFAULT_PORT = 8000
_MIN_PORT = 8000
_MAX_PORT = 8999

_PROTOTYPE_CREATION_FAILURE = "synthetic prototype unavailable"
_CLI_CONFIG_FAILURE = "synthetic prototype invalid configuration"

_COURSE_NAME_REF = "Course.name"
_INSTRUCTOR_NAME_REF = "Instructor.name"
_KNOWN_FIELD_REFS = frozenset({_COURSE_NAME_REF, _INSTRUCTOR_NAME_REF})

__all__ = [
    "SYNTHETIC_CLARIFICATION_PROTOTYPE_VERSION",
    "SYNTHETIC_PROTOTYPE_DATABASE_ID",
    "SYNTHETIC_LEGACY_DATABASE_ID",
    "SYNTHETIC_FLAT_LABEL",
    "SYNTHETIC_LEGACY_LABEL",
    "create_synthetic_clarification_prototype",
    "set_private_repository_root_for_tests",
    "main",
    "app",
]


def _build_ontology() -> Ontology:
    return Ontology(
        entities={"Course", "Instructor"},
        fields={
            _COURSE_NAME_REF: OntologyField(
                name=_COURSE_NAME_REF,
                data_type=DataType.STRING,
                allowed_operators={Operator.EQUALS, Operator.CONTAINS},
            ),
            _INSTRUCTOR_NAME_REF: OntologyField(
                name=_INSTRUCTOR_NAME_REF,
                data_type=DataType.STRING,
                allowed_operators={Operator.EQUALS, Operator.CONTAINS},
            ),
        },
    )


def _build_mapping() -> MappingProfile:
    return MappingProfile(
        database_id=SYNTHETIC_PROTOTYPE_DATABASE_ID,
        version=1,
        status=MappingStatus.PUBLISHED,
        fields={
            _COURSE_NAME_REF: FieldMapping(table="course_offerings", column="course_name"),
            _INSTRUCTOR_NAME_REF: FieldMapping(table="course_offerings", column="instructor_name"),
        },
    )


def _build_legacy_mapping() -> MappingProfile:
    return MappingProfile(
        database_id=SYNTHETIC_LEGACY_DATABASE_ID,
        version=1,
        status=MappingStatus.PUBLISHED,
        fields={
            _COURSE_NAME_REF: FieldMapping(table="lecture_info", column="sbjt_nm"),
            _INSTRUCTOR_NAME_REF: FieldMapping(table="lecture_info", column="prof_nm"),
        },
    )


def _build_rules() -> dict[str, Any]:
    return {
        "version": AMBIGUITY_DETECTOR_VERSION,
        "rules": [
            {
                "rule_id": "rule-prototype-field",
                "triggers": ["강의"],
                "options": [
                    {"option_key": "opt-course-name", "semantic_ref": _COURSE_NAME_REF},
                    {"option_key": "opt-instructor-name", "semantic_ref": _INSTRUCTOR_NAME_REF},
                ],
            }
        ],
    }


def _discover_private_repository_root() -> Path | None:
    try:
        here = Path(__file__).resolve()
    except Exception:
        return None
    for parent in (here, *here.parents):
        try:
            if (parent / ".git").exists():
                return parent
        except Exception:
            continue
    return None


# Repository root override for the clarification loader/executor, which have
# no app reference. Production leaves this unset (checkout discovery applies);
# tests point it at their temp git repo.
_PRIVATE_REPOSITORY_ROOT_OVERRIDE: Path | None = None


def set_private_repository_root_for_tests(root: str | Path | None) -> None:
    global _PRIVATE_REPOSITORY_ROOT_OVERRIDE
    _PRIVATE_REPOSITORY_ROOT_OVERRIDE = Path(root) if root is not None else None


def _private_repository_root() -> Path | None:
    if _PRIVATE_REPOSITORY_ROOT_OVERRIDE is not None:
        return _PRIVATE_REPOSITORY_ROOT_OVERRIDE
    return _discover_private_repository_root()


def _load_private_clarification_mapping(database_id: str) -> Any | None:
    """Build the 2-field clarification mapping from a strict activation."""
    try:
        from ..harness.contracts import FieldMapping, MappingProfile, MappingStatus
        from ..registration import reviews as _reviews

        root = _private_repository_root()
        if root is None:
            return None
        activation = _reviews.load_activation(
            repository_root=root, database_id=database_id
        )
        if activation.database_id != database_id:
            return None
        course = activation.profile.fields.get(_COURSE_NAME_REF)
        instructor = activation.profile.fields.get(_INSTRUCTOR_NAME_REF)
        if course is None or instructor is None:
            return None
        return MappingProfile(
            database_id=database_id,
            version=activation.mapping_version,
            status=MappingStatus.PUBLISHED,
            fields={
                _COURSE_NAME_REF: FieldMapping(table=course.table, column=course.column),
                _INSTRUCTOR_NAME_REF: FieldMapping(
                    table=instructor.table, column=instructor.column
                ),
            },
        )
    except Exception:
        return None


class _PrototypeContextLoader:
    """Registry-backed loader returning a defensive copy per call."""

    def __init__(self, schema_is_current: Any, pg_contexts: Any = None) -> None:
        self._schema_is_current = schema_is_current
        from .postgres_runtime import shared_or_copy

        self._pg_contexts = shared_or_copy(pg_contexts) if isinstance(pg_contexts, dict) else {}

    def load(self, database_id: str) -> tuple[Any, Any, Any]:
        pg_context = self._pg_contexts.get(database_id) if isinstance(database_id, str) else None
        if pg_context is not None:
            try:
                from .postgres_runtime import is_postgres_context_current

                current = is_postgres_context_current(pg_context)
            except Exception:
                current = False
            if current is not True:
                raise RuntimeError("synthetic context unavailable")
            try:
                return (
                    copy.deepcopy(_build_ontology()),
                    copy.deepcopy(pg_context.harness_mapping()),
                    copy.deepcopy(_build_rules()),
                )
            except Exception:
                raise RuntimeError("synthetic context unavailable") from None
        if database_id not in (
            SYNTHETIC_PROTOTYPE_DATABASE_ID,
            SYNTHETIC_LEGACY_DATABASE_ID,
        ):
            # Private activations skip the fixed-roster fingerprint guard;
            # strict activation reload is the freshness check.
            mapping = _load_private_clarification_mapping(database_id)
            if mapping is None:
                raise RuntimeError("synthetic context unavailable")
            return (
                copy.deepcopy(_build_ontology()),
                copy.deepcopy(mapping),
                copy.deepcopy(_build_rules()),
            )
        if not self._schema_is_current(database_id):
            raise RuntimeError("synthetic context unavailable")
        if database_id == SYNTHETIC_PROTOTYPE_DATABASE_ID:
            mapping = _build_mapping()
        else:
            mapping = _build_legacy_mapping()
        return (
            copy.deepcopy(_build_ontology()),
            copy.deepcopy(mapping),
            copy.deepcopy(_build_rules()),
        )


class _SchemaGuardedExecutor:
    """Reject execution when a registered fixture schema has drifted."""

    def __init__(self, executor: Any, schema_is_current: Any) -> None:
        self._executor = executor
        self._schema_is_current = schema_is_current

    def execute(self, database_id: str, compiled: Any) -> tuple[list[str], list[dict[str, Any]]]:
        try:
            current = self._schema_is_current(database_id)
        except Exception:
            current = False
        if current is not True:
            # Private activations are not covered by the fixed-roster
            # schema guard; strict activation reload is the freshness check.
            if database_id not in (
                SYNTHETIC_PROTOTYPE_DATABASE_ID,
                SYNTHETIC_LEGACY_DATABASE_ID,
            ):
                return self._execute_private(database_id, compiled)
            raise HarnessFailure(
                HarnessErrorCode.MAPPING_NOT_PUBLISHED,
                "published mapping is stale",
                stage=HarnessStage.EXECUTED,
            )
        if database_id not in (
            SYNTHETIC_PROTOTYPE_DATABASE_ID,
            SYNTHETIC_LEGACY_DATABASE_ID,
        ):
            return self._execute_private(database_id, compiled)
        return self._executor.execute(database_id, compiled)

    def _execute_private(
        self, database_id: str, compiled: Any
    ) -> tuple[list[str], list[dict[str, Any]]]:
        try:
            from ..harness.executor import SQLiteReadOnlyExecutor
            from ..registration import reviews as _reviews

            root = _private_repository_root()
            if root is None:
                raise HarnessFailure(
                    HarnessErrorCode.MAPPING_NOT_PUBLISHED,
                    "published mapping is stale",
                    stage=HarnessStage.EXECUTED,
                )
            activation = _reviews.load_activation(
                repository_root=root, database_id=database_id
            )
            if activation.database_id != database_id:
                raise HarnessFailure(
                    HarnessErrorCode.MAPPING_NOT_PUBLISHED,
                    "published mapping is stale",
                    stage=HarnessStage.EXECUTED,
                )
            private_executor = SQLiteReadOnlyExecutor(
                {database_id: activation.database_path}
            )
            return private_executor.execute(database_id, compiled)
        except HarnessFailure:
            raise
        except Exception:
            raise HarnessFailure(
                HarnessErrorCode.MAPPING_NOT_PUBLISHED,
                "published mapping is stale",
                stage=HarnessStage.EXECUTED,
            ) from None


class _PrivateActivationGuardCompiler:
    """Refuse to compile a private DB request whose mapping is no longer active.

    A clarification session keeps the mapping it started with; after a
    publish or re-activation (Phase 8-01) that mapping must not execute.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    @property
    def inner(self) -> Any:
        return self._inner

    def compile(self, query_ir: Any, mapping: Any) -> Any:
        database_id = getattr(mapping, "database_id", None)
        if isinstance(database_id, str) and database_id not in (
            SYNTHETIC_PROTOTYPE_DATABASE_ID,
            SYNTHETIC_LEGACY_DATABASE_ID,
        ):
            current = _load_private_clarification_mapping(database_id)
            if (
                current is None
                or getattr(mapping, "version", None) != current.version
                or getattr(mapping, "fields", None) != current.fields
            ):
                raise HarnessFailure(
                    HarnessErrorCode.MAPPING_NOT_PUBLISHED,
                    "published mapping is stale",
                    stage=HarnessStage.SQL_COMPILED,
                )
        return self._inner.compile(query_ir, mapping)


class _FixedConstraintProvider:
    """Deterministic provider: one valid QueryIR for the selected field.

    The question text, arbitrary SQL, and physical identifiers are never
    interpreted. An unknown semantic ref fails closed without detail.
    """

    def generate_ir(self, request: Any, ontology: Any, mapping: Any, constraint: Any) -> dict[str, Any]:
        ref: Any = None
        if isinstance(constraint, dict):
            ref = constraint.get("selected_semantic_ref")
        else:
            ref = getattr(constraint, "selected_semantic_ref", None)
        if ref not in _KNOWN_FIELD_REFS:
            raise RuntimeError("synthetic provider failure")
        entity = "Course" if ref == _COURSE_NAME_REF else "Instructor"
        return {"entity": entity, "select": [ref], "filters": [], "limit": 50}


def _upload_proposer(model: Any) -> Any:
    from ..mapping.ai_proposal import propose_upload_mapping

    def propose(catalog: Any, ontology: Any, rules: Any) -> Any:
        return propose_upload_mapping(catalog, ontology, rules, model=model)

    return propose


def create_synthetic_clarification_prototype(
    postgres_profiles: list[Any] | None = None,
    postgres_adapters: dict[str, Any] | None = None,
    postgres_executors: dict[str, Any] | None = None,
    ollama_model: Any | None = None,
    mapping_model: Any | None = None,
    postgres_value_reader: Any | None = None,
    show_chat_sql: bool = False,
    postgres_sources: dict[str, Any] | None = None,
    postgres_registration_options: dict[str, Any] | None = None,
    chat_models: Any = None,
    chat_model_repository_root: str | Path | None = None,
    chat_model_base_url: str | None = None,
    chat_model_timeout: str = "30",
) -> FastAPI:
    """Assemble the synthetic clarification prototype app.

    ``chat_models`` (Phase 9-08) lists the QueryIR chat's model choices,
    default first; the first Ollama choice also serves the clarification
    flow (Phase 9-09: a choice may be the ChatGPT API). It
    replaces ``ollama_model`` (pass one or the other).

    ``mapping_model`` (Phase 8-12) is a JSON-schema model call used only to
    order the review candidates of uploaded databases.

    ``postgres_value_reader`` (Phase 8-13) reads bounded distinct values for
    chat value hints. By default a read-only reader is built for profiles that
    use the real executor (injected test executors get none).

    ``show_chat_sql`` (Phase 8-14, development option) adds the executed SQL
    to successful chat answers. Off by default.

    ``postgres_sources`` (Phase 8-15) enables registering PostgreSQL schemas
    from these server-owned connections; published ones join the running
    server. ``postgres_registration_options`` injects test doubles
    (``adapter_factory``, ``executor_factory``, ``connector``).
    """
    tmp: Any = None
    try:
        chat_choices: tuple[Any, ...] | None = None
        live_chat_models: Any | None = None
        if chat_models is not None:
            from .chat_models import validate_choices
            from .live_chat_models import LiveChatModels

            if ollama_model is not None:
                raise RuntimeError(_PROTOTYPE_CREATION_FAILURE)
            try:
                from ..harness.ollama import OllamaQueryModel
                from ..harness.openai_chat import OpenAIQueryModel

                if isinstance(chat_models, LiveChatModels):
                    live_chat_models = chat_models
                    chat_choices = validate_choices(chat_models.snapshot())
                else:
                    live_chat_models = None
                    chat_choices = validate_choices(chat_models)
            except Exception:
                raise RuntimeError(_PROTOTYPE_CREATION_FAILURE) from None
            if any(
                not isinstance(choice.model, (OllamaQueryModel, OpenAIQueryModel))
                for choice in chat_choices
            ):
                raise RuntimeError(_PROTOTYPE_CREATION_FAILURE)
            if chat_model_repository_root is not None and chat_model_base_url is not None:
                if live_chat_models is None:
                    live_chat_models = LiveChatModels(chat_choices)
                from .custom_chat_models import load_live_custom

                try:
                    custom_chat_model_status = load_live_custom(
                        chat_model_repository_root, live_chat_models, chat_model_base_url, chat_model_timeout
                    )
                except Exception:
                    custom_chat_model_status = {"chat_model.custom": "disabled (STARTUP_FAILED)"}
            else:
                custom_chat_model_status = {}
            # Phase 9-09: the clarification flow stays on a local model; it
            # takes the first Ollama choice, or none.
            ollama_model = next(
                (choice.model for choice in chat_choices if isinstance(choice.model, OllamaQueryModel)),
                None,
            )
        if postgres_profiles is None:
            pg_profiles: list[Any] = []
        elif isinstance(postgres_profiles, list):
            pg_profiles = list(postgres_profiles)
        else:
            raise RuntimeError(_PROTOTYPE_CREATION_FAILURE)
        if postgres_adapters is None:
            pg_adapter_overrides: dict[str, Any] = {}
        elif isinstance(postgres_adapters, dict):
            pg_adapter_overrides = dict(postgres_adapters)
        else:
            raise RuntimeError(_PROTOTYPE_CREATION_FAILURE)
        if postgres_executors is None:
            pg_executor_overrides: dict[str, Any] = {}
        elif isinstance(postgres_executors, dict):
            pg_executor_overrides = dict(postgres_executors)
        else:
            raise RuntimeError(_PROTOTYPE_CREATION_FAILURE)
        try:
            from .postgres_runtime import (
                DatabaseRoutingCompiler,
                DatabaseRoutingExecutor,
                DatabaseRoutingPolicy,
                PostgresRuntimeProfile,
                build_validated_contexts,
            )
            from .database_registry import MAX_DATABASES
        except Exception:
            raise RuntimeError(_PROTOTYPE_CREATION_FAILURE) from None
        fixed_ids = frozenset({SYNTHETIC_PROTOTYPE_DATABASE_ID, SYNTHETIC_LEGACY_DATABASE_ID})
        for _profile in pg_profiles:
            if not isinstance(_profile, PostgresRuntimeProfile):
                raise RuntimeError(_PROTOTYPE_CREATION_FAILURE)
        if len(fixed_ids) + len(pg_profiles) > int(MAX_DATABASES):
            raise RuntimeError(_PROTOTYPE_CREATION_FAILURE)
        ordered_pg_profiles = sorted(pg_profiles, key=lambda item: item.database_id)
        try:
            from ..ontology.loader import load_default_ontology

            _expected_ontology_version = int(load_default_ontology().version)
        except Exception:
            raise RuntimeError(_PROTOTYPE_CREATION_FAILURE) from None
        adapters_by_id: dict[str, Any] = {}
        executors_by_id: dict[str, Any] = {}
        try:
            from ..catalog.postgres import PostgresAdapter
            from ..harness.postgres import PostgresReadOnlyExecutor
        except Exception:
            raise RuntimeError(_PROTOTYPE_CREATION_FAILURE) from None
        for _profile in ordered_pg_profiles:
            _db_id = _profile.database_id
            if _db_id in fixed_ids or _db_id in adapters_by_id:
                raise RuntimeError(_PROTOTYPE_CREATION_FAILURE)
            _adapter = pg_adapter_overrides.get(_db_id)
            if _adapter is None:
                try:
                    _adapter = PostgresAdapter({_db_id: _profile.connection_config})
                except Exception:
                    raise RuntimeError(_PROTOTYPE_CREATION_FAILURE) from None
            _executor = pg_executor_overrides.get(_db_id)
            if _executor is None:
                try:
                    _executor = PostgresReadOnlyExecutor({_db_id: _profile.connection_config})
                except Exception:
                    raise RuntimeError(_PROTOTYPE_CREATION_FAILURE) from None
            adapters_by_id[_db_id] = _adapter
            executors_by_id[_db_id] = _executor
        for _key in (*pg_adapter_overrides, *pg_executor_overrides):
            if _key not in adapters_by_id:
                raise RuntimeError(_PROTOTYPE_CREATION_FAILURE)
        try:
            pg_contexts = build_validated_contexts(
                ordered_pg_profiles,
                adapters_by_id=adapters_by_id,
                executors_by_id=executors_by_id,
                expected_ontology_version=_expected_ontology_version,
                fixed_database_ids=fixed_ids,
                max_databases=int(MAX_DATABASES),
            )
        except Exception:
            raise RuntimeError(_PROTOTYPE_CREATION_FAILURE) from None
        tmp = tempfile.TemporaryDirectory(prefix="synth-clarify-")
        flat_path = build_flat_database(Path(tmp.name) / "flat.sqlite3", seed=DEFAULT_SEED)
        legacy_path = build_legacy_database(Path(tmp.name) / "legacy.sqlite3", seed=DEFAULT_SEED)
        database_paths = {
            SYNTHETIC_PROTOTYPE_DATABASE_ID: flat_path,
            SYNTHETIC_LEGACY_DATABASE_ID: legacy_path,
        }
        schema_inspector = SQLiteAdapter(database_paths)
        expected_fingerprints = {
            database_id: schema_inspector.inspect(database_id).schema_fingerprint
            for database_id in database_paths
        }

        def schema_is_current(database_id: str) -> bool:
            expected = expected_fingerprints.get(database_id)
            if expected is None:
                return False
            try:
                actual = schema_inspector.inspect(database_id).schema_fingerprint
            except Exception:
                return False
            return actual == expected

        if postgres_sources is not None:
            from .postgres_runtime import LivePostgresContexts

            # Phase 8-15: one live set shared by every router (the
            # clarification loader included), so a schema published from the
            # admin screen is served without a restart.
            pg_contexts = LivePostgresContexts(pg_contexts)
        loader = _PrototypeContextLoader(schema_is_current, pg_contexts)
        if ollama_model is None:
            provider = _FixedConstraintProvider()
        else:
            try:
                from ..harness.ollama import OllamaQueryModel
                from ..harness.ollama_constraint import OllamaConstraintProvider
            except Exception:
                raise RuntimeError(_PROTOTYPE_CREATION_FAILURE) from None
            if not isinstance(ollama_model, OllamaQueryModel):
                raise RuntimeError(_PROTOTYPE_CREATION_FAILURE) from None
            try:
                provider = OllamaConstraintProvider(ollama_model)
            except Exception:
                raise RuntimeError(_PROTOTYPE_CREATION_FAILURE) from None
        executor = SQLiteReadOnlyExecutor(database_paths)
        guarded_executor = _SchemaGuardedExecutor(executor, schema_is_current)
        compiler = _PrivateActivationGuardCompiler(SingleTableSQLiteCompiler())
        sql_policy = ConservativeSqlPolicy()
        coordinator = ClarificationSessionCoordinator()
        if pg_contexts or postgres_sources is not None:
            wired_compiler: Any = DatabaseRoutingCompiler(compiler, pg_contexts)
            wired_policy: Any = DatabaseRoutingPolicy(sql_policy, pg_contexts)
            wired_executor: Any = DatabaseRoutingExecutor(guarded_executor, pg_contexts)
        else:
            wired_compiler = compiler
            wired_policy = sql_policy
            wired_executor = guarded_executor
        app = create_clarification_http_app(
            coordinator,
            loader,
            provider,
            wired_executor,
            compiler=wired_compiler,
            sql_policy=wired_policy,
        )
        registry_entries = [
            {
                "database_id": SYNTHETIC_PROTOTYPE_DATABASE_ID,
                "label": SYNTHETIC_FLAT_LABEL,
                "schema": "flat",
            },
            {
                "database_id": SYNTHETIC_LEGACY_DATABASE_ID,
                "label": SYNTHETIC_LEGACY_LABEL,
                "schema": "legacy",
            },
        ]
        for _db_id in sorted(pg_contexts):
            registry_entries.append(
                {
                    "database_id": _db_id,
                    "label": pg_contexts[_db_id].label,
                    "schema": "postgres",
                }
            )
        registry = SyntheticDatabaseRegistry(registry_entries)
        schema_by_id = {
            SYNTHETIC_PROTOTYPE_DATABASE_ID: "flat",
            SYNTHETIC_LEGACY_DATABASE_ID: "legacy",
        }
        for _db_id in sorted(pg_contexts):
            schema_by_id[_db_id] = "postgres"
        register_synthetic_database_routes(
            app,
            registry=registry,
            executor=wired_executor,
            compiler=wired_compiler,
            sql_policy=wired_policy,
            schema_by_id=schema_by_id,
            schema_is_current=schema_is_current,
            postgres_contexts=pg_contexts,
        )
        if mapping_model is not None and not callable(mapping_model):
            raise RuntimeError(_PROTOTYPE_CREATION_FAILURE)
        try:
            register_sqlite_upload_routes(
                app,
                mapping_proposer=(
                    None if mapping_model is None else _upload_proposer(mapping_model)
                ),
            )
        except Exception:
            raise RuntimeError(_PROTOTYPE_CREATION_FAILURE) from None
        value_reader = postgres_value_reader
        if value_reader is None:
            reader_configs = {
                _profile.database_id: _profile.connection_config
                for _profile in ordered_pg_profiles
                if _profile.database_id not in pg_executor_overrides
            }
            if reader_configs or postgres_sources is not None:
                from ..harness.postgres_values import PostgresDistinctValueReader

                options = postgres_registration_options or {}
                value_reader = PostgresDistinctValueReader(
                    reader_configs, connector=options.get("connector")
                )
        from .value_hints import ChatValueIndexes
        from ..ontology import load_default_ontology

        value_indexes = None if value_reader is None else ChatValueIndexes(value_reader, load_default_ontology())
        postgres_service = None
        if postgres_sources is not None:
            try:
                from .postgres_registration import (
                    PostgresRegistrationService,
                    register_postgres_registration_routes,
                )
                from .registration_upload import _discover_repository_root

                options = postgres_registration_options or {}
                postgres_service = PostgresRegistrationService(
                    repository_root=options.get("repository_root") or _discover_repository_root(),
                    sources=postgres_sources,
                    live_contexts=pg_contexts,
                    reserved_ids=frozenset(fixed_ids),
                    max_databases=int(MAX_DATABASES),
                    expected_ontology_version=_expected_ontology_version,
                    value_reader=value_reader,
                    value_indexes=value_indexes,
                    mapping_proposer=None if mapping_model is None else _upload_proposer(mapping_model),
                    alias_model=mapping_model,
                    adapter_factory=options.get("adapter_factory"),
                    executor_factory=options.get("executor_factory"),
                    connector=options.get("connector"),
                )
                register_postgres_registration_routes(app, postgres_service)
            except Exception:
                raise RuntimeError(_PROTOTYPE_CREATION_FAILURE) from None
        try:
            register_mapping_review_routes(app, postgres_service=postgres_service)
            register_admin_test_query_routes(app)
        except Exception:
            raise RuntimeError(_PROTOTYPE_CREATION_FAILURE) from None
        try:
            app.state.custom_chat_model_status = custom_chat_model_status if chat_models is not None else {}
            # Phase 7-13: one-shot QueryIR chat over PostgreSQL profiles only;
            # without an Ollama model the route answers 503.
            register_chat_query_routes(
                app,
                pg_contexts=pg_contexts,
                model=ollama_model if chat_choices is None else None,
                models=live_chat_models if chat_choices is not None and live_chat_models is not None else chat_choices,
                value_reader=value_reader,
                value_indexes=value_indexes,
                show_sql=show_chat_sql is True,
            )
            if chat_choices and live_chat_models is not None and chat_model_repository_root is not None and chat_model_base_url is not None:
                from .custom_chat_models import register_custom_chat_model_routes

                register_custom_chat_model_routes(
                    app,
                    repository_root=chat_model_repository_root,
                    registry=live_chat_models,
                    base_url=chat_model_base_url,
                    timeout=chat_model_timeout,
                )
            else:
                from .custom_chat_models import register_custom_chat_models_unavailable

                register_custom_chat_models_unavailable(app)
        except Exception:
            raise RuntimeError(_PROTOTYPE_CREATION_FAILURE) from None
    except Exception:
        if tmp is not None:
            try:
                tmp.cleanup()
            except Exception:
                pass
        raise RuntimeError(_PROTOTYPE_CREATION_FAILURE) from None

    lock = threading.Lock()
    state = {"status": "active"}

    def _cleanup() -> None:
        with lock:
            if state["status"] != "active":
                return
            state["status"] = "cleaning"
        try:
            tmp.cleanup()
        except Exception:
            with lock:
                state["status"] = "active"
            return
        with lock:
            state["status"] = "cleaned"
        try:
            atexit.unregister(_cleanup)
        except Exception:
            pass

    try:
        app.router.on_shutdown.append(_cleanup)
    except Exception:
        try:
            tmp.cleanup()
        except Exception:
            pass
        raise RuntimeError(_PROTOTYPE_CREATION_FAILURE) from None
    try:
        atexit.register(_cleanup)
    except Exception:
        pass

    try:
        app.state.adaptive_app_version = SYNTHETIC_CLARIFICATION_PROTOTYPE_VERSION
        app.state.adaptive_app_database_id = SYNTHETIC_PROTOTYPE_DATABASE_ID
        app.state.adaptive_app_legacy_database_id = SYNTHETIC_LEGACY_DATABASE_ID
        app.state.adaptive_app_registry = registry
        app.state.adaptive_app_schema_by_id = dict(schema_by_id)
        app.state.adaptive_app_loader = loader
        app.state.adaptive_app_provider = provider
        app.state.adaptive_app_provider_kind = (
            "deterministic" if ollama_model is None else "ollama"
        )
        app.state.adaptive_app_executor = executor
        app.state.adaptive_app_guarded_executor = guarded_executor
        app.state.adaptive_app_schema_fingerprints = dict(expected_fingerprints)
        app.state.adaptive_app_compiler = compiler
        app.state.adaptive_app_policy = sql_policy
        app.state.adaptive_app_coordinator = coordinator
        app.state.adaptive_app_tmpdir = tmp.name
        app.state.adaptive_app_cleanup = _cleanup
        app.state.synthetic_postgres_database_ids = tuple(sorted(pg_contexts))
        app.state.synthetic_postgres_contexts = pg_contexts
        app.state.adaptive_app_chat_sql_shown = show_chat_sql is True
        app.state.adaptive_app_mapping_proposer_kind = (
            "rules" if mapping_model is None else "ai"
        )
    except Exception:
        try:
            _cleanup()
        except Exception:
            pass
        raise RuntimeError(_PROTOTYPE_CREATION_FAILURE) from None
    return app


class _StrictArgumentParser(argparse.ArgumentParser):
    """Report every parse failure with one fixed line; never echo arguments."""

    def error(self, message: str) -> Any:  # noqa: ARG002
        import sys as _sys

        print(_CLI_CONFIG_FAILURE, file=_sys.stderr, flush=True)
        raise SystemExit(2)


class _StoreOnce(argparse.Action):
    """Store a raw string once; a repeated option is a configuration error."""

    def __call__(self, parser, namespace, values, option_string=None):  # noqa: ANN001
        if getattr(namespace, self.dest, None) is not None:
            parser.error(_CLI_CONFIG_FAILURE)
        setattr(namespace, self.dest, values)


def _build_parser() -> argparse.ArgumentParser:
    parser = _StrictArgumentParser(
        prog="backend.adaptive.api.app_factory",
        description="Synthetic clarification prototype (local loopback only).",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--port",
        type=_parse_port,
        default=_DEFAULT_PORT,
        help=f"Local port ({_MIN_PORT}-{_MAX_PORT}).",
    )
    parser.add_argument(
        "--repository-root",
        type=_parse_repository_root,
        default=str(Path.cwd()),
        help="Repository root for private file validation.",
    )
    parser.add_argument(
        "--postgres-profile-id",
        type=_parse_postgres_profile_id,
        action="append",
        default=None,
        dest="postgres_profile_id",
        metavar="DATABASE_ID",
        help="Private PostgreSQL profile ID (repeatable).",
    )
    parser.add_argument(
        "--ollama-model-tag",
        action=_StoreOnce,
        default=None,
        metavar="TAG",
        help="Enable the loopback Ollama QueryIR provider with this model tag.",
    )
    parser.add_argument(
        "--ollama-base-url",
        action=_StoreOnce,
        default=None,
        metavar="URL",
        help="Loopback Ollama URL: http://127.0.0.1:PORT or http://[::1]:PORT.",
    )
    parser.add_argument(
        "--ollama-timeout",
        action=_StoreOnce,
        default=None,
        metavar="SECONDS",
        help="Ollama request timeout in seconds (1-120).",
    )
    parser.add_argument(
        "--show-sql",
        action="store_true",
        help="Development option: show the executed SQL in chat answers (Phase 8-14).",
    )
    parser.add_argument(
        "--mapping-model-tag",
        action=_StoreOnce,
        default=None,
        metavar="TAG",
        help="Order upload mapping candidates with this loopback Ollama model (Phase 8-12).",
    )
    parser.add_argument(
        "--ollama-request-format",
        action=_StoreOnce,
        default=None,
        metavar="FORMAT",
        help="Required with Ollama: model-template or alpaca-raw-no-system-v1.",
    )
    return parser


def _parse_port(value: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(_CLI_CONFIG_FAILURE) from None
    if not (_MIN_PORT <= port <= _MAX_PORT):
        raise argparse.ArgumentTypeError(_CLI_CONFIG_FAILURE)
    return port


def _parse_repository_root(value: str) -> str:
    try:
        if not isinstance(value, str) or not value:
            raise argparse.ArgumentTypeError(_CLI_CONFIG_FAILURE)
        return value
    except argparse.ArgumentTypeError:
        raise
    except Exception:
        raise argparse.ArgumentTypeError(_CLI_CONFIG_FAILURE) from None


def _parse_postgres_profile_id(value: str) -> str:
    try:
        import re as _re

        if not isinstance(value, str):
            raise argparse.ArgumentTypeError(_CLI_CONFIG_FAILURE)
        if _re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", value) is None:
            raise argparse.ArgumentTypeError(_CLI_CONFIG_FAILURE)
        return value
    except argparse.ArgumentTypeError:
        raise
    except Exception:
        raise argparse.ArgumentTypeError(_CLI_CONFIG_FAILURE) from None


def main(argv: list[str] | None = None) -> int:
    import sys as _sys

    parser = _build_parser()
    args = parser.parse_args(argv)
    ollama_values = (
        getattr(args, "ollama_model_tag", None),
        getattr(args, "ollama_base_url", None),
        getattr(args, "ollama_timeout", None),
        getattr(args, "ollama_request_format", None),
    )
    ollama_model: Any = None
    # The base URL alone may serve only the mapping model (Phase 8-12).
    chat_values = ollama_values if getattr(args, "mapping_model_tag", None) is None else (
        ollama_values[0], ollama_values[2], ollama_values[3]
    )
    if any(value is not None for value in chat_values):
        # Explicit opt-in only; builds the adapter without contacting Ollama.
        try:
            from .ollama_startup import (
                build_startup_ollama_model,
                parse_ollama_startup_config,
            )

            # Enabling Ollama requires both the tag and an explicit request format.
            if ollama_values[0] is None or ollama_values[3] is None:
                raise ValueError(_CLI_CONFIG_FAILURE)
            ollama_model = build_startup_ollama_model(
                parse_ollama_startup_config(*ollama_values)
            )
        except Exception:
            print(_CLI_CONFIG_FAILURE, file=_sys.stderr, flush=True)
            return 2
    mapping_model: Any = None
    mapping_tag = getattr(args, "mapping_model_tag", None)
    if mapping_tag is not None:
        try:
            from ..mapping.ai_proposal import DEFAULT_MAPPING_BASE_URL, OllamaJsonModel

            mapping_model = OllamaJsonModel(
                mapping_tag,
                base_url=getattr(args, "ollama_base_url", None) or DEFAULT_MAPPING_BASE_URL,
            )
        except Exception:
            print(_CLI_CONFIG_FAILURE, file=_sys.stderr, flush=True)
            return 2
    try:
        import uvicorn
    except Exception:
        print(_PROTOTYPE_CREATION_FAILURE, flush=True)
        return 2
    profile_ids = getattr(args, "postgres_profile_id", None)
    if profile_ids:
        try:
            from .postgres_startup_profile import load_postgres_startup_profiles
        except Exception:
            print(_CLI_CONFIG_FAILURE, file=_sys.stderr, flush=True)
            return 2
        try:
            pg_profiles = load_postgres_startup_profiles(
                repository_root=getattr(args, "repository_root"),
                database_ids=list(profile_ids),
            )
        except Exception:
            print(_CLI_CONFIG_FAILURE, file=_sys.stderr, flush=True)
            return 2
        factory_kwargs: dict[str, Any] = {"postgres_profiles": pg_profiles}
        if ollama_model is not None:
            factory_kwargs["ollama_model"] = ollama_model
        if mapping_model is not None:
            factory_kwargs["mapping_model"] = mapping_model
        if getattr(args, "show_sql", False):
            factory_kwargs["show_chat_sql"] = True
        try:
            local_app = create_synthetic_clarification_prototype(**factory_kwargs)
        except Exception:
            print(_CLI_CONFIG_FAILURE, file=_sys.stderr, flush=True)
            return 2
    elif ollama_model is not None or mapping_model is not None or getattr(args, "show_sql", False):
        model_kwargs: dict[str, Any] = {}
        if getattr(args, "show_sql", False):
            model_kwargs["show_chat_sql"] = True
        if ollama_model is not None:
            model_kwargs["ollama_model"] = ollama_model
        if mapping_model is not None:
            model_kwargs["mapping_model"] = mapping_model
        try:
            local_app = create_synthetic_clarification_prototype(**model_kwargs)
        except Exception:
            print(_CLI_CONFIG_FAILURE, file=_sys.stderr, flush=True)
            return 2
    else:
        try:
            local_app = create_synthetic_clarification_prototype()
        except Exception:
            print(_PROTOTYPE_CREATION_FAILURE, flush=True)
            return 2
    print(f"synthetic clarification prototype ready ({SYNTHETIC_CLARIFICATION_PROTOTYPE_VERSION})", flush=True)
    try:
        uvicorn.run(
            local_app,
            host=_FIXED_HOST,
            port=args.port,
            reload=False,
            access_log=False,
        )
    except Exception:
        print(_PROTOTYPE_CREATION_FAILURE, flush=True)
        return 2
    finally:
        cleanup = getattr(local_app.state, "adaptive_app_cleanup", None)
        if callable(cleanup):
            cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


app = create_synthetic_clarification_prototype()
