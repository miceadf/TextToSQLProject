"""Private standalone-SQLite registration boundary (Phase 6-17)."""

from .contracts import (
    ALLOWED_FILES,
    CATALOG_FILENAME,
    DATABASE_FILENAME,
    MAX_LABEL_LENGTH,
    MAX_SOURCE_BYTES,
    PROPOSAL_FILENAME,
    REGISTRATION_FILENAME,
    REGISTRATION_SCHEMA_VERSION,
    REGISTRATION_STATUS,
    DatabaseRegistration,
    RegistrationArtifact,
)
from .errors import RegistrationError, RegistrationErrorCode
from .storage import (
    LoadedRegistration,
    load_registration,
    register_database,
    validate_database_id,
    validate_label,
)

__all__ = [
    "ALLOWED_FILES",
    "CATALOG_FILENAME",
    "DATABASE_FILENAME",
    "MAX_LABEL_LENGTH",
    "MAX_SOURCE_BYTES",
    "PROPOSAL_FILENAME",
    "REGISTRATION_FILENAME",
    "REGISTRATION_SCHEMA_VERSION",
    "REGISTRATION_STATUS",
    "DatabaseRegistration",
    "LoadedRegistration",
    "RegistrationArtifact",
    "RegistrationError",
    "RegistrationErrorCode",
    "load_registration",
    "register_database",
    "validate_database_id",
    "validate_label",
]
