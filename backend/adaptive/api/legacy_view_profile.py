"""Register the legacy course view as a partial QueryIR database (Phase 7-12).

The legacy chatbot sends model-written SQL to one PostgreSQL view. This
module maps that same view onto the canonical fields so the QueryIR path
can answer the same questions for comparison. The mapping is developer
authored for the team-built legacy DB (user statement 2026-09-29) and is
not an L2 review of a real school database.

Phase 8-06 declares a reviewed value dictionary for the course category on
both targets (profile v4); registration checks that every stored value
exists in the column.

Phase 7-17 adds a second target: the same canonical fields mapped onto the
view's base tables, with the offering table as the declared row anchor so
every JOIN is a LEFT JOIN from it (profile v3). ``--target`` selects the
target; the default stays the view.

Modes (all opt-in, loopback CLI only):

- ``--check``: strict-load the private profile files (no DB connection).
- ``--write --live``: inspect the DB (views included for the view target),
  verify every mapped column, and write ``profile.json``/``mapping.json``
  under the Git-ignored ``.schema-adaptive/postgres-profiles/<id>/``.
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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..mapping.transforms import FieldTransform
from ..mapping.values import ValueDictionary, ValueEntry

DATABASE_ID = "legacy-view"
LABEL = "Legacy DB (QueryIR)"
DSN_ENV = "SCHEMA_ADAPTIVE_POSTGRES_DSN_LEGACY_VIEW"
SCHEMA = "public"
VIEW = "v_course_info"
PROFILE_DIR = Path(".schema-adaptive/postgres-profiles") / DATABASE_ID
PROFILE_VERSION = "postgres-runtime-profile-v4"
REVIEWER = "developer"
REASON = "Phase 7-12 developer mapping of the team-built legacy view (not an L2 review)"

BASE_DATABASE_ID = "legacy-base"
BASE_LABEL = "Legacy DB tables (QueryIR)"
BASE_DSN_ENV = "SCHEMA_ADAPTIVE_POSTGRES_DSN_LEGACY_BASE"
BASE_PROFILE_DIR = Path(".schema-adaptive/postgres-profiles") / BASE_DATABASE_ID
BASE_PROFILE_VERSION = "postgres-runtime-profile-v4"
BASE_REASON = (
    "Phase 7-17 developer mapping of the team-built legacy base tables (not an L2 review)"
)
# One result row per offering, like the view: the view's FROM table.
BASE_ANCHOR_TABLE = "cnu_courses"
# Phase 8-18: ontology version 2 adds Course.target_grade.
ONTOLOGY_VERSION = 2

# canonical field -> (view column, expected declared type prefix)
VIEW_FIELDS: dict[str, tuple[str, str]] = {
    "Course.code": ("subject_code", "character varying"),
    "Course.name": ("subject_name", "character varying"),
    "Course.credits": ("credit_hours", "character varying"),
    "Course.category": ("category", "character varying"),
    # Phase 8-18: the legacy screen shows ``target_year`` as 대상학년.
    "Course.target_grade": ("target_year", "integer"),
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
# The view has no department code, and its ``course_year`` is a grade
# (1-5), not the offering year; there is no semester column. The target
# grade comes from ``target_year`` (Phase 8-18).
UNSUPPORTED_FIELDS = ("CourseOffering.semester", "CourseOffering.year", "Department.code")

# Phase 8-06: the category values of the team-built legacy DB, with the
# spellings users and the model write without parentheses. Labels are the
# stored values themselves; values outside the list are still searched.
#
# Phase 8-13: the legacy system's own abbreviations (its ``COMMON_ALIASES``)
# move here as this database's data. Left out on purpose: "전기" (collides
# with ordinary words such as 전기공학) and, for departments, "인공지능"
# (a course-name word) and "컴공" (no such department in this DB).
_CATEGORY_ALIASES: dict[str, tuple[str, ...]] = {
    "전공(핵심)": ("전공핵심", "핵심전공", "전핵"),
    "전공(기초)": ("전공기초", "기초전공"),
    "전공(심화)": ("전공심화", "심화전공", "전심"),
    "교양(소양)": ("교양소양", "소양교양", "교소"),
    "교양(균형)": ("교양균형", "균형교양", "교균"),
    "교양(소양필수)": ("교양소양필수", "소양필수교양", "소양필수", "교소필"),
    "교양(기초)": ("교양기초", "기초교양", "교기"),
    "일반선택": ("일선",),
    "교직": (),
}
_DEPARTMENT_ALIASES: dict[str, tuple[str, ...]] = {
    "공과대학 컴퓨터융합학부": ("컴융", "컴융부"),
}
VALUE_DICTIONARIES: dict[str, ValueDictionary] = {
    "Course.category": ValueDictionary(
        entries=tuple(
            ValueEntry(stored=stored, label=stored, aliases=aliases)
            for stored, aliases in _CATEGORY_ALIASES.items()
        ),
        unknown="pass",
        reviewer=REVIEWER,
        reason="Phase 8-06 developer dictionary of the team-built legacy category values (not an L2 review)",
    ),
    "Department.name": ValueDictionary(
        entries=tuple(
            ValueEntry(stored=stored, label=stored, aliases=aliases)
            for stored, aliases in _DEPARTMENT_ALIASES.items()
        ),
        unknown="pass",
        reviewer=REVIEWER,
        reason="Phase 8-13 legacy abbreviations moved from the legacy keyword table (not an L2 review)",
    ),
}

# canonical field -> (base table, column, expected declared type prefix).
# Same fields, transforms and unsupported list as the view so the two
# targets differ only in how rows are joined. The department table does
# have a code column; mapping it is left for later on purpose.
BASE_FIELDS: dict[str, tuple[str, str, str]] = {
    "Course.code": ("subject", "subject_code", "character varying"),
    "Course.name": ("subject", "subject_name", "character varying"),
    "Course.credits": ("subject", "credit_hours", "character varying"),
    "Course.category": ("subject", "category", "character varying"),
    "Course.target_grade": ("subject", "target_year", "integer"),
    "Department.name": ("department", "dept_name", "character varying"),
    "Instructor.name": ("cnu_courses", "professor", "character varying"),
    "CourseOffering.section": ("cnu_courses", "section", "character varying"),
    "CourseOffering.capacity": ("cnu_courses", "capacity", "integer"),
    "CourseOffering.enrolled": ("cnu_courses", "enrolled", "integer"),
    "Schedule.day": ("course_schedule", "day_of_week", "character varying"),
    "Schedule.start_time": ("course_schedule", "start_time", "time"),
    "Schedule.end_time": ("course_schedule", "end_time", "time"),
}


@dataclass(frozen=True)
class LegacyProfileSpec:
    """One registration target over the same legacy DB."""

    target: str
    database_id: str
    label: str
    dsn_env: str
    profile_dir: Path
    profile_version: str
    reason: str
    # canonical field -> (table, column, expected declared type prefix)
    fields: dict[str, tuple[str, str, str]]
    include_views: bool
    anchor_table: str | None
    missing_code: str
    mismatch_code: str


VIEW_SPEC = LegacyProfileSpec(
    target="view",
    database_id=DATABASE_ID,
    label=LABEL,
    dsn_env=DSN_ENV,
    profile_dir=PROFILE_DIR,
    profile_version=PROFILE_VERSION,
    reason=REASON,
    fields={semantic: (VIEW, column, kind) for semantic, (column, kind) in VIEW_FIELDS.items()},
    include_views=True,
    anchor_table=None,
    missing_code="VIEW_MISSING",
    mismatch_code="VIEW_COLUMN_MISMATCH",
)
BASE_SPEC = LegacyProfileSpec(
    target="base",
    database_id=BASE_DATABASE_ID,
    label=BASE_LABEL,
    dsn_env=BASE_DSN_ENV,
    profile_dir=BASE_PROFILE_DIR,
    profile_version=BASE_PROFILE_VERSION,
    reason=BASE_REASON,
    fields=dict(BASE_FIELDS),
    include_views=False,
    anchor_table=BASE_ANCHOR_TABLE,
    missing_code="TABLE_MISSING",
    mismatch_code="COLUMN_MISMATCH",
)
SPECS = {spec.target: spec for spec in (VIEW_SPEC, BASE_SPEC)}

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
        "UNKNOWN_FIELD",
    ),
)


class LegacyViewProfileError(Exception):
    """Fixed-code failure; the code is the only public detail."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_documents(
    schema_fingerprint: str,
    published_at: datetime,
    spec: LegacyProfileSpec = VIEW_SPEC,
) -> tuple[bytes, bytes]:
    """Serialize ``mapping.json`` and the ``profile.json`` that pins its hash."""
    from ..mapping.contracts import PublishedMappingProfile

    mapping = PublishedMappingProfile(
        database_id=spec.database_id,
        version=1,
        schema_fingerprint=schema_fingerprint,
        ontology_version=ONTOLOGY_VERSION,
        published_by=REVIEWER,
        published_at=published_at,
        fields={
            semantic: {"table": table, "column": column, "reviewer": REVIEWER, "reason": spec.reason}
            for semantic, (table, column, _) in spec.fields.items()
        },
    )
    mapping_bytes = (
        json.dumps(mapping.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    profile: dict[str, Any] = {
        "version": spec.profile_version,
        "database_id": spec.database_id,
        "label": spec.label,
        "schema": SCHEMA,
        "dsn_env": spec.dsn_env,
        "mapping_sha256": hashlib.sha256(mapping_bytes).hexdigest(),
        "include_views": spec.include_views,
        "field_transforms": {field: transform.value for field, transform in FIELD_TRANSFORMS.items()},
        "unsupported_fields": sorted(UNSUPPORTED_FIELDS),
        "anchor_table": spec.anchor_table,
        "value_dictionaries": {
            field: dictionary.model_dump(mode="json")
            for field, dictionary in VALUE_DICTIONARIES.items()
        },
    }
    profile_bytes = (
        json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return profile_bytes, mapping_bytes


def verify_catalog(catalog: Any, spec: LegacyProfileSpec = VIEW_SPEC) -> None:
    """Require every mapped table and column with its expected type.

    Transformed fields use the runtime's own column-type rule, so a profile
    written here is never one the runtime would then reject (a ``time``
    prefix alone would also accept ``timestamp``).
    """
    from ..mapping.transforms import column_type_supports

    tables = {getattr(table, "name", None): table for table in getattr(catalog, "tables", [])}
    for semantic, (table_name, column, expected) in spec.fields.items():
        table = tables.get(table_name)
        if table is None:
            raise LegacyViewProfileError(spec.missing_code)
        declared = {item.name: str(item.declared_type or "").lower() for item in table.columns}
        actual = declared.get(column)
        if actual is None or not actual.startswith(expected):
            raise LegacyViewProfileError(spec.mismatch_code)
        transform = FIELD_TRANSFORMS.get(semantic)
        if transform is not None and not column_type_supports(transform, actual):
            raise LegacyViewProfileError(spec.mismatch_code)


DistinctValues = Callable[[str, str], set[str]]


def verify_stored_values(distinct_values: DistinctValues, spec: LegacyProfileSpec = VIEW_SPEC) -> None:
    """Every dictionary stored value must exist in its mapped column."""
    for semantic, dictionary in VALUE_DICTIONARIES.items():
        table, column, _ = spec.fields[semantic]
        try:
            present = distinct_values(table, column)
        except Exception:
            raise LegacyViewProfileError("INSPECTION_FAILED") from None
        if any(entry.stored not in present for entry in dictionary.entries):
            raise LegacyViewProfileError("VALUE_MISSING")


def verify_view_catalog(catalog: Any) -> None:
    """Require the view and every mapped column with its expected type."""
    verify_catalog(catalog, VIEW_SPEC)


def _existing_pair_matches(
    directory: Path, fingerprint: str, spec: LegacyProfileSpec = VIEW_SPEC
) -> bool:
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
        expected_profile, expected_mapping = build_documents(fingerprint, published_at, spec)
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
    spec: LegacyProfileSpec = VIEW_SPEC,
    distinct_values: DistinctValues | None = None,
) -> str:
    """Write the private profile for ``catalog``; return a fixed status."""
    verify_catalog(catalog, spec)
    if distinct_values is not None:
        verify_stored_values(distinct_values, spec)
    if getattr(catalog, "database_id", None) != spec.database_id:
        raise LegacyViewProfileError("INSPECTION_FAILED")
    fingerprint = str(getattr(catalog, "schema_fingerprint", ""))
    directory = repository_root / spec.profile_dir
    mapping_path = directory / "mapping.json"
    profile_path = directory / "profile.json"
    if mapping_path.exists() or profile_path.exists():
        if _existing_pair_matches(directory, fingerprint, spec):
            return "UNCHANGED"
        if not replace:
            raise LegacyViewProfileError("PROFILE_EXISTS")
    profile_bytes, mapping_bytes = build_documents(
        fingerprint, now or datetime.now(timezone.utc).replace(microsecond=0), spec
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
            for name, field in domain.active_fields.items()
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


def run_smoke(context: Any, spec: LegacyProfileSpec = VIEW_SPEC) -> list[dict[str, Any]]:
    """Run the fixed QueryIRs through the validated runtime; counts only."""
    from ..harness.contracts import HarnessRequest, HarnessStatus
    from ..harness.engine import ExecutionHarness
    from ..harness.model import FakeModel
    from .postgres_runtime import (
        DatabaseRoutingCompiler,
        DatabaseRoutingExecutor,
        DatabaseRoutingPolicy,
    )

    contexts = {spec.database_id: context}
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
                request_id=f"{spec.database_id}-smoke:{query_id}",
                database_id=spec.database_id,
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


_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_MAX_DISTINCT = 1000


def _live_distinct_values(dsn: str) -> DistinctValues:
    """Read-only distinct text values of one mapped column (bounded)."""

    def fetch(table: str, column: str) -> set[str]:
        import psycopg2  # type: ignore[import-not-found]

        if _IDENTIFIER.fullmatch(table) is None or _IDENTIFIER.fullmatch(column) is None:
            raise LegacyViewProfileError("INSPECTION_FAILED")
        connection = psycopg2.connect(
            dsn,
            connect_timeout=5,
            options="-c statement_timeout=5000 -c default_transaction_read_only=on",
        )
        try:
            connection.set_client_encoding("UTF8")
            with connection.cursor() as cursor:
                # Identifiers come from the fixed spec and match _IDENTIFIER.
                cursor.execute(
                    f'SELECT DISTINCT "{column}" FROM "{SCHEMA}"."{table}" LIMIT %s',
                    (_MAX_DISTINCT + 1,),
                )
                rows = cursor.fetchall()
        finally:
            connection.close()
        if len(rows) > _MAX_DISTINCT:
            raise LegacyViewProfileError("INSPECTION_FAILED")
        return {row[0] for row in rows if isinstance(row[0], str)}

    return fetch


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
    parser.add_argument("--target", choices=sorted(SPECS), default="view")
    parser.add_argument("--live", action="store_true", help="Allow a DB connection.")
    parser.add_argument("--replace", action="store_true", help="Replace a different profile.")
    parser.add_argument("--repository-root", default=str(Path.cwd()))
    return parser


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def _load_profile(repository_root: str, spec: LegacyProfileSpec = VIEW_SPEC) -> Any:
    from .postgres_startup_profile import load_postgres_startup_profiles

    try:
        return load_postgres_startup_profiles(
            repository_root=repository_root, database_ids=[spec.database_id]
        )[0]
    except Exception:
        raise LegacyViewProfileError("PROFILE_INVALID") from None


def main(argv: list[str] | None = None, *, environ: dict[str, str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    env = os.environ if environ is None else environ
    spec = SPECS[args.target]
    database_id = spec.database_id
    try:
        if args.check:
            if spec.dsn_env not in env:
                raise LegacyViewProfileError("ENV_MISSING")
            _load_profile(args.repository_root, spec)
            _emit({"mode": "check", "status": "OK"})
            return 0
        if not args.live:
            raise LegacyViewProfileError("LIVE_FLAG_REQUIRED")
        dsn = env.get(spec.dsn_env)
        if not isinstance(dsn, str) or not dsn.strip():
            raise LegacyViewProfileError("ENV_MISSING")
        if args.write:
            from ..catalog.postgres import PostgresAdapter, PostgresConnectionConfig

            try:
                catalog = PostgresAdapter(
                    {
                        database_id: PostgresConnectionConfig(
                            dsn, schema=SCHEMA, include_views=spec.include_views
                        )
                    }
                ).inspect(database_id)
            except Exception:
                raise LegacyViewProfileError("INSPECTION_FAILED") from None
            status = write_profile(
                Path(args.repository_root),
                catalog,
                replace=args.replace,
                spec=spec,
                distinct_values=_live_distinct_values(dsn),
            )
            _emit({"mode": "write", "status": status})
            return 0
        from ..catalog.postgres import PostgresAdapter
        from ..harness.postgres import PostgresReadOnlyExecutor
        from .postgres_runtime import build_validated_contexts

        profile = _load_profile(args.repository_root, spec)
        try:
            contexts = build_validated_contexts(
                [profile],
                adapters_by_id={database_id: PostgresAdapter({database_id: profile.connection_config})},
                executors_by_id={
                    database_id: PostgresReadOnlyExecutor({database_id: profile.connection_config})
                },
                expected_ontology_version=ONTOLOGY_VERSION,
            )
        except Exception:
            raise LegacyViewProfileError("RUNTIME_INVALID") from None
        results = run_smoke(contexts[database_id], spec)
        ok = all(item["status"] == "OK" for item in results)
        _emit({
            "mode": "smoke",
            "status": "OK" if ok else "UNEXPECTED",
            "unsupported_fields": list(contexts[database_id].unsupported_fields()),
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
