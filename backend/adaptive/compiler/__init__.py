"""Deterministic SQL compilation from Query IR and resolved JOIN plans."""

from .contracts import JoinCompilationRequest, TableAlias
from .sqlite import JoinCapableSQLiteCompiler

__all__ = [
    "JoinCapableSQLiteCompiler",
    "JoinCompilationRequest",
    "TableAlias",
]
