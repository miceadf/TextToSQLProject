"""Physical-to-semantic schema mapping."""

from .contracts import (
    MappingCandidate,
    MappingEvidence,
    MappingProposal,
    MappingReviewStatus,
    MappingSignal,
)
from .rules import RuleBasedSchemaAligner

__all__ = [
    "MappingCandidate",
    "MappingEvidence",
    "MappingProposal",
    "MappingReviewStatus",
    "MappingSignal",
    "RuleBasedSchemaAligner",
]
