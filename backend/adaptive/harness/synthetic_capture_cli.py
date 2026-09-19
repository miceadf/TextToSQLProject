"""Manual synthetic-only exercise of private capture storage."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .contracts import FieldMapping, HarnessRequest, MappingProfile, MappingStatus, Ontology
from .model import FakeModel
from .private_capture import capture_private_bundle
from .recording_bundle_contracts import ProviderKind, RecordingProvenance
from .recording_bundle_storage import RecordingBundleError, load_recording_bundle
from .recording_capture import RecordingCaptureError
from .recording_orchestrator import CaptureTarget, RecordingOrchestrationError


def _synthetic_inputs() -> tuple[FakeModel, list[CaptureTarget], RecordingProvenance]:
    ontology = Ontology(entities={"Course"}, fields={})
    mapping = MappingProfile(
        database_id="synthetic-capture",
        version=1,
        status=MappingStatus.PUBLISHED,
        fields={"Course.name": FieldMapping(table="synthetic_table", column="synthetic_name")},
    )
    questions = ("합성 질문 하나", "합성 질문 둘")
    targets = [
        CaptureTarget(
            request=HarnessRequest(
                request_id=f"synthetic-{index}",
                database_id=mapping.database_id,
                question=question,
            ),
            ontology=ontology,
            mapping=mapping,
        )
        for index, question in enumerate(questions, start=1)
    ]
    provider = FakeModel({
        questions[0]: {"entity": "Course", "select": ["Course.name"]},
        questions[1]: "synthetic non-QueryIR text",
    })
    provenance = RecordingProvenance(
        provider_kind=ProviderKind.SYNTHETIC,
        provider_id="synthetic-fake-model",
        provider_version="1",
        model_id="synthetic-capture-v1",
        prompt_template_sha256=hashlib.sha256(b"synthetic-capture-prompt-v1").hexdigest(),
        generation_config_sha256=hashlib.sha256(b"synthetic-capture-config-v1").hexdigest(),
        collector_version="synthetic-capture-v1",
    )
    return provider, targets, provenance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synthetic private capture only")
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-run-id", required=True)
    args = parser.parse_args(argv)
    if not args.synthetic:
        print('{"status":"NOT_RUN"}')
        return 2
    provider, targets, provenance = _synthetic_inputs()
    try:
        manifest_path = capture_private_bundle(
            args.model_run_id,
            provider,
            targets,
            provenance,
            args.output,
            repository_root=args.repository_root,
        )
        loaded = load_recording_bundle(manifest_path, repository_root=args.repository_root)
    except RecordingBundleError as failure:
        print(json.dumps({"status": "FAILED", "code": failure.code.value}))
        return 1
    except RecordingCaptureError as failure:
        print(json.dumps({"status": "FAILED", "code": failure.code.value}))
        return 1
    except RecordingOrchestrationError as failure:
        print(json.dumps({"status": "FAILED", "code": failure.code.value}))
        return 1
    except FileExistsError:
        print('{"status":"FAILED","code":"BUNDLE_EXISTS"}')
        return 1
    print(json.dumps({
        "status": "SYNTHETIC_CAPTURED",
        "record_count": loaded.manifest.record_count,
        "verification_status": loaded.manifest.provenance.verification_status,
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
