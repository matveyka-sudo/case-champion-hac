"""Build the urban passenger bundle of one city: territories, grid, points, population,
destination candidates, ownership status, coverage and QA.

Usage (geo dependencies as an overlay until DEVOPS adds them, see README)::

    python -m a2_data_pipeline.build_city kaliningrad --bundle-id kaliningrad-urban-2026-11-08.1
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pyarrow.parquet as pq  # type: ignore[import-untyped]
import shapely  # type: ignore[import-untyped]
from pydantic import BaseModel

from . import categories as cat
from .bundle import (
    OWNERSHIP_REQUIRED,
    PRODUCT_CATEGORIES,
    AcceptanceState,
    BundleManifest,
    CategoryOwnership,
    CoverageRecord,
    Destination,
    DistrictShare,
    Evidence,
    GridSpec,
    PopulationCell,
    SamplePoint,
    SourceVersion,
    Territory,
)
from .cities import CITIES, CityConfig
from .geo import (
    POPULATION_METHOD,
    POPULATION_YEAR,
    GridResult,
    assign_population,
    build_grid,
    geodesic_area_km2,
    load_osm_layers,
    rounded_geojson,
    to_crs,
)
from .manifest import load_dataset_manifest
from .osm import extract
from .paths import processed_dir, raw_dir
from .store import publish_bundle

PARSER_VERSION = "ml2-urban-0.1.0"
GHSL_DATASET = "ghsl_pop.e2025-r2023a-100m-r3c20"
GHSL_TIF = "GHS_POP_E2025_GLOBE_R2023A_54009_100_V1_0_R3_C20.tif"
OURAIRPORTS_DATASET = "ourairports.airports-20261108"
SEARCH_BUFFER_M = 10_000
AIRPORT_BUFFER_M = 60_000
NOT_COLLECTED = (
    "access_nodes",
    "stops",
    "trips",
    "stop_times",
    "calendars",
    "transfers",
    "freight_sites",
    "freight_nodes",
    "freight_compatibility",
    "pipeline_connections",
    "reference_edges",
    "minimum_transfers",
    "traffic_profiles",
)


def _osm_url(ref: str) -> str:
    return f"https://www.openstreetmap.org/{ref}"


def _check(
    checks: list[dict[str, Any]], check_id: str, ok: bool, detail: str, **values: Any
) -> None:
    checks.append(
        {"check_id": check_id, "status": "pass" if ok else "fail", "detail": detail, **values}
    )


def build(config: CityConfig, bundle_id: str, accept: bool) -> Path:
    now = datetime.now(UTC)
    osm_manifest = load_dataset_manifest(config.osm_dataset_id)
    ghsl_manifest = load_dataset_manifest(GHSL_DATASET)
    airports_manifest = load_dataset_manifest(OURAIRPORTS_DATASET)
    osm_dir = processed_dir() / config.city_id / "osm"
    if not (osm_dir / "boundaries.parquet").is_file():
        extract(
            raw_dir() / osm_manifest.raw_path,
            {config.osm_boundary_relation, *config.osm_district_relations},
            osm_dir,
        )
    layers = load_osm_layers(osm_dir, config.osm_boundary_relation, config.osm_district_relations)
    grid = build_grid(layers, config.utm_epsg)
    assign_population(grid, raw_dir() / ghsl_manifest.raw_path, GHSL_TIF)

    checks: list[dict[str, Any]] = []
    evidence: dict[str, Evidence] = {}

    def osm_evidence(ref: str) -> str:
        evidence_id = f"osm:{ref.replace('/', ':')}"
        evidence.setdefault(
            evidence_id,
            Evidence(
                evidence_id=evidence_id,
                source_id="osm_geofabrik",
                dataset_id=osm_manifest.dataset_id,
                record_id=ref,
                url=_osm_url(ref),
                fetched_at=osm_manifest.retrieved_at,
                content_hash=None,
                verification_state="verified",
            ),
        )
        return evidence_id

    territories = _territories(config, layers, grid, osm_evidence, checks)
    cells, points = _cells(config, grid, checks)
    destinations, ownership, rule_counts = _destinations(
        config, layers, osm_dir, airports_manifest, osm_evidence, evidence, checks
    )
    coverage = _coverage(config, destinations)

    failed = [check["check_id"] for check in checks if check["status"] == "fail"]
    state: AcceptanceState = "quarantined" if failed else ("accepted" if accept else "validated")
    excluded = [cell for cell in cells if cell.status == "excluded"]
    offsets = [point.offset_m for point in points]
    qa_report = {
        "bundle_id": bundle_id,
        "generated_at": now.isoformat(),
        "parser_version": PARSER_VERSION,
        "category_rules": cat.RULES_VERSION,
        "result": "fail" if failed else "pass",
        "failed_checks": failed,
        "checks": checks,
        "summary": {
            "city_area_km2": territories[0].area_km2,
            "cells_total": len(cells),
            "cells_sampled": len(points),
            "cells_excluded": {
                reason: sum(1 for cell in excluded if cell.exclusion_reason == reason)
                for reason in ("NO_BUILDING", "WATER_ONLY")
            },
            "population_total": round(grid.population_total, 1),
            "population_in_excluded_cells": round(
                sum(cell.population or 0 for cell in excluded), 1
            ),
            "population_method": POPULATION_METHOD,
            "population_year": POPULATION_YEAR,
            "sample_offset_m": {
                "center_placements": sum(1 for point in points if point.placement == "cell_center"),
                "median": round(statistics.median(offsets), 1),
                "max": round(max(offsets), 1),
            },
            "destinations_by_category": {
                category: {
                    status: sum(
                        1 for d in destinations if d.category == category and d.status == status
                    )
                    for status in ("accepted", "quarantined")
                }
                for category in PRODUCT_CATEGORIES
            },
            "osm_rule_counts": rule_counts,
        },
    }
    manifest = BundleManifest(
        format="a2-ml2-bundle",
        schema_version=2,
        bundle_id=bundle_id,
        created_at=now,
        parser_version=PARSER_VERSION,
        acceptance_state=state,
        accepted_by="ML2 (№5)" if state == "accepted" else None,
        accepted_at=now if state == "accepted" else None,
        synthetic=False,
        geographic_scope=[config.city_id],
        valid_from=None,
        valid_to=None,
        timezone=config.timezone,
        grids=[
            GridSpec(
                grid_id=config.grid_id,
                city_id=config.city_id,
                crs=grid.crs,
                spacing_m=grid.spacing_m,
                origin_x=0.0,
                origin_y=0.0,
                version=config.grid_version,
            )
        ],
        source_versions=[
            SourceVersion(
                source_id=item.source_id,
                dataset_id=item.dataset_id,
                retrieved_at=item.retrieved_at,
                checksum=item.checksum,
                license=item.license,
                publication_date=item.publication_date,
            )
            for item in (osm_manifest, ghsl_manifest, airports_manifest)
        ],
        files={},
        coverage=coverage,
        qa_report="qa_report.json",
        limitations=[
            "Границы — OSM (relation городского округа), не официальный реестр границ МО",
            "Население — модельная оценка GHSL R2023A эпохи 2025, не перепись",
            "Поликлиники, детсады, школы, колледжи и вузы в карантине до сверки с официальным реестром",
            "Ж/д вокзалы, автовокзалы и ТЦ отобраны правилами OSM v1 без сверки со справочником",
            "Входы на граф (access_nodes), расписания и грузовые данные в этот bundle не входят",
        ],
    )
    collections: dict[str, Sequence[BaseModel]] = {
        "evidence": [evidence[key] for key in sorted(evidence)],
        "territories": territories,
        "population_cells": cells,
        "sample_points": points,
        "destinations": destinations,
        "category_ownership": ownership,
    }
    return publish_bundle(manifest, collections, qa_report)


def _territories(
    config: CityConfig,
    layers: Any,
    grid: GridResult,
    osm_evidence: Any,
    checks: list[dict[str, Any]],
) -> list[Territory]:
    city_area = geodesic_area_km2(layers.city)
    utm_area = grid.city_utm.area / 1e6
    _check(
        checks,
        "boundary_valid",
        layers.city.is_valid and all(geom.is_valid for _, _, geom in layers.districts),
        "City and district polygons are valid after make_valid",
    )
    _check(
        checks,
        "area_geodesic_vs_utm",
        abs(city_area - utm_area) / city_area < 0.005,
        "Geodesic area agrees with UTM area within 0.5%",
        geodesic_km2=round(city_area, 3),
        utm_km2=round(utm_area, 3),
    )
    boundary_version = f"{config.osm_dataset_id}:relation/{config.osm_boundary_relation}"
    result = [
        Territory(
            territory_id=config.city_id,
            level="city",
            name=config.name,
            parent_id=None,
            timezone=config.timezone,
            boundary_version=boundary_version,
            geometry=rounded_geojson(layers.city),
            area_km2=round(city_area, 3),
            area_method="geodesic_wgs84_of_osm_boundary",
            evidence_ids=[osm_evidence(layers.city_ref)],
        )
    ]
    district_area = 0.0
    for ref, name, geom in layers.districts:
        area = geodesic_area_km2(geom)
        district_area += area
        result.append(
            Territory(
                territory_id=f"{config.city_id}-{ref.replace('/', '-')}",
                level="district",
                name=name,
                parent_id=config.city_id,
                timezone=config.timezone,
                boundary_version=f"{config.osm_dataset_id}:{ref}",
                geometry=rounded_geojson(geom),
                area_km2=round(area, 3),
                area_method="geodesic_wgs84_of_osm_boundary",
                evidence_ids=[osm_evidence(ref)],
            )
        )
    _check(
        checks,
        "districts_partition_city",
        abs(district_area - city_area) / city_area < 0.005,
        "Districts cover the city area without overlap (area sum within 0.5%)",
        district_sum_km2=round(district_area, 3),
    )
    return result


def _cells(
    config: CityConfig, grid: GridResult, checks: list[dict[str, Any]]
) -> tuple[list[PopulationCell], list[SamplePoint]]:
    cells: list[PopulationCell] = []
    points: list[SamplePoint] = []
    district_ids = {
        ref: f"{config.city_id}-{ref.replace('/', '-')}" for ref, _, _ in grid.districts_utm
    }
    district_geoms = {ref: geom for ref, _, geom in grid.districts_utm}
    clipped_union = 0.0
    points_ok = True
    for (ix, iy), cell in sorted(grid.cells.items()):
        cell_id = f"{config.grid_id}-e{ix}-n{iy}"
        clipped_union += cell.clipped.area
        shares = []
        for ref, geom in district_geoms.items():
            overlap = shapely.intersection(cell.clipped, geom).area
            if overlap <= 0:
                continue
            shares.append(
                DistrictShare(
                    territory_id=district_ids[ref],
                    area_share=min(1.0, overlap / cell.clipped.area),
                    # floor keeps the sum of shares <= the rounded cell total
                    population=math.floor(cell.district_population.get(ref, 0.0) * 1000) / 1000,
                )
            )
        population = round(cell.population, 3)
        status: Literal["estimated", "zero"] = "zero" if population == 0 else "estimated"
        cells.append(
            PopulationCell(
                cell_id=cell_id,
                city_id=config.city_id,
                grid_id=config.grid_id,
                ix=ix,
                iy=iy,
                geometry=rounded_geojson(to_crs(cell.clipped, grid.crs, "EPSG:4326")),
                land_area_km2=round(cell.land_area_km2, 6),
                population=population,
                population_status=status,
                population_method=POPULATION_METHOD,
                population_year=POPULATION_YEAR,
                district_shares=shares,
                status="excluded" if cell.point is None else "sampled",
                exclusion_reason=cell.exclusion_reason,  # type: ignore[arg-type]
            )
        )
        if cell.point is None:
            continue
        points_ok &= bool(cell.clipped.buffer(1e-6).covers(cell.point))
        lonlat = to_crs(cell.point, grid.crs, "EPSG:4326")
        points.append(
            SamplePoint(
                point_id=f"{cell_id}-p",
                cell_id=cell_id,
                lon=round(lonlat.x, 7),
                lat=round(lonlat.y, 7),
                placement=cell.placement,  # type: ignore[arg-type]
                building_ref=cell.building_ref or "",
                offset_m=round(cell.offset_m or 0.0, 2),
            )
        )
    _check(
        checks,
        "cells_partition_city",
        abs(clipped_union - grid.city_utm.area) / grid.city_utm.area < 1e-6,
        "Clipped cells cover the city exactly once",
    )
    _check(
        checks,
        "points_inside_own_cell",
        points_ok,
        "Every sample point lies in its own clipped cell",
    )
    total_cells = sum(cell.population or 0.0 for cell in cells)
    _check(
        checks,
        "population_conserved",
        abs(total_cells - grid.population_total) < 0.5 and grid.population_outside_cells == 0,
        "All GHSL population inside the city is assigned to exactly one cell",
        cells_sum=round(total_cells, 1),
        raster_sum=round(grid.population_total, 1),
    )
    district_sum = sum(share.population or 0 for cell in cells for share in cell.district_shares)
    _check(
        checks,
        "district_population_not_doubled",
        district_sum <= total_cells + 0.5,
        "District shares never exceed the city population",
        district_sum=round(district_sum, 1),
    )
    return cells, points


def _destinations(
    config: CityConfig,
    layers: Any,
    osm_dir: Path,
    airports_manifest: Any,
    osm_evidence: Any,
    evidence: dict[str, Evidence],
    checks: list[dict[str, Any]],
) -> tuple[list[Destination], list[CategoryOwnership], dict[str, int]]:
    rows = pq.read_table(osm_dir / "pois.parquet").to_pylist()
    candidates, rule_counts = cat.build_candidates(rows, config.name)
    crs = f"EPSG:{config.utm_epsg}"
    city_utm = to_crs(layers.city, "EPSG:4326", crs)
    shapely.prepare(city_utm)
    airports = {
        row["iata_code"]: row
        for row in csv.DictReader((raw_dir() / airports_manifest.raw_path).open(encoding="utf-8"))
        if row["iata_code"]
    }
    destinations: list[Destination] = []
    ownership: list[CategoryOwnership] = []
    for candidate in sorted(candidates, key=lambda c: (c.category, c.osm_refs[0])):
        point_utm = to_crs(candidate.point, "EPSG:4326", crs)
        distance = city_utm.distance(point_utm)
        limit = AIRPORT_BUFFER_M if candidate.category == "airport" else SEARCH_BUFFER_M
        if distance > limit:
            continue
        evidence_ids = [osm_evidence(ref) for ref in candidate.osm_refs]
        reasons: list[str] = []
        if candidate.category == "airport":
            record = airports.get(str(candidate.tags.get("iata")))
            if record and record["scheduled_service"] == "yes":
                evidence_id = f"ourairports:{record['ident'].lower()}"
                evidence[evidence_id] = Evidence(
                    evidence_id=evidence_id,
                    source_id="ourairports",
                    dataset_id=airports_manifest.dataset_id,
                    record_id=record["ident"],
                    url=f"https://ourairports.com/airports/{record['ident']}/",
                    fetched_at=airports_manifest.retrieved_at,
                    content_hash=None,
                    verification_state="verified",
                )
                evidence_ids.append(evidence_id)
            else:
                reasons.append("NO_SCHEDULED_SERVICE_CONFIRMATION")
        if candidate.category in OWNERSHIP_REQUIRED:
            reasons.append("OWNERSHIP_UNVERIFIED")
        if candidate.name is None:
            reasons.append("UNNAMED")
        destination_id = (
            f"{config.city_id}-{candidate.category}-{candidate.osm_refs[0].replace('/', '-')}"
        )
        destinations.append(
            Destination(
                destination_id=destination_id,
                category=candidate.category,  # type: ignore[arg-type]
                serving_city_id=config.city_id,
                name=candidate.name,
                lon=round(candidate.point.x, 7),
                lat=round(candidate.point.y, 7),
                inside_city_boundary=distance == 0,
                osm_ref=candidate.osm_refs[0],
                status="quarantined" if reasons else "accepted",
                quarantine_reasons=reasons,
                evidence_ids=evidence_ids,
            )
        )
        required = candidate.category in OWNERSHIP_REQUIRED
        hint = candidate.ownership_hint
        ownership.append(
            CategoryOwnership(
                destination_id=destination_id,
                category=candidate.category,  # type: ignore[arg-type]
                ownership=hint if required else "unknown",  # type: ignore[arg-type]
                basis="osm_tags" if required else "not_required",
                verification_state="unknown",
                evidence_ids=evidence_ids if required else [],
            )
        )
    accepted_public = [
        d for d in destinations if d.category in OWNERSHIP_REQUIRED and d.status == "accepted"
    ]
    _check(
        checks,
        "public_categories_need_registry",
        not accepted_public,
        "No medical/education object is accepted without registry confirmation",
    )
    airports_in = [d for d in destinations if d.category == "airport" and d.status == "accepted"]
    _check(
        checks,
        "airport_outside_boundary_kept",
        any(not d.inside_city_boundary for d in airports_in),
        "Scheduled airport outside the city boundary is kept (PRODUCT_SPEC §2)",
        airports=[d.name for d in airports_in],
    )
    return destinations, ownership, rule_counts


def _coverage(config: CityConfig, destinations: list[Destination]) -> list[CoverageRecord]:
    city = config.city_id
    records = [
        CoverageRecord(
            collection="territories",
            city_id=city,
            status="complete",
            reason="Городской округ и все три района из OSM",
        ),
        CoverageRecord(
            collection="population_cells",
            city_id=city,
            status="complete",
            reason="Все ячейки сетки 1 км, пересекающие границу; население GHSL по центроидам пикселей",
        ),
        CoverageRecord(
            collection="sample_points",
            city_id=city,
            status="complete",
            reason="По одной точке на ячейку со зданием; исключённые ячейки с причиной и населением в QA",
        ),
    ]
    for category in PRODUCT_CATEGORIES:
        accepted = sum(1 for d in destinations if d.category == category and d.status == "accepted")
        if category in OWNERSHIP_REQUIRED:
            status, reason = (
                "unknown",
                "Кандидаты OSM в карантине: форма собственности не подтверждена реестром",
            )
        elif category == "airport":
            status = "complete" if accepted else "unknown"
            reason = "Аэродромы с IATA в 60 км, регулярные рейсы подтверждены OurAirports"
        else:
            status = "partial"
            reason = (
                f"Правила {cat.RULES_VERSION} по OSM в 10 км от границы; сверки со справочником нет"
            )
        records.append(
            CoverageRecord.model_validate(
                {
                    "collection": "destinations",
                    "city_id": city,
                    "category": category,
                    "status": status,
                    "reason": reason,
                }
            )
        )
    records.append(
        CoverageRecord(
            collection="category_ownership",
            city_id=city,
            status="unknown",
            reason="Официальный реестр учреждений ещё не загружен",
        )
    )
    for collection in NOT_COLLECTED:
        records.append(
            CoverageRecord(
                collection=collection,  # type: ignore[arg-type]
                city_id=city,
                status="not_collected",
                reason="Не входит в этот bundle; пустой список не означает отсутствие",
            )
        )
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("city", choices=sorted(CITIES))
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument(
        "--accept", action="store_true", help="mark accepted if every QA check passes"
    )
    args = parser.parse_args()
    path = build(CITIES[args.city], args.bundle_id, args.accept)
    print(path)


if __name__ == "__main__":
    main()
