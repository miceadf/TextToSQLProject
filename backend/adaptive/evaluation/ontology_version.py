"""Select the ontology of an offline record, independently of product runtime."""

from ..ontology import DomainOntology, load_default_ontology


def recorded_ontology(
    ontology: DomainOntology | None = None, *, version: int = 2
) -> DomainOntology:
    """Unversioned pre-retirement records use v2; invalid versions fail closed."""
    return (ontology or load_default_ontology()).at_version(version)
