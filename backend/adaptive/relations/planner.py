from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from functools import lru_cache

from backend.adaptive.mapping.contracts import PublishedMappingProfile
from backend.adaptive.relations.contracts import (
    JoinPlan,
    JoinPlanningResult,
    JoinPlanStatus,
    JoinStep,
    PhysicalRelation,
    PhysicalRelationGraph,
)
from backend.adaptive.relations.errors import (
    RelationErrorCode,
    RelationGraphError,
    RelationIssue,
)


def plan_join_path(
    graph: PhysicalRelationGraph,
    profile: PublishedMappingProfile,
    semantic_fields: Iterable[str],
    *,
    max_states: int = 50_000,
    anchor_table: str | None = None,
) -> JoinPlanningResult:
    """Plan the smallest FK-backed relation tree for published semantic fields.

    Relations are traversed in either direction, while each ``JoinStep`` retains
    the original child-to-parent FK relation for later SQL predicate generation.

    With ``anchor_table`` (Phase 7-17) that table is always required and is
    the plan's anchor, so every result row stands for one anchor row.
    """
    if max_states < 1:
        raise ValueError("max_states must be at least 1")

    normalized_fields = sorted(
        {field.strip() for field in semantic_fields if field.strip()}
    )
    if not normalized_fields:
        raise ValueError("semantic_fields must contain at least one non-empty field")

    _validate_profile(graph, profile)
    required_tables = _resolve_required_tables(
        graph, profile, normalized_fields
    )
    if anchor_table is None:
        anchor = required_tables[0]
    else:
        if anchor_table not in graph.tables:
            _raise_issue(
                RelationErrorCode.UNKNOWN_PHYSICAL_TABLE,
                "anchor table is absent from the graph",
            )
        anchor = anchor_table
        required_tables = sorted({*required_tables, anchor_table})

    if len(required_tables) == 1:
        return JoinPlanningResult(
            status=JoinPlanStatus.RESOLVED,
            semantic_fields=normalized_fields,
            required_tables=required_tables,
            plan=JoinPlan(
                anchor_table=anchor,
                required_tables=required_tables,
                steps=[],
            ),
        )

    solution_edge_sets = _find_minimum_edge_sets(
        graph,
        required_tables=set(required_tables),
        anchor=anchor,
        max_states=max_states,
    )
    if len(solution_edge_sets) > 1:
        # Phase 8-17: among equally short plans, the only one whose every FK
        # joins tables holding the same or directly related ontology entities
        # wins (a course's department, not its professor's department).
        confirmed = _confirmed_relation_ids(graph, profile)
        preferred = [edges for edges in solution_edge_sets if edges <= confirmed]
        if len(preferred) == 1:
            solution_edge_sets = preferred
    plans = [
        _build_plan(graph, anchor, required_tables, edge_ids)
        for edge_ids in solution_edge_sets
    ]

    if len(plans) == 1:
        return JoinPlanningResult(
            status=JoinPlanStatus.RESOLVED,
            semantic_fields=normalized_fields,
            required_tables=required_tables,
            plan=plans[0],
        )
    return JoinPlanningResult(
        status=JoinPlanStatus.AMBIGUOUS,
        semantic_fields=normalized_fields,
        required_tables=required_tables,
        alternatives=plans,
    )


def _validate_profile(
    graph: PhysicalRelationGraph, profile: PublishedMappingProfile
) -> None:
    mismatches: dict[str, dict[str, str]] = {}
    if profile.database_id != graph.database_id:
        mismatches["database_id"] = {
            "graph": graph.database_id,
            "profile": profile.database_id,
        }
    if profile.schema_fingerprint != graph.schema_fingerprint:
        mismatches["schema_fingerprint"] = {
            "graph": graph.schema_fingerprint,
            "profile": profile.schema_fingerprint,
        }
    if mismatches:
        _raise_issue(
            RelationErrorCode.PROFILE_MISMATCH,
            "published mapping profile does not match the relation graph",
            mismatches=mismatches,
        )


def _resolve_required_tables(
    graph: PhysicalRelationGraph,
    profile: PublishedMappingProfile,
    semantic_fields: list[str],
) -> list[str]:
    unknown_fields = [
        semantic_field
        for semantic_field in semantic_fields
        if semantic_field not in profile.fields
    ]
    if unknown_fields:
        _raise_issue(
            RelationErrorCode.UNKNOWN_SEMANTIC_FIELD,
            "published mapping has no entry for one or more semantic fields",
            semantic_fields=unknown_fields,
        )

    graph_tables = set(graph.tables)
    mapped_tables = {
        semantic_field: profile.fields[semantic_field].table
        for semantic_field in semantic_fields
    }
    unknown_tables = {
        semantic_field: table
        for semantic_field, table in mapped_tables.items()
        if table not in graph_tables
    }
    if unknown_tables:
        _raise_issue(
            RelationErrorCode.UNKNOWN_PHYSICAL_TABLE,
            "published mapping references one or more tables absent from the graph",
            field_tables=unknown_tables,
        )
    return sorted(set(mapped_tables.values()))


@lru_cache(maxsize=1)
def _ontology_relations() -> tuple[frozenset[str], frozenset[frozenset[str]]]:
    """(entity names, related entity pairs) of the default ontology."""
    from backend.adaptive.ontology import load_default_ontology

    ontology = load_default_ontology()
    entities = frozenset(entity.name for entity in ontology.entities)
    pairs = frozenset(
        frozenset((relation.source_entity, relation.target_entity))
        for relation in ontology.relations
    )
    return entities, pairs


