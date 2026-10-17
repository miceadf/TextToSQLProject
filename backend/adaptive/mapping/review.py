"""연결 제안을 검토 초안으로 바꾸고 사람의 승인·변경·제외 결정을 적용한다.

초안·catalog·ontology를 대조해 게시 문제를 검사하고 PublishedMappingProfile을 만든다.
기록 버전의 항목을 확인하되 현재 ontology에서 사용 중단된 항목은 게시 연결에서 제외한다.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..catalog import DatabaseCatalog
from ..ontology import DomainOntology, load_default_ontology
from .contracts import (
    MappingCandidate,
    MappingDraft,
    MappingProposal,
    PublishedFieldMapping,
    PublishedMappingProfile,
    ReviewDecision,
    ReviewedFieldMapping,
)
from .errors import (
    MappingPublishError,
    MappingWorkflowError,
    MappingWorkflowErrorCode,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MappingReviewService:
    def __init__(self, ontology: DomainOntology | None = None) -> None:
        self._retired_fields = (ontology or load_default_ontology()).retired_fields

    def _require_active_field(self, semantic_field: str) -> None:
        if semantic_field in self._retired_fields:
            self._unknown_field(semantic_field)

    def create_draft(self, proposal: MappingProposal) -> MappingDraft:
        fields: dict[str, ReviewedFieldMapping] = {}
        for semantic_field, candidates in sorted(proposal.candidates.items()):
            top = candidates[0] if candidates else None
            fields[semantic_field] = ReviewedFieldMapping(
                semantic_field=semantic_field,
                table=top.table if top else None,
                column=top.column if top else None,
                source_confidence=top.confidence if top else None,
            )
        return MappingDraft(
            database_id=proposal.database_id,
            schema_fingerprint=proposal.schema_fingerprint,
            ontology_version=proposal.ontology_version,
            fields=fields,
        )

    def approve(
        self,
        draft: MappingDraft,
        proposal: MappingProposal,
        semantic_field: str,
        *,
        reviewer: str,
        candidate_index: int = 0,
        reason: str | None = None,
    ) -> MappingDraft:
        self._validate_proposal(draft, proposal)
        self._require_active_field(semantic_field)
        candidates = proposal.candidates.get(semantic_field)
        if candidates is None:
            self._unknown_field(semantic_field)
        if candidate_index < 0 or candidate_index >= len(candidates):
            raise MappingWorkflowError(
                MappingWorkflowErrorCode.CANDIDATE_NOT_FOUND,
                f"candidate {candidate_index} not found for '{semantic_field}'",
            )
        candidate = candidates[candidate_index]
        entry = self._reviewed_entry(
            candidate,
            ReviewDecision.APPROVED,
            reviewer=reviewer,
            reason=reason,
        )
        return self._replace(draft, semantic_field, entry)

    def remap(
        self,
        draft: MappingDraft,
        semantic_field: str,
        *,
        table: str,
        column: str,
        reviewer: str,
        reason: str,
    ) -> MappingDraft:
        self._require_field(draft, semantic_field)
        self._require_active_field(semantic_field)
        entry = ReviewedFieldMapping(
            semantic_field=semantic_field,
            decision=ReviewDecision.REMAPPED,
            table=self._required_text(table, "table"),
            column=self._required_text(column, "column"),
            reviewer=self._required_text(reviewer, "reviewer"),
            reason=self._required_text(reason, "reason"),
            reviewed_at=utc_now(),
        )
        return self._replace(draft, semantic_field, entry)

    def reject(
        self,
        draft: MappingDraft,
        semantic_field: str,
        *,
        reviewer: str,
        reason: str,
    ) -> MappingDraft:
        return self._dismiss(
            draft,
            semantic_field,
            ReviewDecision.REJECTED,
            reviewer,
            reason,
        )

    def ignore(
        self,
        draft: MappingDraft,
        semantic_field: str,
        *,
        reviewer: str,
        reason: str,
    ) -> MappingDraft:
        self._require_active_field(semantic_field)
        return self._dismiss(
            draft,
            semantic_field,
            ReviewDecision.IGNORED,
            reviewer,
            reason,
        )

    def publish(
        self,
        draft: MappingDraft,
        catalog: DatabaseCatalog,
        ontology: DomainOntology,
        *,
        version: int,
        published_by: str,
    ) -> PublishedMappingProfile:
        issues = self.validate_for_publish(draft, catalog, ontology)
        if issues:
            raise MappingPublishError(issues)

        fields = {
            name: PublishedFieldMapping(
                table=entry.table or "",
                column=entry.column or "",
                reviewer=entry.reviewer or "",
                reason=entry.reason,
            )
            for name, entry in draft.fields.items()
            if name not in ontology.retired_fields
            if entry.decision in {ReviewDecision.APPROVED, ReviewDecision.REMAPPED}
        }
        return PublishedMappingProfile(
            database_id=draft.database_id,
            version=version,
            schema_fingerprint=draft.schema_fingerprint,
            ontology_version=draft.ontology_version,
            published_by=self._required_text(published_by, "published_by"),
            published_at=utc_now(),
            fields=fields,
        )

    def validate_for_publish(
        self,
        draft: MappingDraft,
        catalog: DatabaseCatalog,
        ontology: DomainOntology,
    ) -> list[str]:
        issues: list[str] = []
        if draft.database_id != catalog.database_id:
            issues.append("draft and catalog database_id differ")
        if draft.schema_fingerprint != catalog.schema_fingerprint:
            issues.append("schema fingerprint changed; regenerate proposal")
        if not ontology.accepts_version(draft.ontology_version):
            issues.append("ontology version changed; regenerate proposal")

        # Phase 8-18: a draft covers the fields of the version it was made under.
        ontology_fields = (
            ontology.fields_at(draft.ontology_version)
            if ontology.accepts_version(draft.ontology_version)
            else ontology.fields
        )
        active_entities = {
            semantic_field.split(".", 1)[0]
            for semantic_field, entry in draft.fields.items()
            if semantic_field not in ontology.retired_fields
            if entry.decision in {ReviewDecision.APPROVED, ReviewDecision.REMAPPED}
        }
        physical_columns = {
            (table.name, column.name)
            for table in catalog.tables
            for column in table.columns
        }
        used_physical: dict[tuple[str, str], str] = {}

        if not active_entities:
            issues.append("at least one field must be mapped")

        for semantic_field, entry in sorted(draft.fields.items()):
            if semantic_field in ontology.retired_fields:
                continue
            field = ontology_fields.get(semantic_field)
            if field is None:
                issues.append(f"unknown ontology field: {semantic_field}")
                continue
            if entry.decision is ReviewDecision.PENDING:
                issues.append(f"unreviewed field: {semantic_field}")
                continue
            entity_name = semantic_field.split(".", 1)[0]
            if field.required and entity_name in active_entities and entry.decision in {
                ReviewDecision.REJECTED,
                ReviewDecision.IGNORED,
            }:
                issues.append(f"required field is not mapped: {semantic_field}")
                continue
            if entry.decision in {ReviewDecision.APPROVED, ReviewDecision.REMAPPED}:
                physical = (entry.table or "", entry.column or "")
                if physical not in physical_columns:
                    issues.append(
                        f"physical column does not exist: {physical[0]}.{physical[1]}"
                    )
                    continue
                previous = used_physical.get(physical)
                if previous is not None:
                    issues.append(
                        f"physical column mapped twice: {physical[0]}.{physical[1]} "
                        f"({previous}, {semantic_field})"
                    )
                else:
                    used_physical[physical] = semantic_field

        for semantic_field, field in ontology_fields.items():
            if semantic_field in ontology.retired_fields:
                continue
            entity_name = semantic_field.split(".", 1)[0]
            if semantic_field not in draft.fields:
                issues.append(f"ontology field missing from draft: {semantic_field}")
                continue
            if (
                field.required
                and entity_name in active_entities
            ):
                entry = draft.fields[semantic_field]
                if entry.decision in {ReviewDecision.REJECTED, ReviewDecision.IGNORED}:
                    required_issue = f"required field is not mapped: {semantic_field}"
                    if required_issue not in issues:
                        issues.append(required_issue)
        return issues

    @staticmethod
    def _validate_proposal(draft: MappingDraft, proposal: MappingProposal) -> None:
        if (
            draft.database_id != proposal.database_id
            or draft.schema_fingerprint != proposal.schema_fingerprint
            or draft.ontology_version != proposal.ontology_version
        ):
            raise MappingWorkflowError(
                MappingWorkflowErrorCode.PROPOSAL_MISMATCH,
                "draft and proposal metadata differ",
            )

    @staticmethod
    def _reviewed_entry(
        candidate: MappingCandidate,
        decision: ReviewDecision,
        *,
        reviewer: str,
        reason: str | None,
    ) -> ReviewedFieldMapping:
        return ReviewedFieldMapping(
            semantic_field=candidate.semantic_field,
            decision=decision,
            table=candidate.table,
            column=candidate.column,
            source_confidence=candidate.confidence,
            reviewer=MappingReviewService._required_text(reviewer, "reviewer"),
            reason=reason.strip() if reason and reason.strip() else None,
            reviewed_at=utc_now(),
        )

    def _dismiss(
        self,
        draft: MappingDraft,
        semantic_field: str,
        decision: ReviewDecision,
        reviewer: str,
        reason: str,
    ) -> MappingDraft:
        self._require_field(draft, semantic_field)
        entry = ReviewedFieldMapping(
            semantic_field=semantic_field,
            decision=decision,
            reviewer=self._required_text(reviewer, "reviewer"),
            reason=self._required_text(reason, "reason"),
            reviewed_at=utc_now(),
        )
        return self._replace(draft, semantic_field, entry)

    @staticmethod
    def _replace(
        draft: MappingDraft,
        semantic_field: str,
        entry: ReviewedFieldMapping,
    ) -> MappingDraft:
        fields = dict(draft.fields)
        fields[semantic_field] = entry
        return draft.model_copy(update={"fields": fields})

    @staticmethod
    def _require_field(draft: MappingDraft, semantic_field: str) -> None:
        if semantic_field not in draft.fields:
            MappingReviewService._unknown_field(semantic_field)

    @staticmethod
    def _unknown_field(semantic_field: str) -> None:
        raise MappingWorkflowError(
            MappingWorkflowErrorCode.UNKNOWN_FIELD,
            f"unknown semantic field '{semantic_field}'",
        )

    @staticmethod
    def _required_text(value: str, name: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{name} must not be blank")
        return normalized
