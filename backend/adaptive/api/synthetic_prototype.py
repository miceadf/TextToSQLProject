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

from ..fixtures.flat import build_flat_database
from ..fixtures.seed import DEFAULT_SEED
from ..harness.ambiguity_detector import AMBIGUITY_DETECTOR_VERSION
from ..harness.clarification_session import ClarificationSessionCoordinator
from ..harness.compiler import SingleTableSQLiteCompiler
from ..harness.contracts import DataType, FieldMapping, MappingProfile, MappingStatus, Ontology, OntologyField, Operator
from ..harness.executor import SQLiteReadOnlyExecutor
from ..harness.policy import ConservativeSqlPolicy
from .clarification_http import create_clarification_http_app


SYNTHETIC_CLARIFICATION_PROTOTYPE_VERSION = "synthetic-clarification-prototype-v1"
SYNTHETIC_PROTOTYPE_DATABASE_ID = "synth-clarification-prototype"

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
    "create_synthetic_clarification_prototype",
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


class _PrototypeContextLoader:
    """Fixed-ID loader returning a defensive copy per call."""

    def load(self, database_id: str) -> tuple[Any, Any, Any]:
        if database_id != SYNTHETIC_PROTOTYPE_DATABASE_ID:
            raise RuntimeError("synthetic context unavailable")
        return (
            copy.deepcopy(_build_ontology()),
            copy.deepcopy(_build_mapping()),
            copy.deepcopy(_build_rules()),
        )


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
        db_path = build_flat_database(Path(tmp.name) / "prototype.sqlite3", seed=DEFAULT_SEED)
        loader = _PrototypeContextLoader()
        provider = _FixedConstraintProvider()
        executor = SQLiteReadOnlyExecutor({SYNTHETIC_PROTOTYPE_DATABASE_ID: db_path})
        compiler = SingleTableSQLiteCompiler()
        sql_policy = ConservativeSqlPolicy()
        coordinator = ClarificationSessionCoordinator()
        app = create_clarification_http_app(
            coordinator,
            loader,
            provider,
            executor,
            compiler=compiler,
            sql_policy=sql_policy,
        )
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
        app.state.synthetic_prototype_loader = loader
        app.state.synthetic_prototype_provider = provider
        app.state.synthetic_prototype_executor = executor
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
