from __future__ import annotations

from backend.adaptive.ontology.contracts import DomainOntology

from .contracts import OntologyRelationGraph


def build_ontology_relation_graph(
    ontology: DomainOntology,
) -> OntologyRelationGraph:
    """Build a deterministic relation graph without mutating the ontology."""

    entities = sorted(entity.name for entity in ontology.entities)
    relations = sorted(
        (relation.model_copy(deep=True) for relation in ontology.relations),
        key=lambda relation: (
            relation.name,
            relation.source_entity,
            relation.target_entity,
            relation.cardinality.value,
        ),
    )

    return OntologyRelationGraph(
        ontology_version=ontology.version,
        entities=entities,
        relations=relations,
    )
