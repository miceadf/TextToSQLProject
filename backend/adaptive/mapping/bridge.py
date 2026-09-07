from __future__ import annotations

from ..harness.contracts import FieldMapping, MappingProfile, MappingStatus
from .contracts import PublishedMappingProfile


def to_harness_mapping(profile: PublishedMappingProfile) -> MappingProfile:
    return MappingProfile(
        database_id=profile.database_id,
        version=profile.version,
        status=MappingStatus.PUBLISHED,
        fields={
            semantic_field: FieldMapping(table=field.table, column=field.column)
            for semantic_field, field in profile.fields.items()
        },
    )
