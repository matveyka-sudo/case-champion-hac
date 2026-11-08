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
    ghsl_dataset_id: str
    ghsl_tif: str
    grid_version: int = 1
    extract_bbox: tuple[float, float, float, float] | None = None  # west, south, east, north
    poi_bbox: tuple[float, float, float, float] | None = None
    airport_service_unverified: bool = False
    education_registry_dataset_id: str | None = None
    medical_registry_dataset_id: str | None = None
    region_name: str | None = None
    higher_education_registry_dataset_id: str | None = None

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
        ghsl_dataset_id="ghsl_pop.e2025-r2023a-100m-r3c20",
        ghsl_tif="GHS_POP_E2025_GLOBE_R2023A_54009_100_V1_0_R3_C20.tif",
        education_registry_dataset_id="klgd_education.20261108",
        medical_registry_dataset_id="tfoms39_trmo.2026-07",
        region_name="Калининградская область",
        higher_education_registry_dataset_id="obrnadzor_accreditation.20261108",
    ),
    "novorossiysk": CityConfig(
        city_id="novorossiysk",
        name="Новороссийск",
        timezone="Europe/Moscow",
        osm_dataset_id="osm_geofabrik.south-fed-district-261107",
        osm_boundary_relation=364545,
        osm_district_relations=(14071756, 14071831, 14071830, 14071757),
        utm_epsg=32637,
        ghsl_dataset_id="ghsl_pop.e2025-r2023a-100m-r4c22",
        ghsl_tif="GHS_POP_E2025_GLOBE_R2023A_54009_100_V1_0_R4_C22.tif",
        # city boundary + 10 km for buildings and walk network; +60 km for destinations
        extract_bbox=(37.10, 44.45, 38.35, 45.12),
        poi_bbox=(36.55, 44.10, 38.95, 45.60),
        region_name="Краснодарский край",
        higher_education_registry_dataset_id="obrnadzor_accreditation.20261108",
        medical_registry_dataset_id="admnvrsk_health.20261108",
        airport_service_unverified=True,
    ),
}
