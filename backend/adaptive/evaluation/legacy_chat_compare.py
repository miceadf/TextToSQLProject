"""Same-question comparison of the legacy chatbot and the QueryIR path (Phase 7-15).

Both paths answer a fixed developer-written question set over the team-built
legacy course view. Each answer is reduced to a set of offering keys
(subject code, section) and compared with a reference condition. The
questions and references are developer-authored and pending L1 review, so
every number here is provisional.

Default use performs no connection. ``--live`` sends each question to the two
loopback chat endpoints, runs the reference SQL read-only with the
server-owned DSN, and re-runs successful QueryIR answers with alternative
server policies from the same model output (no extra model call). Raw
material (legacy SQL, key sets) is written only under the Git-ignored
``.schema-adaptive/reports/phase7-15/``; stdout carries counts only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

QUESTION_SET_VERSION = "legacy-chat-compare-questions-v1"
RUN_VERSION = "legacy-chat-compare-run-v1"
SUMMARY_VERSION = "legacy-chat-compare-summary-v1"
DEFAULT_QUESTION_SET = Path(__file__).with_name("data") / "legacy-chat-compare-v1.json"
REPORT_DIR = Path(".schema-adaptive/reports/phase7-15")
LEGACY_URL = "http://127.0.0.1:8000/api/v1/chat/query"
QUERYIR_URL = "http://127.0.0.1:8123/api/adaptive/v1/chat/query"
DSN_ENV = "SCHEMA_ADAPTIVE_POSTGRES_DSN_LEGACY_VIEW"
DATABASE_ID = "legacy-view"
# Phase 7-17: the QueryIR side can also answer from the base-table profile.
QUERYIR_DATABASES = ("legacy-view", "legacy-base")
HTTP_TIMEOUT_SECONDS = 240.0

REFERENCE_PREFIX = "SELECT DISTINCT subject_code, section FROM v_course_info WHERE "
# String fields whose ``equals`` the relaxation variant turns into ``contains``.
RELAXED_STRING_FIELDS = frozenset(
    {"Course.name", "Department.name", "Instructor.name", "Course.category"}
)
ACCEPTABLE = frozenset({"EXACT", "CORRECT_SUBSET", "REFUSED"})
ARMS = ("legacy", "queryir", "queryir_limit200", "queryir_limit200_contains")

OfferingKey = tuple[str, str]


class Scope(str, Enum):
    ONTOLOGY = "ONTOLOGY"
    OUTSIDE_SCOPE = "OUTSIDE_SCOPE"
    UNSUPPORTED_FIELD = "UNSUPPORTED_FIELD"


class CompareCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=r"^q[0-9]{2}$")
    scope: Scope
    question: str = Field(min_length=1, max_length=200)
    reference_sql: str | None = None
    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_reference(self) -> "CompareCase":
        if self.scope is Scope.UNSUPPORTED_FIELD:
            if self.reference_sql is not None:
                raise ValueError("unsupported-field cases have no reference")
            return self
        sql = self.reference_sql
        if (
            sql is None
            or not sql.startswith(REFERENCE_PREFIX)
            or ";" in sql
            or "--" in sql
            or "/*" in sql
            or not _is_reference_shape(sql)
        ):
            raise ValueError("reference must be the fixed read-only key query")
        return self


def _is_reference_shape(sql: str) -> bool:
    """One DISTINCT key SELECT over the view with a WHERE clause and nothing else."""
    import sqlglot
    from sqlglot import exp

    try:
        parsed = sqlglot.parse(sql, read="postgres")
    except Exception:
        return False
    if len(parsed) != 1 or not isinstance(parsed[0], exp.Select):
        return False
    root = parsed[0]
    if not root.args.get("distinct") or not isinstance(root.args.get("where"), exp.Where):
        return False
    if [expression.sql() for expression in root.expressions] != ["subject_code", "section"]:
        return False
    tables = list(root.find_all(exp.Table))
    if len(tables) != 1 or tables[0].name != "v_course_info" or tables[0].args.get("db") is not None:
        return False
    if len(list(root.find_all(exp.Select))) != 1:
        return False
    forbidden = (exp.Union, exp.Subquery, exp.Join, exp.With, exp.Command)
    if any(isinstance(node, forbidden) for node in root.walk()):
        return False
    return all(root.args.get(name) is None for name in ("limit", "offset", "group", "having", "order"))


class QuestionSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    database: str
    authored_by: str
    cases: list[CompareCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_set(self) -> "QuestionSet":
        if self.version != QUESTION_SET_VERSION:
            raise ValueError("unknown question set version")
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate case id")
        return self


def load_question_set(path: str | Path = DEFAULT_QUESTION_SET) -> QuestionSet:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    text = Path(path).read_text(encoding="utf-8")
    return QuestionSet.model_validate(json.loads(text, object_pairs_hook=reject_duplicates))


# --- scoring -----------------------------------------------------------------


def score_answer(
    scope: Scope,
    *,
    answered: bool,
    keys: set[OfferingKey] | None,
    reference: set[OfferingKey] | None,
    refused: bool = False,
    truncated: bool = False,
) -> dict[str, Any]:
    """Classify one path's answer against the reference key set.

    ``answered`` means rows came back (possibly zero); ``refused`` means the
    path explicitly said it cannot answer; ``keys`` is ``None`` when rows
    lack offering keys; ``truncated`` means the path's row cap was reached,
    so a correct proper subset is explained by the cap rather than by a
    wrong condition.
    """
    if scope is Scope.UNSUPPORTED_FIELD:
        if refused:
            outcome = "REFUSED"
        elif not answered:
            outcome = "NO_ANSWER"
        elif keys is not None and not keys:
            outcome = "EMPTY_ANSWER"
        else:
            outcome = "ANSWERED_WITHOUT_BASIS"
        return {"outcome": outcome}
    if reference is None:
        raise ValueError("reference required")
    if not answered:
        return {"outcome": "NO_ANSWER", "reference_count": len(reference)}
    if keys is None:
        return {"outcome": "UNKEYED", "reference_count": len(reference)}
    hit = len(keys & reference)
    detail: dict[str, Any] = {
        "returned_count": len(keys),
        "reference_count": len(reference),
        "correct_count": hit,
        "precision": round(hit / len(keys), 4) if keys else None,
        "recall": round(hit / len(reference), 4) if reference else None,
    }
    if keys == reference:
        outcome = "EXACT"
    elif not keys:
        outcome = "EMPTY"
    elif keys < reference:
        outcome = "CORRECT_SUBSET" if truncated else "INCOMPLETE"
    else:
        outcome = "INCORRECT"
    return {"outcome": outcome, "truncated": truncated, **detail}


def relax_filters(filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """String ``equals`` on name-like fields becomes ``contains``; others unchanged."""
    relaxed = []
    for item in filters:
        item = dict(item)
        if item.get("operator") == "equals" and item.get("field") in RELAXED_STRING_FIELDS:
            item["operator"] = "contains"
        relaxed.append(item)
    return relaxed


def summarize(run: dict[str, Any]) -> dict[str, Any]:
    """Per-case outcomes and per-arm, per-scope counts (no row values or SQL).

    Scores are recomputed from the stored raw answers and reference keys, so
    a scoring fix never needs another model call.
    """
    per_case = []
    counts: dict[str, dict[str, Counter[str]]] = {arm: {} for arm in ARMS}
    for case in run["cases"]:
        scope = case["scope"]
        reference = _as_keys(case.get("reference_keys"))
        scores = score_case(
            CompareCase(
                case_id=case["case_id"],
                scope=Scope(scope),
                question=case["question"],
                reference_sql=None if reference is None else REFERENCE_PREFIX + "TRUE",
            ),
            case,
            reference,
        )
        row: dict[str, Any] = {"case_id": case["case_id"], "scope": scope}
        if reference is not None:
            row["reference_count"] = len(reference)
        for arm in ARMS:
            outcome = scores.get(arm, {}).get("outcome")
            if outcome is None:
                continue
            row[arm] = outcome
            if scores[arm].get("recall") is not None:
                row[f"{arm}_recall"] = scores[arm]["recall"]
            counts[arm].setdefault(scope, Counter())[outcome] += 1
            counts[arm].setdefault("ALL", Counter())[outcome] += 1
        per_case.append(row)
    totals = {
        arm: {
            scope: {
                "outcomes": dict(sorted(counter.items())),
                "acceptable": sum(n for key, n in counter.items() if key in ACCEPTABLE),
                "cases": sum(counter.values()),
            }
            for scope, counter in sorted(by_scope.items())
        }
        for arm, by_scope in counts.items()
    }
    return {"version": SUMMARY_VERSION, "run_label": run.get("label"), "cases": per_case, "totals": totals}


# --- live execution ------------------------------------------------------------

PostJson = Callable[[str, dict[str, Any], float], tuple[int, Any]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None  # a redirect is answered as the HTTP error it is


_OPENER = urllib.request.build_opener(_NoRedirect)


def _post_json(url: str, payload: dict[str, Any], timeout: float) -> tuple[int, Any]:
    if not url.startswith(("http://127.0.0.1:", "http://localhost:")):
        raise ValueError("loopback only")
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read())
        except Exception:
            return exc.code, None


def _key(code: Any, section: Any) -> OfferingKey:
    # NULL stays NULL, so it never collides with a literal "None" section.
    return (str(code), None if section is None else str(section))  # type: ignore[return-value]


def _sorted_keys(keys: set[OfferingKey]) -> list[OfferingKey]:
    return sorted(keys, key=lambda item: (item[0], item[1] is None, item[1] or ""))


def _keys_from_rows(rows: Any, code_key: str) -> set[OfferingKey] | None:
    if not isinstance(rows, list):
        return None
    keys: set[OfferingKey] = set()
    for row in rows:
        if not isinstance(row, dict) or row.get(code_key) is None or "section" not in row:
            return None
        keys.add(_key(row[code_key], row["section"]))
    return keys


_LIMIT = re.compile(r"\blimit\s+(\d+)\b", re.IGNORECASE)


def _sql_limit(sql: Any) -> int | None:
    """The last LIMIT in the executed legacy SQL (its default is 50, not a cap)."""
    if not isinstance(sql, str):
        return None
    found = _LIMIT.findall(sql)
    return int(found[-1]) if found else None


def ask_legacy(post: PostJson, question: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        status, payload = post(LEGACY_URL, {"query": question, "excludeCompletedCourses": False}, HTTP_TIMEOUT_SECONDS)
    except Exception:
        return {"answered": False, "error": "TRANSPORT", "seconds": round(time.perf_counter() - started, 2), "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}
    record: dict[str, Any] = {"http_status": status, "seconds": round(time.perf_counter() - started, 2), "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}
    if status != 200 or not isinstance(payload, dict) or payload.get("error") or payload.get("warning"):
        record.update({"answered": False, "error": "LEGACY_ERROR" if status == 200 else f"HTTP_{status}"})
        return record
    rows = payload.get("data")
    keys = _keys_from_rows(rows, "subject_code")
    row_count = len(rows) if isinstance(rows, list) else None
    limit = _sql_limit(payload.get("sql"))
    record.update({
        "answered": True,
        "sql": payload.get("sql"),
        "row_count": row_count,
        "limit": limit,
        "capped": limit is not None and row_count is not None and row_count >= limit,
        "keys": _sorted_keys(keys) if keys is not None else None,
    })
    return record


def ask_queryir(post: PostJson, question: str, database_id: str = DATABASE_ID) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        status, payload = post(QUERYIR_URL, {"database_id": database_id, "query": question}, HTTP_TIMEOUT_SECONDS)
    except Exception:
        return {"answered": False, "error": "TRANSPORT", "seconds": round(time.perf_counter() - started, 2), "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}
    record: dict[str, Any] = {"http_status": status, "seconds": round(time.perf_counter() - started, 2), "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}
    if status != 200 or not isinstance(payload, dict):
        code = (payload or {}).get("error", {}).get("code") if isinstance(payload, dict) else None
        record.update({"answered": False, "error": code or f"HTTP_{status}"})
        return record
    answer_status = payload.get("status")
    record["status"] = answer_status
    if answer_status == "SUCCEEDED":
        keys = _keys_from_rows(payload.get("rows"), "course_code")
        row_count, limit = payload.get("row_count"), payload.get("limit")
        record.update({
            "answered": True,
            "row_count": row_count,
            "limit": limit,
            "capped": isinstance(row_count, int) and isinstance(limit, int) and row_count >= limit,
            "filters": payload.get("understood", {}).get("filters", []),
            "keys": _sorted_keys(keys) if keys is not None else None,
        })
    elif answer_status == "UNSUPPORTED":
        record.update({"answered": False, "refused": True,
                       "unsupported": [item.get("field") for item in payload.get("unsupported", [])]})
    else:
        record.update({"answered": False, "failure_code": payload.get("failure_code")})
    return record


def rerun_with_policy(
    context: Any,
    question: str,
    filters: list[dict[str, Any]],
    *,
    limit: int,
    relax: bool,
    database_id: str = DATABASE_ID,
) -> dict[str, Any]:
    """Replay one model output under a different server policy (no model call)."""
    from ..api.chat_query import ChatQueryError, answer_chat_query

    ir = {
        "entity": "Course",
        "select": ["Course.name"],
        "filters": [
            {"field": item["field"], "operator": item["operator"], "value": item["value"]}
            for item in (relax_filters(filters) if relax else filters)
        ],
        "limit": limit,
    }

    class _Replay:
        def generate_ir(self, *_args: Any, **_hints: Any) -> dict[str, Any]:
            return ir

    try:
        answer = answer_chat_query(
            database_id=database_id, question=question, pg_contexts={database_id: context}, model=_Replay()
        )
    except ChatQueryError as exc:
        return {"answered": False, "error": exc.code.value}
    if answer["status"] != "SUCCEEDED":
        return {"answered": False, "failure_code": answer.get("failure_code")}
    keys = _keys_from_rows(answer["rows"], "course_code")
    return {
        "answered": True,
        "row_count": answer["row_count"],
        "limit": limit,
        "capped": answer["row_count"] >= limit,
        "keys": _sorted_keys(keys) if keys is not None else None,
    }


def reference_keys(cursor: Any, sql: str) -> set[OfferingKey]:
    cursor.execute(sql)
    return {_key(code, section) for code, section in cursor.fetchall()}


def _as_keys(value: Any) -> set[OfferingKey] | None:
    if value is None:
        return None
    return {_key(code, section) for code, section in value}


def score_case(case: CompareCase, record: dict[str, Any], reference: set[OfferingKey] | None) -> dict[str, Any]:
    scores = {}
    for arm in ARMS:
        answer = record.get(arm)
        if answer is None:
            continue
        scores[arm] = score_answer(
            case.scope,
            answered=bool(answer.get("answered")),
            keys=_as_keys(answer.get("keys")),
            reference=reference,
            refused=bool(answer.get("refused")),
            truncated=bool(answer.get("capped")),
        )
    return scores


def run_live(
    question_set: QuestionSet,
    *,
    post: PostJson,
    cursor: Any,
    context: Any,
    label: str,
    database_id: str = DATABASE_ID,
) -> dict[str, Any]:
    cases = []
    for case in question_set.cases:
        reference = reference_keys(cursor, case.reference_sql) if case.reference_sql else None
        record: dict[str, Any] = {
            "case_id": case.case_id,
            "scope": case.scope.value,
            "question": case.question,
            "reference_keys": _sorted_keys(reference) if reference is not None else None,
            "legacy": ask_legacy(post, case.question),
            "queryir": ask_queryir(post, case.question, database_id),
        }
        qir = record["queryir"]
        if qir.get("answered") and context is not None:
            record["queryir_limit200"] = rerun_with_policy(
                context, case.question, qir.get("filters", []), limit=200, relax=False,
                database_id=database_id,
            )
            record["queryir_limit200_contains"] = rerun_with_policy(
                context, case.question, qir.get("filters", []), limit=200, relax=True,
                database_id=database_id,
            )
        else:
            # Refusals and rejected model outputs do not depend on the
            # server's limit or string policy.
            record["queryir_limit200"] = dict(qir)
            record["queryir_limit200_contains"] = dict(qir)
        record["scores"] = score_case(case, record, reference)
        cases.append(record)
    return {
        "version": RUN_VERSION,
        "label": label,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "question_set": question_set.version,
        "queryir_database": database_id,
        "cases": cases,
    }


def _write_private(repository_root: Path, label: str, payload: dict[str, Any]) -> Path:
    directory = repository_root / REPORT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{label}.json"
    # Exclusive create: a concurrent run with the same label cannot overwrite.
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2))
    return path


def _print_totals(summary: dict[str, Any]) -> None:
    for arm, by_scope in summary["totals"].items():
        for scope, total in by_scope.items():
            print(json.dumps({"arm": arm, "scope": scope, **total}, ensure_ascii=False), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backend.adaptive.evaluation.legacy_chat_compare", allow_abbrev=False)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Strict-load the question set only.")
    mode.add_argument("--live", action="store_true", help="Ask both loopback endpoints.")
    mode.add_argument("--summarize", metavar="RUN_JSON", help="Summarize a stored private run.")
    parser.add_argument("--label", default=None)
    parser.add_argument("--queryir-database", choices=QUERYIR_DATABASES, default=DATABASE_ID)
    parser.add_argument("--repository-root", default=str(Path.cwd()))
    args = parser.parse_args(argv)
    question_set = load_question_set()
    if args.check:
        print(json.dumps({"mode": "check", "cases": len(question_set.cases)}), flush=True)
        return 0
    if args.summarize:
        run = json.loads(Path(args.summarize).read_text(encoding="utf-8"))
        summary = summarize(run)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 0
    label = args.label or "p715-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", label) is None:
        print(json.dumps({"status": "FAILED", "code": "INVALID_LABEL"}), flush=True)
        return 2
    from ..api.legacy_view_profile import SPECS, _load_profile

    database_id = args.queryir_database
    [spec] = [item for item in SPECS.values() if item.database_id == database_id]
    # The reference SQL runs on the same database the QueryIR side answers
    # from, so both sides are scored against one set of rows.
    dsn = os.environ.get(spec.dsn_env)
    if not dsn:
        print(json.dumps({"status": "FAILED", "code": "ENV_MISSING"}), flush=True)
        return 2
    if (Path(args.repository_root) / REPORT_DIR / f"{label}.json").exists():
        print(json.dumps({"status": "FAILED", "code": "LABEL_EXISTS"}), flush=True)
        return 2
    import psycopg2

    from ..api.postgres_runtime import build_validated_contexts
    from ..catalog.postgres import PostgresAdapter
    from ..harness.postgres import PostgresReadOnlyExecutor

    profile = _load_profile(args.repository_root, spec)
    context = build_validated_contexts(
        [profile],
        adapters_by_id={database_id: PostgresAdapter({database_id: profile.connection_config})},
        executors_by_id={database_id: PostgresReadOnlyExecutor({database_id: profile.connection_config})},
        expected_ontology_version=1,
    )[database_id]
    try:
        connection = psycopg2.connect(dsn, connect_timeout=5, options="-c statement_timeout=5000")
    except Exception:
        print(json.dumps({"status": "FAILED", "code": "REFERENCE_DB_UNAVAILABLE"}), flush=True)
        return 2
    connection.set_session(readonly=True)
    try:
        run = run_live(
            question_set, post=_post_json, cursor=connection.cursor(), context=context, label=label,
            database_id=database_id,
        )
    finally:
        connection.close()
    _write_private(Path(args.repository_root), label, run)
    print(json.dumps({"mode": "live", "label": label, "cases": len(run["cases"])}), flush=True)
    _print_totals(summarize(run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
