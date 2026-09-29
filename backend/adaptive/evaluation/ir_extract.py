"""Evaluation-only IR output extraction symmetric with the direct-SQL arm.

``single-fence-v1`` applies the same single-code-fence rule as
``direct-sql-extract-v1`` to QueryIR model output: when the output is a
string with exactly one fence, its body is parsed with the adapter's strict
JSON rules. Anything else is returned untouched so the existing failure
classification stays the same. No other repair. Runtime code never imports
this module.
"""

from __future__ import annotations

import json
from typing import Any

from ..harness.ollama import _finite_float, _reject_duplicate_keys, _reject_non_finite
from .direct_sql import find_code_fences


IR_EXTRACT_NONE = "none"
IR_EXTRACT_SINGLE_FENCE = "single-fence-v1"
IR_EXTRACT_RULES = (IR_EXTRACT_NONE, IR_EXTRACT_SINGLE_FENCE)


def extract_single_fence_ir(output: Any) -> Any:
    if not isinstance(output, str):
        return output
    fences = find_code_fences(output.strip())
    if len(fences) != 1:
        return output
    try:
        return json.loads(
            fences[0].strip(),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
            parse_float=_finite_float,
        )
    except (ValueError, RecursionError):
        # Same outcome as any unparsable output: keep the original string.
        return output


class SingleFenceIrModel:
    """Wrap a QueryModel; delegate once, then apply ``single-fence-v1``."""

    __slots__ = ("_delegate",)

    def __init__(self, delegate: Any) -> None:
        if not callable(getattr(delegate, "generate_ir", None)):
            raise ValueError("INVALID_IR_MODEL")
        self._delegate = delegate

    def __repr__(self) -> str:
        return "SingleFenceIrModel()"

    def generate_ir(self, request: Any, ontology: Any, mapping: Any) -> Any:
        return extract_single_fence_ir(self._delegate.generate_ir(request, ontology, mapping))
