"""Preflighted capture into existing private recording bundle storage."""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True, repr=False)
class PreparedPrivateCapture:
    model_run_id: str
    targets: tuple[CaptureTarget, ...]
    provenance: RecordingProvenance
    output_dir: str | Path
    repository_root: str | Path
    overwrite: bool


def preflight_private_capture(
    model_run_id: str,
    provider: QueryModel,
    targets: Sequence[CaptureTarget],
    provenance: RecordingProvenance,
    output_dir: str | Path,
    *,
    repository_root: str | Path,
    overwrite: bool = False,
) -> PreparedPrivateCapture:
    """Validate all static inputs and storage policy without calls or writes."""
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
    return PreparedPrivateCapture(
        model_run_id=model_run_id,
        targets=tuple(materialized_targets),
        provenance=provenance,
        output_dir=output_dir,
        repository_root=repository_root,
        overwrite=overwrite,
    )


def capture_preflighted_private_bundle(
    prepared: PreparedPrivateCapture, provider: QueryModel
) -> Path:
    """Capture a prepared batch; writer rechecks the path before publishing."""
    recording = capture_recording(prepared.model_run_id, provider, prepared.targets)
    return write_recording_bundle(
        prepared.output_dir,
        recording,
        prepared.provenance,
        repository_root=prepared.repository_root,
        overwrite=prepared.overwrite,
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
    prepared = preflight_private_capture(
        model_run_id, provider, targets, provenance, output_dir,
        repository_root=repository_root, overwrite=overwrite,
    )
    return capture_preflighted_private_bundle(prepared, provider)
