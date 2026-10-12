"""Evaluation-only direct-SQL baseline arm (dynamic schema prompt -> raw SQL).

Implements the §15.1 comparison system "existing model + dynamic schema
prompt": the model sees the fixture Catalog and the question and returns SQL,
which is checked by a read-only SELECT policy and executed by the existing
``SQLiteReadOnlyExecutor``. Results are compared to gold rows without column
names. This path exists only for offline evaluation; runtime HTTP,
prototype, and clarification code must never import it (raw SQL fallback
stays forbidden at runtime, ADR-048/049).

Reports carry fixed outcome codes and fingerprints only: no question,
prompt, raw model output, SQL text, or row values.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Literal, Protocol

import sqlite3

import sqlglot
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp

from ..catalog.contracts import DatabaseCatalog
from ..fixtures.contracts import FixtureBundle, FixtureSeed, FixtureVariant
from ..fixtures.seed import DEFAULT_SEED
from ..harness.contracts import CompiledQuery, HarnessErrorCode
from ..harness.errors import HarnessFailure
from ..harness.executor import SQLiteReadOnlyExecutor
from ..ontology.contracts import DomainOntology
from ..ontology.loader import load_default_ontology
from .contracts import EvaluationCase, EvaluationSet, ReviewStatus
from .coverage import evaluation_set_fingerprint
from .run_contracts import EvaluationRunConfig, FixtureRunMetadata, Metric
from .run_report import deterministic_request_id
from .runner import _prepare_fixture_contexts
from .validator import validate_evaluation_set
from .ontology_version import recorded_ontology


DIRECT_SQL_PROMPT_VERSION = "direct-sql-v1"
DIRECT_SQL_EXTRACT_VERSION = "direct-sql-extract-v1"
DIRECT_SQL_COMPARISON_VERSION = "positional-row-multiset-v1"
DIRECT_SQL_RUN_SCHEMA_VERSION = "direct-sql-run-v1"

DIRECT_SQL_TEMPLATE = (
    "You are a strict SQL generator for a SQLite database.\n"
    "Schema:\n"
    "{schema}\n"
    "Rules:\n"
    "- Return exactly one read-only SELECT statement.\n"
    "- Use only the tables and columns listed in the schema.\n"
    "- Do not invent table or column names.\n"
    "- Return SQL only, without Markdown or explanation.\n"
    "- If the question cannot be answered from the schema, return UNKNOWN.\n"
    "Question: {question}\n"
    "SQL:"
)
DIRECT_SQL_TEMPLATE_SHA256 = hashlib.sha256(DIRECT_SQL_TEMPLATE.encode("utf-8")).hexdigest()


class DirectSqlOutcome(str, Enum):
    CORRECT = "CORRECT"
    INCORRECT_RESULT = "INCORRECT_RESULT"
    NON_STRING_OUTPUT = "NON_STRING_OUTPUT"
    EMPTY_OUTPUT = "EMPTY_OUTPUT"
    AMBIGUOUS_OUTPUT = "AMBIGUOUS_OUTPUT"
    ABSTAIN = "ABSTAIN"
    SQL_PARSE_ERROR = "SQL_PARSE_ERROR"
    SQL_MULTIPLE_STATEMENTS = "SQL_MULTIPLE_STATEMENTS"
    SQL_NOT_SELECT = "SQL_NOT_SELECT"
    SQL_FORBIDDEN_NODE = "SQL_FORBIDDEN_NODE"
    SQL_UNKNOWN_TABLE = "SQL_UNKNOWN_TABLE"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    EXECUTION_TIMEOUT = "EXECUTION_TIMEOUT"
    RESULT_TOO_LARGE = "RESULT_TOO_LARGE"
    RESULT_DUPLICATE_COLUMNS = "RESULT_DUPLICATE_COLUMNS"
    RESULT_UNSUPPORTED_VALUE = "RESULT_UNSUPPORTED_VALUE"
    COLUMN_COUNT_MISMATCH = "COLUMN_COUNT_MISMATCH"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"


class DirectSqlProviderError(Exception):
    """Provider failure with a fixed outcome; never carries provider text."""

    def __init__(self, outcome: DirectSqlOutcome = DirectSqlOutcome.PROVIDER_ERROR) -> None:
        if outcome not in {DirectSqlOutcome.PROVIDER_ERROR, DirectSqlOutcome.PROVIDER_TIMEOUT}:
            outcome = DirectSqlOutcome.PROVIDER_ERROR
        self.outcome = outcome
        super().__init__(outcome.value)


class DirectSqlRejected(Exception):
    """Extraction or policy rejection with a fixed outcome."""

    def __init__(self, outcome: DirectSqlOutcome) -> None:
        self.outcome = outcome
        super().__init__(outcome.value)


@dataclass(frozen=True)
class DirectSqlRequest:
    request_id: str
    database_id: str
    question: str = field(repr=False)
    prompt: str = field(repr=False)


class DirectSqlModel(Protocol):
    def generate_sql(self, request: DirectSqlRequest) -> object: ...


class FakeDirectSqlModel:
    """Deterministic test provider keyed by (database_id, question)."""

    def __init__(self, responses: Mapping[tuple[str, str], object]) -> None:
        self._responses = dict(responses)

    def generate_sql(self, request: DirectSqlRequest) -> object:
        key = (request.database_id, request.question)
        if key not in self._responses:
            raise DirectSqlProviderError()
        value = self._responses[key]
        if isinstance(value, BaseException):
            raise value
        return value


# ---------------------------------------------------------------------------
# prompt


def render_catalog_schema(catalog: DatabaseCatalog) -> str:
    """Deterministic schema text: tables by name, columns in Catalog order."""
    lines: list[str] = []
    for table in sorted(catalog.tables, key=lambda item: item.name):
        columns = ", ".join(
            f"{column.name} {column.declared_type}".rstrip() for column in table.columns
        )
        lines.append(f"{table.name}({columns})")
        if table.primary_key:
            lines.append(f"  PRIMARY KEY ({', '.join(table.primary_key)})")
        for foreign_key in sorted(table.foreign_keys, key=lambda item: (item.id, item.sequence)):
            lines.append(
                f"  FOREIGN KEY ({foreign_key.column}) REFERENCES "
                f"{foreign_key.referenced_table}({foreign_key.referenced_column})"
            )
    return "\n".join(lines)


def build_direct_sql_prompt(catalog: DatabaseCatalog, question: str) -> str:
    """Substitute by partition so braces in the question are never interpreted."""
    prefix, _, remainder = DIRECT_SQL_TEMPLATE.partition("{schema}")
    between, _, suffix = remainder.partition("{question}")
    return prefix + render_catalog_schema(catalog) + between + question + suffix


# ---------------------------------------------------------------------------
# extraction and policy

_FENCE = re.compile(r"```[A-Za-z0-9_-]*[ \t]*\r?\n?(.*?)```", re.DOTALL)
_LABEL = re.compile(r"^sql\s*:", re.IGNORECASE)


def find_code_fences(text: str) -> list[str]:
    """Bodies of Markdown code fences; the one rule shared by both arms."""
    return _FENCE.findall(text)


def extract_sql(output: object) -> str:
    """Apply ``direct-sql-extract-v1`` without repairing the SQL itself."""
    if not isinstance(output, str):
        raise DirectSqlRejected(DirectSqlOutcome.NON_STRING_OUTPUT)
    text = output.strip()
    if not text:
        raise DirectSqlRejected(DirectSqlOutcome.EMPTY_OUTPUT)
    fences = find_code_fences(text)
    if len(fences) > 1:
        raise DirectSqlRejected(DirectSqlOutcome.AMBIGUOUS_OUTPUT)
    if len(fences) == 1:
        text = fences[0].strip()
    text = _LABEL.sub("", text, count=1).strip()
    text = text.rstrip().rstrip(";").rstrip()
    if not text:
        raise DirectSqlRejected(DirectSqlOutcome.EMPTY_OUTPUT)
    if text.upper() == "UNKNOWN":
        raise DirectSqlRejected(DirectSqlOutcome.ABSTAIN)
    return text


def _node_types(*names: str) -> tuple[type, ...]:
    return tuple(getattr(exp, name) for name in names if hasattr(exp, name))


_ROOT_TYPES = _node_types("Select", "Union", "Intersect", "Except")
_FORBIDDEN_TYPES = _node_types(
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "Command",
    "Pragma", "Transaction", "Commit", "Rollback", "Attach", "Detach", "Set",
    "Into", "Copy", "Use", "LoadData", "Analyze",
)


def check_direct_sql(sql: str, table_names: frozenset[str]) -> None:
    """Allow one read-only SELECT over known Catalog tables or its own CTEs."""
    try:
        statements = [item for item in sqlglot.parse(sql, read="sqlite") if item is not None]
    except Exception:
        raise DirectSqlRejected(DirectSqlOutcome.SQL_PARSE_ERROR) from None
    if not statements:
        raise DirectSqlRejected(DirectSqlOutcome.SQL_PARSE_ERROR)
    if len(statements) != 1:
        raise DirectSqlRejected(DirectSqlOutcome.SQL_MULTIPLE_STATEMENTS)
    root = statements[0]
    if not isinstance(root, _ROOT_TYPES):
        raise DirectSqlRejected(DirectSqlOutcome.SQL_NOT_SELECT)
    if any(True for _ in root.find_all(*_FORBIDDEN_TYPES)):
        raise DirectSqlRejected(DirectSqlOutcome.SQL_FORBIDDEN_NODE)
    known = {name.lower() for name in table_names}
    for cte in root.find_all(exp.CTE):
        if cte.alias_or_name.lower().startswith("sqlite_"):
            raise DirectSqlRejected(DirectSqlOutcome.SQL_UNKNOWN_TABLE)
    for table in root.find_all(exp.Table):
        if table.args.get("db") is not None or table.args.get("catalog") is not None:
            raise DirectSqlRejected(DirectSqlOutcome.SQL_UNKNOWN_TABLE)
        name = table.name.lower() if isinstance(table.name, str) else ""
        if not name or name.startswith("sqlite_"):
            raise DirectSqlRejected(DirectSqlOutcome.SQL_UNKNOWN_TABLE)
        if name not in known and name not in _visible_cte_names(table):
            raise DirectSqlRejected(DirectSqlOutcome.SQL_UNKNOWN_TABLE)


def _visible_cte_names(node: exp.Expression) -> set[str]:
    """CTE names defined by a WITH on this node's ancestor chain only."""
    names: set[str] = set()
    parent = node.parent
    while parent is not None:
        # The arg key differs across sqlglot versions ("with" / "with_").
        for with_clause in parent.args.values():
            if isinstance(with_clause, exp.With):
                names.update(cte.alias_or_name.lower() for cte in with_clause.expressions)
        parent = parent.parent
    return names


