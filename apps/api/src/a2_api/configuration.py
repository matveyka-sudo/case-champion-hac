"""Explicit region/profile configuration. No production city list is built in."""

from pathlib import Path
from typing import Self

from a2_contracts.common import ContractModel
from a2_contracts.profiles import ProfileConfig, TaskSet
from a2_contracts.regions import StudyArea
from pydantic import model_validator

from .services import Services


class CatalogConfig(ContractModel):
    study_areas: list[StudyArea]
    profiles: list[ProfileConfig]
    task_sets: list[TaskSet]

    @model_validator(mode="after")
    def linked_configuration(self) -> Self:
        areas = {(a.area_id, a.area_version): a for a in self.study_areas}
        profiles = {(p.profile_id, p.profile_version): p for p in self.profiles}
        if len(areas) != len(self.study_areas) or len(profiles) != len(self.profiles):
            raise ValueError("Duplicate area/profile version")
        task_hashes = [t.task_set_hash for t in self.task_sets]
        if len(set(task_hashes)) != len(task_hashes):
            raise ValueError("Duplicate TaskSet hash")
        for profile in self.profiles:
            area = areas.get((profile.study_area_id, profile.study_area_version))
            if area is None:
                raise ValueError("Profile refers to unknown study area revision")
            if area.timezone != profile.timezone:
                raise ValueError("Profile timezone differs from its study area")
        for tasks in self.task_sets:
            task_profile = profiles.get((tasks.profile_id, tasks.profile_version))
            if task_profile is None:
                raise ValueError("TaskSet refers to unknown profile revision")
            if (tasks.start_date, tasks.end_date) != (
                task_profile.start_date,
                task_profile.end_date,
            ):
                raise ValueError("TaskSet period differs from profile period")
        return self


def load_catalog(path: Path) -> CatalogConfig:
    return CatalogConfig.model_validate_json(path.read_bytes())


class CatalogServices(Services):
    """Configured metadata only; real data/calculations remain separate adapters."""

    def __init__(self, catalog: CatalogConfig) -> None:
        self.catalog = catalog

    async def profiles(self) -> list[ProfileConfig]:
        return self.catalog.profiles

    async def study_areas(self) -> list[StudyArea]:
        return self.catalog.study_areas
