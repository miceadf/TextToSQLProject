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
    "RecordingCaptureError",
    "RecordingCaptureErrorCode",
    "RecordingCaptureSession",
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
    "question_fingerprint",
]
