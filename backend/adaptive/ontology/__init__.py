"""Canonical university-course ontology."""

from .contracts import (
    DataType,
    DomainOntology,
    EntityDefinition,
    OntologyField,
    Operator,
)
from .loader import default_ontology_path, load_default_ontology, load_ontology

__all__ = [
    "DataType",
    "DomainOntology",
    "EntityDefinition",
    "OntologyField",
    "Operator",
    "default_ontology_path",
    "load_default_ontology",
    "load_ontology",
]
