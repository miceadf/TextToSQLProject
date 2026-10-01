"""Explicit, private input boundary for manual Ollama recording capture."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pydantic import ValidationError

from .contracts import MappingStatus
from .ollama import OllamaQueryModel, OllamaTransport, _default_transport
from .private_capture import (
    PreparedPrivateCapture,
    capture_preflighted_private_bundle,
    preflight_private_capture,
)
from .recording_bundle_storage import (
    RecordingBundleError,
    RecordingBundleErrorCode,
    _read_private_file,
    _validate_repository,
    _validate_target,
    load_recording_bundle,
    provenance_fingerprint,
)
from .recording_capture import RecordingCaptureError
from .recording_orchestrator import CaptureTarget, RecordingOrchestrationError


TARGET_SCHEMA_VERSION = "capture-targets-v1"


class LiveCaptureErrorCode(str, Enum):
    INVALID_INPUT = "INVALID_INPUT"
    INVALID_TARGETS = "INVALID_TARGETS"
    MAPPING_NOT_PUBLISHED = "MAPPING_NOT_PUBLISHED"


class LiveCaptureError(ValueError):
    def __init__(self, code: LiveCaptureErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_non_finite(_: str) -> None:
    raise ValueError("non-finite number")


def _exact_keys(value: object, keys: set[str]) -> bool:
    return isinstance(value, dict) and set(value) == keys


def _validate_shape(value: object) -> bool:
    if not _exact_keys(value, {"request", "ontology", "mapping"}):
        return False
    request, ontology, mapping = (value[key] for key in ("request", "ontology", "mapping"))
    if not _exact_keys(request, {"request_id", "database_id", "question"}):
        return False
    if not all(isinstance(item, str) for item in request.values()):
        return False
    if not _exact_keys(ontology, {"entities", "fields"}) or not isinstance(ontology["fields"], dict):
        return False
    if not isinstance(ontology["entities"], list) or not all(isinstance(item, str) for item in ontology["entities"]):
        return False
    if len(ontology["entities"]) != len(set(ontology["entities"])):
        return False
    base_keys = {"name", "data_type", "allowed_operators"}
    # Phase 8-19: an optional label (None, or text for an extension field).
    if not all(
        _exact_keys(field, base_keys) or _exact_keys(field, base_keys | {"label"})
        for field in ontology["fields"].values()
    ):
        return False
    for name, field in ontology["fields"].items():
        if not isinstance(name, str) or not isinstance(field["name"], str) or not isinstance(field["data_type"], str):
            return False
        if not (field.get("label") is None or isinstance(field.get("label"), str)):
            return False
        if not isinstance(field["allowed_operators"], list) or not all(isinstance(item, str) for item in field["allowed_operators"]):
            return False
        if len(field["allowed_operators"]) != len(set(field["allowed_operators"])):
            return False
    if not _exact_keys(mapping, {"database_id", "version", "status", "fields"}) or not isinstance(mapping["fields"], dict):
        return False
    if not isinstance(mapping["database_id"], str) or type(mapping["version"]) is not int or not isinstance(mapping["status"], str):
        return False
    return all(
        isinstance(name, str) and _exact_keys(field, {"table", "column"})
        and all(isinstance(item, str) for item in field.values())
        for name, field in mapping["fields"].items()
    )


def load_private_targets(path: str | Path, *, repository_root: str | Path) -> list[CaptureTarget]:
    """Load one git-ignored private JSON file without exposing its contents in errors."""
    root = _validate_repository(repository_root)
    candidate = _validate_target(path, root)
    try:
        raw = _read_private_file(candidate, candidate.parent, RecordingBundleErrorCode.INVALID_PATH)
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (UnicodeError, ValueError, OSError, RecordingBundleError):
        raise LiveCaptureError(LiveCaptureErrorCode.INVALID_INPUT) from None
    if not _exact_keys(document, {"schema_version", "targets"}) or document["schema_version"] != TARGET_SCHEMA_VERSION:
        raise LiveCaptureError(LiveCaptureErrorCode.INVALID_INPUT)
    values = document["targets"]
    if not isinstance(values, list) or not values or not all(_validate_shape(value) for value in values):
        raise LiveCaptureError(LiveCaptureErrorCode.INVALID_TARGETS)
    try:
        targets = [CaptureTarget.model_validate(value, extra="forbid") for value in values]
    except ValidationError:
        raise LiveCaptureError(LiveCaptureErrorCode.INVALID_TARGETS) from None
    if any(target.mapping.status is not MappingStatus.PUBLISHED for target in targets):
        raise LiveCaptureError(LiveCaptureErrorCode.MAPPING_NOT_PUBLISHED)
    return targets


@dataclass(frozen=True, repr=False)
class PreparedLiveCapture:
    model: OllamaQueryModel
    private: PreparedPrivateCapture

    @property
    def provenance_sha256(self) -> str:
        return provenance_fingerprint(self.private.provenance)


def preflight_live_capture(
    *,
    target_file: str | Path,
    model_tag: str,
    base_url: str,
    timeout: float,
    ollama_version: str,
    model_digest: str | None,
    model_run_id: str,
    repository_root: str | Path,
    output: str | Path,
    transport: OllamaTransport | None = None,
    prompt_version: str = "query-ir-v1",
) -> PreparedLiveCapture:
    """Validate live settings and private inputs without calling or writing."""
    targets = load_private_targets(target_file, repository_root=repository_root)
    model = OllamaQueryModel(base_url, model_tag, timeout, transport=transport or _default_transport, prompt_version=prompt_version)
    provenance = model.declared_provenance(ollama_version, model_digest)
    prepared = preflight_private_capture(
        model_run_id, model, targets, provenance, output, repository_root=repository_root
    )
    return PreparedLiveCapture(model=model, private=prepared)


def capture_live(
    *,
    target_file: str | Path,
    model_tag: str,
    base_url: str,
    timeout: float,
    ollama_version: str,
    model_digest: str | None,
    model_run_id: str,
    repository_root: str | Path,
    output: str | Path,
    transport: OllamaTransport | None = None,
    prompt_version: str = "query-ir-v1",
) -> tuple[int, int, int, str]:
    """Capture only after full input, provenance, and storage preflight."""
    prepared = preflight_live_capture(
        target_file=target_file, model_tag=model_tag, base_url=base_url,
        timeout=timeout, ollama_version=ollama_version, model_digest=model_digest,
        model_run_id=model_run_id, repository_root=repository_root,
        output=output, transport=transport, prompt_version=prompt_version,
    )
    manifest_path = capture_preflighted_private_bundle(prepared.private, prepared.model)
    manifest = load_recording_bundle(manifest_path, repository_root=repository_root).manifest
    return (
        manifest.record_count,
        manifest.success_count,
        manifest.failure_count,
        prepared.provenance_sha256,
    )


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise LiveCaptureError(LiveCaptureErrorCode.INVALID_INPUT)


def main(argv: list[str] | None = None) -> int:
    parser = _SafeParser(description="Manual private Ollama capture")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--target-file", required=True)
    parser.add_argument("--model-tag", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--ollama-version", required=True)
    parser.add_argument("--model-digest")
    parser.add_argument("--prompt-version", choices=("query-ir-v1", "query-ir-v2"), default="query-ir-v1")
    parser.add_argument("--model-run-id", required=True)
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--output", required=True)
    try:
        args = parser.parse_args(argv)
        if not (args.live or args.check):
            print('{"status":"NOT_RUN"}')
            return 2
        settings = dict(
            target_file=args.target_file,
            model_tag=args.model_tag,
            base_url=args.base_url,
            timeout=args.timeout,
            ollama_version=args.ollama_version,
            model_digest=args.model_digest,
            model_run_id=args.model_run_id,
            repository_root=args.repository_root,
            output=args.output,
            prompt_version=args.prompt_version,
        )
        if args.check:
            prepared = preflight_live_capture(**settings)
            print(json.dumps({
                "status": "CHECKED",
                "target_count": len(prepared.private.targets),
                "provenance_sha256": prepared.provenance_sha256,
                "verification_status": "DECLARED_UNVERIFIED",
            }, separators=(",", ":")))
            return 0
        count, successes, failures, fingerprint = capture_live(**settings)
    except (LiveCaptureError, RecordingBundleError, RecordingCaptureError, RecordingOrchestrationError) as failure:
        print(json.dumps({"status": "FAILED", "code": failure.code.value}, separators=(",", ":")))
        return 1
    except FileExistsError:
        print('{"status":"FAILED","code":"BUNDLE_EXISTS"}')
        return 1
    except ValueError:
        print('{"status":"FAILED","code":"INVALID_CONFIG"}')
        return 1
    except Exception:
        print('{"status":"FAILED","code":"CAPTURE_ERROR"}')
        return 1
    print(json.dumps({
        "status": "CAPTURED",
        "record_count": count,
        "success_count": successes,
        "failure_count": failures,
        "provenance_sha256": fingerprint,
        "verification_status": "DECLARED_UNVERIFIED",
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
