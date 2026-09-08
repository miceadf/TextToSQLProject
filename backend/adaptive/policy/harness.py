from __future__ import annotations

from backend.adaptive.harness.contracts import (
    CompiledQuery,
    HarnessErrorCode,
    HarnessStage,
    MappingProfile,
    MappingStatus,
    QueryIR,
)
from backend.adaptive.harness.errors import HarnessFailure
from backend.adaptive.mapping.contracts import PublishedMappingProfile
from backend.adaptive.relations.contracts import PhysicalRelationGraph
from backend.adaptive.relations.errors import RelationGraphError
from backend.adaptive.relations.planner import plan_join_path

from .ast import AstReadOnlyPolicy
from .contracts import AstPolicyContext


class SchemaAdaptiveAstPolicy:
    """Build strict AST context for the existing execution harness."""

    def __init__(
        self,
        graph: PhysicalRelationGraph,
        mapping: PublishedMappingProfile,
        *,
        policy: AstReadOnlyPolicy | None = None,
    ) -> None:
        self._graph = graph.model_copy(deep=True)
        self._mapping = mapping.model_copy(deep=True)
        self._policy = policy or AstReadOnlyPolicy()

    def validate(
        self,
        query: CompiledQuery,
        *,
        query_ir: QueryIR | None = None,
        mapping: MappingProfile | None = None,
    ) -> None:
        if query_ir is None or mapping is None:
            self._fail("AST policy requires QueryIR and runtime mapping context")
        self._validate_runtime_mapping(mapping)
        semantic_fields = [
            *query_ir.select,
            *(item.field for item in query_ir.filters),
        ]
        try:
            join_result = plan_join_path(
                self._graph,
                self._mapping,
                semantic_fields,
            )
        except RelationGraphError as exc:
            self._fail(
                f"AST policy planning failed ({exc.issue.code.value}): "
                f"{exc.issue.message}"
            )
        self._policy.validate(
            AstPolicyContext(
                query=query,
                query_ir=query_ir,
                mapping=self._mapping,
                join_result=join_result,
            )
        )

    def _validate_runtime_mapping(self, mapping: MappingProfile) -> None:
        expected_fields = {
            semantic_field: (field.table, field.column)
            for semantic_field, field in self._mapping.fields.items()
        }
        actual_fields = {
            semantic_field: (field.table, field.column)
            for semantic_field, field in mapping.fields.items()
        }
        if (
            mapping.status is not MappingStatus.PUBLISHED
            or mapping.database_id != self._mapping.database_id
            or mapping.version != self._mapping.version
            or actual_fields != expected_fields
        ):
            self._fail("runtime mapping does not match the AST policy Published Mapping")

    @staticmethod
    def _fail(message: str) -> None:
        raise HarnessFailure(
            HarnessErrorCode.UNSAFE_SQL,
            message,
            stage=HarnessStage.SQL_VALIDATED,
        )
