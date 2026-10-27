from __future__ import annotations

from typing import Any, NoReturn

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from backend.adaptive.harness.contracts import (
    HarnessErrorCode,
    HarnessStage,
)
from backend.adaptive.harness.errors import HarnessFailure
from backend.adaptive.mapping.transforms import (
    render_postgres_transform,
    transform_operators,
)
from backend.adaptive.mapping.values import bind_filter_value, contains_label_values, time_label_values
from backend.adaptive.ontology.contracts import Operator
from backend.adaptive.relations.contracts import JoinPlan, JoinPlanStatus

from .contracts import AstPolicyContext


_ALLOWED_NODE_TYPES = (
    exp.Select,
    exp.Distinct,
    exp.Alias,
    exp.Column,
    exp.Identifier,
    exp.From,
    exp.Table,
    exp.TableAlias,
    exp.Join,
    exp.Where,
    exp.And,
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.Like,
    exp.Placeholder,
    exp.Limit,
    # Phase 8-21: only inside "(column LIKE %s OR column IN (%s, ...))",
    # which _validate_where checks exactly.
    exp.Paren,
    exp.Or,
    exp.In,
)

_FILTER_NODE_TYPES: dict[Operator, type[exp.Expression]] = {
    Operator.EQUALS: exp.EQ,
    Operator.NOT_EQUALS: exp.NEQ,
    Operator.GREATER_THAN: exp.GT,
    Operator.GREATER_THAN_OR_EQUAL: exp.GTE,
    Operator.LESS_THAN: exp.LT,
    Operator.LESS_THAN_OR_EQUAL: exp.LTE,
    Operator.CONTAINS: exp.Like,
}


