"""Cross-set isolation between the development set and the independent draft.

`validate_evaluation_set()` only checks consistency inside one set. This
module checks overlap between two sets without changing the generic
validator or loader contracts. Failures use fixed codes only and never
include original questions, gold values, or case identifiers.

Automatic checks cannot catch every paraphrase with similar meaning;
independent human review is still required.
"""

from __future__ import annotations

import unicodedata

from .contracts import EvaluationSet
from .validator import _intent_identity


def _normalized_question(question: str) -> str:
    return unicodedata.normalize("NFC", " ".join(question.split()))


def check_set_isolation(candidate: EvaluationSet, reference: EvaluationSet) -> None:
    """Reject any overlap between two evaluation sets.

    Requires different benchmark IDs, disjoint case IDs, disjoint
    normalized question sentences, no reused paraphrase family IDs, and
    no identical QueryIR intents under the existing `_intent_identity()`
    rule. Only non-empty paraphrase families are compared, so sets that
    leave the field empty do not collide with each other.
    """
    if candidate.benchmark_id == reference.benchmark_id:
        raise ValueError("ISOLATION_SAME_BENCHMARK")
    candidate_ids = {case.case_id for case in candidate.cases}
    reference_ids = {case.case_id for case in reference.cases}
    if candidate_ids & reference_ids:
        raise ValueError("ISOLATION_CASE_ID_OVERLAP")
    candidate_questions = {_normalized_question(case.question_ko) for case in candidate.cases}
    reference_questions = {_normalized_question(case.question_ko) for case in reference.cases}
    if candidate_questions & reference_questions:
        raise ValueError("ISOLATION_QUESTION_OVERLAP")
    candidate_families = {
        case.paraphrase_family for case in candidate.cases if case.paraphrase_family
    }
    reference_families = {
        case.paraphrase_family for case in reference.cases if case.paraphrase_family
    }
    if candidate_families & reference_families:
        raise ValueError("ISOLATION_PARAPHRASE_FAMILY_REUSE")
    candidate_intents = {_intent_identity(case.gold_query_ir) for case in candidate.cases}
    reference_intents = {_intent_identity(case.gold_query_ir) for case in reference.cases}
    if candidate_intents & reference_intents:
        raise ValueError("ISOLATION_INTENT_OVERLAP")
