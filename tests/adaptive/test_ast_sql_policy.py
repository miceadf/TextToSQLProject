from __future__ import annotations

import unittest
from datetime import datetime, timezone

from backend.adaptive.compiler import JoinCapableSQLiteCompiler, JoinCompilationRequest
from backend.adaptive.harness import (
    FieldMapping,
    HarnessErrorCode,
    HarnessStage,
    MappingProfile,
    MappingStatus,
    QueryIR,
    SingleTableSQLiteCompiler,
)
from backend.adaptive.harness.contracts import CompiledQuery
from backend.adaptive.harness.errors import HarnessFailure
from backend.adaptive.mapping import PublishedFieldMapping, PublishedMappingProfile
from backend.adaptive.policy.ast import AstReadOnlyPolicy
from backend.adaptive.policy.contracts import AstPolicyContext
from backend.adaptive.relations import (
    JoinColumnPair,
    JoinPlan,
    JoinPlanningResult,
    JoinPlanStatus,
    JoinStep,
    PhysicalRelation,
)


def _relation(
    relation_id: str,
    source: str,
    target: str,
    pairs: list[tuple[str, str]] | None = None,
) -> PhysicalRelation:
    return PhysicalRelation(
        relation_id=relation_id,
        source_table=source,
        target_table=target,
        column_pairs=[
            JoinColumnPair(source_column=source_column, target_column=target_column)
            for source_column, target_column in (pairs or [("parent_id", "id")])
        ],
        on_update="NO ACTION",
        on_delete="NO ACTION",
        match="NONE",
    )


def _profile(fields: dict[str, tuple[str, str]]) -> PublishedMappingProfile:
    return PublishedMappingProfile(
        database_id="policy-db",
        version=1,
        schema_fingerprint="0" * 64,
        ontology_version=1,
        published_by="policy-test",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        fields={
            field: PublishedFieldMapping(
                table=table,
                column=column,
                reviewer="policy-test",
            )
            for field, (table, column) in fields.items()
        },
    )


def _resolved(fields: list[str], plan: JoinPlan) -> JoinPlanningResult:
    return JoinPlanningResult(
        status=JoinPlanStatus.RESOLVED,
        semantic_fields=sorted(fields),
        required_tables=plan.required_tables,
        plan=plan,
    )


def _context(
    query_ir: QueryIR,
    mapping: PublishedMappingProfile,
    plan: JoinPlan,
) -> AstPolicyContext:
    join_result = _resolved(
        sorted({*query_ir.select, *(item.field for item in query_ir.filters)}),
        plan,
    )
    query = JoinCapableSQLiteCompiler().compile(
        JoinCompilationRequest(
            query_ir=query_ir,
            mapping=mapping,
            join_result=join_result,
        )
    )
    return AstPolicyContext(
        query=query,
        query_ir=query_ir,
        mapping=mapping,
        join_result=join_result,
    )


def _with_sql(
    context: AstPolicyContext,
    sql: str,
    *,
    parameters: list[str | int | float | bool] | None = None,
) -> AstPolicyContext:
    query = context.query.model_copy(
        update={
            "sql": sql,
            "parameters": context.query.parameters if parameters is None else parameters,
        }
    )
    return context.model_copy(update={"query": query})


class AstReadOnlyPolicyAllowedSqlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = AstReadOnlyPolicy()

    def test_legacy_single_table_compiler_sql_is_accepted(self) -> None:
        query_ir = QueryIR(
            entity="Course",
            select=["Course.name", "Course.credits"],
            filters=[
                {"field": "Course.credits", "operator": "equals", "value": 3}
            ],
            limit=25,
        )
        legacy_mapping = MappingProfile(
            database_id="policy-db",
            version=1,
            status=MappingStatus.PUBLISHED,
            fields={
                "Course.name": FieldMapping(table="courses", column="subject_name"),
                "Course.credits": FieldMapping(
                    table="courses", column="credit_hours"
                ),
            },
        )
        published = _profile(
            {
                "Course.name": ("courses", "subject_name"),
                "Course.credits": ("courses", "credit_hours"),
            }
        )
        plan = JoinPlan(
            anchor_table="courses", required_tables=["courses"], steps=[]
        )
        context = AstPolicyContext(
            query=SingleTableSQLiteCompiler().compile(query_ir, legacy_mapping),
            query_ir=query_ir,
            mapping=published,
            join_result=_resolved(
                ["Course.name", "Course.credits"],
                plan,
            ),
        )

        self.policy.validate(context)

    def test_direct_and_reverse_join_sql_are_accepted(self) -> None:
        edge = _relation("child-parent", "children", "parents")
        mapping = _profile(
            {
                "Parent.name": ("parents", "name"),
                "Child.name": ("children", "name"),
            }
        )
        query_ir = QueryIR(
            entity="Parent",
            select=["Parent.name", "Child.name"],
            limit=10,
        )
        plans = [
            JoinPlan(
                anchor_table="children",
                required_tables=["children", "parents"],
                steps=[
                    JoinStep(
                        relation=edge,
                        traversal_from="children",
                        traversal_to="parents",
                    )
                ],
            ),
            JoinPlan(
                anchor_table="parents",
                required_tables=["children", "parents"],
                steps=[
                    JoinStep(
                        relation=edge,
                        traversal_from="parents",
                        traversal_to="children",
                    )
                ],
            ),
        ]

        for plan in plans:
            with self.subTest(anchor=plan.anchor_table):
                self.policy.validate(_context(query_ir, mapping, plan))

    def test_multi_join_with_where_and_limit_is_accepted(self) -> None:
        ab = _relation("ab", "b", "a", [("a_id", "id")])
        cb = _relation("cb", "c", "b", [("b_id", "id")])
        plan = JoinPlan(
            anchor_table="a",
            required_tables=["a", "b", "c"],
            steps=[
                JoinStep(relation=ab, traversal_from="a", traversal_to="b"),
                JoinStep(relation=cb, traversal_from="b", traversal_to="c"),
            ],
        )
        query_ir = QueryIR(
            entity="A",
            select=["A.name", "C.value"],
            filters=[{"field": "B.label", "operator": "contains", "value": "x"}],
            limit=7,
        )
        context = _context(
            query_ir,
            _profile(
                {
                    "A.name": ("a", "name"),
                    "B.label": ("b", "label"),
                    "C.value": ("c", "value"),
                }
            ),
            plan,
        )

        self.policy.validate(context)
        self.assertEqual(context.query.parameters, ["%x%", 7])

    def test_composite_fk_and_quoted_identifiers_are_accepted(self) -> None:
        edge = _relation(
            "quoted-composite",
            'child" rows',
            'parent" rows',
            [("parent_a", "id_a"), ("parent_b", "id_b")],
        )
        plan = JoinPlan(
            anchor_table='parent" rows',
            required_tables=['child" rows', 'parent" rows'],
            steps=[
                JoinStep(
                    relation=edge,
                    traversal_from='parent" rows',
                    traversal_to='child" rows',
                )
            ],
        )
        context = _context(
            QueryIR(
                entity="Parent",
                select=["Parent.name", "Child.value"],
                limit=5,
            ),
            _profile(
                {
                    "Parent.name": ('parent" rows', 'na"me'),
                    "Child.value": ('child" rows', "value"),
                }
            ),
            plan,
        )

        self.policy.validate(context)


class AstReadOnlyPolicyRejectedSqlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = AstReadOnlyPolicy()
        edge = _relation("child-parent", "children", "parents")
        self.plan = JoinPlan(
            anchor_table="parents",
            required_tables=["children", "parents"],
            steps=[
                JoinStep(
                    relation=edge,
                    traversal_from="parents",
                    traversal_to="children",
                )
            ],
        )
        self.query_ir = QueryIR(
            entity="Parent",
            select=["Parent.name", "Child.name"],
            filters=[
                {"field": "Child.name", "operator": "contains", "value": "자료"}
            ],
            limit=20,
        )
        self.context = _context(
            self.query_ir,
            _profile(
                {
                    "Parent.name": ("parents", "name"),
                    "Child.name": ("children", "name"),
                }
            ),
            self.plan,
        )

    def assert_rejected(
        self,
        sql: str,
        code: HarnessErrorCode,
        *,
        parameters: list[str | int | float | bool] | None = None,
    ) -> None:
        with self.assertRaises(HarnessFailure) as raised:
            self.policy.validate(_with_sql(self.context, sql, parameters=parameters))
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(raised.exception.stage, HarnessStage.SQL_VALIDATED)

    def test_write_and_database_control_statements_are_rejected(self) -> None:
        cases = {
            "insert": 'INSERT INTO "parents" ("name") VALUES (?)',
            "update": 'UPDATE "parents" SET "name" = ?',
            "delete": 'DELETE FROM "parents"',
            "create": 'CREATE TABLE "x" ("id" INTEGER)',
            "drop": 'DROP TABLE "parents"',
            "alter": 'ALTER TABLE "parents" ADD COLUMN "x" TEXT',
            "pragma": "PRAGMA table_info('parents')",
            "attach": "ATTACH DATABASE 'other.db' AS other",
        }
        for name, sql in cases.items():
            with self.subTest(name=name):
                self.assert_rejected(sql, HarnessErrorCode.UNSAFE_SQL_NODE)

    def test_multiple_or_empty_statements_are_rejected(self) -> None:
        valid = self.context.query.sql
        for sql in (f"{valid}; SELECT 1", f"{valid}; ;"):
            with self.subTest(sql=sql):
                self.assert_rejected(sql, HarnessErrorCode.MULTIPLE_STATEMENTS)

    def test_disallowed_select_shapes_are_rejected(self) -> None:
        base = self.context.query.sql
        cases = {
            "union": f"{base} UNION SELECT NULL, NULL LIMIT ?",
            "intersect": f"{base} INTERSECT SELECT NULL, NULL LIMIT ?",
            "except": f"{base} EXCEPT SELECT NULL, NULL LIMIT ?",
            "cte": f'WITH "x" AS (SELECT 1) {base}',
            "subquery": f'SELECT * FROM ({base}) AS "x" LIMIT ?',
            "order": base.replace(" LIMIT ?", ' ORDER BY "t0"."name" LIMIT ?'),
            "group": base.replace(" LIMIT ?", ' GROUP BY "t0"."name" LIMIT ?'),
            "having": base.replace(" LIMIT ?", " HAVING 1 = 1 LIMIT ?"),
            "distinct": base.replace("SELECT ", "SELECT DISTINCT ", 1),
            "function": base.replace('"t0"."name"', 'LOWER("t0"."name")', 1),
            "arithmetic": base.replace('"t0"."name"', '"t0"."name" + 1', 1),
            "case": base.replace(
                '"t0"."name"',
                'CASE WHEN 1 = 1 THEN "t0"."name" END',
                1,
            ),
            "or": base.replace(" LIKE ?", ' LIKE ? OR "t0"."name" = ?', 1),
            "not": base.replace('"t1"."name" LIKE ?', 'NOT "t1"."name" LIKE ?'),
            "comment": "/* policy bypass */ " + base,
        }
        for name, sql in cases.items():
            with self.subTest(name=name):
                self.assert_rejected(sql, HarnessErrorCode.UNSAFE_SQL_NODE)

    def test_table_and_column_allowlist_are_enforced(self) -> None:
        base = self.context.query.sql
        cases = [
            (
                "table",
                base.replace('FROM "parents"', 'FROM "secrets"'),
                HarnessErrorCode.UNAUTHORIZED_TABLE,
            ),
            (
                "column",
                base.replace('"t0"."name"', '"t0"."secret"', 1),
                HarnessErrorCode.UNAUTHORIZED_COLUMN,
            ),
            (
                "unqualified",
                base.replace('"t0"."name"', '"name"', 1),
                HarnessErrorCode.UNAUTHORIZED_COLUMN,
            ),
        ]
        for name, sql, code in cases:
            with self.subTest(name=name):
                self.assert_rejected(sql, code)

    def test_projection_alias_and_order_are_enforced(self) -> None:
        base = self.context.query.sql
        first = '"t0"."name" AS "Parent.name"'
        second = '"t1"."name" AS "Child.name"'
        cases = [
            base.replace('AS "Parent.name"', 'AS "Other.name"', 1),
            base.replace(f"{first}, {second}", f"{second}, {first}"),
        ]
        for sql in cases:
            with self.subTest(sql=sql):
                self.assert_rejected(sql, HarnessErrorCode.INVALID_PROJECTION)

    def test_join_shape_is_enforced(self) -> None:
        base = self.context.query.sql
        join = (
            'JOIN "children" AS "t1" '
            'ON "t1"."parent_id" = "t0"."id"'
        )
        cases = {
            "extra": base.replace(
                " WHERE ",
                ' JOIN "secrets" AS "t2" ON "t2"."id" = "t0"."id" WHERE ',
            ),
            "missing": base.replace(" " + join, ""),
            "target": base.replace('JOIN "children"', 'JOIN "parents"'),
            "kind": base.replace('JOIN "children"', 'LEFT JOIN "children"'),
            "modifier": base.replace(
                'JOIN "children"',
                'DIRECTED JOIN "children"',
            ),
        }
        expected = {
            "extra": HarnessErrorCode.INVALID_JOIN_PREDICATE,
            "missing": HarnessErrorCode.INVALID_JOIN_PREDICATE,
            "target": HarnessErrorCode.UNAUTHORIZED_TABLE,
            "kind": HarnessErrorCode.UNSAFE_SQL_NODE,
            "modifier": HarnessErrorCode.UNSAFE_SQL_NODE,
        }
        for name, sql in cases.items():
            with self.subTest(name=name):
                self.assert_rejected(sql, expected[name])

    def test_join_order_is_enforced_for_multi_join(self) -> None:
        ab = _relation("ab", "b", "a", [("a_id", "id")])
        cb = _relation("cb", "c", "b", [("b_id", "id")])
        plan = JoinPlan(
            anchor_table="a",
            required_tables=["a", "b", "c"],
            steps=[
                JoinStep(relation=ab, traversal_from="a", traversal_to="b"),
                JoinStep(relation=cb, traversal_from="b", traversal_to="c"),
            ],
        )
        context = _context(
            QueryIR(
                entity="A",
                select=["A.name", "B.name", "C.name"],
                limit=8,
            ),
            _profile(
                {
                    "A.name": ("a", "name"),
                    "B.name": ("b", "name"),
                    "C.name": ("c", "name"),
                }
            ),
            plan,
        )
        sql = context.query.sql
        first = 'JOIN "b" AS "t1" ON "t1"."a_id" = "t0"."id"'
        second = 'JOIN "c" AS "t2" ON "t2"."b_id" = "t1"."id"'
        mutated = sql.replace(f" {first} {second}", f" {second} {first}")

        with self.assertRaises(HarnessFailure) as raised:
            self.policy.validate(_with_sql(context, mutated))
        self.assertEqual(raised.exception.code, HarnessErrorCode.UNAUTHORIZED_TABLE)
        self.assertEqual(raised.exception.stage, HarnessStage.SQL_VALIDATED)

    def test_join_predicate_is_exact_and_directional(self) -> None:
        base = self.context.query.sql
        predicate = '"t1"."parent_id" = "t0"."id"'
        cases = [
            base.replace(predicate, '"t0"."id" = "t1"."parent_id"'),
            base.replace(predicate, '"t1"."id" = "t0"."id"'),
            base.replace(predicate, '"t1"."parent_id" = "t1"."id"'),
            base.replace(predicate, predicate + " AND " + predicate),
            base.replace(predicate, predicate + ' AND "t1"."name" = ?'),
        ]
        for sql in cases:
            with self.subTest(sql=sql):
                self.assert_rejected(sql, HarnessErrorCode.INVALID_JOIN_PREDICATE)

    def test_composite_predicates_cannot_be_missing_or_duplicated(self) -> None:
        edge = _relation(
            "composite",
            "children",
            "parents",
            [("parent_a", "id_a"), ("parent_b", "id_b")],
        )
        plan = JoinPlan(
            anchor_table="parents",
            required_tables=["children", "parents"],
            steps=[
                JoinStep(
                    relation=edge,
                    traversal_from="parents",
                    traversal_to="children",
                )
            ],
        )
        context = _context(
            QueryIR(
                entity="Parent",
                select=["Parent.name", "Child.name"],
                limit=20,
            ),
            _profile(
                {
                    "Parent.name": ("parents", "name"),
                    "Child.name": ("children", "name"),
                }
            ),
            plan,
        )
        second = ' AND "t1"."parent_b" = "t0"."id_b"'
        first = '"t1"."parent_a" = "t0"."id_a"'
        cases = [
            context.query.sql.replace(second, ""),
            context.query.sql.replace(second, " AND " + first),
        ]
        for sql in cases:
            with self.subTest(sql=sql):
                with self.assertRaises(HarnessFailure) as raised:
                    self.policy.validate(_with_sql(context, sql))
                self.assertEqual(
                    raised.exception.code,
                    HarnessErrorCode.INVALID_JOIN_PREDICATE,
                )

    def test_placeholder_contract_is_enforced(self) -> None:
        base = self.context.query.sql
        cases = [
            (base.replace("LIKE ?", "LIKE :name"), self.context.query.parameters),
            (base, ["%자료%"]),
            (base, ["%자료%", 20, "extra"]),
            (base.replace("LIKE ?", "LIKE '자료'"), self.context.query.parameters),
        ]
        for sql, parameters in cases:
            with self.subTest(sql=sql, parameters=parameters):
                self.assert_rejected(
                    sql,
                    HarnessErrorCode.INVALID_PLACEHOLDER,
                    parameters=parameters,
                )

    def test_parameter_values_and_order_must_match_query_ir(self) -> None:
        cases = [
            [20, "%자료%"],
            ["%다른값%", 20],
            ["%자료%", 19],
        ]
        for parameters in cases:
            with self.subTest(parameters=parameters):
                self.assert_rejected(
                    self.context.query.sql,
                    HarnessErrorCode.INVALID_PLACEHOLDER,
                    parameters=parameters,
                )

    def test_limit_contract_is_enforced(self) -> None:
        base = self.context.query.sql
        cases = [
            base.replace("LIMIT ?", "LIMIT 20"),
            base.replace(" LIMIT ?", ""),
            base.replace("LIMIT ?", "LIMIT ? OFFSET ?"),
        ]
        parameters = ["%자료%", 20, 0]
        for index, sql in enumerate(cases):
            with self.subTest(sql=sql):
                self.assert_rejected(
                    sql,
                    HarnessErrorCode.INVALID_LIMIT,
                    parameters=parameters if index == 2 else self.context.query.parameters,
                )

    def test_parse_error_is_explicit(self) -> None:
        cases = [
            'SELECT "t0"."name" FROM',
            "SELECT 'unterminated",
            "SELECT 1 /* unterminated",
        ]
        for sql in cases:
            with self.subTest(sql=sql):
                self.assert_rejected(sql, HarnessErrorCode.SQL_PARSE_ERROR)

    def test_not_indexed_table_modifiers_are_rejected(self) -> None:
        base = self.context.query.sql
        cases = [
            base.replace(
                'FROM "parents" AS "t0"',
                'FROM "parents" AS "t0" NOT INDEXED',
            ),
            base.replace(
                'JOIN "children" AS "t1"',
                'JOIN "children" AS "t1" NOT INDEXED',
            ),
        ]
        for sql in cases:
            with self.subTest(sql=sql):
                self.assert_rejected(sql, HarnessErrorCode.UNSAFE_SQL_NODE)

    def test_ambiguous_join_result_is_rejected_before_sql_analysis(self) -> None:
        first = self.plan
        second_edge = _relation("other-path", "children", "parents")
        second = JoinPlan(
            anchor_table="parents",
            required_tables=["children", "parents"],
            steps=[
                JoinStep(
                    relation=second_edge,
                    traversal_from="parents",
                    traversal_to="children",
                )
            ],
        )
        ambiguous = JoinPlanningResult(
            status=JoinPlanStatus.AMBIGUOUS,
            semantic_fields=["Child.name", "Parent.name"],
            required_tables=["children", "parents"],
            alternatives=[first, second],
        )
        context = self.context.model_copy(update={"join_result": ambiguous})

        with self.assertRaises(HarnessFailure) as raised:
            self.policy.validate(context)
        self.assertEqual(raised.exception.code, HarnessErrorCode.AMBIGUOUS_JOIN_PATH)
        self.assertEqual(raised.exception.stage, HarnessStage.SQL_VALIDATED)


if __name__ == "__main__":
    unittest.main()
