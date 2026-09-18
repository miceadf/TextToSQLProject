"""Deterministic execution harness around a non-deterministic query model."""

from .contracts import (
    CompiledQuery,
    DataType,
    FieldMapping,
    FilterIR,
    HarnessErrorCode,
    HarnessRequest,
    HarnessResult,
    HarnessStage,
    HarnessStatus,
    MappingProfile,
    MappingStatus,
    Ontology,
    OntologyField,
    Operator,
    QueryIR,
)
from .compiler import SingleTableSQLiteCompiler
from .engine import ExecutionHarness
from .executor import SQLiteReadOnlyExecutor
from .model import FakeModel
from .recorded import (
    ModelRecording,
    RecordedFailure,
    RecordedFailureCode,
    RecordedModel,
    RecordedModelReplayError,
    RecordedReplayErrorCode,
    RecordedSuccess,
    RecordingLoadError,
    load_model_recording,
    mapping_fingerprint,
    question_fingerprint,
)
from .policy import ConservativeSqlPolicy
from .recording_capture import (
    RecordingCaptureError,
    RecordingCaptureErrorCode,
    RecordingCaptureSession,
)
from .recording_bundle_contracts import (
    LoadedRecordingBundle,
    ProviderKind,
    RecordingArtifactMetadata,
    RecordingBundleManifest,
    RecordingProvenance,
)
from .recording_bundle_storage import (
    RecordingBundleError,
    RecordingBundleErrorCode,
    load_recording_bundle,
    provenance_fingerprint,
    write_recording_bundle,
)

__all__ = [
    "CompiledQuery",
    "ConservativeSqlPolicy",
    "DataType",
    "ExecutionHarness",
    "FakeModel",
    "FieldMapping",
    "FilterIR",
    "HarnessErrorCode",
    "HarnessRequest",
    "HarnessResult",
    "HarnessStage",
    "HarnessStatus",
    "MappingProfile",
    "MappingStatus",
    "Ontology",
    "OntologyField",
    "Operator",
    "QueryIR",
    "LoadedRecordingBundle",
    "ProviderKind",
    "RecordingArtifactMetadata",
    "RecordingBundleError",
    "RecordingBundleErrorCode",
    "RecordingBundleManifest",
    "RecordingCaptureError",
    "RecordingCaptureErrorCode",
    "RecordingCaptureSession",
    "RecordingProvenance",
    "ModelRecording",
    "RecordedFailure",
    "RecordedFailureCode",
    "RecordedModel",
    "RecordedModelReplayError",
    "RecordedReplayErrorCode",
    "RecordedSuccess",
    "RecordingLoadError",
    "SingleTableSQLiteCompiler",
    "SQLiteReadOnlyExecutor",
    "load_model_recording",
    "mapping_fingerprint",
    "load_recording_bundle",
    "provenance_fingerprint",
    "question_fingerprint",
    "write_recording_bundle",
]
