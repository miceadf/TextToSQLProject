from __future__ import annotations

from typing import NoReturn

from backend.adaptive.harness.compiler import SQL_OPERATORS, quote_identifier
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
from backend.adaptive.mapping.transforms import (
    FieldTransform,
    render_postgres_transform,
    transform_operators,
)
from backend.adaptive.mapping.values import (
    ValueDictionary,
    bind_filter_value,
    contains_label_values,
    time_label_values,
)
from backend.adaptive.ontology.contracts import Operator
from backend.adaptive.relations.contracts import JoinPlan, JoinPlanStatus, PhysicalRelationGraph
from backend.adaptive.relations.errors import RelationGraphError
from backend.adaptive.relations.planner import plan_join_path

from .contracts import JoinCompilationRequest, MemberFilter, TableAlias

POSTGRES_PLACEHOLDER = "%s"


def postgres_contains_pattern(value: object) -> str:
    """``LIKE`` pattern for a literal substring.

    PostgreSQL's default LIKE escape character is backslash, so escaping
    ``\\``, ``%`` and ``_`` keeps a value such as ``전공_핵심`` from matching
    ``전공X핵심``.
    """
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class JoinCapablePostgresCompiler:
    """Compile QueryIR into parameterized PostgreSQL SQL.

    Synthetic runtime scope only: fake-connector verified, no live
    PostgreSQL server, CNU data, or Ollama call is used.
    """

    def compile(self, request: JoinCompilationRequest) -> CompiledQuery:
        join_result = request.join_result
        if join_result.status is JoinPlanStatus.AMBIGUOUS:
            self._fail(
                HarnessErrorCode.AMBIGUOUS_JOIN_PATH,
                "SQL compilation requires one resolved JOIN path",
            )

        plan = join_result.plan
        if plan is None:
            self._fail(
                HarnessErrorCode.INVALID_JOIN_PLAN,
                "resolved JOIN result does not contain a plan",
            )

        referenced_fields = [
            *request.query_ir.select,
            *(item.field for item in request.query_ir.filters),
            *(item.field for item in request.member_filters),
        ]
        mappings = request.mapping.fields
        missing_fields = sorted(set(referenced_fields) - mappings.keys())
        if missing_fields:
            self._fail(
                HarnessErrorCode.MAPPING_MISSING,
                "published mapping is missing fields: " + ", ".join(missing_fields),
            )

        mapped_tables = {mappings[field].table for field in referenced_fields}
        anchor_table = request.anchor_table
        if anchor_table is not None and plan.anchor_table != anchor_table:
            self._fail(
                HarnessErrorCode.INVALID_JOIN_PLAN,
                "JOIN plan does not start from the declared anchor table",
            )
        expected_tables = mapped_tables | ({anchor_table} if anchor_table else set())
        if expected_tables != set(plan.required_tables):
            self._fail(
                HarnessErrorCode.INVALID_JOIN_PLAN,
                "JOIN plan required tables do not match QueryIR field mappings",
            )
        if join_result.required_tables != plan.required_tables:
            self._fail(
                HarnessErrorCode.INVALID_JOIN_PLAN,
                "JOIN result and plan required tables do not match",
            )

        aliases = self._assign_aliases(plan)
        alias_by_table = {item.table: item.alias for item in aliases}
        if not mapped_tables.issubset(alias_by_table):
            self._fail(
                HarnessErrorCode.INVALID_JOIN_PLAN,
                "JOIN plan does not introduce every mapped table",
            )

        transforms = request.field_transforms

        def value_sql(field: str) -> str:
            physical = mappings[field]
            qualified = self._qualified(alias_by_table[physical.table], physical.column)
            transform = transforms.get(field)
            if transform is None:
                return qualified
            return render_postgres_transform(transform, qualified)

        projections = [
            f"{value_sql(field)} AS {quote_identifier(field)}"
            for field in request.query_ir.select
        ]

        anchor_alias = alias_by_table[plan.anchor_table]
        sql = (
            f"SELECT {'DISTINCT ' if request.distinct else ''}{', '.join(projections)} "
            f"FROM {quote_identifier(plan.anchor_table)} AS {quote_identifier(anchor_alias)}"
        )
        sql += self._compile_joins(
            plan, alias_by_table, keyword="LEFT JOIN" if anchor_table else "JOIN"
        )

        predicates: list[str] = []
        parameters: list[str | int | float | bool] = []
        for item in request.query_ir.filters:
            if item.field in transforms and item.operator not in transform_operators(
                transforms[item.field]
            ):
                self._fail(
                    HarnessErrorCode.INVALID_OPERATOR,
                    f"operator '{item.operator.value}' is not allowed on transformed field "
                    f"'{item.field}'",
                )
            try:
                periods = time_label_values(
                    item.field,
                    item.operator,
                    item.value,
                    value_dictionaries=request.value_dictionaries,
                )
            except ValueError:
                self._fail(
                    HarnessErrorCode.INVALID_VALUE_TYPE,
                    f"value for field '{item.field}' is not representable",
                )
            if periods is not None:
                # Phase 8-21: a period column compared through its period table.
                if not periods:
                    self._fail(
                        HarnessErrorCode.INVALID_VALUE_TYPE,
                        f"no stored value of '{item.field}' satisfies the condition",
                    )
                placeholders = ", ".join(POSTGRES_PLACEHOLDER for _ in periods)
                predicates.append(f"{value_sql(item.field)} IN ({placeholders})")
                parameters.extend(periods)
                continue
            labelled = contains_label_values(
                item.field,
                item.operator,
                item.value,
                value_dictionaries=request.value_dictionaries,
            )
            if labelled:
                # Phase 8-21: a coded column also matches through its labels.
                column = value_sql(item.field)
                placeholders = ", ".join(POSTGRES_PLACEHOLDER for _ in labelled)
                predicates.append(
                    f"({column} LIKE {POSTGRES_PLACEHOLDER} OR {column} IN ({placeholders}))"
                )
            else:
                predicates.append(
                    f"{value_sql(item.field)} "
                    f"{SQL_OPERATORS[item.operator]} {POSTGRES_PLACEHOLDER}"
                )
            try:
                value = bind_filter_value(
                    item.field,
                    item.value,
                    field_transforms=transforms,
                    value_dictionaries=request.value_dictionaries,
                )
            except ValueError:
                self._fail(
                    HarnessErrorCode.INVALID_VALUE_TYPE,
                    f"value for field '{item.field}' is not representable",
                )
            if item.operator is Operator.CONTAINS:
                value = postgres_contains_pattern(value)
            parameters.append(value)
            parameters.extend(labelled)

        # Phase 9-36: catalog membership predicates follow ordinary filters.
        for item in request.member_filters:
            column = value_sql(item.field)
            placeholders = ", ".join(POSTGRES_PLACEHOLDER for _ in item.values)
            predicates.append(f"{column} IN ({placeholders})")
            try:
                parameters.extend(bind_filter_value(
                    item.field, value, field_transforms=transforms,
                    value_dictionaries=request.value_dictionaries,
                ) for value in item.values)
            except ValueError:
                self._fail(HarnessErrorCode.INVALID_VALUE_TYPE, f"values for field '{item.field}' are not representable")

        if predicates:
            sql += " WHERE " + " AND ".join(predicates)
        sql += f" LIMIT {POSTGRES_PLACEHOLDER}"
        parameters.append(request.query_ir.limit)

        return CompiledQuery(
            sql=sql,
            parameters=parameters,
            expected_columns=request.query_ir.select,
        )

    def _assign_aliases(self, plan: JoinPlan) -> list[TableAlias]:
        aliases = [TableAlias(table=plan.anchor_table, alias="t0")]
        joined = {plan.anchor_table}
        for index, step in enumerate(plan.steps, start=1):
            relation = step.relation
            if relation.source_table == relation.target_table:
                self._fail(
                    HarnessErrorCode.SELF_JOIN_NOT_SUPPORTED,
                    f"self relation '{relation.relation_id}' is not supported",
                )
            if step.traversal_from not in joined:
                self._fail(
                    HarnessErrorCode.INVALID_JOIN_PLAN,
                    f"JOIN step '{relation.relation_id}' starts from an unjoined table",
                )
            if step.traversal_to in joined:
                self._fail(
                    HarnessErrorCode.INVALID_JOIN_PLAN,
                    f"JOIN step '{relation.relation_id}' repeats a joined table",
                )
            endpoints = {relation.source_table, relation.target_table}
            if {step.traversal_from, step.traversal_to} != endpoints:
                self._fail(
                    HarnessErrorCode.INVALID_JOIN_PLAN,
                    f"JOIN step '{relation.relation_id}' does not match its relation",
                )
            aliases.append(TableAlias(table=step.traversal_to, alias=f"t{index}"))
            joined.add(step.traversal_to)

        if not set(plan.required_tables).issubset(joined):
            self._fail(
                HarnessErrorCode.INVALID_JOIN_PLAN,
                "JOIN plan does not connect every required table",
            )
        return aliases

    def _compile_joins(
        self,
        plan: JoinPlan,
        alias_by_table: dict[str, str],
        *,
        keyword: str = "JOIN",
    ) -> str:
        clauses: list[str] = []
        for step in plan.steps:
            relation = step.relation
            try:
                source_alias = alias_by_table[relation.source_table]
                target_alias = alias_by_table[relation.target_table]
                joined_alias = alias_by_table[step.traversal_to]
            except KeyError:
                self._fail(
                    HarnessErrorCode.INVALID_JOIN_PLAN,
                    f"JOIN step '{relation.relation_id}' references an unknown table",
                )

            if not relation.column_pairs:
                self._fail(
                    HarnessErrorCode.INVALID_JOIN_PLAN,
                    f"JOIN relation '{relation.relation_id}' has no predicates",
                )
            predicates = [
                f"{self._qualified(source_alias, pair.source_column)} = "
                f"{self._qualified(target_alias, pair.target_column)}"
                for pair in relation.column_pairs
            ]
            clauses.append(
                f" {keyword} {quote_identifier(step.traversal_to)} "
                f"AS {quote_identifier(joined_alias)} ON {' AND '.join(predicates)}"
            )
        return "".join(clauses)

    @staticmethod
    def _qualified(alias: str, column: str) -> str:
        return f"{quote_identifier(alias)}.{quote_identifier(column)}"

    @staticmethod
    def _fail(code: HarnessErrorCode, message: str) -> NoReturn:
        raise HarnessFailure(code, message, stage=HarnessStage.SQL_COMPILED)


