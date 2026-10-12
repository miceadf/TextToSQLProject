"""AI mapping proposal check on the developer's synthetic schools (Phase 8-23).

Runs the same proposal path as screen registration (rules, sampled catalog,
local model) on the five AI-made development schemas of Phase 8-17/8-21 and
scores the first candidate per field against the developer's answer key.
Columns joined by a foreign key hold the same value (``gangjwa.gm_cd`` and
``gyogwamok.gm_cd``), so either counts as right.

These schemas and answers are the developer's own: the numbers show
progress during development, never adaptation to unseen schools (ADR-086).
Only the synthetic database (loopback, ``schema_adaptive_synthetic``) is
read, through ``SCHEMA_ADAPTIVE_SYNTHETIC_POSTGRES_DSN``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# field -> (table, column), or None when the school has no such column
Answer = dict[str, tuple[str, str] | None]

_OTHER: Answer = {
    "Course.category": ("isu_gubun", "isu_nm"), "Course.code": ("gyogwamok", "gm_cd"),
    "Course.credits": ("gyogwamok", "hakjeom"), "Course.name": ("gyogwamok", "gm_nm"),
    "Course.target_grade": ("gyogwamok", "suggang_hangnyeon"), "CourseOffering.capacity": ("gangjwa", "jeongwon"),
    "CourseOffering.enrolled": ("gangjwa", "sugang_inwon"), "CourseOffering.section": ("gangjwa", "ban_no"),
    "CourseOffering.semester": ("gangjwa", "hakgi_cd"), "CourseOffering.year": ("gangjwa", "nyeondo"),
    "Department.code": ("hakgwa", "hg_cd"), "Department.name": ("hakgwa", "hg_nm"),
    "Instructor.name": ("gyosu", "gs_nm"), "Schedule.day": ("siganpyo", "yoil"),
    "Schedule.end_time": ("siganpyo", "si_ed"), "Schedule.start_time": ("siganpyo", "si_st"),
}
SCHOOLS: dict[str, Answer] = {
    "other_school": _OTHER,
    "wide_school": {
        name: ("sugang_pyeollam", column)
        for name, column in {
            "Course.category": "isu_gubun", "Course.code": "gwamok_bunho", "Course.credits": "hakjeom",
            "Course.name": "gwamok_myeong", "Course.target_grade": "daesang_hangnyeon",
            "CourseOffering.capacity": "jeongwon", "CourseOffering.enrolled": "sugang_inwon",
            "CourseOffering.section": "bunban", "CourseOffering.semester": "hakgi", "CourseOffering.year": "nyeondo",
            "Department.code": "hakgwa_cd", "Department.name": "hakgwa_myeong", "Instructor.name": "damdang_gyosu",
            "Schedule.day": "yoil", "Schedule.end_time": "jongryo", "Schedule.start_time": "sijak",
        }.items()
    },
    "code_school": {
        **_OTHER,
        "Course.category": ("gyogwamok", "isu_cd"),
        "Schedule.day": ("siganpyo", "yoil_cd"),
    },
    "period_school": {
        **_OTHER,
        "Schedule.start_time": ("siganpyo", "gyosi_from"),
        "Schedule.end_time": ("siganpyo", "gyosi_to"),
    },
    "packed_school": {
        **_OTHER,
        "Schedule.day": ("gangjwa", "sigan_txt"),
        "Schedule.start_time": None,
        "Schedule.end_time": None,
    },
}

__all__ = ["SCHOOLS", "equivalents", "main", "score"]


def equivalents(catalog: Any) -> dict[tuple[str, str], set[tuple[str, str]]]:
    """Columns holding the same value through a foreign key (both directions, transitively)."""
    links: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for table in catalog.tables:
        for key in table.foreign_keys:
            if not key.referenced_column:
                continue
            a, b = (table.name, key.column), (key.referenced_table, key.referenced_column)
            links.setdefault(a, set()).add(b)
            links.setdefault(b, set()).add(a)
    closed: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for start in links:
        seen, stack = {start}, [start]
        while stack:
            for nxt in links.get(stack.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        closed[start] = seen
    return closed


def score(proposal: Any, answer: Answer, catalog: Any) -> dict[str, Any]:
    """First candidate per field against the answer; FK-equivalent columns count as right."""
    same = equivalents(catalog)
    right, wrong = 0, []
    for name, expected in sorted(answer.items()):
        candidates = proposal.candidates.get(name, [])
        first = (candidates[0].table, candidates[0].column) if candidates else None
        if expected is None:
            ok = first is None
        else:
            ok = first is not None and (first == expected or first in same.get(expected, set()))
        if ok:
            right += 1
        else:
            wrong.append({"field": name, "expected": expected and ".".join(expected), "got": first and ".".join(first)})
    return {"right": right, "total": len(answer), "wrong": wrong}


def _propose(schema: str, dsn: str, model: Callable[..., Any] | None) -> tuple[Any, Any]:
    from ..catalog.postgres import PostgresAdapter, PostgresConnectionConfig
    from ..harness.postgres_values import PostgresDistinctValueReader
    from ..mapping.ai_proposal import propose_upload_mapping
    from ..mapping.rules import RuleBasedSchemaAligner
    from .ontology_version import recorded_ontology
    from ..registration.postgres_registrations import _sampled_catalog

    database_id = schema.replace("_", "-")
    config = PostgresConnectionConfig(dsn, schema=schema)
    catalog = PostgresAdapter({database_id: config}).inspect(database_id)
    ontology = recorded_ontology()
    rules = RuleBasedSchemaAligner().align(catalog, ontology)
    if model is None:
        return rules, catalog
    sampled = _sampled_catalog(catalog, PostgresDistinctValueReader({database_id: config}), database_id)
    proposal = None if sampled is None else propose_upload_mapping(sampled, ontology, rules, model=model)
    return (proposal or rules), catalog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.adaptive.evaluation.school_mapping_check")
    parser.add_argument("--label", required=True)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--rules-only", action="store_true")
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)
    from ..harness.postgres_live_smoke import validate_dsn
    from ..mapping.ai_proposal import DEFAULT_MAPPING_MODEL, OllamaJsonModel

    dsn = os.environ.get("SCHEMA_ADAPTIVE_SYNTHETIC_POSTGRES_DSN", "")
    validate_dsn(dsn)  # loopback, synthetic database only
    model = None if args.rules_only else OllamaJsonModel(args.model or DEFAULT_MAPPING_MODEL)
    runs = []
    for repeat in range(1, args.repeat + 1):
        for schema, answer in SCHOOLS.items():
            proposal, catalog = _propose(schema, dsn, model)
            result = score(proposal, answer, catalog)
            runs.append({"repeat": repeat, "schema": schema, **result})
            print(f"{repeat} {schema}: {result['right']}/{result['total']} "
                  + ", ".join(f"{w['field']}={w['got']}" for w in result["wrong"]), flush=True)
    total = sum(run["right"] for run in runs)
    print(f"{args.label}: {total}/{sum(run['total'] for run in runs)}")
    out = Path(".schema-adaptive") / "reports" / "school-mapping-check"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.label}.json").write_text(json.dumps({
        "label": args.label, "at": datetime.now(timezone.utc).isoformat(),
        "model": None if model is None else (args.model or DEFAULT_MAPPING_MODEL), "runs": runs,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
