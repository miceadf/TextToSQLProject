"""Deterministic, strict loader for the synthetic evaluation gold set."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from ..fixtures.contracts import FixtureSeed
from ..fixtures.seed import DEFAULT_SEED
from ..ontology.contracts import DomainOntology
from ..ontology.loader import load_default_ontology
from .contracts import EvaluationSet
from .validator import validate_evaluation_set


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _unique_yaml_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str):
            raise ValueError("evaluation object keys must be strings")
        if key in mapping:
            raise ValueError(f"duplicate evaluation object key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_yaml_mapping
)


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate evaluation object key: {key}")
        result[key] = value
    return result


def _require_explicit_ir_contract(raw: object) -> None:
    if not isinstance(raw, dict) or not isinstance(raw.get("cases"), list):
        raise ValueError("evaluation set must contain a case list")
    for index, case in enumerate(raw["cases"]):
        if not isinstance(case, dict):
            raise ValueError(f"case {index}: expected an object")
        ir = case.get("gold_query_ir")
        if not isinstance(ir, dict):
            raise ValueError(f"case {index}: gold_query_ir must be an object")
        if "limit" not in ir:
            raise ValueError(f"case {index}: gold_query_ir.limit must be explicit")
        extra = set(ir) - {"entity", "select", "filters", "limit"}
        if extra:
            raise ValueError(f"case {index}: unsupported gold_query_ir keys: {sorted(extra)}")
        filters = ir.get("filters", [])
        if not isinstance(filters, list):
            raise ValueError(f"case {index}: gold_query_ir.filters must be a list")
        for filter_index, item in enumerate(filters):
            if not isinstance(item, dict):
                raise ValueError(f"case {index} filter {filter_index}: expected an object")
            extra_filter = set(item) - {"field", "operator", "value"}
            if extra_filter:
                raise ValueError(
                    f"case {index} filter {filter_index}: unsupported keys: {sorted(extra_filter)}"
                )


def evaluation_set_bytes(evaluation_set: EvaluationSet) -> bytes:
    """Stable fingerprint input, independent of YAML formatting and absolute path."""
    payload = evaluation_set.model_dump(mode="json")
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def load_evaluation_set(
    path: str | Path,
    *,
    seed: FixtureSeed = DEFAULT_SEED,
    ontology: DomainOntology | None = None,
    require_full: bool = True,
) -> EvaluationSet:
    """Load a checked YAML/JSON gold set without reading any private dataset."""
    source = Path(path)
    raw_bytes = source.read_bytes()
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        raw_bytes = raw_bytes[3:]
    raw_text = raw_bytes.decode("utf-8", errors="strict")
    if source.suffix.lower() == ".json":
        raw = json.loads(raw_text, object_pairs_hook=_unique_json_object)
    elif source.suffix.lower() in {".yaml", ".yml"}:
        raw = yaml.load(raw_text, Loader=_UniqueKeyLoader)
    else:
        raise ValueError("evaluation file must be .json, .yaml, or .yml")
    _require_explicit_ir_contract(raw)
    evaluation_set = EvaluationSet.model_validate(raw)
    validate_evaluation_set(
        evaluation_set,
        seed=seed,
        ontology=ontology or load_default_ontology(),
        require_full=require_full,
    )
    return evaluation_set
