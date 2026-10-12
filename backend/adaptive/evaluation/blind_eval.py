"""Blind evaluation of teammate-built databases (Phase 8-22).

A teammate imitates another school's course catalogue as a PostgreSQL
database, registers and reviews it on the admin screen, and fixes questions
with their expected course codes before anyone runs them (ADR-086). This
module checks that question file without printing it, asks every question
through the same chat API the screen uses, and keeps every run: the first
run is pinned to the question file's hash and later runs never overwrite it.

Files live under ``.schema-adaptive/blind-evals/<eval id>/`` (git-ignored):
``questions.json`` (the teammate's), ``questions.lock.json`` (written by the
first run) and ``run-NNN.json``. Records keep statuses, counts and the
understood conditions, never result rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

QUESTIONS_FORMAT = "blind-eval-questions-v1"
LOCK_FORMAT = "blind-eval-lock-v1"
RUN_FORMAT = "blind-eval-run-v1"
CHAT_PATH = "/api/adaptive/v1/chat/query"
CHAT_MODELS_PATH = "/api/adaptive/v1/chat/models"
REGISTRATIONS_PATH = "/api/adaptive/v1/postgres/registrations"
EVAL_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
DATABASE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
MAX_QUESTIONS = 200
MAX_CODES = 150
ROW_CAP = 200
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
DECLINE_STATUSES = frozenset({"UNSUPPORTED", "NOT_UNDERSTOOD"})

__all__ = ["BlindEvalError", "QuestionFile", "check", "grade", "load_questions", "main", "report", "run"]


class BlindEvalError(Exception):
    """A fixed code plus question positions; never question text."""

    def __init__(self, code: str, positions: list[int] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.positions = positions or []


class CodeAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    codes: tuple[str, ...] = Field(min_length=1, max_length=MAX_CODES)

    @field_validator("codes")
    @classmethod
    def _codes(cls, codes: tuple[str, ...]) -> tuple[str, ...]:
        if any(not code.strip() or len(code) > 64 for code in codes) or len(set(codes)) != len(codes):
            raise ValueError("codes must be distinct, non-blank and short")
        return codes


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    text: str = Field(min_length=1, max_length=500)
    expect: CodeAnswer | Literal["decline"]

    @field_validator("text")
    @classmethod
    def _text(cls, text: str) -> str:
        if not text.strip() or any(ord(char) < 0x20 and char not in "\t\n\r" for char in text):
            raise ValueError("text must be printable")
        return text


class QuestionFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["blind-eval-questions-v1"]
    database_id: str = Field(pattern=DATABASE_ID.pattern)
    author: str = Field(min_length=1, max_length=64)
    fixed_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    questions: tuple[Question, ...] = Field(min_length=1, max_length=MAX_QUESTIONS)

    @model_validator(mode="after")
    def _unique(self) -> "QuestionFile":
        ids = [question.id for question in self.questions]
        if len(set(ids)) != len(ids):
            raise ValueError("question ids must be distinct")
        return self


# --- files ----------------------------------------------------------------------------------


def eval_dir(root: str | Path, eval_id: str) -> Path:
    if not isinstance(eval_id, str) or EVAL_ID.fullmatch(eval_id) is None:
        raise BlindEvalError("INVALID_EVAL_ID")
    base = Path(root) / ".schema-adaptive" / "blind-evals"
    directory = base / eval_id
    # A link or junction must not lead the files out of the private folder.
    if directory.exists() and not directory.resolve().is_relative_to(base.resolve()):
        raise BlindEvalError("INVALID_EVAL_ID")
    return directory


def load_questions(root: str | Path, eval_id: str) -> tuple[QuestionFile, str]:
    """The validated question file and its SHA-256."""
    path = eval_dir(root, eval_id) / "questions.json"
    try:
        raw = path.read_bytes()
    except OSError:
        raise BlindEvalError("QUESTIONS_MISSING") from None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BlindEvalError("QUESTIONS_NOT_JSON") from None
    try:
        questions = QuestionFile.model_validate(data)
    except ValidationError as exc:
        positions = sorted(
            {
                int(error["loc"][1]) + 1
                for error in exc.errors()
                if len(error["loc"]) > 1 and error["loc"][0] == "questions" and isinstance(error["loc"][1], int)
            }
        )
        raise BlindEvalError("QUESTIONS_INVALID", positions) from None
    return questions, hashlib.sha256(raw).hexdigest()


def check(root: str | Path, eval_id: str) -> dict[str, Any]:
    """Counts only: safe to print before the evaluation."""
    questions, digest = load_questions(root, eval_id)
    declines = sum(1 for question in questions.questions if question.expect == "decline")
    return {
        "eval_id": eval_id,
        "database_id": questions.database_id,
        "questions": len(questions.questions),
        "code_answers": len(questions.questions) - declines,
        "declines": declines,
        "sha256": digest,
    }


# --- grading --------------------------------------------------------------------------------


def grade(question: Question, answer: dict[str, Any]) -> dict[str, Any]:
    """One record: never result rows, only statuses and counts."""
    status = answer.get("status")
    record: dict[str, Any] = {"id": question.id, "answer_status": status}
    understood = answer.get("understood")
    if isinstance(understood, dict) and isinstance(understood.get("filters"), list):
        record["understood"] = [
            {key: item.get(key) for key in ("field", "operator", "value")}
            for item in understood["filters"]
            if isinstance(item, dict)
        ]
    if isinstance(answer.get("failure_code"), str):
        record["failure_code"] = answer["failure_code"]
    if status is None:  # no answer at all: transport or HTTP error
        if isinstance(answer.get("http_status"), int):
            record["http_status"] = answer["http_status"]
        record["result"] = "ERROR"
        return record
    if question.expect == "decline":
        record["result"] = "PASS" if status in DECLINE_STATUSES else "FAIL"
        return record
    if status != "SUCCEEDED":
        record["result"] = "FAIL"
        return record
    rows = answer.get("rows") if isinstance(answer.get("rows"), list) else []
    record["row_count"] = len(rows)
    if len(rows) >= ROW_CAP:
        record["result"] = "CAPPED"
        return record
    got = {row.get("course_code") for row in rows if isinstance(row, dict)}
    expected = set(question.expect.codes)
    record["missing"] = len(expected - got)
    record["extra"] = len(got - expected)
    record["result"] = "PASS" if got == expected else "FAIL"
    return record


# --- running --------------------------------------------------------------------------------


def _base_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname not in LOOPBACK or parts.path not in ("", "/") or parts.query:
        raise BlindEvalError("BASE_URL_NOT_LOOPBACK")
    return url.rstrip("/")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None  # a redirect could lead off the machine


# No proxy and no redirects: questions only ever reach the loopback server
# (Luna 8-22 review).
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _http_json(method: str, url: str, body: dict[str, Any] | None = None, timeout: float = 300.0) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        method=method,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return {"http_status": exc.code}
    except Exception:
        return {}


def _lock(directory: Path, questions: QuestionFile, digest: str) -> None:
    """Pin the question file at the first run; refuse a changed file later."""
    path = directory / "questions.lock.json"
    if path.exists():
        try:
            lock = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            raise BlindEvalError("LOCK_UNREADABLE") from None
        if lock.get("sha256") != digest:
            raise BlindEvalError("QUESTIONS_CHANGED")
        return
    document = {
        "format": LOCK_FORMAT,
        "sha256": digest,
        "database_id": questions.database_id,
        "questions": len(questions.questions),
        "locked_at": datetime.now(timezone.utc).isoformat(),
    }
    with path.open("x", encoding="utf-8") as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2)


def _runs(directory: Path) -> dict[int, Path]:
    return {
        int(match.group(1)): item
        for item in directory.glob("run-*.json")
        if (match := re.fullmatch(r"run-(\d{3})\.json", item.name))
    }


def _reserve_run(directory: Path) -> tuple[int, Path]:
    """Claim the next run number before any question is asked, so two
    concurrent runs never share one (Luna 8-22 review)."""
    number = max(_runs(directory), default=0) + 1
    while True:
        path = directory / f"run-{number:03d}.json"
        try:
            with path.open("x", encoding="utf-8") as stream:
                json.dump({"format": RUN_FORMAT, "run": number, "status": "IN_PROGRESS"}, stream)
            return number, path
        except FileExistsError:
            number += 1


def _earlier_hashes(directory: Path) -> set[str]:
    hashes = set()
    for path in _runs(directory).values():
        try:
            digest = json.loads(path.read_text(encoding="utf-8")).get("questions_sha256")
        except Exception:
            raise BlindEvalError("RUN_UNREADABLE") from None
        if digest is not None:
            hashes.add(digest)
    return hashes


def _summary(results: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"total": len(results)}
    for key in ("PASS", "FAIL", "CAPPED", "ERROR"):
        summary[key.lower()] = sum(1 for item in results if item["result"] == key)
    return summary


def run(
    root: str | Path,
    eval_id: str,
    *,
    ask: Callable[[str, str], dict[str, Any]],
    registration: dict[str, Any] | None = None,
    commit: str | None = None,
    model: str | dict[str, Any] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    arm: str | None = None,
    sanitize: bool = False,
    run_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ask every question once and store the run without overwriting any."""
    directory = eval_dir(root, eval_id)
    questions, digest = load_questions(root, eval_id)
    # The lock and every earlier run pin the questions: deleting the lock does
    # not let a changed set pass as a rerun (Luna 8-22 review).
    if _earlier_hashes(directory) - {digest}:
        raise BlindEvalError("QUESTIONS_CHANGED")
    _lock(directory, questions, digest)
    number, run_path = _reserve_run(directory)
    started = datetime.now(timezone.utc).isoformat()
    results = []
    for position, question in enumerate(questions.questions, start=1):
        timer = time.perf_counter()
        try:
            answer = ask(questions.database_id, question.text)
        except Exception:
            answer = {}
        elapsed_ms = round((time.perf_counter() - timer) * 1000, 3)
        if not isinstance(answer, dict):
            answer = {}
        elapsed_ms = answer.get("elapsed_ms", elapsed_ms)
        record = grade(question, answer)
        record["elapsed_ms"] = elapsed_ms
        if sanitize:
            from .direct_sql_pg import safe_record
            answer.setdefault("elapsed_ms", record["elapsed_ms"])
            record = safe_record(answer, record)
        results.append(record)
        if progress is not None:
            progress(position, len(questions.questions), record["result"])
    document = {
        "format": RUN_FORMAT,
        "eval_id": eval_id,
        "run": number,
        "first_run": number == 1,
        "questions_sha256": digest,
        "database_id": questions.database_id,
        "registration": registration,
        "code_commit": commit,
        "model": model,
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "summary": _summary(results),
        "results": results,
    }
    if arm is not None:
        document["arm"] = arm
    if sanitize:
        from .direct_sql_pg import summary
        document["metrics"] = summary(results)
        if run_metadata is not None:
            document.update(run_metadata)
    run_path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return document


