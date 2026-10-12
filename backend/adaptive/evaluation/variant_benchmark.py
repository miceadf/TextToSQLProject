"""Synthetic schema-variant mapping benchmark (Phase 8-08).

Scores a mapping answer (column, value transform and value dictionary per
canonical field, plus the row anchor) against the gold of every variant in
``variant_specs``, in two ways:

- field scores: offline comparison with the gold;
- execution scores (``--live-synthetic``): each variant is built in a fresh
  schema of the dedicated synthetic PostgreSQL database, the answer becomes
  a runtime profile, and fixed gold QueryIRs run through the usual compiler,
  AST policy and read-only executor. Results are compared with a reference
  evaluation of the seed that does not use the compiler.

The gold answer (oracle) must score 100% on both; that validates the
benchmark itself. The rule-based baseline is the existing aligner unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from ..mapping.transforms import FieldTransform
from .variant_specs import (
    BENCHMARK_VERSION,
    CANONICAL_FIELDS,
    SUITES,
    VARIANTS,
    GoldField,
    Variant,
    canonical_rows,
    gold_fields,
    spec_catalog,
    table_rows,
)
from .ontology_version import recorded_ontology

REPORT_DIR = Path(".schema-adaptive/reports/variant-benchmark")
REVIEW_THRESHOLD = 0.60
REFUSED = "REFUSED"

__all__ = [
    "QUESTIONS",
    "FieldAnswer",
    "VariantMappingAnswer",
    "gold_answer",
    "reference_result",
    "rule_based_answer",
    "score_fields",
]


# --- answer format -----------------------------------------------------------------


class FieldAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    table: str | None = None
    column: str | None = None
    transform: FieldTransform | None = None
    dictionary: dict[str, str] | None = None  # stored -> canonical value

    @model_validator(mode="after")
    def validate_shape(self) -> "FieldAnswer":
        if (self.table is None) != (self.column is None):
            raise ValueError("table and column must be set together")
        if self.table is None and (self.transform is not None or self.dictionary is not None):
            raise ValueError("an unsupported field has no transform or dictionary")
        if self.transform is not None and self.dictionary is not None:
            raise ValueError("a field has at most one of a transform and a dictionary")
        return self


class VariantMappingAnswer(BaseModel):
    """One proposer's answer for one variant (also the AI proposal format)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    variant: str
    fields: dict[str, FieldAnswer]
    anchor_table: str | None = None

    @model_validator(mode="after")
    def validate_fields(self) -> "VariantMappingAnswer":
        if set(self.fields) != set(CANONICAL_FIELDS):
            raise ValueError("an answer covers exactly the canonical fields")
        return self


def gold_answer(spec: Variant) -> VariantMappingAnswer:
    fields = {}
    for semantic, gold in gold_fields(spec).items():
        if gold.primary is None:
            fields[semantic] = FieldAnswer()
            continue
        transform = None if None in gold.transforms else next(iter(gold.transforms))
        fields[semantic] = FieldAnswer(
            table=gold.primary[0], column=gold.primary[1],
            transform=transform, dictionary=gold.dictionary,
        )
    return VariantMappingAnswer(variant=spec.name, fields=fields, anchor_table=spec.anchor)


def rule_based_answer(catalog: Any, ontology: Any, variant_name: str) -> VariantMappingAnswer:
    """The existing aligner's top candidates, one column per field.

    Fields are assigned in order of their best confidence; each takes the
    first of its (up to three) candidates at or above the review threshold
    whose column is still free. Rules propose no transform, dictionary or
    anchor.
    """
    from ..mapping.rules import RuleBasedSchemaAligner

    # This benchmark has no ontology version metadata: reproduce it with v2.
    proposal = RuleBasedSchemaAligner(review_threshold=REVIEW_THRESHOLD).align(
        catalog, recorded_ontology(ontology)
    )
    order = sorted(
        CANONICAL_FIELDS,
        key=lambda name: (-max((c.confidence for c in proposal.candidates.get(name, [])), default=0.0), name),
    )
    used: set[tuple[str, str]] = set()
    fields: dict[str, FieldAnswer] = {}
    for semantic in order:
        chosen = FieldAnswer()
        for candidate in proposal.candidates.get(semantic, []):
            if candidate.confidence < REVIEW_THRESHOLD:
                break
            location = (candidate.table, candidate.column)
            if location not in used:
                used.add(location)
                chosen = FieldAnswer(table=candidate.table, column=candidate.column)
                break
        fields[semantic] = chosen
    return VariantMappingAnswer(variant=variant_name, fields=fields, anchor_table=None)


