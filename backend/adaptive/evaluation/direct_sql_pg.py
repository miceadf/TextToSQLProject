"""Evaluation-only registered PostgreSQL SQL arm (ADR-100).

No runtime imports this module. Models are called once; reports retain only
fixed codes, counts, digests and timings. Importing it performs no I/O.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from ..api.value_hints import MAX_HINTS, MAX_INDEXED_VALUES, ValueIndex
from ..catalog.contracts import DatabaseCatalog
from ..harness.contracts import CompiledQuery, HarnessErrorCode
from ..harness.errors import HarnessFailure
from ..mapping.values import lookup_key
from .direct_sql import (
    DirectSqlOutcome, DirectSqlProviderError, DirectSqlRejected, DirectSqlRequest,
    _FORBIDDEN_TYPES, _ROOT_TYPES, extract_sql,
)

PROMPT_VERSION = "direct-sql-pg-v1"
DIRECT_SQL_PG_TEMPLATE = (
    "You are a strict SQL generator for a PostgreSQL database that stores a university course catalog.\n"
    "Schema, example values, and value hints:\n{context}\nRules:\n"
    "- Return exactly one read-only SELECT statement.\n"
    "- Use only the listed tables and columns; do not invent names.\n"
    "- Return the course code as the first column, AS course_code.\n"
    "- If a section column exists, also return it AS section.\n"
    "- Apply only conditions stated in the question.\n"
    "- If the question cannot be answered, return UNKNOWN.\n"
    "- Return SQL only, without Markdown or explanation.\n"
    "Question: {question}\nSQL:"
)
TEMPLATE_SHA256 = hashlib.sha256(DIRECT_SQL_PG_TEMPLATE.encode()).hexdigest()
ROW_CAP = 200
_ALLOWED_ANONYMOUS_FUNCTIONS = frozenset({
    "split_part", "string_to_array", "regexp_split_to_array", "array_to_string",
    "unnest", "btrim", "initcap", "to_char", "date_part",
})


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    default=str, separators=(",", ":")).encode()).hexdigest()


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@dataclass(frozen=True)
class PgContext:
    database_id: str
    schema: str
    catalog: DatabaseCatalog = field(repr=False)
    schema_text: str = field(repr=False)
    index: ValueIndex = field(repr=False)

    def render(self, question: str) -> str:
        lines = [self.schema_text, "Value hints:"]
        # ValueIndex returns a piece for containment matches. Expand that
        # piece into actual stored values before rendering equality hints.
        hints = self.index.find(question)
        count = 0
        for hint in hints:
            for value in self.index_values[hint.field]:
                if lookup_key(hint.value) not in lookup_key(value):
                    continue
                lines.append(f'{json.dumps(hint.text, ensure_ascii=False)} -> {hint.field} = '
                             + "'" + value.replace("'", "''") + "'")
                count += 1
                if count == MAX_HINTS:
                    return "\n".join(lines)
        return "\n".join(lines)

    @property
    def index_values(self) -> dict[str, tuple[str, ...]]:
        return self.index._values


def build_context(database_id: str, schema: str, catalog: DatabaseCatalog, reader: Any) -> PgContext:
    lines: list[str] = []
    indexed: dict[str, tuple[str, ...]] = {}
    for table in sorted(catalog.tables, key=lambda item: item.name):
        qualified = f"{quote(schema)}.{quote(table.name)}"
        lines.append(qualified + "(" + ", ".join(
            f"{quote(c.name)} {c.declared_type}" for c in sorted(table.columns, key=lambda c: c.name)) + ")")
        if table.primary_key:
            lines.append("  PRIMARY KEY (" + ", ".join(map(quote, table.primary_key)) + ")")
        for fk in sorted(table.foreign_keys, key=lambda f: (f.id, f.sequence)):
            lines.append(f"  FOREIGN KEY ({quote(fk.column)}) REFERENCES "
                         f"{quote(schema)}.{quote(fk.referenced_table)}({quote(fk.referenced_column or '')})")
        for column in sorted(table.columns, key=lambda c: c.name):
            try:
                samples = reader.read(database_id, table.name, column.name, 10, sample=True)
            except Exception:
                samples = None
            if isinstance(samples, (list, tuple)):
                samples = sorted({str(value) for value in samples if value is not None})[:10]
                lines.append(f"  {quote(column.name)} examples: " + json.dumps(
                    [value[:40] for value in samples], ensure_ascii=False))
            # PostgreSQL char/varchar/text (including their format_type forms).
            if not any(kind in column.declared_type.lower() for kind in ("char", "text")):
                continue
            try:
                values = reader.read(database_id, table.name, column.name, MAX_INDEXED_VALUES)
            except Exception:
                values = None
            if isinstance(values, (list, tuple)) and len(values) <= MAX_INDEXED_VALUES:
                indexed[f"{qualified}.{quote(column.name)}"] = tuple(sorted({
                    value for value in values if isinstance(value, str) and 0 < len(value) <= 200
                }))
    return PgContext(database_id, schema, catalog, "\n".join(lines), ValueIndex(indexed))


def build_prompt(context: PgContext, question: str) -> str:
    prefix, _, rest = DIRECT_SQL_PG_TEMPLATE.partition("{context}")
    between, _, suffix = rest.partition("{question}")
    return prefix + context.render(question) + between + question + suffix


def _identifier_name(identifier: exp.Identifier) -> str:
    return identifier.name if identifier.args.get("quoted") else identifier.name.lower()


def _visible_pg_cte_names(node: exp.Expression) -> set[str]:
    names: set[str] = set()
    parent = node.parent
    while parent is not None:
        for clause in parent.args.values():
            if isinstance(clause, exp.With):
                for cte in clause.expressions:
                    alias = cte.args.get("alias")
                    if isinstance(alias, exp.TableAlias) and isinstance(alias.this, exp.Identifier):
                        names.add(_identifier_name(alias.this))
        parent = parent.parent
    return names


def check_sql(sql: str, context: PgContext) -> None:
    # sqlglot can warn with raw command text for unsupported syntax.
    # Evaluation errors must remain fixed codes even on those parse paths.
    logger = logging.getLogger("sqlglot")
    quiet = lambda record: False
    logger.addFilter(quiet)
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except Exception:
        statements = []
    finally:
        logger.removeFilter(quiet)
    if not statements:
        raise DirectSqlRejected(DirectSqlOutcome.SQL_PARSE_ERROR)
    if len(statements) != 1:
        raise DirectSqlRejected(DirectSqlOutcome.SQL_MULTIPLE_STATEMENTS)
    root = statements[0]
    if not isinstance(root, _ROOT_TYPES):
        raise DirectSqlRejected(DirectSqlOutcome.SQL_NOT_SELECT)
    if any(root.find_all(*_FORBIDDEN_TYPES)) or root.find(exp.Lock):
        raise DirectSqlRejected(DirectSqlOutcome.SQL_FORBIDDEN_NODE)
    tables = {t.name for t in context.catalog.tables}
    for table in root.find_all(exp.Table):
        schema_identifier = table.args.get("db")
        schema = _identifier_name(schema_identifier) if isinstance(schema_identifier, exp.Identifier) else ""
        if (not isinstance(table.this, exp.Identifier) or table.catalog
                or schema in {"pg_catalog", "information_schema"}
                or (schema_identifier is not None and (not schema or schema != context.schema))):
            raise DirectSqlRejected(DirectSqlOutcome.SQL_UNKNOWN_TABLE)
        name = _identifier_name(table.this)
        if not schema and name in _visible_pg_cte_names(table):
            continue
        if name not in tables:
            raise DirectSqlRejected(DirectSqlOutcome.SQL_UNKNOWN_TABLE)
    for function in root.find_all(exp.Func):
        name = (function.name if isinstance(function, exp.Anonymous) else function.sql_name()).lower()
        if (function.find_ancestor(exp.Dot) is not None
                or (isinstance(function, exp.Anonymous) and name not in _ALLOWED_ANONYMOUS_FUNCTIONS)
                or name.startswith(("pg_", "lo_", "dblink", "query_to_xml", "txid_"))
                or name in {"current_setting", "set_config", "nextval", "setval"}):
            raise DirectSqlRejected(DirectSqlOutcome.SQL_FORBIDDEN_NODE)


def answer(context: PgContext, question: str, model: Any, executor: Any, *, request_id: str = "evaluation") -> dict[str, Any]:
    started = time.perf_counter()
    metadata: dict[str, Any] = {}

    def done(status: str, code: str, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return {"status": status, "failure_code": code, "rows": rows or [],
                "row_count": len(rows or []), "limit": ROW_CAP,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3), **metadata}

    try:
        output = model.generate_sql(DirectSqlRequest(request_id, context.database_id, question, build_prompt(context, question)))
    except DirectSqlProviderError as exc:
        return done("FAILED", exc.outcome.value)
    except Exception:
        return done("FAILED", "PROVIDER_ERROR")
    try:
        sql = extract_sql(output)
        metadata["sql_sha256"] = hashlib.sha256(sql.encode()).hexdigest()
        check_sql(sql, context)
    except DirectSqlRejected as exc:
        return done("UNSUPPORTED" if exc.outcome is DirectSqlOutcome.ABSTAIN else "FAILED", exc.outcome.value)
    try:
        # The executor always passes a tuple to psycopg2, including ().
        # Escape literal percent signs for that parameterized driver call.
        capped_sql = f"SELECT * FROM (\n{sql}\n) AS direct_sql_result LIMIT {ROW_CAP}"
        columns, rows = executor.execute(context.database_id, CompiledQuery(sql=capped_sql.replace("%", "%%"), parameters=[], expected_columns=[]))
    except HarnessFailure as exc:
        code = {HarnessErrorCode.EXECUTION_TIMEOUT: "EXECUTION_TIMEOUT",
                HarnessErrorCode.RESULT_TOO_LARGE: "RESULT_TOO_LARGE"}.get(exc.code, "EXECUTION_ERROR")
        return done("FAILED", code)
    except Exception:
        return done("FAILED", "EXECUTION_ERROR")
    if "course_code" not in columns:
        return done("FAILED", "RESULT_MISSING_COURSE_CODE")
    if len(columns) != len(set(columns)):
        return done("FAILED", "RESULT_DUPLICATE_COLUMNS")
    if len(rows) > ROW_CAP:
        return done("FAILED", "RESULT_TOO_LARGE")
    try:
        kept = [{key: row[key] for key in ("course_code", "section") if key in columns} for row in rows]
    except Exception:
        return done("FAILED", "RESULT_UNSUPPORTED_VALUE")
    metadata["result_sha256"] = fingerprint(kept)
    return done("SUCCEEDED", "CORRECT", kept)


def load_context(root: Path, database_id: str, env: dict[str, str]) -> tuple[PgContext, Any, Any]:
    from ..registration.postgres_registrations import load_postgres_registration, sources_from_env
    from ..harness.postgres import PostgresReadOnlyExecutor
    from ..harness.postgres_values import PostgresDistinctValueReader
    loaded = load_postgres_registration(repository_root=root, database_id=database_id)
    source = sources_from_env(env).get(loaded.manifest.source_id)
    if source is None or source.identity() != loaded.manifest.source_identity:
        raise ValueError("SOURCE_MISMATCH")
    config = source.config(loaded.manifest.postgres_schema)

    class OrderedReader(PostgresDistinctValueReader):
        def read(self, *args: Any, **kwargs: Any) -> Any:
            return super().read(*args, **kwargs, ordered=True)

    reader = OrderedReader({database_id: config})
    context = build_context(database_id, config.schema, loaded.catalog, reader)
    return context, PostgresReadOnlyExecutor({database_id: config}), config


def make_model(model_id: str, env: dict[str, str]) -> tuple[Any, str]:
    if model_id != "general":
        raise ValueError("INVALID_MODEL")
    from ..api.chat_models import DEFAULT_CHAT_MODEL
    from ..api.ollama_startup import parse_ollama_startup_config
    from .direct_sql_ollama import OllamaDirectSqlModel
    tag = env.get("CHAT_MODEL") or DEFAULT_CHAT_MODEL
    config = parse_ollama_startup_config(tag, (env.get("OLLAMA_BASE_URL") or "").rstrip("/") or None, "120")
    return OllamaDirectSqlModel(config), tag


def safe_record(answer: dict[str, Any], graded: dict[str, Any]) -> dict[str, Any]:
    allowed = {"id", "result", "answer_status", "row_count", "missing", "extra"}
    record = {key: value for key, value in graded.items() if key in allowed}
    for key in ("status", "failure_code", "row_count", "elapsed_ms", "sql_sha256", "result_sha256"):
        if key in answer:
            record[key] = answer[key]
    if answer.get("status") == "SUCCEEDED" and answer.get("failure_code") == "CORRECT":
        record["failure_code"] = "CORRECT" if graded.get("result") == "PASS" else "INCORRECT_RESULT"
    return record


def summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    for row in records:
        code = row.get("failure_code", "")
        status, result = row.get("status", row.get("answer_status")), row.get("result")
        counts["correct"] += result == "PASS"
        counts["silent_wrong"] += status == "SUCCEEDED" and result == "FAIL"
        counts["generation_failure"] += status == "NOT_UNDERSTOOD" or code in {"NON_STRING_OUTPUT", "EMPTY_OUTPUT", "AMBIGUOUS_OUTPUT", "SQL_PARSE_ERROR", "PROVIDER_ERROR", "PROVIDER_TIMEOUT"}
        counts["policy_rejection"] += code.startswith("SQL_") and code != "SQL_PARSE_ERROR"
        counts["execution_failure"] += code.startswith(("EXECUTION_", "RESULT_")) and code != "RESULT_TOO_LARGE"
        declined = code == "ABSTAIN" or status == "UNSUPPORTED"
        counts["decline_correct"] += declined and result == "PASS"
        counts["decline_wrong"] += declined and result != "PASS"
        counts["capped"] += result == "CAPPED" or code == "RESULT_TOO_LARGE" or row.get("row_count", 0) >= ROW_CAP
    latency = sorted(row["elapsed_ms"] for row in records if isinstance(row.get("elapsed_ms"), (int, float)))
    return {"total": len(records), **counts,
            "latency_median_ms": statistics.median(latency) if latency else None,
            "latency_p90_ms": latency[max(0, math.ceil(len(latency) * .9) - 1)] if latency else None}


def run_cases(cases: Any, ask: Any, grade: Any) -> list[dict[str, Any]]:
    records = []
    for case in cases:
        started = time.perf_counter()
        try:
            response = ask(case)
        except Exception:
            response = {"status": "FAILED", "failure_code": "PROVIDER_ERROR"}
        if not isinstance(response, dict):
            response = {"status": "FAILED", "failure_code": "PROVIDER_ERROR"}
        response.setdefault("elapsed_ms", round((time.perf_counter() - started) * 1000, 3))
        records.append(safe_record(response, grade(case, response)))
    return records


def private_directory(root: Path, out: str | None) -> Path:
    base = (root / ".schema-adaptive").resolve()
    if not base.is_relative_to(root.resolve()):
        raise ValueError("OUTPUT_NOT_PRIVATE")
    directory = Path(out).resolve() if out else base / "direct-sql-pg"
    if not directory.is_relative_to(base):
        raise ValueError("OUTPUT_NOT_PRIVATE")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def main(argv: list[str] | None = None) -> int:
    from . import blind_eval as blind
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--set", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--model", required=True, choices=["general", "legacy-tuned"])
    parser.add_argument("--out")
    parser.add_argument("--arm", choices=["direct-sql", "queryir"], default="direct-sql")
    parser.add_argument("--base-url")
    parser.add_argument("--reference-database")
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    if args.arm == "direct-sql" and args.model != "general":
        parser.error("direct-sql requires --model general")
    root = Path(args.root).resolve()
    try:
        if args.set not in {"legacy", "other_school"} and not args.set.startswith("blind:"):
            raise ValueError("INVALID_SET")
        if args.arm == "queryir" and not args.base_url:
            raise ValueError("BASE_URL_REQUIRED")
        # Validate private output before connecting or asking anything.
        directory = private_directory(root, args.out)
        questions = None
        if args.set.startswith("blind:"):
            eval_id = args.set.partition(":")[2]
            questions, _digest = blind.load_questions(root, eval_id)
            if questions.database_id != args.database:
                raise ValueError("DATABASE_MISMATCH")
            if args.out is not None and directory != blind.eval_dir(root, eval_id).resolve():
                raise ValueError("BLIND_OUTPUT_FIXED")
        if args.arm == "queryir":
            base = blind._base_url(args.base_url)
            chosen = blind._chat_model(base, args.model)
            tag = chosen["tag"] if chosen else None

            def ask_text(database_id: str, question: str) -> dict[str, Any]:
                return blind._http_json("POST", base + blind.CHAT_PATH,
                    {"database_id": database_id, "query": question, "model": args.model})
            config = None
        else:
            from ..api.dev_launcher import read_env_file
            env = {**read_env_file(root / "backend" / ".env"), **os.environ}
            context, executor, config = load_context(root, args.database, env)
            model, tag = make_model(args.model, env)

            def ask_text(database_id: str, question: str) -> dict[str, Any]:
                if database_id != context.database_id:
                    raise ValueError("DATABASE_MISMATCH")
                return answer(context, question, model, executor)
        metadata = {"id": args.model, "tag": tag}
        if questions is not None:
            document = blind.run(root, eval_id, ask=ask_text, model=metadata,
                arm=args.arm, sanitize=True,
                run_metadata={"prompt_version": PROMPT_VERSION if args.arm == "direct-sql" else None,
                              "template_sha256": TEMPLATE_SHA256 if args.arm == "direct-sql" else None},
                progress=lambda pos, total, code: print(json.dumps({"position": pos, "total": total, "result": code}), flush=True))
            print(json.dumps(document["metrics"]))
            return 0
        if args.set == "other_school":
            from ..fixtures.other_school import QUESTIONS, grade as other_grade
            cases = QUESTIONS[:12]

            def grader(case: Any, response: dict[str, Any]) -> dict[str, Any]:
                passed, _reason = other_grade(case, response)
                return {"id": case.qid, "result": "PASS" if passed else "FAIL"}
        else:
            from . import legacy_chat_compare as legacy
            cases = legacy.load_question_set().cases
            # References are always evaluated on this registration, read-only.
            # A QueryIR profile (e.g. legacy-view) has no registration, so its
            # references may come from a registration of the same database.
            if config is None or args.reference_database:
                from ..api.dev_launcher import read_env_file
                env = {**read_env_file(root / "backend" / ".env"), **os.environ}
                _context, _executor, config = load_context(root, args.reference_database or args.database, env)
            from ..harness.postgres import _production_connector
            connection = _production_connector(config)
            try:
                connection.set_session(readonly=True)
                cursor = connection.cursor()
                cursor.execute("SELECT pg_catalog.set_config('search_path', %s, true)", (config.schema,))
                references = {case.case_id: legacy.reference_keys(cursor, case.reference_sql)
                              if case.reference_sql else None for case in cases}
            finally:
                connection.close()

            def grader(case: Any, response: dict[str, Any]) -> dict[str, Any]:
                keys = legacy._keys_from_rows(response.get("rows"), "course_code")
                payload = {"answered": response.get("status") == "SUCCEEDED",
                           "refused": response.get("status") in blind.DECLINE_STATUSES,
                           "keys": legacy._sorted_keys(keys) if keys is not None else None,
                           "capped": response.get("row_count", 0) >= ROW_CAP}
                score = legacy.score_case(case, {"queryir": payload}, references[case.case_id])["queryir"]
                return {"id": case.case_id, "result": "PASS" if score["outcome"] in legacy.ACCEPTABLE else "FAIL"}
        # The same exclusive numbering as blind runs, never overwrite.
        number, path = blind._reserve_run(directory)
        records = run_cases(cases, lambda case: ask_text(args.database, case.text if args.set == "other_school" else case.question), grader)
        document = {"format": "direct-sql-pg-run-v1", "run": number, "arm": args.arm,
                    "model": metadata, "set": args.set, "database_id": args.database,
                    "prompt_version": PROMPT_VERSION if args.arm == "direct-sql" else None,
                    "template_sha256": TEMPLATE_SHA256 if args.arm == "direct-sql" else None,
                    "results": records, "metrics": summary(records)}
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(document["metrics"]))
        return 0
    except Exception:
        # No exception text, config, prompt or provider response reaches stderr.
        print(json.dumps({"status": "FAILED", "code": "EVALUATION_FAILED"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