def report(root: str | Path, eval_id: str) -> list[dict[str, Any]]:
    """Summaries of every stored run, oldest first, with what looks wrong.

    Warnings (Luna 8-22 review): a missing run number (``MISSING_RUN``), a run
    over other questions than the first stored one (``QUESTIONS_DIFFER``),
    and a run that never finished (``IN_PROGRESS``).
    """
    directory = eval_dir(root, eval_id)
    runs = _runs(directory)
    rows: list[dict[str, Any]] = []
    first_hash = None
    for number in range(1, max(runs, default=0) + 1):
        path = runs.get(number)
        if path is None:
            rows.append({"run": number, "warnings": ["MISSING_RUN"]})
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            raise BlindEvalError("RUN_UNREADABLE") from None
        warnings = []
        if document.get("status") == "IN_PROGRESS":
            warnings.append("IN_PROGRESS")
        digest = document.get("questions_sha256")
        if first_hash is None:
            first_hash = digest
        elif digest is not None and digest != first_hash:
            warnings.append("QUESTIONS_DIFFER")
        rows.append(
            {
                "run": number,
                "arm": document.get("arm", "queryir"),
                "first_run": document.get("first_run"),
                "questions_sha256": digest,
                "code_commit": document.get("code_commit"),
                "model": document.get("model"),
                "started_at": document.get("started_at"),
                "summary": document.get("summary"),
                "metrics": document.get("metrics"),
                "warnings": warnings,
            }
        )
    return rows


