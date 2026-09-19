"""Preflighted capture into existing private recording bundle storage."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .model import QueryModel
from .recording_bundle_contracts import RecordingProvenance
from .recording_bundle_storage import (
    RecordingBundleError,
    RecordingBundleErrorCode,
    preflight_recording_bundle,
    write_recording_bundle,
)
from .recording_capture import RecordingCaptureSession
from .recording_orchestrator import (
    CaptureTarget,
    capture_recording,
    preflight_capture,
)


def capture_private_bundle(
    model_run_id: str,
    provider: QueryModel,
    targets: Sequence[CaptureTarget],
    provenance: RecordingProvenance,
    output_dir: str | Path,
    *,
    repository_root: str | Path,
    overwrite: bool = False,
) -> Path:
    """Invoke each target once after all static inputs and output path pass preflight."""
    materialized_targets = list(targets)
    RecordingCaptureSession(model_run_id)
    preflight_capture(provider, materialized_targets)

    valid_provenance = isinstance(provenance, RecordingProvenance)
    if valid_provenance:
        try:
            provenance = RecordingProvenance.model_validate(
                provenance.model_dump(mode="json")
            )
        except Exception:
            valid_provenance = False
    if not valid_provenance:
        raise RecordingBundleError(RecordingBundleErrorCode.INVALID_CONTRACT)

    preflight_recording_bundle(
        output_dir, repository_root=repository_root, overwrite=overwrite
    )
    recording = capture_recording(model_run_id, provider, materialized_targets)
    return write_recording_bundle(
        output_dir,
        recording,
        provenance,
        repository_root=repository_root,
        overwrite=overwrite,
    )
