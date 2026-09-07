"""Canonical university-course ontology."""

from .contracts import (
    DataType,
    DomainOntology,
    EntityDefinition,
    OntologyCardinality,
    OntologyField,
    OntologyRelation,
    Operator,
)
from .loader import default_ontology_path, load_default_ontology, load_ontology

__all__ = [
    "DataType",
    "DomainOntology",
    "EntityDefinition",
    "OntologyCardinality",
    "OntologyField",
    "OntologyRelation",
    "Operator",
    "default_ontology_path",
    "load_default_ontology",
    "load_ontology",
]