# --- command line -----------------------------------------------------------------------------


def _git_commit(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=10, check=True
        )
    except Exception:
        return None
    commit = result.stdout.strip()
    try:
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=root, capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
    except Exception:
        dirty = ""
    return commit + ("+dirty" if dirty else "")


def _chat_model(base: str, wanted: str | None) -> dict[str, Any] | None:
    """The model the server answers with: its choice id and tag (Phase 9-08).

    Read from the server, not from ``.env``: the chat may answer with a
    model other than ``OLLAMA_MODEL``. ``wanted`` must be one the server
    offers; without it the server's default answers.
    """
    listing = _http_json("GET", base + CHAT_MODELS_PATH, timeout=30)
    if listing == {"http_status": 404} and wanted is None:
        return None  # a server from before Phase 9-08 has no such route
    models = listing.get("models") if isinstance(listing, dict) else None
    if not isinstance(models, list):
        # Any other failure: the run would not know which model answered
        # (Luna 9-08 review).
        raise BlindEvalError("UNKNOWN_MODEL" if wanted is not None else "MODELS_UNAVAILABLE")
    chosen = listing.get("default") if wanted is None else wanted
    for item in models:
        if isinstance(item, dict) and item.get("id") == chosen:
            return {"id": item.get("id"), "tag": item.get("tag")}
    if wanted is not None:
        raise BlindEvalError("UNKNOWN_MODEL")
    return None


