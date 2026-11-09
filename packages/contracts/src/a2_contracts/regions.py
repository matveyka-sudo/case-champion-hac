"""Versioned study scope: full first-stage coverage is separate from demo selection."""

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from .common import ContractModel, Identifier, Version, valid_timezone


class StudyArea(ContractModel):
    area_id: Identifier
    area_version: Version
    region_ids: list[Identifier] = Field(min_length=1)
    city_ids: list[Identifier] = Field(min_length=1)
    demo_city_ids: list[Identifier]
    timezone: Identifier
    basis: Literal["scenario", "verified"]
    evidence_ids: list[Identifier]

    _timezone = field_validator("timezone")(valid_timezone)

    @model_validator(mode="after")
    def explicit_scope(self) -> Self:
        for name in ("region_ids", "city_ids", "demo_city_ids"):
            values = getattr(self, name)
            if len(set(values)) != len(values):
                raise ValueError(f"Duplicate IDs in {name}")
        if not set(self.demo_city_ids).issubset(self.city_ids):
            raise ValueError("Demo cities must be a subset of full study area")
        if self.basis == "verified" and not self.evidence_ids:
            raise ValueError("Verified scope requires evidence")
        return self
