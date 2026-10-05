"""L1 review tables: prepared by the developer, decided by people (Phase 9 entry).

L1 asks an independent reviewer to check, for every gold question, the Korean
meaning, the gold QueryIR and the expected result against the synthetic seed
alone (``docs/human-validation-checkpoints.md``). This module only prepares
that review: a readable Markdown table, a CSV with blank decision columns,
and a check of a filled CSV that writes a review record. It never changes a
case's ``review_status``; people approve gold, not this code.

Teammates' blind question files (Phase 8-22) get the same CSV inside their
private folder, and nothing about them is printed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from ..fixtures.seed import DEFAULT_SEED
from ..mapping.values import lookup_key
from ..ontology.loader import load_default_ontology
from .contracts import EvaluationSet
from .loader import load_evaluation_set

DATA = Path(__file__).with_name("data")
SETS = (
    ("개발 40", DATA / "fixture_v1_cases.yaml"),
    ("보류 30", DATA / "fixture_v1_independent_cases.yaml"),
)
DECISIONS = ("승인", "수정", "보류")
DECISION_COLUMNS = ("판정(승인/수정/보류)", "의견(수정·보류 사유)", "검토자", "검토일")
COLUMNS = (
    "사례 ID", "세트", "질문", "조건(gold)", "보여 줄 항목", "정답 행 수", "정답 행",
    "자동 확인 메모", "gold 지문", *DECISION_COLUMNS,
)
BLIND_COLUMNS = ("질문 ID", "질문", "정답", "gold 지문", *DECISION_COLUMNS)
# Cells Excel would run as formulas (Luna 9-01 review).
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
OPERATOR_TEXT = {
    "equals": "=", "not_equals": "≠", "greater_than": ">", "greater_than_or_equal": "≥",
    "less_than": "<", "less_than_or_equal": "≤", "contains": "포함",
}
RECORD_FORMAT = "l1-review-record-v1"

__all__ = ["build_rows", "check_filled", "main", "render_markdown", "sets_fingerprint"]


def _labels() -> dict[str, str]:
    return {name: field.label for name, field in load_default_ontology().fields.items()}


def _condition(item: Any, labels: dict[str, str]) -> str:
    label = labels.get(item.field, item.field)
    operator = item.operator.value
    if operator == "contains":
        return f'{label}에 "{item.value}" 포함'
    return f"{label} {OPERATOR_TEXT.get(operator, operator)} {item.value}"


def _flags(case: Any, labels: dict[str, str]) -> list[str]:
    """Things a reviewer should look at; facts about the gold, not verdicts."""
    flags = []
    expected = case.expected
    if expected.cardinality == 0:
        flags.append("빈 결과: 이 질문에 맞는 강좌가 정말 없어야 하는지")
    repeated = Counter(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in expected.rows)
    if any(count > 1 for count in repeated.values()):
        flags.append("같은 내용의 행이 여러 개(서로 다른 개설 강좌): 중복으로 볼지")
    question = lookup_key(case.question_ko)
    for item in case.gold_query_ir.filters:
        operator = item.operator.value
        if isinstance(item.value, str) and lookup_key(item.value) not in question:
            flags.append(f'질문 표현과 조건 값이 다름: {_condition(item, labels)}')
        if operator in ("greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal"):
            flags.append(f"경계값 포함 여부: {_condition(item, labels)}")
    if case.paraphrase_family:
        flags.append(f"같은 뜻 다른 표현 묶음: {case.paraphrase_family}")
    return flags


def build_rows(sets: list[tuple[str, EvaluationSet]], fingerprint: str = "") -> list[dict[str, str]]:
    labels = _labels()
    rows = []
    for set_name, evaluation_set in sets:
        for case in evaluation_set.cases:
            ir = case.gold_query_ir
            rows.append({
                "사례 ID": case.case_id,
                "세트": set_name,
                "질문": case.question_ko,
                "조건(gold)": ", ".join(_condition(item, labels) for item in ir.filters) or "조건 없음(전체)",
                "보여 줄 항목": ", ".join(labels.get(field, field) for field in ir.select),
                "정답 행 수": str(case.expected.cardinality),
                "정답 행": " / ".join(
                    ", ".join(f"{labels.get(key, key)}={value}" for key, value in row.items())
                    for row in case.expected.rows
                ) or "(없음)",
                "자동 확인 메모": "; ".join(_flags(case, labels)),
                "gold 지문": fingerprint,
                "판정(승인/수정/보류)": "",
                "의견(수정·보류 사유)": "",
                "검토자": "",
                "검토일": "",
            })
    return rows


def sets_fingerprint(paths: list[Path]) -> str:
    """A changed gold file invalidates earlier decisions (no automatic carry-over)."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _safe(text: str) -> str:
    return "'" + text if text.startswith(_FORMULA_START) else text


