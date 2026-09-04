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
from .policy import ConservativeSqlPolicy

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
    "SingleTableSQLiteCompiler",
    "SQLiteReadOnlyExecutor",
]