# --- field scores ----------------------------------------------------------------------


def _field_marks(answer: FieldAnswer, gold: GoldField) -> dict[str, bool]:
    location = None if answer.table is None else (answer.table, answer.column)
    column_ok = location in gold.locations if gold.locations else location is None
    transform_ok = answer.transform in gold.transforms if gold.locations else answer.transform is None
    dictionary_ok = (answer.dictionary or None) == gold.dictionary
    return {
        "column": column_ok,
        "transform": transform_ok,
        "dictionary": dictionary_ok,
        "field": column_ok and transform_ok and dictionary_ok,
    }


def score_fields(spec: Variant, answer: VariantMappingAnswer) -> dict[str, Any]:
    gold = gold_fields(spec)
    counts = Counter()
    wrong: dict[str, list[str]] = {}
    for semantic in CANONICAL_FIELDS:
        marks = _field_marks(answer.fields[semantic], gold[semantic])
        counts.update(name for name, ok in marks.items() if ok)
        missed = [name for name in ("column", "transform", "dictionary") if not marks[name]]
        if missed:
            wrong[semantic] = missed
    single_table = len(spec.tables) == 1
    anchor_ok = answer.anchor_table == spec.anchor or (single_table and answer.anchor_table is None)
    return {
        "fields": len(CANONICAL_FIELDS),
        "column": counts["column"],
        "transform": counts["transform"],
        "dictionary": counts["dictionary"],
        "field": counts["field"],
        "anchor": anchor_ok,
        "wrong": wrong,
    }


# --- questions and reference ----------------------------------------------------------------

_KEY = ["Course.code", "CourseOffering.section"]


def _q(qid: str, select: list[str], filters: list[tuple[str, str, Any]]) -> dict[str, Any]:
    return {
        "id": qid,
        "ir": {
            "entity": "CourseOffering",
            "select": [*_KEY, *select],
            "filters": [{"field": f, "operator": o, "value": v} for f, o, v in filters],
            "limit": 200,
        },
    }


# Fixed before any AI proposer exists; every canonical field is selected or
# filtered at least once, and every question keeps offering grain.
QUESTIONS: tuple[dict[str, Any], ...] = (
    _q("v01", ["Course.name"], []),
    _q("v02", ["Course.credits"], [("Course.credits", "equals", 3)]),
    _q("v03", ["Course.name", "Course.credits"], [("Course.credits", "less_than", 3)]),
    _q("v04", ["Course.category"], [("Course.category", "equals", "전공")]),
    _q("v05", ["Department.name"], [("Course.category", "equals", "일반선택")]),
    _q("v06", ["Department.name"], [("Department.name", "contains", "컴퓨터")]),
    _q("v07", ["Department.code"], [("Department.code", "equals", "MATH")]),
    _q("v08", ["Course.name"], [("Instructor.name", "equals", "교원A")]),
    _q("v09", ["CourseOffering.year"], [("CourseOffering.year", "equals", 2025)]),
    _q("v10", ["CourseOffering.semester"], [("CourseOffering.semester", "equals", "2")]),
    _q("v11", ["CourseOffering.capacity"], [("CourseOffering.capacity", "greater_than", 40)]),
    _q("v12", ["CourseOffering.enrolled"], [("CourseOffering.enrolled", "greater_than_or_equal", 35)]),
    _q("v13", ["Schedule.day", "Schedule.start_time"], [("Schedule.day", "equals", "화")]),
    _q("v14", ["Schedule.start_time"], [("Schedule.start_time", "greater_than_or_equal", "13:00")]),
    _q("v15", ["Schedule.end_time"], [("Schedule.end_time", "less_than_or_equal", "12:00")]),
    _q("v16", ["Schedule.day", "Schedule.start_time", "Schedule.end_time"], []),
    _q("v17", ["Course.name"], [("Course.category", "equals", "교양"), ("Schedule.day", "equals", "수")]),
    _q("v18", ["Course.name"], [("Course.name", "contains", "데이터")]),
    _q("v19", ["CourseOffering.capacity", "CourseOffering.enrolled"],
       [("CourseOffering.year", "equals", 2026), ("CourseOffering.semester", "equals", "1")]),
    _q("v20", ["Instructor.name", "Course.category"], [("Course.code", "equals", "CSE201")]),
)


