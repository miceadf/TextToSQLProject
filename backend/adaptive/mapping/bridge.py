"""게시된 연결 profile을 실행 harness의 MappingProfile로 변환한다.

공통 항목과 실제 table·column 연결 및 버전을 옮겨 PUBLISHED 상태로 반환한다.
제안·검토·게시 검증은 이 변환 전에 끝나 있어야 한다.
"""

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
