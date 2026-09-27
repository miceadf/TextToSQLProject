"""Deterministic SQL compilation from Query IR and resolved JOIN plans."""

from .contracts import JoinCompilationRequest, TableAlias
from .harness import SchemaAdaptiveSQLiteCompiler
from .postgres import JoinCapablePostgresCompiler, SchemaAdaptivePostgresCompiler
from .sqlite import JoinCapableSQLiteCompiler

__all__ = [
    "JoinCapablePostgresCompiler",
    "JoinCapableSQLiteCompiler",
    "JoinCompilationRequest",
    "SchemaAdaptivePostgresCompiler",
    "SchemaAdaptiveSQLiteCompiler",
    "TableAlias",
]