def _referenced(ir: dict[str, Any]) -> set[str]:
    return {*ir["select"], *(item["field"] for item in ir["filters"])}


def _matches(value: Any, operator: str, expected: Any) -> bool:
    if value is None:
        return False  # SQL comparisons with NULL are never true
    if operator == "equals":
        return value == expected
    if operator == "contains":
        return isinstance(value, str) and str(expected) in value
    if operator == "greater_than":
        return value > expected
    if operator == "greater_than_or_equal":
        return value >= expected
    if operator == "less_than":
        return value < expected
    if operator == "less_than_or_equal":
        return value <= expected
    raise ValueError("unsupported operator")


def _canonical_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return f"<{type(value).__name__}>"  # e.g. a raw time object: never canonical


def _row_counter(rows: Iterable[dict[str, Any]], select: list[str]) -> Counter:
    return Counter(tuple(_canonical_value(row.get(name)) for name in select) for row in rows)


def reference_result(spec: Variant, ir: dict[str, Any]) -> Counter | str:
    """Seed-only answer: rows as a multiset, or ``REFUSED`` for gold-unsupported fields."""
    gold = gold_fields(spec)
    if any(not gold[name].locations for name in _referenced(ir)):
        return REFUSED
    rows = [
        row for row in canonical_rows(spec.unscheduled)
        if all(_matches(row[f["field"]], f["operator"], f["value"]) for f in ir["filters"])
    ]
    # Without ORDER BY a truncated result is not defined; questions must fit.
    if len(rows) > ir["limit"]:
        raise ValueError("benchmark question result exceeds its limit")
    return _row_counter(rows, ir["select"])


# --- live execution -------------------------------------------------------------------------

_SCHEMA_PATTERN = re.compile(r"^schema_adaptive_smoke_[0-9a-f]{16}$")


def build_variant_schema(connection: Any, schema: str, spec: Variant) -> None:
    """Create and fill one variant in ``schema`` (identifiers via psycopg2.sql)."""
    from psycopg2 import sql  # type: ignore[import-not-found]

    if _SCHEMA_PATTERN.fullmatch(schema) is None:
        raise ValueError("invalid schema name")
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        for table in _creation_order(spec):
            parts = [
                sql.SQL("{} {}{}").format(
                    sql.Identifier(col.name),
                    sql.SQL(col.pg_type),
                    sql.SQL(" NOT NULL") if col.name in table.pk or not _nullable_col(spec, table, col) else sql.SQL(""),
                )
                for col in table.columns
            ]
            parts.append(sql.SQL("PRIMARY KEY ({})").format(
                sql.SQL(", ").join(sql.Identifier(name) for name in table.pk)))
            for name, columns, target, target_columns in table.fks:
                parts.append(sql.SQL("CONSTRAINT {} FOREIGN KEY ({}) REFERENCES {}.{} ({})").format(
                    sql.Identifier(name),
                    sql.SQL(", ").join(sql.Identifier(c) for c in columns),
                    sql.Identifier(schema), sql.Identifier(target),
                    sql.SQL(", ").join(sql.Identifier(c) for c in target_columns),
                ))
            cursor.execute(sql.SQL("CREATE TABLE {}.{} ({})").format(
                sql.Identifier(schema), sql.Identifier(table.name), sql.SQL(", ").join(parts)))
            insert = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
                sql.Identifier(schema), sql.Identifier(table.name),
                sql.SQL(", ").join(sql.Identifier(col.name) for col in table.columns),
                sql.SQL(", ").join(sql.Placeholder() for _ in table.columns),
            )
            for row in table_rows(spec, table):
                cursor.execute(insert, row)
    connection.commit()


