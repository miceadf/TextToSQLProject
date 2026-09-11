from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from .contracts import DatasetRecord
from .sql_contracts import SqlAnalysisIssue, SqlAnalysisIssueCode


_INTEGER_LITERAL = re.compile(r"\d+\Z")
_CANONICAL_HASH_DOMAIN = b"text-to-sql:canonical-sql:v1\0"
_SHAPE_HASH_DOMAIN = b"text-to-sql:sql-shape:v1\0"


@dataclass(frozen=True)
class CanonicalizedSql:
    """Internal successful parse result.

    ``canonical_sql`` and ``shape_sql`` are analysis artifacts only. They must
    never be treated as executable or corrected ground-truth SQL.
    """

    expression: exp.Expression
    canonical_sql: str
    shape_sql: str
    canonical_hash: str
    shape_hash: str


def parse_and_canonicalize(
    record: DatasetRecord,
    dialect: str = "postgres",
) -> tuple[CanonicalizedSql | None, SqlAnalysisIssue | None]:
    """Parse one dataset SQL value without executing or mutating it."""

    raw_sql = record.sql
    if raw_sql == "":
        return None, _issue(
            record,
            SqlAnalysisIssueCode.EMPTY_SQL,
            "SQL is empty.",
        )
    if raw_sql.strip() == "":
        return None, _issue(
            record,
            SqlAnalysisIssueCode.WHITESPACE_ONLY_SQL,
            "SQL contains only whitespace.",
        )

    try:
        statements = sqlglot.parse(raw_sql, read=dialect)
    except (ParseError, ValueError, TypeError):
        return None, _issue(
            record,
            SqlAnalysisIssueCode.PARSE_ERROR,
            "SQL could not be parsed with the configured dialect.",
        )
    except Exception:
        # Third-party parser exceptions can contain source fragments. Keep the
        # public issue fixed and free of SQL and literal values.
        return None, _issue(
            record,
            SqlAnalysisIssueCode.PARSE_ERROR,
            "SQL parsing failed with the configured dialect.",
        )

    parsed_statements = [statement for statement in statements if statement is not None]
    if len(parsed_statements) != 1:
        return None, _issue(
            record,
            SqlAnalysisIssueCode.MULTIPLE_STATEMENTS,
            "Exactly one SQL statement is required.",
        )

    expression = parsed_statements[0]
    if not _is_select_query(expression):
        return None, _issue(
            record,
            SqlAnalysisIssueCode.NON_SELECT_STATEMENT,
            "Only SELECT queries are supported for dataset analysis.",
        )

    try:
        canonical_expression = _without_comments(expression)
        canonical_sql = canonical_expression.sql(
            dialect=dialect,
            pretty=False,
            comments=False,
        )
        reparsed = sqlglot.parse(canonical_sql, read=dialect)
    except Exception:
        return None, _issue(
            record,
            SqlAnalysisIssueCode.CANONICALIZATION_ERROR,
            "SQL canonicalization failed with the configured dialect.",
        )

    reparsed_statements = [statement for statement in reparsed if statement is not None]
    if len(reparsed_statements) != 1 or (
        canonical_expression != _without_comments(reparsed_statements[0])
    ):
        return None, _issue(
            record,
            SqlAnalysisIssueCode.ROUND_TRIP_MISMATCH,
            "Canonical SQL did not round-trip to the same AST.",
        )

    try:
        shape_expression = canonical_expression.copy().transform(
            _redact_literal,
            copy=False,
        )
        shape_sql = shape_expression.sql(
            dialect=dialect,
            pretty=False,
            comments=False,
        )
    except Exception:
        return None, _issue(
            record,
            SqlAnalysisIssueCode.CANONICALIZATION_ERROR,
            "SQL shape canonicalization failed with the configured dialect.",
        )

    return (
        CanonicalizedSql(
            expression=expression,
            canonical_sql=canonical_sql,
            shape_sql=shape_sql,
            canonical_hash=_hash_sql(_CANONICAL_HASH_DOMAIN, dialect, canonical_sql),
            shape_hash=_hash_sql(_SHAPE_HASH_DOMAIN, dialect, shape_sql),
        ),
        None,
    )


def _is_select_query(expression: exp.Expression) -> bool:
    if isinstance(expression, (exp.Select, exp.SetOperation)):
        return True
    return isinstance(expression, exp.Subquery) and _is_select_query(expression.this)


def _without_comments(expression: exp.Expression) -> exp.Expression:
    copied = expression.copy()
    for node in copied.walk():
        node.comments = None
    return copied


def _redact_literal(node: exp.Expression) -> exp.Expression:
    if isinstance(node, exp.Literal):
        if node.is_string:
            literal_type = "string"
        elif _INTEGER_LITERAL.fullmatch(node.this):
            literal_type = "integer"
        else:
            literal_type = "decimal"
        return exp.Placeholder(this=f"__{literal_type}__")
    if isinstance(node, exp.Boolean):
        return exp.Placeholder(this="__boolean__")
    if isinstance(node, exp.Null):
        return exp.Placeholder(this="__null__")
    return node


def _hash_sql(domain: bytes, dialect: str, sql: str) -> str:
    digest = hashlib.sha256()
    digest.update(domain)
    digest.update(dialect.casefold().encode("utf-8"))
    digest.update(b"\0")
    digest.update(sql.encode("utf-8"))
    return digest.hexdigest()


def _issue(
    record: DatasetRecord,
    code: SqlAnalysisIssueCode,
    message: str,
) -> SqlAnalysisIssue:
    return SqlAnalysisIssue(
        code=code,
        line_number=record.source_line,
        message=message,
    )