def _csv_text(columns: tuple[str, ...], rows: list[dict[str, str]]) -> str:
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(columns), lineterminator="\n")
    writer.writeheader()
    writer.writerows({key: _safe(value) for key, value in row.items()} for row in rows)
    return "\ufeff" + stream.getvalue()  # Excel opens UTF-8 with a BOM correctly


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(rows: list[dict[str, str]], fingerprint: str) -> str:
    seed_columns = (
        ("course_code", "과목 코드"), ("course_name", "과목명"), ("credits", "학점"), ("category", "이수 구분"),
        ("department_name", "학과명"), ("instructor_name", "교원명"), ("year", "연도"), ("semester", "학기"),
        ("section", "분반"), ("capacity", "정원"), ("enrolled", "수강 인원"), ("day", "요일"),
        ("start_time", "시작"), ("end_time", "종료"),
    )
    lines = [
        "# L1 검토 표: 합성 benchmark 질문 70개",
        "",
        "> 이 표는 AI가 준비했다. 판정은 사람(사용자 먼저, 그다음 팀원)이 한다. 승인 전 모든 사례는 `NEEDS_HUMAN_REVIEW`다.",
        "> 판정은 같은 폴더의 CSV(`fixture-v1-review.csv`)에 적고, `python -m backend.adaptive.evaluation.l1_review record <CSV>`로 확인·기록한다.",
        "",
        f"- 대상 gold 파일 지문(SHA-256): `{fingerprint}`. gold가 바뀌면 이전 판정은 이어받지 않는다.",
        "- 확인할 것(L1): 질문의 한국어 뜻, gold 조건과 보여 줄 항목, 정답 행과 행 수, 중복 행, 빈 결과. 아래 seed만 보고 판단한다.",
        "- 판정: **승인**(그대로 맞음), **수정**(의견에 고칠 내용), **보류**(판단이 어려움, 의견에 이유).",
        "- 자동 확인 메모는 살펴볼 곳일 뿐 판정이 아니다.",
        "",
        "## 합성 seed (개설 강좌 6개)",
        "",
        "| " + " | ".join(label for _key, label in seed_columns) + " |",
        "|" + "---|" * len(seed_columns),
    ]
    for offering in DEFAULT_SEED.offerings:
        values = offering.model_dump()
        lines.append("| " + " | ".join(_cell(str(values[key])) for key, _label in seed_columns) + " |")
    lines += ["", "## 질문", ""]
    shown = ("사례 ID", "세트", "질문", "조건(gold)", "보여 줄 항목", "정답 행 수", "정답 행", "자동 확인 메모")
    lines.append("| " + " | ".join(shown) + " |")
    lines.append("|" + "---|" * len(shown))
    for row in rows:
        lines.append("| " + " | ".join(_cell(row[key]) for key in shown) + " |")
    return "\n".join(lines) + "\n"