def _nullable_col(spec: Variant, table: Any, col: Any) -> bool:
    from .variant_specs import _nullable

    return _nullable(spec, table, col)


def _creation_order(spec: Variant) -> list[Any]:
    """Referenced tables first."""
    ordered: list[Any] = []
    pending = list(spec.tables)
    while pending:
        created = {table.name for table in ordered}
        ready = [t for t in pending if all(target in created for _n, _c, target, _tc in t.fks)]
        if not ready:
            raise ValueError("foreign keys form a cycle")
        ordered.extend(ready)
        pending = [t for t in pending if t not in ready]
    return ordered


def answer_profile(answer: VariantMappingAnswer, database_id: str, catalog: Any, config: Any) -> Any:
    """Turn an answer into a runtime profile (the runtime validates it)."""
    from ..api.postgres_runtime import PostgresRuntimeProfile
    from ..mapping.contracts import PublishedFieldMapping, PublishedMappingProfile
    from ..mapping.values import ValueDictionary, ValueEntry

    mapped = {name: item for name, item in answer.fields.items() if item.table is not None}
    published = PublishedMappingProfile(
        database_id=database_id,
        version=1,
        schema_fingerprint=catalog.schema_fingerprint,
        ontology_version=1,
        published_by="variant-benchmark",
        published_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
        fields={
            name: PublishedFieldMapping(table=item.table, column=item.column, reviewer="variant-benchmark")
            for name, item in mapped.items()
        },
    )
    return PostgresRuntimeProfile(
        database_id=database_id,
        label="Variant benchmark",
        connection_config=config,
        published_mapping=published,
        field_transforms={n: i.transform for n, i in mapped.items() if i.transform is not None},
        unsupported_fields=sorted(set(CANONICAL_FIELDS) - set(mapped)),
        anchor_table=answer.anchor_table,
        value_dictionaries={
            name: ValueDictionary(
                entries=tuple(ValueEntry(stored=s, label=l) for s, l in sorted(item.dictionary.items())),
                unknown="pass", reviewer="variant-benchmark", reason="benchmark answer",
            )
            for name, item in mapped.items() if item.dictionary
        },
    )