class _CatalogBoundExecutor(SQLiteReadOnlyExecutor):
    """Existing read-only executor plus a read allowlist of Catalog tables."""

    def __init__(self, databases: Mapping[str, Any], allowed_tables: frozenset[str]) -> None:
        super().__init__(dict(databases))
        self._allowed_tables = frozenset(name.lower() for name in allowed_tables)

    def _authorize(self, action_code, first, second, database, trigger):  # noqa: ANN001
        if action_code == sqlite3.SQLITE_READ and (
            not isinstance(first, str) or first.lower() not in self._allowed_tables
        ):
            return sqlite3.SQLITE_DENY
        return SQLiteReadOnlyExecutor._authorize(action_code, first, second, database, trigger)


# ---------------------------------------------------------------------------
# comparison


class _UnsupportedValue(Exception):
    pass


def normalize_result_value(value: Any) -> str:
    """``positional-row-multiset-v1`` scalar form.

    NULL is tagged apart from every value; strings and numbers share one tag
    (type-lenient by design, so '3' and 3 compare equal).
    """
    if value is None:
        return "N:"
    return "V:" + _normalize_non_null(value)


def _normalize_non_null(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float, Decimal)):
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            raise _UnsupportedValue() from None
        if not number.is_finite():
            raise _UnsupportedValue()
        text = format(number.normalize(), "f")
        return "0" if text in {"-0", "0"} else text
    if isinstance(value, str):
        return value
    raise _UnsupportedValue()