def _confirmed_relation_ids(
    graph: PhysicalRelationGraph, profile: PublishedMappingProfile
) -> frozenset[str]:
    """FKs joining two mapped tables whose entities are equal or directly related.

    An FK touching a table without mapped fields, or a field outside the
    ontology, is never confirmed.
    """
    entities, related = _ontology_relations()
    hosted: dict[str, set[str]] = {}
    for semantic_field, target in profile.fields.items():
        hosted.setdefault(target.table, set()).add(semantic_field.split(".", 1)[0])
    confirmed = set()
    for relation in graph.relations:
        source = hosted.get(relation.source_table)
        target = hosted.get(relation.target_table)
        if not source or not target or not (source | target) <= entities:
            continue
        if source & target or any(
            frozenset((a, b)) in related for a in source for b in target
        ):
            confirmed.add(relation.relation_id)
    return frozenset(confirmed)


def _find_minimum_edge_sets(
    graph: PhysicalRelationGraph,
    *,
    required_tables: set[str],
    anchor: str,
    max_states: int,
) -> list[frozenset[str]]:
    _validate_required_reachability(graph, required_tables, anchor)
    relations = sorted(graph.relations, key=lambda relation: relation.relation_id)
    current_level: list[tuple[frozenset[str], frozenset[str]]] = [
        (frozenset({anchor}), frozenset())
    ]
    seen = set(current_level)
    state_count = 1

    while current_level:
        solutions = {
            edge_ids
            for tables, edge_ids in current_level
            if required_tables.issubset(tables)
        }
        if solutions:
            return sorted(solutions, key=lambda edge_ids: tuple(sorted(edge_ids)))

        next_states: set[tuple[frozenset[str], frozenset[str]]] = set()
        for tables, edge_ids in current_level:
            for relation in relations:
                source_inside = relation.source_table in tables
                target_inside = relation.target_table in tables
                if source_inside == target_inside:
                    continue
                new_table = (
                    relation.target_table if source_inside else relation.source_table
                )
                state = (
                    tables | {new_table},
                    edge_ids | {relation.relation_id},
                )
                if state in seen or state in next_states:
                    continue
                state_count += 1
                if state_count > max_states:
                    _raise_issue(
                        RelationErrorCode.SEARCH_LIMIT_EXCEEDED,
                        "join path search exceeded its deterministic state limit",
                        max_states=max_states,
                        required_tables=sorted(required_tables),
                    )
                next_states.add(state)
        seen.update(next_states)
        current_level = sorted(
            next_states,
            key=lambda state: (
                tuple(sorted(state[1])),
                tuple(sorted(state[0])),
            ),
        )

    _raise_issue(
        RelationErrorCode.DISCONNECTED_TABLES,
        "required tables cannot be connected using catalog foreign keys",
        required_tables=sorted(required_tables),
        anchor_table=anchor,
    )


def _validate_required_reachability(
    graph: PhysicalRelationGraph,
    required_tables: set[str],
    anchor: str,
) -> None:
    adjacency = {table: set() for table in graph.tables}
    for relation in graph.relations:
        adjacency[relation.source_table].add(relation.target_table)
        adjacency[relation.target_table].add(relation.source_table)

    reachable = {anchor}
    pending = deque([anchor])
    while pending:
        table = pending.popleft()
        for neighbor in sorted(adjacency[table]):
            if neighbor in reachable:
                continue
            reachable.add(neighbor)
            pending.append(neighbor)

    unreachable = sorted(required_tables - reachable)
    if unreachable:
        _raise_issue(
            RelationErrorCode.DISCONNECTED_TABLES,
            "required tables cannot be connected using catalog foreign keys",
            required_tables=sorted(required_tables),
            anchor_table=anchor,
            unreachable_tables=unreachable,
        )


def _build_plan(
    graph: PhysicalRelationGraph,
    anchor: str,
    required_tables: list[str],
    edge_ids: frozenset[str],
) -> JoinPlan:
    relation_by_id = {
        relation.relation_id: relation for relation in graph.relations
    }
    remaining = {
        relation_id: relation_by_id[relation_id]
        for relation_id in sorted(edge_ids)
    }
    joined = {anchor}
    steps: list[JoinStep] = []

    while remaining:
        relation_id, relation, traversal_from, traversal_to = _next_join_step(
            remaining.values(), joined
        )
        steps.append(
            JoinStep(
                relation=relation,
                traversal_from=traversal_from,
                traversal_to=traversal_to,
            )
        )
        joined.add(traversal_to)
        del remaining[relation_id]

    return JoinPlan(
        anchor_table=anchor,
        required_tables=required_tables,
        steps=steps,
    )


def _next_join_step(
    relations: Iterable[PhysicalRelation], joined: set[str]
) -> tuple[str, PhysicalRelation, str, str]:
    candidates: list[tuple[str, PhysicalRelation, str, str]] = []
    for relation in relations:
        source_inside = relation.source_table in joined
        target_inside = relation.target_table in joined
        if source_inside == target_inside:
            continue
        if source_inside:
            candidates.append(
                (
                    relation.relation_id,
                    relation,
                    relation.source_table,
                    relation.target_table,
                )
            )
        else:
            candidates.append(
                (
                    relation.relation_id,
                    relation,
                    relation.target_table,
                    relation.source_table,
                )
            )
    if not candidates:
        raise ValueError("join edge set is not connected to its anchor")
    return min(candidates, key=lambda candidate: candidate[0])


def _raise_issue(
    code: RelationErrorCode, message: str, **context: object
) -> None:
    raise RelationGraphError(
        RelationIssue(code=code, message=message, context=context)
    )