def execute_answer(
    spec: Variant,
    answer: VariantMappingAnswer,
    *,
    database_id: str,
    catalog: Any,
    config: Any,
    make_adapter: Callable[[], Any],
    make_executor: Callable[[], Any],
) -> dict[str, Any]:
    """Run every question with this answer; outcome per question."""
    from ..api.legacy_view_profile import _NoSqlite
    from .runner import _runtime_ontology
    from ..api.postgres_runtime import (
        DatabaseRoutingCompiler,
        DatabaseRoutingExecutor,
        DatabaseRoutingPolicy,
        build_validated_contexts,
    )
    from ..harness.contracts import HarnessRequest, HarnessStatus
    from ..harness.engine import ExecutionHarness
    from ..harness.model import FakeModel

    # The database must be reachable and unchanged before the answer is judged,
    # so an infrastructure failure is never scored as a rejected answer.
    try:
        inspected = make_adapter().inspect(database_id)
    except Exception:
        raise RuntimeError("INSPECTION_FAILED") from None
    if inspected != catalog:
        raise RuntimeError("CATALOG_MISMATCH")
    try:
        profile = answer_profile(answer, database_id, catalog, config)
        contexts = build_validated_contexts(
            [profile],
            adapters_by_id={database_id: make_adapter()},
            executors_by_id={database_id: make_executor()},
            expected_ontology_version=profile.published_mapping.ontology_version,
        )
    except Exception:
        return {"profile": "REJECTED", "outcomes": {q["id"]: "PROFILE_REJECTED" for q in QUESTIONS}}
    context = contexts[database_id]
    none = _NoSqlite()
    harness = ExecutionHarness(
        model=FakeModel({q["id"]: q["ir"] for q in QUESTIONS}),
        compiler=DatabaseRoutingCompiler(none, contexts),
        sql_policy=DatabaseRoutingPolicy(none, contexts),
        executor=DatabaseRoutingExecutor(none, contexts),
    )
    ontology = _runtime_ontology(recorded_ontology(version=profile.published_mapping.ontology_version))
    outcomes: dict[str, str] = {}
    for question in QUESTIONS:
        expected = reference_result(spec, question["ir"])
        result = harness.run(
            HarnessRequest(request_id=f"bench-{question['id']}", database_id=database_id,
                           question=question["id"]),
            ontology,
            context.harness_mapping(),
        )
        if result.status is HarnessStatus.SUCCEEDED:
            actual = _row_counter(result.rows, question["ir"]["select"])
            outcomes[question["id"]] = (
                "CORRECT" if expected != REFUSED and actual == expected
                else "ANSWERED_UNSUPPORTED" if expected == REFUSED else "WRONG_ROWS"
            )
        else:
            code = result.error.code.value if result.error is not None else "HARNESS_FAILURE"
            refused = code == "MAPPING_MISSING"
            outcomes[question["id"]] = (
                "CORRECT" if refused and expected == REFUSED
                else "REFUSED_SUPPORTED" if refused else f"FAILED_{code}"
            )
    return {"profile": "ACCEPTED", "outcomes": outcomes}


def with_section_anchor(answer: VariantMappingAnswer) -> VariantMappingAnswer:
    """Anchor rule shared by proposers: the table of the offering section."""
    return answer.model_copy(update={"anchor_table": answer.fields["CourseOffering.section"].table})