def positional_row_multiset(rows: Sequence[Sequence[Any]]) -> Counter[tuple[str, ...]]:
    return Counter(tuple(sorted(normalize_result_value(item) for item in row)) for row in rows)


def gold_row_multiset(case: EvaluationCase) -> Counter[tuple[str, ...]]:
    select = list(case.gold_query_ir.select)
    return positional_row_multiset([[row[name] for name in select] for row in case.expected.rows])


def _multiset_fingerprint(rows: Counter[tuple[str, ...]]) -> str:
    encoded = json.dumps(
        sorted([list(key), count] for key, count in rows.items()),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# report contracts


class DirectSqlAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^P2-[0-9]{3}$")
    variant: FixtureVariant
    request_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,199}$")
    outcome: DirectSqlOutcome
    valid_sql: bool
    execution_success: bool
    correct: bool
    sql_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    result_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class DirectSqlMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheduled_attempts: int = Field(ge=0)
    valid_sql: Metric
    execution_success: Metric
    execution_correct: Metric
    abstain: Metric
    outcome_counts: dict[str, int]


class DirectSqlRunReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["direct-sql-run-v1"] = DIRECT_SQL_RUN_SCHEMA_VERSION
    config: EvaluationRunConfig
    prompt_version: Literal["direct-sql-v1"] = DIRECT_SQL_PROMPT_VERSION
    prompt_template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    extract_version: Literal["direct-sql-extract-v1"] = DIRECT_SQL_EXTRACT_VERSION
    comparison_version: Literal["positional-row-multiset-v1"] = DIRECT_SQL_COMPARISON_VERSION
    benchmark_id: str
    benchmark_version: int = Field(ge=1)
    gold_set_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_metadata: list[FixtureRunMetadata]
    gold_human_review_pending: bool
    warnings: list[str]
    attempts: list[DirectSqlAttempt]
    totals: DirectSqlMetrics
    by_variant: dict[str, DirectSqlMetrics]


