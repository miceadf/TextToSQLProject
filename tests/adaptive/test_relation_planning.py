from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from backend.adaptive.catalog import (
    ColumnCatalog,
    DatabaseCatalog,
    ForeignKeyCatalog,
    SQLiteAffinity,
    TableCatalog,
)
from backend.adaptive.fixtures import FixtureVariant, build_fixture_bundle
from backend.adaptive.mapping import PublishedFieldMapping, PublishedMappingProfile
from backend.adaptive.ontology import load_default_ontology
from backend.adaptive.relations import (
    JoinColumnPair,
    JoinPlanStatus,
    PhysicalRelation,
    PhysicalRelationGraph,
    RelationErrorCode,
    RelationGraphError,
)
from backend.adaptive.relations.ontology import build_ontology_relation_graph
from backend.adaptive.relations.physical import build_physical_relation_graph
from backend.adaptive.relations.planner import plan_join_path


FINGERPRINT = "0" * 64


def published_profile(
    graph: PhysicalRelationGraph,
    fields: dict[str, tuple[str, str]],
) -> PublishedMappingProfile:
    return PublishedMappingProfile(
        database_id=graph.database_id,
        version=1,
        schema_fingerprint=graph.schema_fingerprint,
        ontology_version=1,
        published_by="test-admin",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        fields={
            name: PublishedFieldMapping(
                table=table,
                column=column,
                reviewer="test-admin",
            )
            for name, (table, column) in fields.items()
        },
    )


def relation(
    relation_id: str,
    source: str,
    target: str,
) -> PhysicalRelation:
    return PhysicalRelation(
        relation_id=relation_id,
        source_table=source,
        target_table=target,
        column_pairs=[JoinColumnPair(source_column="parent_id", target_column="id")],
        on_update="NO ACTION",
        on_delete="NO ACTION",
        match="NONE",
    )


class FixtureRelationPlanningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.bundle = build_fixture_bundle(Path(self.temp_dir.name) / "fixtures")
        self.catalogs = {
            variant: DatabaseCatalog.model_validate_json(
                artifact.catalog_path.read_text(encoding="utf-8")
            )
            for variant, artifact in self.bundle.artifacts.items()
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_ontology_relation_graph_is_deterministic(self) -> None:
        graph = build_ontology_relation_graph(load_default_ontology())

        self.assertEqual(
            [relation.name for relation in graph.relations],
            [
                "Course.department",
                "CourseOffering.course",
                "CourseOffering.instructor",
                "CourseOffering.schedule",
            ],
        )
        self.assertEqual(
            graph.entities,
            ["Course", "CourseOffering", "Department", "Instructor", "Schedule"],
        )

    def test_fixture_graphs_preserve_expected_foreign_keys(self) -> None:
        flat = build_physical_relation_graph(self.catalogs[FixtureVariant.FLAT])
        legacy = build_physical_relation_graph(self.catalogs[FixtureVariant.LEGACY])
        normalized = build_physical_relation_graph(
            self.catalogs[FixtureVariant.NORMALIZED]
        )

        self.assertEqual(flat.relations, [])
        self.assertEqual(legacy.relations, [])
        predicates = {
            (
                edge.source_table,
                edge.column_pairs[0].source_column,
                edge.target_table,
                edge.column_pairs[0].target_column,
            )
            for edge in normalized.relations
        }
        self.assertEqual(
            predicates,
            {
                ("course_offerings", "course_code", "courses", "course_code"),
                ("courses", "department_code", "departments", "department_code"),
                ("course_offerings", "instructor_id", "instructors", "instructor_id"),
                ("schedules", "offering_id", "course_offerings", "offering_id"),
            },
        )

    def test_normalized_fixture_direct_and_multi_step_paths(self) -> None:
        graph = build_physical_relation_graph(
            self.catalogs[FixtureVariant.NORMALIZED]
        )
        profile = published_profile(
            graph,
            {
                "Course.name": ("courses", "course_name"),
                "Department.name": ("departments", "department_name"),
                "CourseOffering.year": ("course_offerings", "academic_year"),
                "Instructor.name": ("instructors", "instructor_name"),
                "Schedule.day": ("schedules", "day_of_week"),
            },
        )

        direct = plan_join_path(
            graph,
            profile,
            ["Course.name", "CourseOffering.year"],
        )
        self.assertEqual(direct.status, JoinPlanStatus.RESOLVED)
        self.assertEqual(len(direct.plan.steps), 1)
        self.assertEqual(
            (
                direct.plan.steps[0].relation.source_table,
                direct.plan.steps[0].relation.target_table,
            ),
            ("course_offerings", "courses"),
        )

        multi = plan_join_path(
            graph,
            profile,
            ["Department.name", "Schedule.day", "Instructor.name"],
        )
        self.assertEqual(multi.status, JoinPlanStatus.RESOLVED)
        self.assertEqual(len(multi.plan.steps), 4)
        self.assertEqual(
            {step.relation.relation_id for step in multi.plan.steps},
            {edge.relation_id for edge in graph.relations},
        )

    def test_flat_and_legacy_profiles_need_no_join(self) -> None:
        targets = {
            FixtureVariant.FLAT: {
                "Course.name": ("course_offerings", "course_name"),
                "Schedule.day": ("course_offerings", "day"),
            },
            FixtureVariant.LEGACY: {
                "Course.name": ("lecture_info", "sbjt_nm"),
                "Schedule.day": ("lecture_info", "lecture_day"),
            },
        }
        for variant, fields in targets.items():
            with self.subTest(variant=variant.value):
                graph = build_physical_relation_graph(self.catalogs[variant])
                profile = published_profile(graph, fields)
                result = plan_join_path(
                    graph,
                    profile,
                    ["Schedule.day", "Course.name"],
                )
                self.assertEqual(result.status, JoinPlanStatus.RESOLVED)
                self.assertEqual(result.plan.steps, [])


class RelationEdgeCaseTest(unittest.TestCase):
    def test_composite_foreign_key_becomes_one_ordered_edge(self) -> None:
        column = lambda name: ColumnCatalog(  # noqa: E731
            name=name,
            declared_type="TEXT",
            affinity=SQLiteAffinity.TEXT,
            nullable=False,
        )
        catalog = DatabaseCatalog(
            database_id="composite",
            schema_fingerprint=FINGERPRINT,
            tables=[
                TableCatalog(
                    name="children",
                    columns=[column("parent_a"), column("parent_b")],
                    foreign_keys=[
                        ForeignKeyCatalog(
                            id=0,
                            sequence=1,
                            column="parent_b",
                            referenced_table="parents",
                            referenced_column="id_b",
                            on_update="NO ACTION",
                            on_delete="CASCADE",
                            match="NONE",
                        ),
                        ForeignKeyCatalog(
                            id=0,
                            sequence=0,
                            column="parent_a",
                            referenced_table="parents",
                            referenced_column="id_a",
                            on_update="NO ACTION",
                            on_delete="CASCADE",
                            match="NONE",
                        ),
                    ],
                ),
                TableCatalog(
                    name="parents",
                    columns=[column("id_a"), column("id_b")],
                ),
            ],
        )

        graph = build_physical_relation_graph(catalog)

        self.assertEqual(len(graph.relations), 1)
        self.assertEqual(
            [pair.model_dump() for pair in graph.relations[0].column_pairs],
            [
                {"source_column": "parent_a", "target_column": "id_a"},
                {"source_column": "parent_b", "target_column": "id_b"},
            ],
        )

    def test_malformed_composite_foreign_key_is_rejected(self) -> None:
        def column(name: str) -> ColumnCatalog:
            return ColumnCatalog(
                name=name,
                declared_type="TEXT",
                affinity=SQLiteAffinity.TEXT,
                nullable=False,
            )

        def catalog_with(rows: list[ForeignKeyCatalog]) -> DatabaseCatalog:
            return DatabaseCatalog(
                database_id="malformed-composite",
                schema_fingerprint=FINGERPRINT,
                tables=[
                    TableCatalog(
                        name="children",
                        columns=[column("left_id"), column("right_id")],
                        foreign_keys=rows,
                    ),
                    TableCatalog(
                        name="parents",
                        columns=[column("left_id"), column("right_id")],
                    ),
                ],
            )

        def fk(sequence: int, source: str, target: str) -> ForeignKeyCatalog:
            return ForeignKeyCatalog(
                id=0,
                sequence=sequence,
                column=source,
                referenced_table="parents",
                referenced_column=target,
                on_update="NO ACTION",
                on_delete="NO ACTION",
                match="NONE",
            )

        malformed_rows = [
            [fk(0, "left_id", "left_id"), fk(2, "right_id", "right_id")],
            [fk(0, "left_id", "left_id"), fk(1, "left_id", "right_id")],
            [fk(0, "left_id", "left_id"), fk(1, "right_id", "left_id")],
        ]
        for rows in malformed_rows:
            with self.subTest(rows=[row.model_dump() for row in rows]):
                with self.assertRaises(RelationGraphError) as raised:
                    build_physical_relation_graph(catalog_with(rows))
                self.assertEqual(
                    raised.exception.issue.code,
                    RelationErrorCode.INVALID_FOREIGN_KEY,
                )

    def test_reverse_traversal_preserves_original_predicate_direction(self) -> None:
        edge = relation("fk-child-parent", "z_child", "a_parent")
        graph = PhysicalRelationGraph(
            database_id="reverse",
            schema_fingerprint=FINGERPRINT,
            tables=["a_parent", "z_child"],
            relations=[edge],
        )
        profile = published_profile(
            graph,
            {"Parent.id": ("a_parent", "id"), "Child.id": ("z_child", "id")},
        )

        result = plan_join_path(graph, profile, ["Parent.id", "Child.id"])

        step = result.plan.steps[0]
        self.assertEqual((step.traversal_from, step.traversal_to), ("a_parent", "z_child"))
        self.assertEqual(
            (step.relation.source_table, step.relation.target_table),
            ("z_child", "a_parent"),
        )

    def test_cycle_terminates_and_chooses_unique_shortest_edge(self) -> None:
        graph = PhysicalRelationGraph(
            database_id="cycle",
            schema_fingerprint=FINGERPRINT,
            tables=["a", "b", "c"],
            relations=[
                relation("ab", "a", "b"),
                relation("bc", "b", "c"),
                relation("ca", "c", "a"),
            ],
        )
        profile = published_profile(
            graph,
            {"A.id": ("a", "id"), "B.id": ("b", "id")},
        )

        result = plan_join_path(graph, profile, ["A.id", "B.id"])

        self.assertEqual(result.status, JoinPlanStatus.RESOLVED)
        self.assertEqual([step.relation.relation_id for step in result.plan.steps], ["ab"])

    def test_equal_shortest_paths_return_ambiguity(self) -> None:
        graph = PhysicalRelationGraph(
            database_id="diamond",
            schema_fingerprint=FINGERPRINT,
            tables=["a", "b", "c", "d"],
            relations=[
                relation("ab", "a", "b"),
                relation("bd", "b", "d"),
                relation("ac", "a", "c"),
                relation("cd", "c", "d"),
            ],
        )
        profile = published_profile(
            graph,
            {"A.id": ("a", "id"), "D.id": ("d", "id")},
        )

        result = plan_join_path(graph, profile, ["D.id", "A.id"])

        self.assertEqual(result.status, JoinPlanStatus.AMBIGUOUS)
        self.assertIsNone(result.plan)
        self.assertEqual(len(result.alternatives), 2)
        self.assertEqual(
            [
                [step.relation.relation_id for step in plan.steps]
                for plan in result.alternatives
            ],
            [["ab", "bd"], ["ac", "cd"]],
        )

    def test_disconnected_and_unknown_targets_fail_explicitly(self) -> None:
        graph = PhysicalRelationGraph(
            database_id="broken",
            schema_fingerprint=FINGERPRINT,
            tables=["a", "b"],
            relations=[],
        )
        profile = published_profile(
            graph,
            {"A.id": ("a", "id"), "B.id": ("b", "id")},
        )
        with self.assertRaises(RelationGraphError) as disconnected:
            plan_join_path(graph, profile, ["A.id", "B.id"], max_states=1)
        self.assertEqual(
            disconnected.exception.issue.code,
            RelationErrorCode.DISCONNECTED_TABLES,
        )

        with self.assertRaises(RelationGraphError) as unknown_field:
            plan_join_path(graph, profile, ["Missing.id"])
        self.assertEqual(
            unknown_field.exception.issue.code,
            RelationErrorCode.UNKNOWN_SEMANTIC_FIELD,
        )

        invalid_profile = published_profile(graph, {"Ghost.id": ("ghost", "id")})
        with self.assertRaises(RelationGraphError) as unknown_table:
            plan_join_path(graph, invalid_profile, ["Ghost.id"])
        self.assertEqual(
            unknown_table.exception.issue.code,
            RelationErrorCode.UNKNOWN_PHYSICAL_TABLE,
        )

    def test_profile_mismatch_and_search_limit_fail_explicitly(self) -> None:
        graph = PhysicalRelationGraph(
            database_id="limits",
            schema_fingerprint=FINGERPRINT,
            tables=["a", "b", "c"],
            relations=[relation("ab", "a", "b"), relation("bc", "b", "c")],
        )
        profile = published_profile(
            graph,
            {"A.id": ("a", "id"), "C.id": ("c", "id")},
        )
        mismatched = profile.model_copy(update={"database_id": "another-db"})
        with self.assertRaises(RelationGraphError) as mismatch:
            plan_join_path(graph, mismatched, ["A.id"])
        self.assertEqual(
            mismatch.exception.issue.code,
            RelationErrorCode.PROFILE_MISMATCH,
        )

        with self.assertRaises(RelationGraphError) as limited:
            plan_join_path(graph, profile, ["A.id", "C.id"], max_states=1)
        self.assertEqual(
            limited.exception.issue.code,
            RelationErrorCode.SEARCH_LIMIT_EXCEEDED,
        )


if __name__ == "__main__":
    unittest.main()