def run_live(
    dsn: str,
    *,
    token_hex: Callable[[int], str] | None = None,
    extra_answers: dict[str, dict[str, VariantMappingAnswer]] | None = None,
    suite: str = "dev",
) -> dict[str, Any]:
    """``extra_answers``: proposer name -> variant name -> stored answer."""
    import psycopg2  # type: ignore[import-not-found]

    from ..catalog.postgres import PostgresAdapter, PostgresConnectionConfig
    from ..fixtures.postgres_synthetic import cleanup_synthetic_schema, generate_schema_name
    from ..harness.postgres import PostgresReadOnlyExecutor
    from ..ontology.loader import load_default_ontology

    ontology = recorded_ontology(load_default_ontology())
    variants: dict[str, Any] = {}
    connection = psycopg2.connect(dsn, connect_timeout=5)
    try:
        for spec in SUITES[suite]:
            database_id = f"bench-{spec.name}"
            schema = generate_schema_name(token_hex)
            try:
                build_variant_schema(connection, schema, spec)
                config = PostgresConnectionConfig(dsn, schema=schema)
                catalog = PostgresAdapter({database_id: config}).inspect(database_id)
                expected_catalog = spec_catalog(spec, database_id)
                if catalog != expected_catalog:
                    raise RuntimeError("CATALOG_MISMATCH")
                offline_rules = rule_based_answer(expected_catalog, ontology, spec.name)
                live_rules = rule_based_answer(catalog, ontology, spec.name)
                if live_rules != offline_rules:
                    raise RuntimeError("RULES_NONDETERMINISTIC")
                answers = {
                    "oracle": gold_answer(spec),
                    "rules": live_rules,
                    "rules+anchor": with_section_anchor(live_rules),
                }
                for proposer, by_variant in (extra_answers or {}).items():
                    answers[proposer] = by_variant[spec.name]
                record: dict[str, Any] = {"axis": spec.axis, "fingerprint": catalog.schema_fingerprint}
                for proposer, answer in answers.items():
                    record[proposer] = {
                        "fields": score_fields(spec, answer),
                        "execution": execute_answer(
                            spec, answer, database_id=database_id, catalog=catalog, config=config,
                            make_adapter=lambda: PostgresAdapter({database_id: config}),
                            make_executor=lambda: PostgresReadOnlyExecutor({database_id: config}),
                        ),
                        "answer": answer.model_dump(mode="json"),
                    }
                variants[spec.name] = record
            except RuntimeError as exc:
                known = {"CATALOG_MISMATCH", "RULES_NONDETERMINISTIC", "INSPECTION_FAILED"}
                code = str(exc) if str(exc) in known else "FAILED"
                variants[spec.name] = {"axis": spec.axis, "error": code}
            except Exception:
                variants[spec.name] = {"axis": spec.axis, "error": "FAILED"}
            finally:
                # Always end the failed or finished transaction, then drop the schema.
                try:
                    connection.rollback()
                    cleanup_synthetic_schema(connection, schema)
                except Exception:
                    variants.setdefault(spec.name, {})["cleanup"] = "FAILED"
    finally:
        connection.close()
    return {
        "version": BENCHMARK_VERSION,
        "suite": suite,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "questions": len(QUESTIONS),
        "variants": variants,
    }


# --- summaries and CLI --------------------------------------------------------------------------


def offline_scores(suite: str = "dev") -> dict[str, Any]:
    from ..ontology.loader import load_default_ontology

    ontology = recorded_ontology(load_default_ontology())
    scores = {}
    for spec in SUITES[suite]:
        catalog = spec_catalog(spec, f"bench-{spec.name}")
        scores[spec.name] = {
            "axis": spec.axis,
            "oracle": score_fields(spec, gold_answer(spec)),
            "rules": score_fields(spec, rule_based_answer(catalog, ontology, spec.name)),
        }
    return scores


def proposers(record: dict[str, Any]) -> list[str]:
    return [key for key, value in record.items() if isinstance(value, dict) and "fields" in value]


def run_problems(run: dict[str, Any]) -> dict[str, str]:
    """Variants that make a run invalid: errors, leftover schemas, or an
    oracle below full marks (the benchmark would then be wrong, not the
    proposer)."""
    problems: dict[str, str] = {}
    for name, record in run["variants"].items():
        if "error" in record:
            problems[name] = record["error"]
            continue
        if record.get("cleanup") == "FAILED":
            problems[name] = "CLEANUP_FAILED"
            continue
        oracle = record["oracle"]
        fields = oracle["fields"]
        execution = oracle["execution"]
        if (
            execution["profile"] != "ACCEPTED"
            or fields["field"] != fields["fields"]
            or not fields["anchor"]
            or len(execution["outcomes"]) != len(QUESTIONS)
            or any(outcome != "CORRECT" for outcome in execution["outcomes"].values())
        ):
            problems[name] = "ORACLE_NOT_PERFECT"
    return problems


