"""Strict contracts for private SQLite registrations."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


REGISTRATION_SCHEMA_VERSION = "private-sqlite-registration-v1"
REGISTRATION_STATUS = "PENDING_REVIEW"

DATABASE_FILENAME = "database.sqlite3"
CATALOG_FILENAME = "catalog.json"
PROPOSAL_FILENAME = "proposal.json"
REGISTRATION_FILENAME = "registration.json"

ALLOWED_FILES = frozenset(
    {DATABASE_FILENAME, CATALOG_FILENAME, PROPOSAL_FILENAME, REGISTRATION_FILENAME}
)
ARTIFACT_FILENAMES = (DATABASE_FILENAME, CATALOG_FILENAME, PROPOSAL_FILENAME)

MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_LABEL_LENGTH = 64

DATABASE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


class RegistrationArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)


class DatabaseRegistration(BaseModel):
    """Commit marker and manifest for one immutable registration.

    The manifest carries hashes and fingerprints only: never source
    absolute paths, row values, or sample values.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["private-sqlite-registration-v1"] = (
        "private-sqlite-registration-v1"
    )
    status: Literal["PENDING_REVIEW"] = "PENDING_REVIEW"
    database_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    label: str = Field(min_length=1, max_length=64)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_bytes: int = Field(ge=1)
    schema_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    ontology_version: int = Field(ge=1)
    artifacts: list[RegistrationArtifact] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_artifact_set(self) -> "DatabaseRegistration":
        paths = [artifact.path for artifact in self.artifacts]
        if set(paths) != set(ARTIFACT_FILENAMES) or len(set(paths)) != len(paths):
            raise ValueError("registration artifacts must list each payload once")
        database_artifact = next(
            artifact for artifact in self.artifacts if artifact.path == DATABASE_FILENAME
        )
        if (
            database_artifact.sha256 != self.content_sha256
            or database_artifact.bytes != self.content_bytes
        ):
            raise ValueError("database artifact must match registration content hash")
        return self
