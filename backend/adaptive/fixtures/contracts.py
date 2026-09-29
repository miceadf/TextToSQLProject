from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class FixtureVariant(str, Enum):
    FLAT = "flat"
    NORMALIZED = "normalized"
    LEGACY = "legacy"
    CODED = "coded"
    HYBRID = "hybrid"


CORE_VARIANTS = (FixtureVariant.FLAT, FixtureVariant.NORMALIZED, FixtureVariant.LEGACY)
EXTENDED_VARIANTS = (*CORE_VARIANTS, FixtureVariant.CODED, FixtureVariant.HYBRID)
# The only variant sets a bundle may contain; core keeps pre-5-23 bundles unchanged.
FIXTURE_SUITES = {
    "core-v1": CORE_VARIANTS,
    "extended-v1": EXTENDED_VARIANTS,
}


class LogicalOffering(BaseModel):
    course_code: str = Field(min_length=1)
    course_name: str = Field(min_length=1)
    credits: float = Field(gt=0)
    category: str = Field(min_length=1)
    department_code: str = Field(min_length=1)
    department_name: str = Field(min_length=1)
    instructor_name: str = Field(min_length=1)
    year: int = Field(ge=2000, le=2100)
    semester: str = Field(min_length=1)
    section: str = Field(min_length=1)
    capacity: int = Field(gt=0)
    enrolled: int = Field(ge=0)
    day: str = Field(min_length=1)
    start_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(pattern=r"^\d{2}:\d{2}$")

    @model_validator(mode="after")
    def validate_limits(self) -> "LogicalOffering":
        if self.enrolled > self.capacity:
            raise ValueError("enrolled cannot exceed capacity")
        if self.start_time >= self.end_time:
            raise ValueError("start_time must precede end_time")
        return self

    @property
    def offering_key(self) -> tuple[int, str, str, str]:
        return self.year, self.semester, self.course_code, self.section


class FixtureSeed(BaseModel):
    version: int = Field(ge=1)
    offerings: list[LogicalOffering] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_consistency(self) -> "FixtureSeed":
        keys = [offering.offering_key for offering in self.offerings]
        if len(keys) != len(set(keys)):
            raise ValueError("offering keys must be unique")

        courses: dict[str, tuple[object, ...]] = {}
        departments: dict[str, str] = {}
        for offering in self.offerings:
            course = (
                offering.course_name,
                offering.credits,
                offering.category,
                offering.department_code,
            )
            previous_course = courses.setdefault(offering.course_code, course)
            if previous_course != course:
                raise ValueError(f"course '{offering.course_code}' is inconsistent")
            previous_department = departments.setdefault(
                offering.department_code,
                offering.department_name,
            )
            if previous_department != offering.department_name:
                raise ValueError(
                    f"department '{offering.department_code}' is inconsistent"
                )
        return self

    def canonical_rows(self) -> list[dict[str, object]]:
        return [
            offering.model_dump(mode="json")
            for offering in sorted(self.offerings, key=lambda item: item.offering_key)
        ]


class FixtureArtifact(BaseModel):
    variant: FixtureVariant
    database_id: str = Field(min_length=1)
    database_path: Path
    catalog_path: Path
    proposal_path: Path


class FixtureBundle(BaseModel):
    generation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    seed_version: int = Field(ge=1)
    manifest_path: Path
    artifacts: dict[FixtureVariant, FixtureArtifact]

    @model_validator(mode="after")
    def validate_suite(self) -> "FixtureBundle":
        if set(self.artifacts) not in [set(item) for item in FIXTURE_SUITES.values()]:
            raise ValueError("fixture bundle variants must match one fixture suite")
        return self
