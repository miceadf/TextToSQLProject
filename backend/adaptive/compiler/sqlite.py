from __future__ import annotations

from typing import NoReturn

from backend.adaptive.harness.compiler import SQL_OPERATORS, quote_identifier
from backend.adaptive.harness.contracts import (
    CompiledQuery,
    HarnessErrorCode,
    HarnessStage,
)
from backend.adaptive.harness.errors import HarnessFailure
from backend.adaptive.ontology.contracts import Operator
from backend.adaptive.relations.contracts import (
    JoinPlan,
    JoinPlanStatus,
)

from .contracts import JoinCompilationRequest, TableAlias


class JoinCapableSQLiteCompiler:
    """Compile Query IR from a published mapping and resolved JOIN plan."""

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
        ]
        mappings = request.mapping.fields
        missing_fields = sorted(set(referenced_fields) - mappings.keys())
        if missing_fields:
            self._fail(
                HarnessErrorCode.MAPPING_MISSING,
                "published mapping is missing fields: " + ", ".join(missing_fields),
            )

        mapped_tables = {mappings[field].table for field in referenced_fields}
        if mapped_tables != set(plan.required_tables):
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

        projections = [
            f"{self._qualified(alias_by_table[mappings[field].table], mappings[field].column)} "
            f"AS {quote_identifier(field)}"
            for field in request.query_ir.select
        ]

        anchor_alias = alias_by_table[plan.anchor_table]
        sql = (
            f"SELECT {', '.join(projections)} "
            f"FROM {quote_identifier(plan.anchor_table)} AS {quote_identifier(anchor_alias)}"
        )
        sql += self._compile_joins(plan, alias_by_table)

        predicates: list[str] = []
        parameters: list[str | int | float | bool] = []
        for item in request.query_ir.filters:
            physical = mappings[item.field]
            predicates.append(
                f"{self._qualified(alias_by_table[physical.table], physical.column)} "
                f"{SQL_OPERATORS[item.operator]} ?"
            )
            value = item.value
            if item.operator is Operator.CONTAINS:
                value = f"%{value}%"
            parameters.append(value)

        if predicates:
            sql += " WHERE " + " AND ".join(predicates)
        sql += " LIMIT ?"
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

    def _compile_joins(self, plan: JoinPlan, alias_by_table: dict[str, str]) -> str:
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
                f" JOIN {quote_identifier(step.traversal_to)} "
                f"AS {quote_identifier(joined_alias)} ON {' AND '.join(predicates)}"
            )
        return "".join(clauses)

    @staticmethod
    def _qualified(alias: str, column: str) -> str:
        return f"{quote_identifier(alias)}.{quote_identifier(column)}"

    @staticmethod
    def _fail(code: HarnessErrorCode, message: str) -> NoReturn:
        raise HarnessFailure(code, message, stage=HarnessStage.SQL_COMPILED)
