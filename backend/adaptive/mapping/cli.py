"""파일 기반 연결 제안→검토→게시 작업을 제공하는 관리자 CLI.

명령 인자와 JSON catalog·제안·ontology를 받아 review 서비스와 저장소를 호출한다.
검토 상태·게시 결과를 JSON으로 출력하며 모델을 호출해 제안을 생성하지는 않는다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..catalog import DatabaseCatalog
from ..ontology import load_default_ontology, load_ontology
from .contracts import MappingProposal
from .errors import MappingWorkflowError, MappingWorkflowErrorCode
from .repository import MappingRepository
from .review import MappingReviewService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review and publish schema mappings")
    parser.add_argument(
        "--store",
        type=Path,
        default=Path(".schema-adaptive"),
        help="mapping state directory",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    initialize = commands.add_parser("init", help="import proposal and create draft")
    initialize.add_argument("--proposal", type=Path, required=True)

    list_command = commands.add_parser("list", help="list draft review states")
    list_command.add_argument("--database-id", required=True)

    show = commands.add_parser("show", help="show field and candidates")
    show.add_argument("--database-id", required=True)
    show.add_argument("--field", required=True)

    approve = commands.add_parser("approve", help="approve proposed candidate")
    _add_review_arguments(approve)
    approve.add_argument("--candidate", type=int, default=0)
    approve.add_argument("--reason")

    remap = commands.add_parser("remap", help="select physical column manually")
    _add_review_arguments(remap)
    remap.add_argument("--table", required=True)
    remap.add_argument("--column", required=True)
    remap.add_argument("--reason", required=True)

    reject = commands.add_parser("reject", help="reject mapping for field")
    _add_review_arguments(reject)
    reject.add_argument("--reason", required=True)

    ignore = commands.add_parser("ignore", help="exclude optional field")
    _add_review_arguments(ignore)
    ignore.add_argument("--reason", required=True)

    publish = commands.add_parser("publish", help="validate and publish mapping")
    publish.add_argument("--database-id", required=True)
    publish.add_argument("--catalog", type=Path, required=True)
    publish.add_argument("--ontology", type=Path)
    publish.add_argument("--reviewer", required=True)
    return parser


def _add_review_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database-id", required=True)
    parser.add_argument("--field", required=True)
    parser.add_argument("--reviewer", required=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository = MappingRepository(args.store)
    service = MappingReviewService()
    try:
        result = _run(args, repository, service)
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
        return 0
    except (MappingWorkflowError, ValidationError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2


def _run(
    args: argparse.Namespace,
    repository: MappingRepository,
    service: MappingReviewService,
) -> dict[str, Any]:
    if args.command == "init":
        proposal = MappingProposal.model_validate_json(
            args.proposal.read_text(encoding="utf-8")
        )
        if repository.has_draft(proposal.database_id):
            raise MappingWorkflowError(
                MappingWorkflowErrorCode.PROPOSAL_MISMATCH,
                "draft already exists; preserve or remove it explicitly",
            )
        proposal_path = repository.save_proposal(proposal)
        draft_path = repository.save_draft(service.create_draft(proposal))
        return {
            "database_id": proposal.database_id,
            "proposal": str(proposal_path),
            "draft": str(draft_path),
        }

    database_id = args.database_id
    draft = repository.load_draft(database_id)

    if args.command == "list":
        return {
            "database_id": database_id,
            "fields": [
                {
                    "semantic_field": name,
                    "decision": entry.decision.value,
                    "physical_field": (
                        f"{entry.table}.{entry.column}" if entry.table else None
                    ),
                }
                for name, entry in sorted(draft.fields.items())
            ],
        }

    if args.command == "show":
        entry = draft.fields.get(args.field)
        if entry is None:
            raise MappingWorkflowError(
                MappingWorkflowErrorCode.UNKNOWN_FIELD,
                f"unknown semantic field '{args.field}'",
            )
        proposal = repository.load_proposal(database_id)
        return {
            "database_id": database_id,
            "field": entry.model_dump(mode="json"),
            "candidates": [
                candidate.model_dump(mode="json")
                for candidate in proposal.candidates.get(args.field, [])
            ],
        }

    if args.command == "approve":
        proposal = repository.load_proposal(database_id)
        updated = service.approve(
            draft,
            proposal,
            args.field,
            reviewer=args.reviewer,
            candidate_index=args.candidate,
            reason=args.reason,
        )
        repository.save_draft(updated)
        return _entry_result(updated, args.field)

    if args.command == "remap":
        updated = service.remap(
            draft,
            args.field,
            table=args.table,
            column=args.column,
            reviewer=args.reviewer,
            reason=args.reason,
        )
        repository.save_draft(updated)
        return _entry_result(updated, args.field)

    if args.command in {"reject", "ignore"}:
        operation = service.reject if args.command == "reject" else service.ignore
        updated = operation(
            draft,
            args.field,
            reviewer=args.reviewer,
            reason=args.reason,
        )
        repository.save_draft(updated)
        return _entry_result(updated, args.field)

    if args.command == "publish":
        catalog = DatabaseCatalog.model_validate_json(
            args.catalog.read_text(encoding="utf-8")
        )
        ontology = load_ontology(args.ontology) if args.ontology else load_default_ontology()
        profile = service.publish(
            draft,
            catalog,
            ontology,
            version=repository.next_version(database_id),
            published_by=args.reviewer,
        )
        path = repository.save_published(profile)
        return {
            "database_id": database_id,
            "version": profile.version,
            "published": str(path),
        }

    raise ValueError(f"unsupported command: {args.command}")


def _entry_result(draft, semantic_field: str) -> dict[str, Any]:
    return {
        "database_id": draft.database_id,
        "field": draft.fields[semantic_field].model_dump(mode="json"),
    }
