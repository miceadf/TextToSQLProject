from __future__ import annotations

from pathlib import Path

import yaml

from .contracts import DomainOntology


def default_ontology_path() -> Path:
    return Path(__file__).with_name("course-domain.yaml")


def load_ontology(path: str | Path) -> DomainOntology:
    ontology_path = Path(path)
    with ontology_path.open("r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    return DomainOntology.model_validate(data)


def load_default_ontology() -> DomainOntology:
    return load_ontology(default_ontology_path())
