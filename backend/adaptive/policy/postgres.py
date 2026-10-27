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
from backend.adaptive.mapping.transforms import FieldTransform
from backend.adaptive.mapping.values import ValueDictionary
from backend.adaptive.compiler.contracts import MemberFilter
from backend.adaptive.relations.contracts import PhysicalRelationGraph
from backend.adaptive.relations.errors import RelationGraphError
from backend.adaptive.relations.planner import plan_join_path

from .ast import AstReadOnlyPolicy
from .contracts import AstPolicyContext


class SchemaAdaptivePostgresPolicy:
    """Build strict postgres-dialect AST context for the execution harness.

    Synthetic runtime scope only: fake-connector verified, no live
    PostgreSQL server, CNU data, or Ollama call is used. ``%s``
    placeholders are validated as anonymous placeholders after sqlglot
    ``postgres``-dialect normalization.
    """

    DIALECT = "postgres"

    def __init__(
        self,
        graph: PhysicalRelationGraph,
        mapping: PublishedMappingProfile,
        *,
        policy: AstReadOnlyPolicy | None = None,
        field_transforms: dict[str, FieldTransform] | None = None,
        anchor_table: str | None = None,
        value_dictionaries: dict[str, ValueDictionary] | None = None,
    ) -> None:
        self._graph = graph.model_copy(deep=True)
        self._mapping = mapping.model_copy(deep=True)
        self._policy = policy or AstReadOnlyPolicy()
        self._value_dictionaries = dict(value_dictionaries or {})
        self._field_transforms = {
            field: FieldTransform(transform)
            for field, transform in (field_transforms or {}).items()
        }
        if not set(self._field_transforms).issubset(self._mapping.fields):
            raise ValueError("field transforms must target published mapping fields")
        if anchor_table is not None and anchor_table not in {
            field.table for field in self._mapping.fields.values()
        }:
            raise ValueError("anchor table must be a published mapping table")
        self._anchor_table = anchor_table
        if not set(self._value_dictionaries).issubset(self._mapping.fields) or (
            set(self._value_dictionaries) & set(self._field_transforms)
        ):
            raise ValueError("value dictionaries must target untransformed mapping fields")

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
                anchor_table=self._anchor_table,
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
                dialect=self.DIALECT,
                field_transforms=dict(self._field_transforms),
                anchor_table=self._anchor_table,
                value_dictionaries=dict(self._value_dictionaries),
            )
        )

    def validate_catalog(
        self, query: CompiledQuery, *, query_ir: QueryIR, mapping: MappingProfile,
        member_filters: tuple[MemberFilter, ...], distinct: bool,
    ) -> None:
        """Phase 9-36: validate catalog-only predicates and projection mode."""
        self._validate_runtime_mapping(mapping)
        fields = [*query_ir.select, *(item.field for item in query_ir.filters), *(item.field for item in member_filters)]
        try:
            join_result = plan_join_path(self._graph, self._mapping, fields, anchor_table=self._anchor_table)
        except RelationGraphError as exc:
            self._fail(f"AST policy planning failed ({exc.issue.code.value}): {exc.issue.message}")
        self._policy.validate(AstPolicyContext(
            query=query, query_ir=query_ir, mapping=self._mapping, join_result=join_result,
            dialect=self.DIALECT, field_transforms=dict(self._field_transforms),
            anchor_table=self._anchor_table, value_dictionaries=dict(self._value_dictionaries),
            member_filters=member_filters, distinct=distinct,
        ))

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
