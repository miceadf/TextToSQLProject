"""Evaluation tools for versioned gold, model comparisons, and teammate DBs.

Includes fixture replay, mapping checks, blind evaluation, and human-review
records; results keep their declared ontology version and evaluation conditions.
"""

from .contracts import EvaluationCase, EvaluationSet, ExpectedSemanticResult
from .reference import ontology_fingerprint, query_ir_fingerprint, seed_fingerprint

__all__ = [
    "EvaluationCase", "EvaluationSet", "ExpectedSemanticResult",
    "ontology_fingerprint", "query_ir_fingerprint", "seed_fingerprint",
]
