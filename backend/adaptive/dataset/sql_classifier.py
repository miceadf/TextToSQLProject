from __future__ import annotations

from sqlglot import exp

from ..ontology.contracts import Operator
from .sql_contracts import (
    IrCompatibilityStatus,
    IrReasonCode,
    SqlCompatibility,
    SqlFeatures,
)


_UNSUPPORTED_REASONS = {
    IrReasonCode.SET_OPERATION,
    IrReasonCode.CTE,
    IrReasonCode.SUBQUERY,
    IrReasonCode.WINDOW_FUNCTION,
    IrReasonCode.SELECT_INTO,
    IrReasonCode.LOCKING_CLAUSE,
    IrReasonCode.UNSUPPORTED_SELECT_CLAUSE,
}
_MANUAL_REVIEW_REASONS = {
    IrReasonCode.WILDCARD_PROJECTION,
    IrReasonCode.RESULT_ALIAS,
    IrReasonCode.FUNCTION_PROJECTION,
    IrReasonCode.NON_LITERAL_FILTER_VALUE,
    IrReasonCode.LIKE_PREFIX_PATTERN,
    IrReasonCode.LIKE_SUFFIX_PATTERN,
    IrReasonCode.LIKE_EXACT_PATTERN,
    IrReasonCode.LIKE_COMPLEX_PATTERN,
    IrReasonCode.LIMIT_NOT_INTEGER_LITERAL,
    IrReasonCode.LIMIT_OUT_OF_RANGE,
    IrReasonCode.PLACEHOLDER_VALUE,
}


def _ordered_reasons(reasons: set[IrReasonCode]) -> list[IrReasonCode]:
    return [reason for reason in IrReasonCode if reason in reasons]


def _unwrap_parentheses(node: exp.Expression) -> exp.Expression:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _literal_value(node: exp.Expression) -> bool:
    if isinstance(node, (exp.Literal, exp.Boolean)):
        return True
    return (
        isinstance(node, exp.Neg)
        and isinstance(_unwrap_parentheses(node.this), exp.Literal)
        and not _unwrap_parentheses(node.this).is_string
    )


def _like_reason(pattern: exp.Expression) -> IrReasonCode | None:
    if not isinstance(pattern, exp.Literal) or not pattern.is_string:
        return IrReasonCode.NON_LITERAL_FILTER_VALUE
    value = str(pattern.this)
    if "_" in value or "\\" in value:
        return IrReasonCode.LIKE_COMPLEX_PATTERN
    percent_count = value.count("%")
    if percent_count == 2 and value.startswith("%") and value.endswith("%"):
        if len(value) > 2 and Operator.CONTAINS in set(Operator):
            return None
        return IrReasonCode.LIKE_COMPLEX_PATTERN
    if percent_count == 0:
        return IrReasonCode.LIKE_EXACT_PATTERN
    if percent_count == 1 and value.endswith("%"):
        return IrReasonCode.LIKE_PREFIX_PATTERN
    if percent_count == 1 and value.startswith("%"):
        return IrReasonCode.LIKE_SUFFIX_PATTERN
    return IrReasonCode.LIKE_COMPLEX_PATTERN


def _classify_filter_leaf(node: exp.Expression) -> set[IrReasonCode]:
    supported_operators = set(Operator)
    comparison_operators: tuple[tuple[type[exp.Expression], Operator], ...] = (
        (exp.EQ, Operator.EQUALS),
        (exp.NEQ, Operator.NOT_EQUALS),
        (exp.GT, Operator.GREATER_THAN),
        (exp.GTE, Operator.GREATER_THAN_OR_EQUAL),
        (exp.LT, Operator.LESS_THAN),
        (exp.LTE, Operator.LESS_THAN_OR_EQUAL),
    )
    for node_type, operator in comparison_operators:
        if isinstance(node, node_type):
            if operator not in supported_operators:
                return {IrReasonCode.UNSUPPORTED_COMPARISON}
            if not isinstance(_unwrap_parentheses(node.this), exp.Column):
                return {IrReasonCode.NON_LITERAL_FILTER_VALUE}
            if not _literal_value(_unwrap_parentheses(node.expression)):
                return {IrReasonCode.NON_LITERAL_FILTER_VALUE}
            return set()

    if isinstance(node, exp.Like):
        if not isinstance(_unwrap_parentheses(node.this), exp.Column):
            return {IrReasonCode.NON_LITERAL_FILTER_VALUE}
        reason = _like_reason(_unwrap_parentheses(node.expression))
        return {reason} if reason is not None else set()
    if isinstance(node, exp.In):
        return {IrReasonCode.IN_PREDICATE}
    if isinstance(node, exp.Between):
        return {IrReasonCode.BETWEEN_PREDICATE}
    if isinstance(node, exp.Is):
        if isinstance(node.expression, exp.Null):
            reasons = {IrReasonCode.IS_NULL_PREDICATE}
            if node.args.get("negate"):
                reasons.add(IrReasonCode.NOT_PREDICATE)
            return reasons
        return {IrReasonCode.UNSUPPORTED_COMPARISON}
    return {IrReasonCode.UNSUPPORTED_COMPARISON}


def _filter_reasons(node: exp.Expression) -> set[IrReasonCode]:
    node = _unwrap_parentheses(node)
    if isinstance(node, exp.And):
        return _filter_reasons(node.this) | _filter_reasons(node.expression)
    if isinstance(node, exp.Or):
        return (
            {IrReasonCode.OR_PREDICATE}
            | _filter_reasons(node.this)
            | _filter_reasons(node.expression)
        )
    if isinstance(node, exp.Not):
        return {IrReasonCode.NOT_PREDICATE} | _filter_reasons(node.this)
    return _classify_filter_leaf(node)


