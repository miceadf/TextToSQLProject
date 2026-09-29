"""Register the legacy course view as a partial QueryIR database (Phase 7-12).

The legacy chatbot sends model-written SQL to one PostgreSQL view. This
module maps that same view onto the canonical fields so the QueryIR path
can answer the same questions for comparison. The mapping is developer
authored for the team-built legacy DB (user statement 2026-09-29) and is
not an L2 review of a real school database.

Modes (all opt-in, loopback CLI only):

- ``--check``: strict-load the private profile files (no DB connection).
- ``--write --live``: inspect the DB with views included, verify the view
  columns, and write ``profile.json``/``mapping.json`` under the Git-ignored
  ``.schema-adaptive/postgres-profiles/legacy-view/`` directory.
- ``--smoke --live``: run fixed QueryIRs through the validated runtime
  (QueryIR validation, compiler, AST policy, read-only executor).

Output is fixed codes and counts only: no DSN, SQL, physical identifier,
or row value is printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..mapping.transforms import FieldTransform

DATABASE_ID = "legacy-view"
LABEL = "Legacy DB (QueryIR)"
DSN_ENV = "SCHEMA_ADAPTIVE_POSTGRES_DSN_LEGACY_VIEW"
SCHEMA = "public"
VIEW = "v_course_info"
PROFILE_DIR = Path(".schema-adaptive/postgres-profiles") / DATABASE_ID
PROFILE_VERSION = "postgres-runtime-profile-v2"
REVIEWER = "developer"
REASON = "Phase 7-12 developer mapping of the team-built legacy view (not an L2 review)"

# canonical field -> (view column, expected declared type prefix)
VIEW_FIELDS: dict[str, tuple[str, str]] = {
    "Course.code": ("subject_code", "character varying"),
    "Course.name": ("subject_name", "character varying"),
    "Course.credits": ("credit_hours", "character varying"),
    "Course.category": ("category", "character varying"),
    "Department.name": ("dept_name", "character varying"),
    "Instructor.name": ("professor", "character varying"),
    "CourseOffering.section": ("section", "character varying"),
    "CourseOffering.capacity": ("capacity", "integer"),
    "CourseOffering.enrolled": ("enrolled", "integer"),
    "Schedule.day": ("day_of_week", "character varying"),
    "Schedule.start_time": ("start_time", "time"),
    "Schedule.end_time": ("end_time", "time"),
}
FIELD_TRANSFORMS: dict[str, FieldTransform] = {
    "Course.credits": FieldTransform.SLASH_FIRST_NUMERIC,
    "Schedule.start_time": FieldTransform.TIME_TO_HHMM,
    "Schedule.end_time": FieldTransform.TIME_TO_HHMM,
    # Phase 7-16: the view stores one-character weekdays ("월").
    "Schedule.day": FieldTransform.KOREAN_WEEKDAY,
}
# The view has no department code, and its ``course_year`` is the target
# grade (1-5), not the offering year; there is no semester column.
UNSUPPORTED_FIELDS = ("CourseOffering.semester", "CourseOffering.year", "Department.code")

_HHMM = re.compile(r"^\d{2}:\d{2}$")

# Fixed smoke queries: (id, QueryIR, expected failure code or None).
SMOKE_QUERIES: tuple[tuple[str, dict[str, Any], str | None], ...] = (
    (
        "list",
        {
            "entity": "CourseOffering",
            "select": ["Course.name", "CourseOffering.section", "Instructor.name"],
            "filters": [],
            "limit": 200,
        },
        None,
    ),
    (
        "credits_equals_3",
        {
            "entity": "Course",
            "select": ["Course.name", "Course.credits"],
            "filters": [{"field": "Course.credits", "operator": "equals", "value": 3}],
            "limit": 200,
        },
        None,
    ),
    (
        "afternoon",
        {
            "entity": "Schedule",
            "select": ["Course.name", "Schedule.day", "Schedule.start_time", "Schedule.end_time"],
            "filters": [
                {"field": "Schedule.start_time", "operator": "greater_than_or_equal", "value": "12:00"}
            ],
            "limit": 200,
        },
        None,
    ),
    (
        "category_contains",
        {
            "entity": "Course",
            "select": ["Course.name", "Course.category"],
            "filters": [{"field": "Course.category", "operator": "contains", "value": "전공"}],
            "limit": 200,
        },
        None,
    ),
    (
        "unsupported_year",
        {
            "entity": "CourseOffering",
            "select": ["Course.name", "CourseOffering.year"],
            "filters": [],
            "limit": 10,
        },
        "MAPPING_MISSING",
    ),
)


class LegacyViewProfileError(Exception):
    """Fixed-code failure; the code is the only public detail."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_documents(schema_fingerprint: str, published_at: datetime) -> tuple[bytes, bytes]:
    """Serialize ``mapping.json`` and the ``profile.json`` that pins its hash."""
    from ..mapping.contracts import PublishedMappingProfile

    mapping = PublishedMappingProfile(
        database_id=DATABASE_ID,
        version=1,
        schema_fingerprint=schema_fingerprint,
        ontology_version=1,
        published_by=REVIEWER,
        published_at=published_at,
        fields={
            semantic: {"table": VIEW, "column": column, "reviewer": REVIEWER, "reason": REASON}
            for semantic, (column, _) in VIEW_FIELDS.items()
        },
    )
    mapping_bytes = (
        json.dumps(mapping.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    profile = {
        "version": PROFILE_VERSION,
        "database_id": DATABASE_ID,
        "label": LABEL,
        "schema": SCHEMA,
        "dsn_env": DSN_ENV,
        "mapping_sha256": hashlib.sha256(mapping_bytes).hexdigest(),
        "include_views": True,
        "field_transforms": {field: transform.value for field, transform in FIELD_TRANSFORMS.items()},
        "unsupported_fields": sorted(UNSUPPORTED_FIELDS),
    }
    profile_bytes = (
        json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return profile_bytes, mapping_bytes


def verify_view_catalog(catalog: Any) -> None:
    """Require the view and every mapped column with its expected type."""
    tables = {getattr(table, "name", None): table for table in getattr(catalog, "tables", [])}
    view = tables.get(VIEW)
    if view is None:
        raise LegacyViewProfileError("VIEW_MISSING")
    declared = {column.name: str(column.declared_type or "").lower() for column in view.columns}
    for column, expected in VIEW_FIELDS.values():
        actual = declared.get(column)
        if actual is None or not actual.startswith(expected):
            raise LegacyViewProfileError("VIEW_COLUMN_MISMATCH")


def _existing_pair_matches(directory: Path, fingerprint: str) -> bool:
    """True only if both files are exactly what this fingerprint would produce.

    Rebuilding with the stored ``published_at`` and comparing bytes also
    checks the pinned mapping hash, so a half-replaced pair never counts.
    """
    try:
        mapping_raw = (directory / "mapping.json").read_bytes()
        profile_raw = (directory / "profile.json").read_bytes()
        published_at = datetime.fromisoformat(
            str(json.loads(mapping_raw.decode("utf-8"))["published_at"]).replace("Z", "+00:00")
        )
        expected_profile, expected_mapping = build_documents(fingerprint, published_at)
    except Exception:
        return False
    return mapping_raw == expected_mapping and profile_raw == expected_profile


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


def write_profile(
    repository_root: Path,
    catalog: Any,
    *,
    replace: bool,
    now: datetime | None = None,
) -> str:
    """Write the private profile for ``catalog``; return a fixed status."""
    verify_view_catalog(catalog)
    if getattr(catalog, "database_id", None) != DATABASE_ID:
        raise LegacyViewProfileError("INSPECTION_FAILED")
    fingerprint = str(getattr(catalog, "schema_fingerprint", ""))
    directory = repository_root / PROFILE_DIR
    mapping_path = directory / "mapping.json"
    profile_path = directory / "profile.json"
    if mapping_path.exists() or profile_path.exists():
        if _existing_pair_matches(directory, fingerprint):
            return "UNCHANGED"
        if not replace:
            raise LegacyViewProfileError("PROFILE_EXISTS")
    profile_bytes, mapping_bytes = build_documents(
        fingerprint, now or datetime.now(timezone.utc).replace(microsecond=0)
    )
    # The profile pins the mapping hash, so a crash between the two writes
    # leaves a pair the strict loader rejects rather than a mixed state.
    _write_atomic(mapping_path, mapping_bytes)
    _write_atomic(profile_path, profile_bytes)
    return "WRITTEN"


class _NoSqlite:
    """The smoke routes only the legacy profile; any other ID fails closed."""

    def compile(self, *_args: Any, **_kwargs: Any) -> Any:
        raise LegacyViewProfileError("ROUTING_FAILED")

    def validate(self, *_args: Any, **_kwargs: Any) -> None:
        raise LegacyViewProfileError("ROUTING_FAILED")

    def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        raise LegacyViewProfileError("ROUTING_FAILED")


def _runtime_ontology() -> Any:
    from ..harness.contracts import Ontology, OntologyField
    from ..ontology.loader import load_default_ontology

    domain = load_default_ontology()
    return Ontology(
        entities={entity.name for entity in domain.entities},
        fields={
            name: OntologyField(
                name=name, data_type=field.data_type, allowed_operators=field.allowed_operators
            )
            for name, field in domain.fields.items()
        },
    )


def _check_rows(query_id: str, rows: list[dict[str, Any]]) -> bool:
    """Shape checks on returned values (never printed)."""
    if query_id == "credits_equals_3":
        return all(row.get("Course.credits") == 3 for row in rows)
    if query_id == "afternoon":
        for row in rows:
            start = row.get("Schedule.start_time")
            end = row.get("Schedule.end_time")
            if not isinstance(start, str) or _HHMM.fullmatch(start) is None or start < "12:00":
                return False
            if end is not None and (not isinstance(end, str) or _HHMM.fullmatch(end) is None):
                return False
        return True
    if query_id == "category_contains":
        return all("전공" in str(row.get("Course.category") or "") for row in rows)
    return True


def run_smoke(context: Any) -> list[dict[str, Any]]:
    """Run the fixed QueryIRs through the validated runtime; counts only."""
    from ..harness.contracts import HarnessRequest, HarnessStatus
    from ..harness.engine import ExecutionHarness
    from ..harness.model import FakeModel
    from .postgres_runtime import (
        DatabaseRoutingCompiler,
        DatabaseRoutingExecutor,
        DatabaseRoutingPolicy,
    )

    contexts = {DATABASE_ID: context}
    none = _NoSqlite()
    harness = ExecutionHarness(
        model=FakeModel({query_id: query_ir for query_id, query_ir, _ in SMOKE_QUERIES}),
        compiler=DatabaseRoutingCompiler(none, contexts),
        sql_policy=DatabaseRoutingPolicy(none, contexts),
        executor=DatabaseRoutingExecutor(none, contexts),
    )
    ontology = _runtime_ontology()
    results: list[dict[str, Any]] = []
    for query_id, _query_ir, expected_failure in SMOKE_QUERIES:
        result = harness.run(
            HarnessRequest(
                request_id=f"legacy-view-smoke:{query_id}",
                database_id=DATABASE_ID,
                question=query_id,
            ),
            ontology,
            context.harness_mapping(),
        )
        if result.status is HarnessStatus.SUCCEEDED:
            rows = [row for row in result.rows if isinstance(row, dict)]
            checks_ok = len(rows) == len(result.rows) and _check_rows(query_id, rows)
            passed = expected_failure is None and checks_ok
            results.append(
                {"query_id": query_id, "status": "OK" if passed else "UNEXPECTED",
                 "row_count": len(result.rows), "checks": checks_ok}
            )
        else:
            code = result.error.code.value if result.error is not None else "HARNESS_FAILURE"
            passed = code == expected_failure
            results.append(
                {"query_id": query_id, "status": "OK" if passed else "UNEXPECTED",
                 "failure_code": code}
            )
    return results


class _StrictArgumentParser(argparse.ArgumentParser):
    """Report parse failures with one fixed code; never echo arguments."""

    def error(self, message: str) -> Any:  # noqa: ARG002
        _emit({"status": "FAILED", "code": "INVALID_ARGUMENTS"})
        raise SystemExit(2)


def _build_parser() -> argparse.ArgumentParser:
    parser = _StrictArgumentParser(
        prog="backend.adaptive.api.legacy_view_profile",
        description="Register the legacy course view as a partial QueryIR database.",
        allow_abbrev=False,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Strict-load the private profile.")
    mode.add_argument("--write", action="store_true", help="Inspect and write the profile.")
    mode.add_argument("--smoke", action="store_true", help="Run fixed QueryIRs.")
    parser.add_argument("--live", action="store_true", help="Allow a DB connection.")
    parser.add_argument("--replace", action="store_true", help="Replace a different profile.")
    parser.add_argument("--repository-root", default=str(Path.cwd()))
    return parser


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def _load_profile(repository_root: str) -> Any:
    from .postgres_startup_profile import load_postgres_startup_profiles

    try:
        return load_postgres_startup_profiles(
            repository_root=repository_root, database_ids=[DATABASE_ID]
        )[0]
    except Exception:
        raise LegacyViewProfileError("PROFILE_INVALID") from None


def main(argv: list[str] | None = None, *, environ: dict[str, str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    env = os.environ if environ is None else environ
    try:
        if args.check:
            if DSN_ENV not in env:
                raise LegacyViewProfileError("ENV_MISSING")
            _load_profile(args.repository_root)
            _emit({"mode": "check", "status": "OK"})
            return 0
        if not args.live:
            raise LegacyViewProfileError("LIVE_FLAG_REQUIRED")
        dsn = env.get(DSN_ENV)
        if not isinstance(dsn, str) or not dsn.strip():
            raise LegacyViewProfileError("ENV_MISSING")
        if args.write:
            from ..catalog.postgres import PostgresAdapter, PostgresConnectionConfig

            try:
                catalog = PostgresAdapter(
                    {DATABASE_ID: PostgresConnectionConfig(dsn, schema=SCHEMA, include_views=True)}
                ).inspect(DATABASE_ID)
            except Exception:
                raise LegacyViewProfileError("INSPECTION_FAILED") from None
            status = write_profile(Path(args.repository_root), catalog, replace=args.replace)
            _emit({"mode": "write", "status": status})
            return 0
        from ..catalog.postgres import PostgresAdapter
        from ..harness.postgres import PostgresReadOnlyExecutor
        from .postgres_runtime import build_validated_contexts

        profile = _load_profile(args.repository_root)
        try:
            contexts = build_validated_contexts(
                [profile],
                adapters_by_id={DATABASE_ID: PostgresAdapter({DATABASE_ID: profile.connection_config})},
                executors_by_id={
                    DATABASE_ID: PostgresReadOnlyExecutor({DATABASE_ID: profile.connection_config})
                },
                expected_ontology_version=1,
            )
        except Exception:
            raise LegacyViewProfileError("RUNTIME_INVALID") from None
        results = run_smoke(contexts[DATABASE_ID])
        ok = all(item["status"] == "OK" for item in results)
        _emit({
            "mode": "smoke",
            "status": "OK" if ok else "UNEXPECTED",
            "unsupported_fields": list(contexts[DATABASE_ID].unsupported_fields()),
            "results": results,
        })
        return 0 if ok else 2
    except LegacyViewProfileError as exc:
        _emit({"status": "FAILED", "code": exc.code})
        return 2
    except Exception:
        _emit({"status": "FAILED", "code": "INTERNAL_ERROR"})
        return 2


if __name__ == "__main__":
    sys.exit(main())
