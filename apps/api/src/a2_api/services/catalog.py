"""Explicit region/profile configuration. No production city list is built in."""

from a2_contracts.profiles import ProfileConfig
from a2_contracts.regions import StudyArea

from a2_api.core.catalog import CatalogConfig
from a2_api.services.legacy import Services


class CatalogServices(Services):
    """Configured metadata only; real data/calculations remain separate adapters."""

    def __init__(self, catalog: CatalogConfig) -> None:
        self.catalog = catalog

    async def profiles(self) -> list[ProfileConfig]:
        return self.catalog.profiles

    async def study_areas(self) -> list[StudyArea]:
        return self.catalog.study_areas
