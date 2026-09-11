from __future__ import annotations

from collections import Counter

from sqlglot import exp

from .sql_contracts import SqlFeatures


_BOOLEAN_NODES: tuple[tuple[type[exp.Expression], str], ...] = (
    (exp.And, "AND"),
    (exp.Or, "OR"),
    (exp.Not, "NOT"),
)
_COMPARISON_NODES: tuple[tuple[type[exp.Expression], str], ...] = (
    (exp.EQ, "="),
    (exp.NEQ, "!="),
    (exp.LT, "<"),
    (exp.LTE, "<="),
    (exp.GT, ">"),
    (exp.GTE, ">="),
    (exp.Like, "LIKE"),
    (exp.ILike, "ILIKE"),
    (exp.In, "IN"),
    (exp.Between, "BETWEEN"),
    (exp.Is, "IS"),
)
_ARITHMETIC_NODES: tuple[tuple[type[exp.Expression], str], ...] = (
    (exp.Add, "+"),
    (exp.Sub, "-"),
    (exp.Mul, "*"),
    (exp.Div, "/"),
    (exp.Mod, "%"),
    (exp.Pow, "POWER"),
)
_SET_OPERATION_NODES = (exp.Union, exp.Intersect, exp.Except)
_NON_GENERAL_FUNCTION_NODES = (
    tuple(item[0] for item in _BOOLEAN_NODES)
    + tuple(item[0] for item in _COMPARISON_NODES)
    + tuple(item[0] for item in _ARITHMETIC_NODES)
)


def _increment_matching(
    counter: Counter[str],
    node: exp.Expression,
    definitions: tuple[tuple[type[exp.Expression], str], ...],
) -> bool:
    for node_type, label in definitions:
        if isinstance(node, node_type):
            counter[label] += 1
            return True
    return False


def _qualified_name(node: exp.Table | exp.Column) -> str:
    parts: list[str] = []
    if isinstance(node, exp.Table):
        for key in ("catalog", "db", "this"):
            value = node.args.get(key)
            if isinstance(value, exp.Identifier):
                parts.append(value.sql(dialect="postgres"))
    else:
        for key in ("catalog", "db", "table", "this"):
            value = node.args.get(key)
            if isinstance(value, exp.Identifier):
                parts.append(value.sql(dialect="postgres"))
            elif isinstance(value, exp.Star):
                parts.append("*")
    return ".".join(parts)


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return node.name.upper()
    return node.sql_name().upper()


def _literal_label(node: exp.Expression) -> str | None:
    if isinstance(node, exp.Boolean):
        return "BOOLEAN"
    if isinstance(node, exp.Null):
        return "NULL"
    if not isinstance(node, exp.Literal):
        return None
    if node.is_string:
        return "STRING"
    text = str(node.this).lower()
    return "DECIMAL" if "." in text or "e" in text else "INTEGER"


def _join_label(node: exp.Join) -> str:
    side = (node.side or "").upper()
    kind = (node.kind or "").upper()
    method = (node.method or "").upper()
    if not side and not kind and not method:
        return "INNER"
    return " ".join(part for part in (method, side, kind) if part)


def _root_kind(expression: exp.Expression) -> str:
    if isinstance(expression, exp.Select):
        return "SELECT"
    if isinstance(expression, _SET_OPERATION_NODES):
        return "SET_OPERATION"
    return "OTHER"


def _projection_features(expression: exp.Expression) -> tuple[int, int, int, int]:
    select = expression if isinstance(expression, exp.Select) else expression.find(exp.Select)
    if select is None:
        return 0, 0, 0, 0

    projection_count = len(select.expressions)
    wildcard_count = 0
    alias_count = 0
    expression_count = 0
    for projection in select.expressions:
        if isinstance(projection, exp.Alias):
            alias_count += 1
            value = projection.this
        else:
            value = projection
        if isinstance(value, exp.Star) or (
            isinstance(value, exp.Column) and isinstance(value.this, exp.Star)
        ):
            wildcard_count += 1
        elif not isinstance(value, exp.Column):
            expression_count += 1
    return projection_count, wildcard_count, alias_count, expression_count