def _metric(count: int, denominator: int) -> Metric:
    return Metric(count=count, denominator=denominator)


def _metrics(attempts: Sequence[DirectSqlAttempt]) -> DirectSqlMetrics:
    total = len(attempts)
    counts = Counter(item.outcome.value for item in attempts)
    return DirectSqlMetrics(
        scheduled_attempts=total,
        valid_sql=_metric(sum(item.valid_sql for item in attempts), total),
        execution_success=_metric(sum(item.execution_success for item in attempts), total),
        execution_correct=_metric(sum(item.correct for item in attempts), total),
        abstain=_metric(counts.get(DirectSqlOutcome.ABSTAIN.value, 0), total),
        outcome_counts=dict(sorted(counts.items())),
    )


# ---------------------------------------------------------------------------
# runner


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _run_attempt(
    case: EvaluationCase,
    variant: FixtureVariant,
    request_id: str,
    database_id: str,
    catalog: DatabaseCatalog,
    table_names: frozenset[str],
    model: DirectSqlModel,
    executor: _CatalogBoundExecutor,
) -> DirectSqlAttempt:
    base = {"case_id": case.case_id, "variant": variant, "request_id": request_id}

    def done(outcome: DirectSqlOutcome, **extra: Any) -> DirectSqlAttempt:
        values = {"valid_sql": False, "execution_success": False, "correct": False}
        values.update(extra)
        return DirectSqlAttempt(**base, outcome=outcome, **values)

    request = DirectSqlRequest(
        request_id=request_id,
        database_id=database_id,
        question=case.question_ko,
        prompt=build_direct_sql_prompt(catalog, case.question_ko),
    )
    try:
        output = model.generate_sql(request)
    except DirectSqlProviderError as failure:
        return done(failure.outcome)
    except Exception:
        return done(DirectSqlOutcome.PROVIDER_ERROR)

    try:
        sql = extract_sql(output)
    except DirectSqlRejected as rejected:
        return done(rejected.outcome)
    sql_fingerprint = _sha256(sql)
    try:
        check_direct_sql(sql, table_names)
    except DirectSqlRejected as rejected:
        return done(rejected.outcome, sql_fingerprint=sql_fingerprint)

    try:
        columns, rows = executor.execute(
            database_id, CompiledQuery(sql=sql, parameters=[], expected_columns=[])
        )
    except HarnessFailure as failure:
        code = {
            HarnessErrorCode.EXECUTION_TIMEOUT: DirectSqlOutcome.EXECUTION_TIMEOUT,
            HarnessErrorCode.RESULT_TOO_LARGE: DirectSqlOutcome.RESULT_TOO_LARGE,
        }.get(failure.code, DirectSqlOutcome.EXECUTION_ERROR)
        return done(code, valid_sql=True, sql_fingerprint=sql_fingerprint)
    except Exception:
        return done(DirectSqlOutcome.EXECUTION_ERROR, valid_sql=True, sql_fingerprint=sql_fingerprint)

    executed = {"valid_sql": True, "execution_success": True, "sql_fingerprint": sql_fingerprint}
    if len(columns) != len(set(columns)):
        return done(DirectSqlOutcome.RESULT_DUPLICATE_COLUMNS, **executed)
    if len(columns) != len(case.gold_query_ir.select):
        return done(DirectSqlOutcome.COLUMN_COUNT_MISMATCH, **executed)
    try:
        actual = positional_row_multiset([[row[name] for name in columns] for row in rows])
    except _UnsupportedValue:
        return done(DirectSqlOutcome.RESULT_UNSUPPORTED_VALUE, **executed)
    fingerprint = _multiset_fingerprint(actual)
    if actual == gold_row_multiset(case):
        return done(DirectSqlOutcome.CORRECT, correct=True, result_fingerprint=fingerprint, **executed)
    return done(DirectSqlOutcome.INCORRECT_RESULT, result_fingerprint=fingerprint, **executed)


