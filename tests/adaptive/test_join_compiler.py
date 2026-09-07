from __future__ import annotations

import unittest
from datetime import datetime, timezone

from backend.adaptive.compiler import (
    JoinCapableSQLiteCompiler,
    JoinCompilationRequest,
)
from backend.adaptive.harness import HarnessErrorCode, QueryIR
from backend.adaptive.harness.errors import HarnessFailure
from backend.adaptive.mapping import PublishedFieldMapping, PublishedMappingProfile
from backend.adaptive.relations import (
    JoinColumnPair,
    JoinPlan,
    JoinPlanningResult,
    JoinPlanStatus,
    JoinStep,
    PhysicalRelation,
)


def relation(
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


def profile(fields: dict[str, tuple[str, str]]) -> PublishedMappingProfile:
    return PublishedMappingProfile(
        database_id="compiler-db",
        version=1,
        schema_fingerprint="0" * 64,
        ontology_version=1,
        published_by="test-admin",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        fields={
            field: PublishedFieldMapping(
                table=table,
                column=column,
                reviewer="test-admin",
            )
            for field, (table, column) in fields.items()
        },
    )


def resolved_result(
    semantic_fields: list[str],
    plan: JoinPlan,
) -> JoinPlanningResult:
    return JoinPlanningResult(
        status=JoinPlanStatus.RESOLVED,
        semantic_fields=sorted(semantic_fields),
        required_tables=plan.required_tables,
        plan=plan,
    )


class JoinCapableSQLiteCompilerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.compiler = JoinCapableSQLiteCompiler()

    def test_reverse_join_qualifies_fields_and_binds_values(self) -> None:
        edge = relation("child-parent", "z_children", "a_parents")
        plan = JoinPlan(
            anchor_table="a_parents",
            required_tables=["a_parents", "z_children"],
            steps=[
                JoinStep(
                    relation=edge,
                    traversal_from="a_parents",
                    traversal_to="z_children",
                )
            ],
        )
        query_ir = QueryIR(
            entity="Parent",
            select=["Parent.name", "Child.name"],
            filters=[
                {"field": "Child.name", "operator": "contains", "value": "자료"}
            ],
            limit=20,
        )
        mapping = profile(
            {
                "Parent.name": ("a_parents", "name"),
                "Child.name": ("z_children", "name"),
            }
        )
        request = JoinCompilationRequest(
            query_ir=query_ir,
            mapping=mapping,
            join_result=resolved_result(
                ["Parent.name", "Child.name"],
                plan,
            ),
        )

        first = self.compiler.compile(request)
        second = self.compiler.compile(request)

        self.assertEqual(first, second)
        self.assertEqual(
            first.sql,
            'SELECT "t0"."name" AS "Parent.name", '
            '"t1"."name" AS "Child.name" '
            'FROM "a_parents" AS "t0" '
            'JOIN "z_children" AS "t1" '
            'ON "t1"."parent_id" = "t0"."id" '
            'WHERE "t1"."name" LIKE ? LIMIT ?',
        )
        self.assertEqual(first.parameters, ["%자료%", 20])
        self.assertNotIn("자료", first.sql)

    def test_composite_fk_emits_every_predicate_in_original_direction(self) -> None:
        edge = relation(
            "child-parent-composite",
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
        query_ir = QueryIR(
            entity="Item",
            select=["Parent.name", "Child.value"],
        )
        request = JoinCompilationRequest(
            query_ir=query_ir,
            mapping=profile(
                {
                    "Parent.name": ("parents", "name"),
                    "Child.value": ("children", "value"),
                }
            ),
            join_result=resolved_result(
                ["Parent.name", "Child.value"],
                plan,
            ),
        )

        compiled = self.compiler.compile(request)

        self.assertIn(
            'ON "t1"."parent_a" = "t0"."id_a" '
            'AND "t1"."parent_b" = "t0"."id_b"',
            compiled.sql,
        )

    def test_single_table_plan_has_no_join(self) -> None:
        plan = JoinPlan(
            anchor_table="courses",
            required_tables=["courses"],
            steps=[],
        )
        query_ir = QueryIR(entity="Course", select=["Course.name"], limit=5)
        request = JoinCompilationRequest(
            query_ir=query_ir,
            mapping=profile({"Course.name": ("courses", "name")}),
            join_result=resolved_result(["Course.name"], plan),
        )

        compiled = self.compiler.compile(request)

        self.assertNotIn(" JOIN ", compiled.sql)
        self.assertEqual(
            compiled.sql,
            'SELECT "t0"."name" AS "Course.name" '
            'FROM "courses" AS "t0" LIMIT ?',
        )
        self.assertEqual(compiled.parameters, [5])

    def test_identifiers_are_quoted_without_changing_aliases(self) -> None:
        plan = JoinPlan(
            anchor_table='odd" table',
            required_tables=['odd" table'],
            steps=[],
        )
        query_ir = QueryIR(entity="Course", select=["Course.name"])
        request = JoinCompilationRequest(
            query_ir=query_ir,
            mapping=profile({"Course.name": ('odd" table', 'na"me')}),
            join_result=resolved_result(["Course.name"], plan),
        )

        compiled = self.compiler.compile(request)

        self.assertIn('FROM "odd"" table" AS "t0"', compiled.sql)
        self.assertIn('"t0"."na""me" AS "Course.name"', compiled.sql)

    def test_ambiguous_path_is_never_compiled(self) -> None:
        edge_one = relation("path-one", "a", "b")
        edge_two = relation("path-two", "a", "b")
        alternatives = [
            JoinPlan(
                anchor_table="a",
                required_tables=["a", "b"],
                steps=[
                    JoinStep(
                        relation=edge,
                        traversal_from="a",
                        traversal_to="b",
                    )
                ],
            )
            for edge in (edge_one, edge_two)
        ]
        query_ir = QueryIR(entity="Item", select=["A.name", "B.name"])
        request = JoinCompilationRequest(
            query_ir=query_ir,
            mapping=profile(
                {"A.name": ("a", "name"), "B.name": ("b", "name")}
            ),
            join_result=JoinPlanningResult(
                status=JoinPlanStatus.AMBIGUOUS,
                semantic_fields=["A.name", "B.name"],
                required_tables=["a", "b"],
                alternatives=alternatives,
            ),
        )

        with self.assertRaises(HarnessFailure) as raised:
            self.compiler.compile(request)
        self.assertEqual(
            raised.exception.code,
            HarnessErrorCode.AMBIGUOUS_JOIN_PATH,
        )

    def test_mapping_plan_mismatch_is_rejected(self) -> None:
        plan = JoinPlan(anchor_table="a", required_tables=["a"], steps=[])
        query_ir = QueryIR(entity="Item", select=["B.name"])
        request = JoinCompilationRequest(
            query_ir=query_ir,
            mapping=profile({"B.name": ("b", "name")}),
            join_result=resolved_result(["B.name"], plan),
        )

        with self.assertRaises(HarnessFailure) as raised:
            self.compiler.compile(request)
        self.assertEqual(raised.exception.code, HarnessErrorCode.INVALID_JOIN_PLAN)

    def test_disconnected_constructed_plan_is_rejected(self) -> None:
        edge = relation("bc", "b", "c")
        unsafe_step = JoinStep.model_construct(
            relation=edge,
            traversal_from="b",
            traversal_to="c",
        )
        unsafe_plan = JoinPlan.model_construct(
            anchor_table="a",
            required_tables=["a", "c"],
            steps=[unsafe_step],
        )
        query_ir = QueryIR(entity="Item", select=["A.name", "C.name"])
        unsafe_result = JoinPlanningResult.model_construct(
            status=JoinPlanStatus.RESOLVED,
            semantic_fields=["A.name", "C.name"],
            required_tables=["a", "c"],
            plan=unsafe_plan,
            alternatives=[],
        )
        request = JoinCompilationRequest(
            query_ir=query_ir,
            mapping=profile({"A.name": ("a", "name"), "C.name": ("c", "name")}),
            join_result=unsafe_result,
        )

        with self.assertRaises(HarnessFailure) as raised:
            self.compiler.compile(request)
        self.assertEqual(raised.exception.code, HarnessErrorCode.INVALID_JOIN_PLAN)

    def test_self_join_fails_explicitly(self) -> None:
        self_edge = relation("self", "nodes", "nodes")
        unsafe_step = JoinStep.model_construct(
            relation=self_edge,
            traversal_from="nodes",
            traversal_to="nodes",
        )
        unsafe_plan = JoinPlan.model_construct(
            anchor_table="nodes",
            required_tables=["nodes"],
            steps=[unsafe_step],
        )
        query_ir = QueryIR(entity="Node", select=["Node.name"])
        unsafe_result = JoinPlanningResult.model_construct(
            status=JoinPlanStatus.RESOLVED,
            semantic_fields=["Node.name"],
            required_tables=["nodes"],
            plan=unsafe_plan,
            alternatives=[],
        )
        request = JoinCompilationRequest(
            query_ir=query_ir,
            mapping=profile({"Node.name": ("nodes", "name")}),
            join_result=unsafe_result,
        )

        with self.assertRaises(HarnessFailure) as raised:
            self.compiler.compile(request)
        self.assertEqual(
            raised.exception.code,
            HarnessErrorCode.SELF_JOIN_NOT_SUPPORTED,
        )


if __name__ == "__main__":
    unittest.main()
