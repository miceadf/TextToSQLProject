from __future__ import annotations

from typing import Protocol

from .contracts import (
    CompiledQuery,
    HarnessErrorCode,
    HarnessStage,
    MappingProfile,
    Operator,
    QueryIR,
)
from .errors import HarnessFailure


SQL_OPERATORS = {
    Operator.EQUALS: "=",
    Operator.NOT_EQUALS: "!=",
    Operator.GREATER_THAN: ">",
    Operator.GREATER_THAN_OR_EQUAL: ">=",
    Operator.LESS_THAN: "<",
    Operator.LESS_THAN_OR_EQUAL: "<=",
    Operator.CONTAINS: "LIKE",
}


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


class QueryCompiler(Protocol):
    def compile(self, query_ir: QueryIR, mapping: MappingProfile) -> CompiledQuery:
        """Compile a validated query for the execution harness."""
        ...


class SQLiteQueryCompiler(QueryCompiler, Protocol):
    def compile(self, query_ir: QueryIR, mapping: MappingProfile) -> CompiledQuery:
        """Compile a validated query for the execution harness."""
        ...


class SingleTableSQLiteCompiler:
    """Compile validated QueryIR into parameterized single-table SQLite SQL."""

    def compile(self, query_ir: QueryIR, mapping: MappingProfile) -> CompiledQuery:
        referenced = [*query_ir.select, *(item.field for item in query_ir.filters)]
        tables = {mapping.fields[field].table for field in referenced}
        if len(tables) != 1:
            raise HarnessFailure(
                HarnessErrorCode.MULTI_TABLE_NOT_SUPPORTED,
                "minimal harness supports exactly one physical table",
                stage=HarnessStage.SQL_COMPILED,
            )

        table = next(iter(tables))
        projections = []
        for semantic_field in query_ir.select:
            physical = mapping.fields[semantic_field]
            projections.append(
                f"{quote_identifier(physical.column)} AS {quote_identifier(semantic_field)}"
            )

        predicates: list[str] = []
        parameters: list[str | int | float | bool] = []
        for item in query_ir.filters:
            physical = mapping.fields[item.field]
            predicates.append(
                f"{quote_identifier(physical.column)} {SQL_OPERATORS[item.operator]} ?"
            )
            value = item.value
            if item.operator is Operator.CONTAINS:
                value = f"%{value}%"
            parameters.append(value)

        sql = f"SELECT {', '.join(projections)} FROM {quote_identifier(table)}"
        if predicates:
            sql += " WHERE " + " AND ".join(predicates)
        sql += " LIMIT ?"
        parameters.append(query_ir.limit)

        return CompiledQuery(
            sql=sql,
            parameters=parameters,
            expected_columns=query_ir.select,
        )