def run_direct_sql_evaluation(
    gold: EvaluationSet,
    model: DirectSqlModel,
    config: EvaluationRunConfig,
    bundle: FixtureBundle,
    *,
    ontology: DomainOntology | None = None,
    seed: FixtureSeed = DEFAULT_SEED,
) -> DirectSqlRunReport:
    """Evaluate every scheduled case×variant once; never show gold to the model."""
    domain_ontology = ontology or load_default_ontology()
    validate_evaluation_set(gold, seed, domain_ontology)
    domain_ontology = recorded_ontology(domain_ontology, version=gold.ontology_version)
    contexts, all_metadata = _prepare_fixture_contexts(bundle, seed, domain_ontology, None)
    if any(FixtureVariant(name) not in contexts for name in config.variants):
        raise ValueError("configured variant missing from fixture bundle")
    catalogs: dict[FixtureVariant, DatabaseCatalog] = {}
    for variant, (artifact, _graph, _mapping) in contexts.items():
        catalog = DatabaseCatalog.model_validate_json(
            artifact.catalog_path.read_text(encoding="utf-8")
        )
        expected = next(item for item in all_metadata if item.variant == variant)
        if catalog.schema_fingerprint != expected.catalog_fingerprint:
            raise ValueError("fixture Catalog drift after preparation")
        catalogs[variant] = catalog
    executors = {
        variant: _CatalogBoundExecutor(
            {artifact.database_id: artifact.database_path},
            frozenset(table.name for table in catalogs[variant].tables),
        )
        for variant, (artifact, _graph, _mapping) in contexts.items()
    }

    attempts: list[DirectSqlAttempt] = []
    for case in gold.cases:
        for variant_name in config.variants:
            variant = FixtureVariant(variant_name)
            artifact = contexts[variant][0]
            catalog = catalogs[variant]
            attempts.append(
                _run_attempt(
                    case,
                    variant,
                    deterministic_request_id(config, gold, case.case_id, variant.value),
                    artifact.database_id,
                    catalog,
                    frozenset(table.name for table in catalog.tables),
                    model,
                    executors[variant],
                )
            )

    metadata = [
        next(item for item in all_metadata if item.variant == FixtureVariant(name))
        for name in config.variants
    ]
    pending = any(case.review_status is ReviewStatus.NEEDS_HUMAN_REVIEW for case in gold.cases)
    warnings = []
    if config.oracle_smoke:
        warnings.append("ORACLE_SMOKE_NOT_MODEL_ACCURACY")
    if pending:
        warnings.append("GOLD_NEEDS_HUMAN_REVIEW")
    return DirectSqlRunReport(
        config=config,
        prompt_template_sha256=DIRECT_SQL_TEMPLATE_SHA256,
        benchmark_id=gold.benchmark_id,
        benchmark_version=gold.version,
        gold_set_fingerprint=evaluation_set_fingerprint(gold),
        seed_fingerprint=gold.seed_fingerprint,
        fixture_metadata=metadata,
        gold_human_review_pending=pending,
        warnings=warnings,
        attempts=attempts,
        totals=_metrics(attempts),
        by_variant={
            name: _metrics([item for item in attempts if item.variant == FixtureVariant(name)])
            for name in config.variants
        },
    )
