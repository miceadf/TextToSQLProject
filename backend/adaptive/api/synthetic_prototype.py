"""Synthetic clarification ASGI prototype (synthetic-only, local loopback).

Assembles the Phase 6-12 ``create_clarification_http_app()`` factory with
one fully synthetic dependency set so a teammate can reproduce
start -> option select -> submit -> read-only result over local HTTP.

Not a deployment service. No legacy mount, real data, model server,
PostgreSQL, UI, external network dependency, or environment-file access
happens here; the CLI serves only on local loopback.
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
from .clarification_http import create_clarification_http_app
from .registration_upload import register_sqlite_upload_routes
from .review_publish import register_mapping_review_routes
from .synthetic_registry import (
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

    def __init__(self, schema_is_current: Any) -> None:
        self._schema_is_current = schema_is_current

    def load(self, database_id: str) -> tuple[Any, Any, Any]:
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


def create_synthetic_clarification_prototype() -> FastAPI:
    """Assemble the synthetic clarification prototype app."""
    tmp: Any = None
    try:
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

        loader = _PrototypeContextLoader(schema_is_current)
        provider = _FixedConstraintProvider()
        executor = SQLiteReadOnlyExecutor(database_paths)
        guarded_executor = _SchemaGuardedExecutor(executor, schema_is_current)
        compiler = SingleTableSQLiteCompiler()
        sql_policy = ConservativeSqlPolicy()
        coordinator = ClarificationSessionCoordinator()
        app = create_clarification_http_app(
            coordinator,
            loader,
            provider,
            guarded_executor,
            compiler=compiler,
            sql_policy=sql_policy,
        )
        registry = SyntheticDatabaseRegistry(
            [
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
        )
        schema_by_id = {
            SYNTHETIC_PROTOTYPE_DATABASE_ID: "flat",
            SYNTHETIC_LEGACY_DATABASE_ID: "legacy",
        }
        register_synthetic_database_routes(
            app,
            registry=registry,
            executor=guarded_executor,
            compiler=compiler,
            sql_policy=sql_policy,
            schema_by_id=schema_by_id,
            schema_is_current=schema_is_current,
        )
        try:
            register_sqlite_upload_routes(app)
        except Exception:
            raise RuntimeError(_PROTOTYPE_CREATION_FAILURE) from None
        try:
            register_mapping_review_routes(app)
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
        app.state.synthetic_prototype_version = SYNTHETIC_CLARIFICATION_PROTOTYPE_VERSION
        app.state.synthetic_prototype_database_id = SYNTHETIC_PROTOTYPE_DATABASE_ID
        app.state.synthetic_prototype_legacy_database_id = SYNTHETIC_LEGACY_DATABASE_ID
        app.state.synthetic_prototype_registry = registry
        app.state.synthetic_prototype_schema_by_id = dict(schema_by_id)
        app.state.synthetic_prototype_loader = loader
        app.state.synthetic_prototype_provider = provider
        app.state.synthetic_prototype_executor = executor
        app.state.synthetic_prototype_guarded_executor = guarded_executor
        app.state.synthetic_prototype_schema_fingerprints = dict(expected_fingerprints)
        app.state.synthetic_prototype_compiler = compiler
        app.state.synthetic_prototype_policy = sql_policy
        app.state.synthetic_prototype_coordinator = coordinator
        app.state.synthetic_prototype_tmpdir = tmp.name
        app.state.synthetic_prototype_cleanup = _cleanup
    except Exception:
        try:
            _cleanup()
        except Exception:
            pass
        raise RuntimeError(_PROTOTYPE_CREATION_FAILURE) from None
    return app


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backend.adaptive.api.synthetic_prototype",
        description="Synthetic clarification prototype (local loopback only).",
    )
    parser.add_argument(
        "--port",
        type=_parse_port,
        default=_DEFAULT_PORT,
        help=f"Local port ({_MIN_PORT}-{_MAX_PORT}).",
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


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        import uvicorn
    except Exception:
        print(_PROTOTYPE_CREATION_FAILURE, flush=True)
        return 2
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
        cleanup = getattr(local_app.state, "synthetic_prototype_cleanup", None)
        if callable(cleanup):
            cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


app = create_synthetic_clarification_prototype()