class AstReadOnlyPolicy:
    """Validate that SQL is exactly the safe shape emitted by our compilers."""

    def validate(self, context: AstPolicyContext) -> None:
        if context.join_result.status is JoinPlanStatus.AMBIGUOUS:
            self._fail(
                HarnessErrorCode.AMBIGUOUS_JOIN_PATH,
                "AST validation requires one resolved JOIN path",
            )

        plan = context.join_result.plan
        if plan is None:
            self._fail(
                HarnessErrorCode.INVALID_JOIN_PLAN,
                "resolved JOIN result does not contain a plan",
            )

        try:
            parsed = sqlglot.parse(context.query.sql, read=context.dialect)
        except SqlglotError as error:
            self._fail(HarnessErrorCode.SQL_PARSE_ERROR, str(error))

        statements = [statement for statement in parsed if statement is not None]
        if len(parsed) != 1 or len(statements) != 1:
            self._fail(
                HarnessErrorCode.MULTIPLE_STATEMENTS,
                "exactly one SQL statement is required",
            )

        root = statements[0]
        if not isinstance(root, exp.Select):
            self._fail(
                HarnessErrorCode.UNSAFE_SQL_NODE,
                "the root SQL node must be SELECT",
            )

        if (root.args.get("distinct") is not None) != context.distinct:
            self._fail(HarnessErrorCode.UNSAFE_SQL_NODE, "DISTINCT does not match policy context")

        # Declared transform expressions are opaque to the node allowlist;
        # projection and WHERE validation require each one to equal the
        # fixed template applied to its mapped column exactly.
        transform_ids = self._transform_candidate_ids(root, context)

        def is_transform(node: exp.Expression) -> bool:
            return id(node) in transform_ids

        for node in root.walk(prune=is_transform):
            if is_transform(node):
                if any(inner.comments for inner in node.walk()):
                    self._fail(
                        HarnessErrorCode.UNSAFE_SQL_NODE,
                        "SQL comments are not allowed",
                    )
                continue
            if node.comments:
                self._fail(
                    HarnessErrorCode.UNSAFE_SQL_NODE,
                    "SQL comments are not allowed",
                )
            if not isinstance(node, (*_ALLOWED_NODE_TYPES, exp.Literal, exp.Offset)):
                self._fail(
                    HarnessErrorCode.UNSAFE_SQL_NODE,
                    f"SQL node '{type(node).__name__}' is not allowed",
                )

        if context.anchor_table is not None and plan.anchor_table != context.anchor_table:
            self._fail(
                HarnessErrorCode.INVALID_JOIN_PLAN,
                "JOIN plan does not start from the declared anchor table",
            )
        alias_by_table, legacy_compatible = self._validate_tables(
            root, plan, left_joins=context.anchor_table is not None
        )
        self._validate_projection(
            root,
            context,
            alias_by_table,
            legacy_compatible=legacy_compatible,
        )
        self._validate_joins(root, plan, alias_by_table)
        self._validate_where(
            root,
            context,
            alias_by_table,
            legacy_compatible=legacy_compatible,
        )
        self._validate_limit(root)
        self._validate_placeholders(root, context)
        self._validate_columns(
            root,
            context,
            alias_by_table,
            legacy_compatible=legacy_compatible,
        )
        for node in root.walk(prune=is_transform):
            if is_transform(node):
                continue
            if not isinstance(node, _ALLOWED_NODE_TYPES):
                self._fail(
                    HarnessErrorCode.UNSAFE_SQL_NODE,
                    f"SQL node '{type(node).__name__}' is not allowed",
                )

    def _validate_tables(
        self,
        root: exp.Select,
        plan: JoinPlan,
        *,
        left_joins: bool = False,
    ) -> tuple[dict[str, str], bool]:
        from_clause = root.args.get("from_")
        if not isinstance(from_clause, exp.From) or not isinstance(
            from_clause.this, exp.Table
        ):
            self._fail(
                HarnessErrorCode.UNAUTHORIZED_TABLE,
                "FROM must contain the JOIN plan anchor table",
            )

        anchor = from_clause.this
        self._validate_plain_table(anchor, plan.anchor_table)
        joins = root.args.get("joins") or []
        actual_tables = list(root.find_all(exp.Table))
        if len(joins) != len(plan.steps):
            self._fail(
                HarnessErrorCode.INVALID_JOIN_PREDICATE,
                "SQL JOIN count does not match the JOIN plan",
            )

        legacy_compatible = not plan.steps and anchor.args.get("alias") is None
        if legacy_compatible:
            alias_by_table = {plan.anchor_table: plan.anchor_table}
        else:
            self._validate_alias(anchor, "t0")
            alias_by_table = {plan.anchor_table: "t0"}

        for index, (join, step) in enumerate(zip(joins, plan.steps), start=1):
            if not isinstance(join, exp.Join) or not isinstance(join.this, exp.Table):
                self._fail(
                    HarnessErrorCode.UNAUTHORIZED_TABLE,
                    "each JOIN must introduce one physical table",
                )
            table = join.this
            self._validate_plain_table(table, step.traversal_to)
            alias = f"t{index}"
            self._validate_alias(table, alias)
            alias_by_table[step.traversal_to] = alias

            if self._has_unsupported_args(
                join,
                allowed={"this", "on", "kind", "side"},
            ):
                self._fail(
                    HarnessErrorCode.UNSAFE_SQL_NODE,
                    "JOIN modifiers are not allowed",
                )
            kind = join.args.get("kind")
            side = join.args.get("side")
            if left_joins:
                # A declared anchor keeps its rows: exactly a plain LEFT JOIN.
                if kind is not None or side is None or str(side).upper() != "LEFT":
                    self._fail(
                        HarnessErrorCode.UNSAFE_SQL_NODE,
                        "only plain LEFT JOIN is allowed from a declared anchor",
                    )
            elif side is not None or (kind is not None and str(kind).upper() != "INNER"):
                self._fail(
                    HarnessErrorCode.UNSAFE_SQL_NODE,
                    "only default or INNER JOIN is allowed",
                )

        expected_tables = [
            plan.anchor_table,
            *(step.traversal_to for step in plan.steps),
        ]
        if len(actual_tables) != len(expected_tables):
            self._fail(
                HarnessErrorCode.UNAUTHORIZED_TABLE,
                "SQL contains an unexpected table reference",
            )
        return alias_by_table, legacy_compatible

    def _validate_columns(
        self,
        root: exp.Select,
        context: AstPolicyContext,
        alias_by_table: dict[str, str],
        *,
        legacy_compatible: bool,
    ) -> None:
        table_by_alias = {alias: table for table, alias in alias_by_table.items()}
        allowed_columns = {
            table: set(columns)
            for table, columns in context.allowed_columns.items()
        }

        for column in root.find_all(exp.Column):
            if column.args.get("db") is not None or column.args.get("catalog") is not None:
                self._fail(
                    HarnessErrorCode.UNAUTHORIZED_COLUMN,
                    "database-qualified columns are not allowed",
                )
            qualifier = column.table
            if legacy_compatible:
                if qualifier:
                    self._fail(
                        HarnessErrorCode.UNAUTHORIZED_COLUMN,
                        "legacy single-table SQL must use unqualified columns",
                    )
                table = next(iter(alias_by_table))
            else:
                if not qualifier or qualifier not in table_by_alias:
                    self._fail(
                        HarnessErrorCode.UNAUTHORIZED_COLUMN,
                        "columns must use a known deterministic table alias",
                    )
                table = table_by_alias[qualifier]
            if column.name not in allowed_columns.get(table, set()):
                self._fail(
                    HarnessErrorCode.UNAUTHORIZED_COLUMN,
                    f"column '{table}.{column.name}' is not allowed",
                )

    def _validate_projection(
        self,
        root: exp.Select,
        context: AstPolicyContext,
        alias_by_table: dict[str, str],
        *,
        legacy_compatible: bool,
    ) -> None:
        if context.query.expected_columns != context.query_ir.select:
            self._fail(
                HarnessErrorCode.INVALID_PROJECTION,
                "compiled expected columns must match QueryIR select fields",
            )
        if len(root.expressions) != len(context.query_ir.select):
            self._fail(
                HarnessErrorCode.INVALID_PROJECTION,
                "projection count does not match QueryIR",
            )

        for expression, semantic_field in zip(
            root.expressions,
            context.query_ir.select,
        ):
            if not isinstance(expression, exp.Alias):
                self._fail(
                    HarnessErrorCode.INVALID_PROJECTION,
                    "each projection must be one aliased physical column",
                )
            if expression.alias != semantic_field:
                self._fail(
                    HarnessErrorCode.INVALID_PROJECTION,
                    f"projection alias must be '{semantic_field}'",
                )
            mapping = context.mapping.fields.get(semantic_field)
            if mapping is None:
                self._fail(
                    HarnessErrorCode.INVALID_PROJECTION,
                    f"semantic field '{semantic_field}' has no published mapping",
                )
            if semantic_field in context.field_transforms:
                self._validate_exact_transform(
                    expression.this,
                    context,
                    semantic_field,
                    alias_by_table,
                    legacy_compatible=legacy_compatible,
                    code=HarnessErrorCode.INVALID_PROJECTION,
                )
                continue
            if not isinstance(expression.this, exp.Column):
                self._fail(
                    HarnessErrorCode.INVALID_PROJECTION,
                    "each projection must be one aliased physical column",
                )
            self._validate_exact_column(
                expression.this,
                mapping.table,
                mapping.column,
                alias_by_table,
                legacy_compatible=legacy_compatible,
                code=HarnessErrorCode.UNAUTHORIZED_COLUMN,
            )

    def _validate_joins(
        self,
        root: exp.Select,
        plan: JoinPlan,
        alias_by_table: dict[str, str],
    ) -> None:
        joins = root.args.get("joins") or []
        for join, step in zip(joins, plan.steps):
            predicates = self._flatten_and(join.args.get("on"))
            expected_pairs = step.relation.column_pairs
            if len(predicates) != len(expected_pairs):
                self._fail(
                    HarnessErrorCode.INVALID_JOIN_PREDICATE,
                    f"JOIN '{step.relation.relation_id}' predicate count is invalid",
                )
            for predicate, pair in zip(predicates, expected_pairs):
                if not isinstance(predicate, exp.EQ):
                    self._fail(
                        HarnessErrorCode.INVALID_JOIN_PREDICATE,
                        "JOIN predicates must be equality comparisons",
                    )
                self._validate_exact_column(
                    predicate.this,
                    step.relation.source_table,
                    pair.source_column,
                    alias_by_table,
                    legacy_compatible=False,
                    code=HarnessErrorCode.INVALID_JOIN_PREDICATE,
                )
                self._validate_exact_column(
                    predicate.expression,
                    step.relation.target_table,
                    pair.target_column,
                    alias_by_table,
                    legacy_compatible=False,
                    code=HarnessErrorCode.INVALID_JOIN_PREDICATE,
                )

    def _validate_where(
        self,
        root: exp.Select,
        context: AstPolicyContext,
        alias_by_table: dict[str, str],
        *,
        legacy_compatible: bool,
    ) -> None:
        where = root.args.get("where")
        if not context.query_ir.filters and not context.member_filters:
            if where is not None:
                self._fail(
                    HarnessErrorCode.UNSAFE_SQL_NODE,
                    "WHERE is not allowed when QueryIR has no filters",
                )
            return
        if not isinstance(where, exp.Where):
            self._fail(
                HarnessErrorCode.UNSAFE_SQL_NODE,
                "WHERE predicates do not match QueryIR filters",
            )

        predicates = self._flatten_and(where.this)
        if len(predicates) != len(context.query_ir.filters) + len(context.member_filters):
            self._fail(
                HarnessErrorCode.UNSAFE_SQL_NODE,
                "WHERE predicate count does not match QueryIR filters",
            )
        for predicate, filter_ir in zip(predicates, context.query_ir.filters):
            labelled = contains_label_values(
                filter_ir.field,
                filter_ir.operator,
                filter_ir.value,
                value_dictionaries=context.value_dictionaries,
            )
            in_list: exp.In | None = None
            periods = self._periods(filter_ir, context)
            if periods is not None:
                if not isinstance(predicate, exp.In):
                    self._fail(
                        HarnessErrorCode.UNSAFE_SQL_NODE,
                        f"filter operator for '{filter_ir.field}' does not match QueryIR",
                    )
                self._check_in_list(predicate, len(periods), filter_ir.field)
                mapping = context.mapping.fields.get(filter_ir.field)
                if mapping is None or filter_ir.field in context.field_transforms:
                    self._fail(
                        HarnessErrorCode.UNAUTHORIZED_COLUMN,
                        f"filter field '{filter_ir.field}' has no period table",
                    )
                self._validate_exact_column(
                    predicate.this,
                    mapping.table,
                    mapping.column,
                    alias_by_table,
                    legacy_compatible=legacy_compatible,
                    code=HarnessErrorCode.UNAUTHORIZED_COLUMN,
                )
                continue
            if labelled:
                predicate, in_list = self._split_labelled(predicate, len(labelled), filter_ir.field)
            elif isinstance(predicate, (exp.Paren, exp.Or, exp.In)):
                self._fail(
                    HarnessErrorCode.UNSAFE_SQL_NODE,
                    f"filter operator for '{filter_ir.field}' does not match QueryIR",
                )
            expected_type = _FILTER_NODE_TYPES[filter_ir.operator]
            if not isinstance(predicate, expected_type):
                self._fail(
                    HarnessErrorCode.UNSAFE_SQL_NODE,
                    f"filter operator for '{filter_ir.field}' does not match QueryIR",
                )
            mapping = context.mapping.fields.get(filter_ir.field)
            if mapping is None:
                self._fail(
                    HarnessErrorCode.UNAUTHORIZED_COLUMN,
                    f"filter field '{filter_ir.field}' has no published mapping",
                )
            if filter_ir.field in context.field_transforms:
                if in_list is not None:
                    self._fail(
                        HarnessErrorCode.UNSAFE_SQL_NODE,
                        f"transformed field '{filter_ir.field}' has no label list",
                    )
                if filter_ir.operator not in transform_operators(
                    context.field_transforms[filter_ir.field]
                ):
                    self._fail(
                        HarnessErrorCode.UNSAFE_SQL_NODE,
                        f"operator for transformed field '{filter_ir.field}' is not allowed",
                    )
                self._validate_exact_transform(
                    predicate.this,
                    context,
                    filter_ir.field,
                    alias_by_table,
                    legacy_compatible=legacy_compatible,
                    code=HarnessErrorCode.UNAUTHORIZED_COLUMN,
                )
            else:
                for column in (predicate.this, *(() if in_list is None else (in_list.this,))):
                    self._validate_exact_column(
                        column,
                        mapping.table,
                        mapping.column,
                        alias_by_table,
                        legacy_compatible=legacy_compatible,
                        code=HarnessErrorCode.UNAUTHORIZED_COLUMN,
                    )
            if not self._is_anonymous_placeholder(predicate.expression):
                self._fail(
                    HarnessErrorCode.INVALID_PLACEHOLDER,
                    "filter values must use anonymous placeholders",
                )

        for predicate, member_filter in zip(
            predicates[len(context.query_ir.filters):], context.member_filters
        ):
            mapping = context.mapping.fields.get(member_filter.field)
            if mapping is None:
                self._fail(HarnessErrorCode.UNAUTHORIZED_COLUMN, f"member filter field '{member_filter.field}' has no published mapping")
            if not isinstance(predicate, exp.In):
                self._fail(HarnessErrorCode.UNSAFE_SQL_NODE, f"member filter for '{member_filter.field}' must use IN")
            self._check_in_list(predicate, len(member_filter.values), member_filter.field)
            self._validate_exact_column(
                predicate.this, mapping.table, mapping.column, alias_by_table,
                legacy_compatible=legacy_compatible, code=HarnessErrorCode.UNAUTHORIZED_COLUMN,
            )

    def _split_labelled(
        self, predicate: exp.Expression, count: int, field: str
    ) -> tuple[exp.Expression, exp.In]:
        """``(column LIKE %s OR column IN (%s × count))`` → (the LIKE, the IN)."""
        inner = predicate.this if isinstance(predicate, exp.Paren) else None
        if (
            not isinstance(inner, exp.Or)
            or not isinstance(inner.this, exp.Like)
            or not isinstance(inner.expression, exp.In)
        ):
            self._fail(
                HarnessErrorCode.UNSAFE_SQL_NODE,
                f"filter operator for '{field}' does not match QueryIR",
            )
        in_list = inner.expression
        self._check_in_list(in_list, count, field)
        return inner.this, in_list

    def _check_in_list(self, in_list: exp.In, count: int, field: str) -> None:
        extra = {key for key, value in in_list.args.items() if value and key not in ("this", "expressions")}
        values = in_list.expressions
        if extra or count < 1 or len(values) != count or not all(self._is_anonymous_placeholder(item) for item in values):
            self._fail(
                HarnessErrorCode.INVALID_PLACEHOLDER,
                f"listed values for '{field}' must be anonymous placeholders",
            )

    def _periods(self, filter_ir: Any, context: AstPolicyContext) -> tuple[str, ...] | None:
        try:
            return time_label_values(
                filter_ir.field,
                filter_ir.operator,
                filter_ir.value,
                value_dictionaries=context.value_dictionaries,
            )
        except ValueError:
            self._fail(
                HarnessErrorCode.INVALID_PLACEHOLDER,
                f"value for field '{filter_ir.field}' is not representable",
            )

    def _validate_limit(self, root: exp.Select) -> None:
        limit = root.args.get("limit")
        if not isinstance(limit, exp.Limit):
            self._fail(
                HarnessErrorCode.INVALID_LIMIT,
                "one parameterized LIMIT is required",
            )
        if (
            limit.args.get("this") is not None
            or limit.args.get("expressions") is not None
            or limit.args.get("limit_options") is not None
            or not self._is_anonymous_placeholder(limit.expression)
            or root.args.get("offset") is not None
        ):
            self._fail(
                HarnessErrorCode.INVALID_LIMIT,
                "LIMIT must contain one anonymous placeholder and no OFFSET",
            )

    def _validate_placeholders(
        self,
        root: exp.Select,
        context: AstPolicyContext,
    ) -> None:
        placeholders = list(root.find_all(exp.Placeholder))
        # Phase 8-21: a labelled contains filter binds its label values too.
        expected_count = 1
        for item in context.query_ir.filters:
            periods = self._periods(item, context)
            if periods is not None:
                expected_count += len(periods)
                continue
            expected_count += 1 + len(
                contains_label_values(
                    item.field, item.operator, item.value, value_dictionaries=context.value_dictionaries
                )
            )
        expected_count += sum(len(item.values) for item in context.member_filters)
        if (
            len(placeholders) != expected_count
            or len(context.query.parameters) != expected_count
            or any(not self._is_anonymous_placeholder(item) for item in placeholders)
        ):
            self._fail(
                HarnessErrorCode.INVALID_PLACEHOLDER,
                "placeholder and parameter counts must equal filters plus LIMIT",
            )
        expected_parameters: list[str | int | float | bool] = []
        for filter_ir in context.query_ir.filters:
            periods = self._periods(filter_ir, context)
            if periods is not None:
                expected_parameters.extend(periods)
                continue
            try:
                value = bind_filter_value(
                    filter_ir.field,
                    filter_ir.value,
                    field_transforms=context.field_transforms,
                    value_dictionaries=context.value_dictionaries,
                )
            except ValueError:
                self._fail(
                    HarnessErrorCode.INVALID_PLACEHOLDER,
                    f"value for field '{filter_ir.field}' is not representable",
                )
            if filter_ir.operator is Operator.CONTAINS:
                if context.dialect == "postgres":
                    from backend.adaptive.compiler.postgres import postgres_contains_pattern

                    value = postgres_contains_pattern(value)
                else:
                    value = f"%{value}%"
            expected_parameters.append(value)
            expected_parameters.extend(
                contains_label_values(
                    filter_ir.field,
                    filter_ir.operator,
                    filter_ir.value,
                    value_dictionaries=context.value_dictionaries,
                )
            )
        for item in context.member_filters:
            try:
                expected_parameters.extend(
                    bind_filter_value(
                        item.field, value, field_transforms=context.field_transforms,
                        value_dictionaries=context.value_dictionaries,
                    ) for value in item.values
                )
            except ValueError:
                self._fail(HarnessErrorCode.INVALID_PLACEHOLDER, f"values for field '{item.field}' are not representable")
        expected_parameters.append(context.query_ir.limit)
        actual_signature = [
            (type(value), value) for value in context.query.parameters
        ]
        expected_signature = [
            (type(value), value) for value in expected_parameters
        ]
        if actual_signature != expected_signature:
            self._fail(
                HarnessErrorCode.INVALID_PLACEHOLDER,
                "bound parameters must exactly match QueryIR filters and LIMIT",
            )

    @staticmethod
    def _flatten_and(expression: exp.Expression | None) -> list[exp.Expression]:
        if expression is None:
            return []
        if isinstance(expression, exp.And):
            return [
                *AstReadOnlyPolicy._flatten_and(expression.this),
                *AstReadOnlyPolicy._flatten_and(expression.expression),
            ]
        return [expression]

    def _validate_plain_table(self, table: exp.Table, expected_name: str) -> None:
        if self._has_unsupported_args(
            table,
            allowed={"this", "alias", "db", "catalog"},
        ):
            self._fail(
                HarnessErrorCode.UNSAFE_SQL_NODE,
                "physical table modifiers are not allowed",
            )
        if (
            not isinstance(table.this, exp.Identifier)
            or table.name != expected_name
            or table.args.get("db") is not None
            or table.args.get("catalog") is not None
        ):
            self._fail(
                HarnessErrorCode.UNAUTHORIZED_TABLE,
                f"expected physical table '{expected_name}'",
            )

    def _validate_alias(self, table: exp.Table, expected_alias: str) -> None:
        alias = table.args.get("alias")
        if isinstance(alias, exp.TableAlias) and self._has_unsupported_args(
            alias,
            allowed={"this", "columns"},
        ):
            self._fail(
                HarnessErrorCode.UNSAFE_SQL_NODE,
                "table alias modifiers are not allowed",
            )
        if (
            not isinstance(alias, exp.TableAlias)
            or not isinstance(alias.this, exp.Identifier)
            or alias.this.name != expected_alias
        ):
            self._fail(
                HarnessErrorCode.UNAUTHORIZED_TABLE,
                f"table alias must be '{expected_alias}'",
            )
        if alias.args.get("columns"):
            self._fail(
                HarnessErrorCode.UNAUTHORIZED_TABLE,
                "column lists in table aliases are not allowed",
            )

    def _validate_exact_column(
        self,
        expression: exp.Expression,
        table: str,
        column_name: str,
        alias_by_table: dict[str, str],
        *,
        legacy_compatible: bool,
        code: HarnessErrorCode,
    ) -> None:
        if not isinstance(expression, exp.Column) or expression.name != column_name:
            self._fail(code, f"expected physical column '{table}.{column_name}'")
        expected_qualifier = "" if legacy_compatible else alias_by_table.get(table)
        if expected_qualifier is None or expression.table != expected_qualifier:
            self._fail(code, f"column '{table}.{column_name}' has an invalid qualifier")

    @staticmethod
    def _transform_candidate_ids(
        root: exp.Select,
        context: AstPolicyContext,
    ) -> set[int]:
        """Positions where a declared transform is expected (validated later)."""
        transforms = context.field_transforms
        if not transforms:
            return set()
        candidates: set[int] = set()
        for expression, semantic_field in zip(root.expressions, context.query_ir.select):
            if semantic_field in transforms and isinstance(expression, exp.Alias):
                candidates.add(id(expression.this))
        where = root.args.get("where")
        if isinstance(where, exp.Where):
            predicates = AstReadOnlyPolicy._flatten_and(where.this)
            for predicate, filter_ir in zip(predicates, context.query_ir.filters):
                target = predicate.args.get("this")
                if filter_ir.field in transforms and isinstance(target, exp.Expression):
                    candidates.add(id(target))
        return candidates

    def _validate_exact_transform(
        self,
        expression: exp.Expression | None,
        context: AstPolicyContext,
        semantic_field: str,
        alias_by_table: dict[str, str],
        *,
        legacy_compatible: bool,
        code: HarnessErrorCode,
    ) -> None:
        if context.dialect != "postgres":
            self._fail(code, "value transforms are only supported for postgres")
        mapping = context.mapping.fields[semantic_field]
        qualifier = "" if legacy_compatible else alias_by_table.get(mapping.table)
        if qualifier is None:
            self._fail(code, f"column '{mapping.table}.{mapping.column}' has an invalid qualifier")
        column_sql = exp.column(mapping.column, table=qualifier or None, quoted=True).sql(
            dialect=context.dialect
        )
        try:
            expected = sqlglot.parse_one(
                render_postgres_transform(context.field_transforms[semantic_field], column_sql),
                read=context.dialect,
            )
        except SqlglotError as error:
            self._fail(code, str(error))
        if not isinstance(expression, exp.Expression) or expression != expected:
            self._fail(
                code,
                f"expected the declared transform of '{mapping.table}.{mapping.column}'",
            )

    @staticmethod
    def _is_anonymous_placeholder(expression: exp.Expression | None) -> bool:
        return isinstance(expression, exp.Placeholder) and expression.this is None

    @staticmethod
    def _has_unsupported_args(
        expression: exp.Expression,
        *,
        allowed: set[str],
    ) -> bool:
        for name, value in expression.args.items():
            if name in allowed or value is None:
                continue
            if isinstance(value, (list, tuple, dict, set)) and not value:
                continue
            return True
        return False

    @staticmethod
    def _fail(code: HarnessErrorCode, message: str) -> NoReturn:
        raise HarnessFailure(code, message, stage=HarnessStage.SQL_VALIDATED)
