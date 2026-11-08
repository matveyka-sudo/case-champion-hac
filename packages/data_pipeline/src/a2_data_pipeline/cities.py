"""Per-city build parameters. IDs are stable; changing a value means a new version."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CityConfig:
    city_id: str
    name: str
    timezone: str
    osm_dataset_id: str
    osm_boundary_relation: int
    osm_district_relations: tuple[int, ...]
    utm_epsg: int
    grid_version: int = 1
    education_registry_dataset_id: str | None = None
    medical_registry_dataset_id: str | None = None

    @property
    def grid_id(self) -> str:
        return f"{self.city_id}-utm{self.utm_epsg % 100}n-1km-v{self.grid_version}"


CITIES: dict[str, CityConfig] = {
    "kaliningrad": CityConfig(
        city_id="kaliningrad",
        name="Калининград",
        timezone="Europe/Kaliningrad",
        osm_dataset_id="osm_geofabrik.kaliningrad-261107",
        osm_boundary_relation=331414,
        osm_district_relations=(331411, 331412, 331413),
        utm_epsg=32634,
        education_registry_dataset_id="klgd_education.20261108",
        medical_registry_dataset_id="tfoms39_trmo.2026-07",
    ),
}
