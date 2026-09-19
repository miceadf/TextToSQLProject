"""Opt-in, synthetic-only Ollama adapter smoke; never stores model output."""

from __future__ import annotations

import argparse
import json
import time
from urllib.parse import urlsplit

from pydantic import ValidationError

from .contracts import (
    DataType,
    FieldMapping,
    HarnessErrorCode,
    HarnessRequest,
    MappingProfile,
    MappingStatus,
    Ontology,
    OntologyField,
    Operator,
    QueryIR,
)
from .errors import HarnessFailure
from .ollama import PROMPT_VERSION, OllamaGeneration, OllamaQueryModel
from .recording_orchestrator import RecordingProviderError
from .validator import validate_query_ir


_CASES = (
    ("synthetic-01", "가상 과목 중 이름에 별빛이 들어간 과목을 찾아줘."),
    ("synthetic-02", "가상 과목 중 학점이 3보다 큰 과목 이름을 알려줘."),
)


def _context() -> tuple[Ontology, MappingProfile]:
    ontology = Ontology(
        entities={"Course"},
        fields={
            "Course.name": OntologyField(
                name="Course.name",
                data_type=DataType.STRING,
                allowed_operators={Operator.EQUALS, Operator.CONTAINS},
            ),
            "Course.credits": OntologyField(
                name="Course.credits",
                data_type=DataType.INTEGER,
                allowed_operators={Operator.GREATER_THAN},
            ),
        },
    )
    mapping = MappingProfile(
        database_id="synthetic-smoke",
        version=1,
        status=MappingStatus.PUBLISHED,
        fields={
            "Course.name": FieldMapping(table="synthetic_courses", column="synthetic_name"),
            "Course.credits": FieldMapping(table="synthetic_courses", column="synthetic_credits"),
        },
    )
    return ontology, mapping


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    return "number"


def _classify(
    generation: OllamaGeneration, ontology: Ontology, mapping: MappingProfile
) -> dict[str, object]:
    if not generation.json_parsed:
        return {
            "status": "NON_JSON_TEXT",
            "json_parsed": False,
            "json_type": None,
            "ir_structure_valid": False,
            "semantic_validation_code": None,
        }

    result: dict[str, object] = {
        "status": "INVALID_QUERY_IR",
        "json_parsed": True,
        "json_type": _json_type(generation.value),
        "ir_structure_valid": False,
        "semantic_validation_code": None,
    }
    try:
        query_ir = QueryIR.model_validate(generation.value)
    except ValidationError:
        result["semantic_validation_code"] = HarnessErrorCode.MODEL_INVALID_OUTPUT.value
        return result
    result["ir_structure_valid"] = True
    try:
        validate_query_ir(query_ir, ontology, mapping)
    except HarnessFailure as failure:
        result["status"] = "INVALID_SEMANTICS"
        result["semantic_validation_code"] = failure.code.value
        return result
    result["status"] = "VALID_QUERY_IR"
    return result


def run_smoke(model: OllamaQueryModel) -> list[dict[str, object]]:
    """Run two fixed synthetic cases, retaining only safe classifications."""
    ontology, mapping = _context()
    summaries: list[dict[str, object]] = []
    for case_id, question in _CASES:
        item: dict[str, object] = {"case_id": case_id}
        started = time.perf_counter()
        try:
            generation = model.generate_observed(
                HarnessRequest(
                    request_id=case_id,
                    database_id=mapping.database_id,
                    question=question,
                ),
                ontology,
                mapping,
            )
        except RecordingProviderError as failure:
            item.update(
                status="PROVIDER_FAILURE",
                failure_code=failure.failure_code.value,
                json_parsed=None,
                json_type=None,
                ir_structure_valid=False,
                semantic_validation_code=None,
            )
            item["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
            summaries.append(item)
            break
        item.update(_classify(generation, ontology, mapping))
        item["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        summaries.append(item)
    return summaries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Opt-in synthetic Ollama adapter smoke")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model-tag", required=True)
    parser.add_argument("--timeout", type=float, required=True)
    args = parser.parse_args(argv)
    if not args.live:
        print('{"status":"NOT_RUN"}')
        return 2
    try:
        hostname = urlsplit(args.base_url).hostname
    except ValueError:
        hostname = None
    if hostname not in {"localhost", "127.0.0.1", "::1"}:
        print('{"status":"CONFIG_ERROR","code":"LOCAL_OLLAMA_REQUIRED"}')
        return 2
    try:
        model = OllamaQueryModel(args.base_url, args.model_tag, args.timeout)
    except ValueError as failure:
        print(json.dumps({"status": "CONFIG_ERROR", "code": str(failure)}))
        return 2
    cases = run_smoke(model)
    status = "COMPLETED" if len(cases) == len(_CASES) and all(
        case["status"] != "PROVIDER_FAILURE" for case in cases
    ) else "INCOMPLETE"
    print(
        json.dumps(
            {
                "status": status,
                "model_tag": args.model_tag,
                "prompt_version": PROMPT_VERSION,
                "timeout_seconds": args.timeout,
                "cases": cases,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return 0 if status == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