def _registration(base: str, database_id: str) -> dict[str, Any] | None:
    listing = _http_json("GET", base + REGISTRATIONS_PATH, timeout=30)
    for item in listing.get("registrations", []) if isinstance(listing, dict) else []:
        if isinstance(item, dict) and item.get("database_id") == database_id:
            return {key: item.get(key) for key in ("status", "mapping_version")}
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.adaptive.evaluation.blind_eval")
    parser.add_argument("command", choices=("check", "run", "report"))
    parser.add_argument("eval_id")
    parser.add_argument("--base-url", default="http://127.0.0.1:8123")
    parser.add_argument("--root", default=".")
    parser.add_argument("--model", default=None, help="chat model choice id (e.g. general, legacy-tuned)")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    try:
        if args.command == "check":
            print(json.dumps(check(root, args.eval_id), ensure_ascii=False))
            return 0
        if args.command == "report":
            for row in sorted(report(root, args.eval_id), key=lambda row: (row.get("arm", "queryir"), row["run"])):
                warnings = ", ".join(row["warnings"])
                if "summary" not in row:
                    print(f"{row.get('arm', 'queryir')} run {row['run']:03d}: {warnings}")
                    continue
                label = "첫 실행" if row["first_run"] else f"재실행(commit {row['code_commit']})"
                suffix = f" [{warnings}]" if warnings else ""
                print(f"{row.get('arm', 'queryir')} run {row['run']:03d} {label}: {json.dumps(row['summary'], ensure_ascii=False)}{suffix}")
                if row.get("metrics") is not None:
                    print(json.dumps({"arm": row.get("arm", "queryir"), "metrics": row["metrics"]}))
            return 0
        base = _base_url(args.base_url)
        questions, _digest = load_questions(root, args.eval_id)

        model = _chat_model(base, args.model)
        extra = {} if args.model is None else {"model": args.model}

        def ask(database_id: str, text: str) -> dict[str, Any]:
            return _http_json("POST", base + CHAT_PATH, {"database_id": database_id, "query": text, **extra})

        document = run(
            root,
            args.eval_id,
            ask=ask,
            registration=_registration(base, questions.database_id),
            commit=_git_commit(root),
            model=model,
            progress=lambda done, total, result: print(f"{done}/{total} {result}", flush=True),
        )
        label = "첫 실행" if document["first_run"] else "재실행"
        print(f"run {document['run']:03d} ({label}): {json.dumps(document['summary'], ensure_ascii=False)}")
        return 0
    except BlindEvalError as exc:
        positions = f" questions {exc.positions}" if exc.positions else ""
        print(f"error: {exc.code}{positions}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