def check_filled(path: Path, built: list[dict[str, str]], columns: tuple[str, ...]) -> dict[str, Any]:
    """Validate a filled review CSV against the current build; returns counts and decisions.

    The sheet must have exactly the built columns, and every reviewed cell
    (question, gold, expected rows, gold fingerprint) must equal the current
    build: a sheet filled for other gold, or without the material, is
    refused (Luna 9-01 review). Raises ``ValueError`` naming the rows.
    """
    text = path.read_text(encoding="utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if tuple(reader.fieldnames or ()) != columns:
        raise ValueError("열이 검토 표와 다름")
    id_column = columns[0]
    expected_ids = [row[id_column] for row in built]
    by_id = {row[id_column]: row for row in built}
    content = [column for column in columns if column not in DECISION_COLUMNS]
    problems = []
    decisions: dict[str, dict[str, str]] = {}
    for number, row in enumerate(reader, start=2):
        case_id = (row.get(id_column) or "").strip()
        if case_id in by_id and any(row.get(column) != _safe(by_id[case_id][column]) for column in content):
            problems.append(f"{number}행: 질문·gold 내용이 현재 gold와 다름")
            continue
        decision = (row.get("판정(승인/수정/보류)") or "").strip()
        reviewer = (row.get("검토자") or "").strip()
        note = (row.get("의견(수정·보류 사유)") or "").strip()
        reviewed_on = (row.get("검토일") or "").strip()
        if case_id not in expected_ids or case_id in decisions:
            problems.append(f"{number}행: 사례 ID")
            continue
        if not decision:
            decisions[case_id] = {"decision": "미검토"}
            continue
        if decision not in DECISIONS:
            problems.append(f"{number}행: 판정은 승인/수정/보류 중 하나")
        if not reviewer:
            problems.append(f"{number}행: 검토자")
        if decision in ("수정", "보류") and not note:
            problems.append(f"{number}행: 수정·보류에는 의견")
        try:
            date.fromisoformat(reviewed_on)
        except ValueError:
            problems.append(f"{number}행: 검토일은 YYYY-MM-DD")
        decisions[case_id] = {"decision": decision, "reviewer": reviewer, "date": reviewed_on, "note": note}
    missing = [case_id for case_id in expected_ids if case_id not in decisions]
    if missing:
        problems.append(f"빠진 사례 {len(missing)}개")
    if problems:
        raise ValueError("; ".join(problems))
    counts = Counter(item["decision"] for item in decisions.values())
    return {"counts": dict(counts), "decisions": decisions}


# --- command line -----------------------------------------------------------------------------


def _load_sets() -> list[tuple[str, EvaluationSet]]:
    return [(name, load_evaluation_set(path)) for name, path in SETS]


def _blind_rows(root: Path, eval_id: str) -> tuple[Path, list[dict[str, str]]]:
    from .blind_eval import eval_dir, load_questions

    questions, digest = load_questions(root, eval_id)
    rows = []
    for question in questions.questions:
        answer = "거절" if question.expect == "decline" else "과목 코드 " + ", ".join(question.expect.codes)
        rows.append({
            "질문 ID": question.id, "질문": question.text, "정답": answer, "gold 지문": digest,
            "판정(승인/수정/보류)": "", "의견(수정·보류 사유)": "", "검토자": "", "검토일": "",
        })
    return eval_dir(root, eval_id), rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.adaptive.evaluation.l1_review")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--out", default="docs/l1-review")
    build.add_argument("--blind", help="teammate eval id: the CSV goes into its private folder, nothing is printed")
    build.add_argument("--root", default=".")
    record = sub.add_parser("record")
    record.add_argument("csv")
    record.add_argument("--blind")
    record.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    try:
        if args.command == "build" and args.blind:
            directory, rows = _blind_rows(root, args.blind)
            target = directory / "l1-review.csv"
            try:
                # exclusive create: an existing sheet or link is never written through
                with target.open("x", encoding="utf-8", newline="") as stream:
                    stream.write(_csv_text(BLIND_COLUMNS, rows))
            except FileExistsError:
                print("error: l1-review.csv already exists", file=sys.stderr)
                return 2
            print(f"{len(rows)} rows")
            return 0
        if args.command == "build":
            sets = _load_sets()
            fingerprint = sets_fingerprint([path for _name, path in SETS])
            rows = build_rows(sets, fingerprint)
            out = root / args.out
            out.mkdir(parents=True, exist_ok=True)
            (out / "fixture-v1-review.md").write_text(render_markdown(rows, fingerprint), encoding="utf-8")
            (out / "fixture-v1-review.csv").write_text(_csv_text(COLUMNS, rows), encoding="utf-8")
            print(f"{len(rows)} rows, gold {fingerprint[:12]}")
            return 0
        path = Path(args.csv)
        if args.blind:
            _directory, built = _blind_rows(root, args.blind)
            fingerprint = built[0]["gold 지문"]
            columns = BLIND_COLUMNS
        else:
            fingerprint = sets_fingerprint([path for _name, path in SETS])
            built = build_rows(_load_sets(), fingerprint)
            columns = COLUMNS
        result = check_filled(path, built, columns)
        document = {
            "format": RECORD_FORMAT,
            "gold_fingerprint": fingerprint,
            "source_csv_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "counts": result["counts"],
            "decisions": result["decisions"],
            "note": "사람의 판정 기록이다. gold의 review_status는 이 도구가 바꾸지 않는다.",
        }
        target = path.with_suffix(".record.json")
        target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result["counts"], ensure_ascii=False))
        return 0
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # blind file problems carry only a code
        code = getattr(exc, "code", type(exc).__name__)
        print(f"error: {code}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