def extract_sql_features(expression: exp.Expression) -> SqlFeatures:
    """Extract deterministic AST occurrence counts without rewriting the AST."""

    tables: list[str] = []
    columns: list[str] = []
    boolean: Counter[str] = Counter()
    comparisons: Counter[str] = Counter()
    aggregates: Counter[str] = Counter()
    functions: Counter[str] = Counter()
    arithmetic: Counter[str] = Counter()
    joins: Counter[str] = Counter()
    literals: Counter[str] = Counter()
    predicate_count = 0
    placeholder_count = 0
    subquery_count = 0
    cte_count = 0
    set_operation_count = 0
    window_count = 0

    for node in expression.walk():
        if isinstance(node, exp.Table):
            tables.append(_qualified_name(node))
        elif isinstance(node, exp.Column):
            columns.append(_qualified_name(node))

        _increment_matching(boolean, node, _BOOLEAN_NODES)
        if _increment_matching(comparisons, node, _COMPARISON_NODES):
            predicate_count += 1
            if isinstance(node, exp.Is) and node.args.get("negate"):
                boolean["NOT"] += 1
        _increment_matching(arithmetic, node, _ARITHMETIC_NODES)

        if isinstance(node, exp.AggFunc):
            aggregates[_function_name(node)] += 1
        elif isinstance(node, exp.Func) and not isinstance(
            node, _NON_GENERAL_FUNCTION_NODES
        ):
            functions[_function_name(node)] += 1

        if isinstance(node, exp.Join):
            joins[_join_label(node)] += 1
        if isinstance(node, exp.Subquery):
            subquery_count += 1
        if isinstance(node, exp.CTE):
            cte_count += 1
        if isinstance(node, _SET_OPERATION_NODES):
            set_operation_count += 1
        if isinstance(node, exp.Window):
            window_count += 1
        if isinstance(node, (exp.Placeholder, exp.Parameter)):
            placeholder_count += 1

        literal_label = _literal_label(node)
        if literal_label is not None:
            literals[literal_label] += 1

    projection_count, wildcard_count, alias_count, expression_count = (
        _projection_features(expression)
    )
    selects = [node for node in expression.walk() if isinstance(node, exp.Select)]
    query_nodes = [
        node
        for node in expression.walk()
        if isinstance(node, (exp.Select, *_SET_OPERATION_NODES))
    ]
    select = expression if isinstance(expression, exp.Select) else expression.find(exp.Select)

    def sorted_counts(values: Counter[str]) -> dict[str, int]:
        return {key: values[key] for key in sorted(values)}

    return SqlFeatures(
        statement_kind="SELECT" if select is not None else "OTHER",
        root_kind=_root_kind(expression),
        table_references=sorted(tables),
        column_references=sorted(columns),
        table_reference_count=len(tables),
        column_reference_count=len(columns),
        projection_count=projection_count,
        wildcard_count=wildcard_count,
        projection_alias_count=alias_count,
        expression_projection_count=expression_count,
        predicate_count=predicate_count,
        has_where=any(item.args.get("where") is not None for item in selects),
        boolean_operators=sorted_counts(boolean),
        comparison_operators=sorted_counts(comparisons),
        aggregate_functions=sorted_counts(aggregates),
        functions=sorted_counts(functions),
        arithmetic_operators=sorted_counts(arithmetic),
        join_types=sorted_counts(joins),
        literal_types=sorted_counts(literals),
        placeholder_count=placeholder_count,
        distinct=any(item.args.get("distinct") is not None for item in selects),
        group_by=any(item.args.get("group") is not None for item in selects),
        having=any(item.args.get("having") is not None for item in selects),
        order_by=any(item.args.get("order") is not None for item in query_nodes),
        limit=any(item.args.get("limit") is not None for item in query_nodes),
        offset=any(item.args.get("offset") is not None for item in query_nodes),
        join_count=sum(joins.values()),
        subquery_count=subquery_count,
        cte_count=cte_count,
        set_operation_count=set_operation_count,
        window_function_count=window_count,
    )