class SchemaAdaptivePostgresCompiler:
    """Adapt the PostgreSQL JOIN compiler to the execution-harness protocol."""

    def __init__(
        self,
        graph: PhysicalRelationGraph,
        mapping: PublishedMappingProfile,
        *,
        compiler: JoinCapablePostgresCompiler | None = None,
        field_transforms: dict[str, FieldTransform] | None = None,
        anchor_table: str | None = None,
        value_dictionaries: dict[str, ValueDictionary] | None = None,
    ) -> None:
        self._graph = graph.model_copy(deep=True)
        self._mapping = mapping.model_copy(deep=True)
        self._compiler = compiler or JoinCapablePostgresCompiler()
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
                anchor_table=self._anchor_table,
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
                field_transforms=dict(self._field_transforms),
                anchor_table=self._anchor_table,
                value_dictionaries=dict(self._value_dictionaries),
            )
        )

    def compile_catalog(self, query_ir: QueryIR, mapping: MappingProfile, *, member_filters: tuple[MemberFilter, ...] = (), distinct: bool = False) -> CompiledQuery:
        """Phase 9-36: compile the registry's private catalog request."""
        self._validate_runtime_mapping(mapping)
        try:
            member_filters = tuple(item if isinstance(item, MemberFilter) else MemberFilter.model_validate(item) for item in member_filters)
        except Exception:
            raise HarnessFailure(HarnessErrorCode.INVALID_VALUE_TYPE, "catalog member filters are invalid", stage=HarnessStage.SQL_COMPILED) from None
        if not isinstance(distinct, bool):
            raise HarnessFailure(HarnessErrorCode.INVALID_VALUE_TYPE, "catalog distinct option is invalid", stage=HarnessStage.SQL_COMPILED)
        if any(item.field not in self._mapping.fields for item in member_filters):
            raise HarnessFailure(HarnessErrorCode.MAPPING_MISSING, "catalog member filter field is not published", stage=HarnessStage.SQL_COMPILED)
        fields = [*query_ir.select, *(f.field for f in query_ir.filters), *(f.field for f in member_filters)]
        try:
            joined = plan_join_path(self._graph, self._mapping, fields, anchor_table=self._anchor_table)
        except RelationGraphError as exc:
            raise HarnessFailure(HarnessErrorCode.INVALID_JOIN_PLAN, f"JOIN planning failed ({exc.issue.code.value}): {exc.issue.message}", stage=HarnessStage.SQL_COMPILED) from exc
        return self._compiler.compile(JoinCompilationRequest(query_ir=query_ir, mapping=self._mapping, join_result=joined, field_transforms=dict(self._field_transforms), anchor_table=self._anchor_table, value_dictionaries=dict(self._value_dictionaries), member_filters=member_filters, distinct=distinct))

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
