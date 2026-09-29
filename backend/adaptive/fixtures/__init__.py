"""Repeatable multi-schema SQLite fixtures."""

from .builder import build_fixture_bundle
from .coded import build_coded_database
from .contracts import (
    CORE_VARIANTS,
    EXTENDED_VARIANTS,
    FIXTURE_SUITES,
    FixtureArtifact,
    FixtureBundle,
    FixtureSeed,
    FixtureVariant,
    LogicalOffering,
)
from .flat import build_flat_database
from .hybrid import build_hybrid_database
from .legacy import build_legacy_database
from .normalized import build_normalized_database
from .seed import DEFAULT_SEED

__all__ = [
    "CORE_VARIANTS",
    "DEFAULT_SEED",
    "EXTENDED_VARIANTS",
    "FIXTURE_SUITES",
    "FixtureArtifact",
    "FixtureBundle",
    "FixtureSeed",
    "FixtureVariant",
    "LogicalOffering",
    "build_coded_database",
    "build_fixture_bundle",
    "build_flat_database",
    "build_hybrid_database",
    "build_legacy_database",
    "build_normalized_database",
]