def summarize(run: dict[str, Any]) -> dict[str, Any]:
    """Per proposer: field and question totals, overall and per axis."""
    totals: dict[str, dict[str, Counter]] = {}
    for record in run["variants"].values():
        if "error" in record:
            continue
        for proposer in proposers(record):
            item = record[proposer]
            for scope in ("ALL", record["axis"]):
                bucket = totals.setdefault(proposer, {}).setdefault(scope, Counter())
                fields = item["fields"]
                bucket["variants"] += 1
                bucket["fields_total"] += fields["fields"]
                for key in ("column", "transform", "dictionary", "field"):
                    bucket[f"fields_{key}"] += fields[key]
                bucket["anchor"] += int(fields["anchor"])
                outcomes = item.get("execution", {}).get("outcomes", {})
                bucket["questions_total"] += len(outcomes)
                bucket["questions_correct"] += sum(1 for o in outcomes.values() if o == "CORRECT")
                # Every question selects the course key, so one unmapped key
                # refuses a whole variant; report refusals apart from errors.
                bucket["questions_refused_supported"] += sum(
                    1 for o in outcomes.values() if o == "REFUSED_SUPPORTED"
                )
    return {
        proposer: {scope: dict(counter) for scope, counter in scopes.items()}
        for proposer, scopes in totals.items()
    }


def _write_private(repository_root: Path, label: str, payload: dict[str, Any]) -> Path:
    directory = repository_root / REPORT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{label}.json"
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2))
    return path


def main(argv: list[str] | None = None, *, environ: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backend.adaptive.evaluation.variant_benchmark", allow_abbrev=False)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Offline field scores only.")
    mode.add_argument("--live-synthetic", action="store_true", help="Build variants and execute.")
    parser.add_argument("--label", default=None)
    parser.add_argument("--suite", choices=sorted(SUITES), default="dev")
    parser.add_argument(
        "--ai-proposals", action="append", default=[], metavar="NAME=PATH",
        help="Stored ai_mapping proposals to execute as proposer NAME (repeatable).",
    )
    parser.add_argument("--repository-root", default=str(Path.cwd()))
    args = parser.parse_args(argv)
    env = os.environ if environ is None else environ
    if args.check:
        scores = offline_scores(args.suite)
        for name, record in scores.items():
            print(json.dumps({
                "variant": name, "axis": record["axis"],
                **{p: {k: record[p][k] for k in ("field", "column", "transform", "dictionary", "anchor")}
                   for p in ("oracle", "rules")},
            }, ensure_ascii=False), flush=True)
        return 0
    from ..harness.postgres_live_smoke import DSN_ENV, SmokeFailure, validate_dsn

    dsn = env.get(DSN_ENV)
    if not dsn:
        print(json.dumps({"status": "FAILED", "code": "ENV_MISSING"}), flush=True)
        return 2
    try:
        validate_dsn(dsn)
    except SmokeFailure as exc:
        print(json.dumps({"status": "FAILED", "code": exc.code}), flush=True)
        return 2
    label = args.label or "vb-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", label) is None:
        print(json.dumps({"status": "FAILED", "code": "INVALID_LABEL"}), flush=True)
        return 2
    if (Path(args.repository_root) / REPORT_DIR / f"{label}.json").exists():
        print(json.dumps({"status": "FAILED", "code": "LABEL_EXISTS"}), flush=True)
        return 2
    try:
        extra: dict[str, dict[str, VariantMappingAnswer]] = {}
        if args.ai_proposals:
            from .ai_mapping import load_answers

            for item in args.ai_proposals:
                name, _, path = item.partition("=")
                if re.fullmatch(r"ai[a-z0-9.+-]{0,30}", name) is None or not path or name in extra:
                    raise ValueError("invalid --ai-proposals entry")
                extra[name] = load_answers(path, args.suite)
        run = run_live(dsn, extra_answers=extra, suite=args.suite)
    except Exception:
        print(json.dumps({"status": "FAILED", "code": "LIVE_FAILED"}), flush=True)
        return 2
    problems = run_problems(run)
    run["problems"] = problems
    _write_private(Path(args.repository_root), label, run)
    print(json.dumps({"mode": "live", "label": label, "variants": len(run["variants"]),
                      "problems": problems}), flush=True)
    for proposer, scopes in summarize(run).items():
        for scope, total in scopes.items():
            print(json.dumps({"proposer": proposer, "scope": scope, **total}, ensure_ascii=False), flush=True)
    return 0 if not problems else 2


if __name__ == "__main__":
    sys.exit(main())
