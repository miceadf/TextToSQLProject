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

from .contracts import JoinCompilationRequest
from .sqlite import JoinCapableSQLiteCompiler


class SchemaAdaptiveSQLiteCompiler:
    """Adapt the JOIN compiler to the existing execution-harness protocol."""

    def __init__(
        self,
        graph: PhysicalRelationGraph,
        mapping: PublishedMappingProfile,
        *,
        compiler: JoinCapableSQLiteCompiler | None = None,
    ) -> None:
        self._graph = graph.model_copy(deep=True)
        self._mapping = mapping.model_copy(deep=True)
        self._compiler = compiler or JoinCapableSQLiteCompiler()

    def compile(
        self,
        query_ir: QueryIR,
        mapping: MappingProfile,
    ) -> CompiledQuery:
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
            raise HarnessFailure(
                HarnessErrorCode.INVALID_JOIN_PLAN,
                f"JOIN planning failed ({exc.issue.code.value}): {exc.issue.message}",
                stage=HarnessStage.SQL_COMPILED,
            ) from exc

        return self._compiler.compile(
            JoinCompilationRequest(
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
            raise HarnessFailure(
                HarnessErrorCode.INVALID_JOIN_PLAN,
                "runtime mapping does not match the compiler's Published Mapping",
                stage=HarnessStage.SQL_COMPILED,
            )