def _projection_reasons(select: exp.Select) -> set[IrReasonCode]:
    reasons: set[IrReasonCode] = set()
    for projection in select.expressions:
        if isinstance(projection, exp.Alias):
            reasons.add(IrReasonCode.RESULT_ALIAS)
            value = projection.this
        else:
            value = projection
        if isinstance(value, exp.Star) or (
            isinstance(value, exp.Column) and isinstance(value.this, exp.Star)
        ):
            reasons.add(IrReasonCode.WILDCARD_PROJECTION)
        elif not isinstance(value, exp.Column):
            reasons.add(IrReasonCode.EXPRESSION_PROJECTION)
            structural_functions = (
                exp.AggFunc,
                exp.And,
                exp.Or,
                exp.Not,
                exp.EQ,
                exp.NEQ,
                exp.LT,
                exp.LTE,
                exp.GT,
                exp.GTE,
                exp.Like,
                exp.ILike,
                exp.In,
                exp.Between,
                exp.Is,
                exp.Add,
                exp.Sub,
                exp.Mul,
                exp.Div,
                exp.Mod,
                exp.Pow,
            )
            if any(
                isinstance(node, exp.Func)
                and not isinstance(node, structural_functions)
                for node in value.walk()
            ):
                reasons.add(IrReasonCode.FUNCTION_PROJECTION)
    return reasons


def _limit_reasons(select: exp.Select) -> set[IrReasonCode]:
    limit = select.args.get("limit")
    if limit is None:
        return set()
    value = _unwrap_parentheses(limit.expression)
    if not isinstance(value, exp.Literal) or value.is_string:
        if isinstance(value, (exp.Placeholder, exp.Parameter)):
            return {
                IrReasonCode.PLACEHOLDER_VALUE,
                IrReasonCode.LIMIT_NOT_INTEGER_LITERAL,
            }
        return {IrReasonCode.LIMIT_NOT_INTEGER_LITERAL}
    text = str(value.this)
    if not text.isdigit():
        return {IrReasonCode.LIMIT_NOT_INTEGER_LITERAL}
    return set() if 1 <= int(text) <= 200 else {IrReasonCode.LIMIT_OUT_OF_RANGE}


def classify_ir_compatibility(
    expression: exp.Expression,
    features: SqlFeatures,
) -> SqlCompatibility:
    """Conservatively classify whether a parsed statement fits the current QueryIR."""

    reasons: set[IrReasonCode] = set()
    if features.set_operation_count:
        reasons.add(IrReasonCode.SET_OPERATION)
    if features.cte_count:
        reasons.add(IrReasonCode.CTE)
    if features.subquery_count:
        reasons.add(IrReasonCode.SUBQUERY)
    if features.window_function_count:
        reasons.add(IrReasonCode.WINDOW_FUNCTION)

    select = expression if isinstance(expression, exp.Select) else expression.find(exp.Select)
    if select is None:
        reasons.add(IrReasonCode.NON_SELECT_STATEMENT)
    else:
        reasons.update(_projection_reasons(select))
        reasons.update(_limit_reasons(select))
        if select.args.get("into") is not None:
            reasons.add(IrReasonCode.SELECT_INTO)
        if select.args.get("locks"):
            reasons.add(IrReasonCode.LOCKING_CLAUSE)
        known_arguments = {
            "expressions",
            "from_",
            "joins",
            "where",
            "limit",
            "distinct",
            "group",
            "having",
            "order",
            "offset",
            "with_",
            "into",
            "locks",
        }
        if any(
            name not in known_arguments and value not in (None, [], {}, False)
            for name, value in select.args.items()
        ):
            reasons.add(IrReasonCode.UNSUPPORTED_SELECT_CLAUSE)
        where = select.args.get("where")
        if where is not None:
            reasons.update(_filter_reasons(where.this))

    if features.join_count:
        reasons.add(IrReasonCode.JOIN)
    if len(set(features.table_references)) != 1:
        reasons.add(IrReasonCode.MULTIPLE_TABLES)
    if features.aggregate_functions:
        reasons.add(IrReasonCode.AGGREGATE)
    if features.distinct:
        reasons.add(IrReasonCode.DISTINCT)
    if features.group_by:
        reasons.add(IrReasonCode.GROUP_BY)
    if features.having:
        reasons.add(IrReasonCode.HAVING)
    if features.order_by:
        reasons.add(IrReasonCode.ORDER_BY)
    if features.offset:
        reasons.add(IrReasonCode.OFFSET)
    if features.arithmetic_operators:
        reasons.add(IrReasonCode.ARITHMETIC_EXPRESSION)
    if features.placeholder_count:
        reasons.add(IrReasonCode.PLACEHOLDER_VALUE)

    ordered = _ordered_reasons(reasons)
    if any(reason in _UNSUPPORTED_REASONS for reason in reasons) or (
        IrReasonCode.NON_SELECT_STATEMENT in reasons
    ):
        status = IrCompatibilityStatus.UNSUPPORTED_OR_UNSAFE
    elif any(reason in _MANUAL_REVIEW_REASONS for reason in reasons):
        status = IrCompatibilityStatus.MANUAL_REVIEW
    elif reasons:
        status = IrCompatibilityStatus.IR_EXTENSION_REQUIRED
    else:
        status = IrCompatibilityStatus.CURRENT_IR_CANDIDATE
    return SqlCompatibility(status=status, reason_codes=ordered)
