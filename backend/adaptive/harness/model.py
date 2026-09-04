from __future__ import annotations

from typing import Any, Protocol

from .contracts import HarnessRequest, MappingProfile, Ontology


class QueryModel(Protocol):
    def generate_ir(
        self,
        request: HarnessRequest,
        ontology: Ontology,
        mapping: MappingProfile,
    ) -> Any:
        """Return data that can be validated as QueryIR."""


class FakeModel:
    """Deterministic model used by unit tests and lightweight development."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses

    def generate_ir(
        self,
        request: HarnessRequest,
        ontology: Ontology,
        mapping: MappingProfile,
    ) -> Any:
        del ontology, mapping
        if request.question not in self._responses:
            return {}
        return self._responses[request.question]
