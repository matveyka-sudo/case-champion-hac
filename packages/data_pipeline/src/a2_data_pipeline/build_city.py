"""Build the urban passenger bundle of one city: territories, grid, points, population,
destination candidates, access nodes, ownership status, coverage and QA.

Usage (geo dependencies as an overlay until DEVOPS adds them, see README)::

    python -m a2_data_pipeline.build_city kaliningrad --bundle-id <id> --accept
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import pyarrow.parquet as pq  # type: ignore[import-untyped]
import shapely  # type: ignore[import-untyped]
from pydantic import BaseModel

from . import categories as cat
from . import klgd, obrnadzor, trmo
from .access import ACCESS_KIND, Access, WalkNetwork, load_walk_network, snap
from .addresses import AddressIndex, parse_address
from .bundle import (
    OWNERSHIP_REQUIRED,
    PRODUCT_CATEGORIES,
    AcceptanceState,
    AccessNode,
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
    OsmLayers,
    assign_population,
    build_grid,
    geodesic_area_km2,
    load_osm_layers,
    rounded_geojson,
    to_crs,
)
from .institutions import KIND_TO_CATEGORY, match_points, match_roster
from .manifest import DatasetManifest, load_dataset_manifest
from .osm import extract, extract_is_current
from .paths import processed_dir, raw_dir
from .store import publish_bundle

PARSER_VERSION = "ml2-urban-0.2.0"
GHSL_DATASET = "ghsl_pop.e2025-r2023a-100m-r3c20"
GHSL_TIF = "GHS_POP_E2025_GLOBE_R2023A_54009_100_V1_0_R3_C20.tif"
OURAIRPORTS_DATASET = "ourairports.airports-20261108"
SEARCH_BUFFER_M = 10_000
AIRPORT_BUFFER_M = 60_000
ACCESS_WARN_M = 500.0
REGISTRY_WARN_M = 150.0
# Declared analysis window of the snapshot (ML2 assumption): geometry and
# population of an OSM state from 2026-11-07 are used for dates in November 2026.
# Not a guarantee that objects are unchanged; schedules have their own periods.
ANALYSIS_WINDOW = (date(2026, 11, 1), date(2026, 11, 30))
NOT_COLLECTED = (
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


def _check(
    checks: list[dict[str, Any]],
    check_id: str,
    ok: bool,
    detail: str,
    *,
    severity: Literal["fail", "warn"] = "fail",
    **values: Any,
) -> None:
    status = "pass" if ok else severity
    checks.append({"check_id": check_id, "status": status, "detail": detail, **values})


def _hash_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _dataset_evidence(
    manifest: DatasetManifest, evidence_id: str, record_id: str | None = None
) -> Evidence:
    """Evidence for a whole fetched file; its hash is the raw file checksum."""

    return Evidence(
        evidence_id=evidence_id,
        source_id=manifest.source_id,
        dataset_id=manifest.dataset_id,
        record_id=record_id or manifest.raw_path,
        url=str(manifest.source_url),
        fetched_at=manifest.retrieved_at,
        content_hash=manifest.checksum.value,
        verification_state="verified",
    )


class EvidenceBook:
    """Evidence rows with record content hashes; one row per source record."""

    def __init__(self, osm_manifest: DatasetManifest, osm_hashes: dict[str, str]) -> None:
        self.osm_manifest = osm_manifest
        self.osm_hashes = osm_hashes
        self.items: dict[str, Evidence] = {}

    def osm(self, ref: str) -> str:
        evidence_id = f"osm:{ref.replace('/', ':')}"
        if evidence_id not in self.items:
            self.items[evidence_id] = Evidence(
                evidence_id=evidence_id,
                source_id="osm_geofabrik",
                dataset_id=self.osm_manifest.dataset_id,
                record_id=ref,
                url=f"https://www.openstreetmap.org/{ref}",
                fetched_at=self.osm_manifest.retrieved_at,
                content_hash=self.osm_hashes[ref],
                verification_state="verified",
            )
        return evidence_id

    def add(self, item: Evidence) -> str:
        self.items.setdefault(item.evidence_id, item)
        return item.evidence_id

    def rows(self) -> list[Evidence]:
        return [self.items[key] for key in sorted(self.items)]


@dataclass
class PointDraft:
    point_id: str
    cell_id: str
    utm: shapely.Point
    center_utm: shapely.Point
    placement: str
    building_ref: str
    offset_m: float


@dataclass
class DestinationDraft:
    destination_id: str
    category: str
    name: str | None
    utm: shapely.Point
    lonlat: shapely.Point
    inside_city_boundary: bool
    osm_ref: str | None
    reasons: list[str]
    evidence_ids: list[str]
    location_source: str = "osm"
    ownership_verified: bool = False


def _osm_hashes(osm_dir: Path, network: WalkNetwork) -> dict[str, str]:
    """Record hash per OSM reference; the first table that holds a reference wins."""

    hashes = dict(network.hashes)
    for name in ("boundaries", "pois", "buildings", "addresses"):
        table = pq.read_table(osm_dir / f"{name}.parquet", columns=["osm_ref", "content_hash"])
        for ref, value in zip(
            table.column("osm_ref").to_pylist(), table.column("content_hash").to_pylist()
        ):
            hashes.setdefault(ref, value)
    return hashes


def build(config: CityConfig, bundle_id: str, accept: bool) -> Path:
    now = datetime.now(UTC)
    osm_manifest = load_dataset_manifest(config.osm_dataset_id)
    ghsl_manifest = load_dataset_manifest(GHSL_DATASET)
    airports_manifest = load_dataset_manifest(OURAIRPORTS_DATASET)
    osm_dir = processed_dir() / config.city_id / "osm"
    boundary_ids = {config.osm_boundary_relation, *config.osm_district_relations}
    if not extract_is_current(osm_dir, boundary_ids):
        extract(raw_dir() / osm_manifest.raw_path, boundary_ids, osm_dir)
    layers = load_osm_layers(osm_dir, config.osm_boundary_relation, config.osm_district_relations)
    grid = build_grid(layers, config.utm_epsg)
    assign_population(grid, raw_dir() / ghsl_manifest.raw_path, GHSL_TIF)
    network = load_walk_network(osm_dir, config.utm_epsg, config.osm_dataset_id)

    checks: list[dict[str, Any]] = []
    book = EvidenceBook(osm_manifest, _osm_hashes(osm_dir, network))
    ghsl_evidence = book.add(_dataset_evidence(ghsl_manifest, "ghsl:" + GHSL_DATASET, GHSL_TIF))
    snapshot_evidence = {
        "osm": book.add(_dataset_evidence(osm_manifest, "osm:dataset:" + osm_manifest.dataset_id)),
        "ghsl": ghsl_evidence,
        "airports": book.add(
            _dataset_evidence(
                airports_manifest, "ourairports:dataset:" + airports_manifest.dataset_id
            )
        ),
    }

    roster = None
    source_manifests = [osm_manifest, ghsl_manifest, airports_manifest]
    if config.education_registry_dataset_id:
        registry_manifest = load_dataset_manifest(config.education_registry_dataset_id)
        source_manifests.append(registry_manifest)
        roster = (klgd.load_roster(raw_dir() / registry_manifest.raw_path), registry_manifest)
        snapshot_evidence["education_registry"] = book.add(
            _dataset_evidence(
                registry_manifest, f"{klgd.SOURCE_ID}:dataset:{registry_manifest.dataset_id}"
            )
        )
    medical = None
    if config.medical_registry_dataset_id:
        medical_manifest = load_dataset_manifest(config.medical_registry_dataset_id)
        source_manifests.append(medical_manifest)
        medical = (trmo.read_units(raw_dir() / medical_manifest.raw_path), medical_manifest)
        snapshot_evidence["medical_registry"] = book.add(
            _dataset_evidence(
                medical_manifest, f"{trmo.SOURCE_ID}:dataset:{medical_manifest.dataset_id}"
            )
        )
    education = None
    if config.higher_education_registry_dataset_id and config.region_name:
        education_manifest = load_dataset_manifest(config.higher_education_registry_dataset_id)
        source_manifests.append(education_manifest)
        orgs = obrnadzor.read_orgs(
            raw_dir() / education_manifest.raw_path, config.region_name, config.name
        )
        education = (orgs, education_manifest)
        snapshot_evidence["higher_education_registry"] = book.add(
            _dataset_evidence(
                education_manifest,
                f"{obrnadzor.SOURCE_ID}:dataset:{education_manifest.dataset_id}",
            )
        )

    territories = _territories(config, layers, grid, book, checks)
    cells, point_drafts = _cells(config, grid, ghsl_evidence, checks)
    drafts, ownership, rule_counts = _destination_drafts(
        config, layers, osm_dir, airports_manifest, book, checks, roster, medical, education
    )
    accesses = snap([p.utm for p in point_drafts] + [d.utm for d in drafts], network)
    point_access, destination_access = accesses[: len(point_drafts)], accesses[len(point_drafts) :]
    access_nodes = _access_nodes(accesses, network, grid.crs, book)
    points = [
        _sample_point(draft, access, grid.crs, book)
        for draft, access in zip(point_drafts, point_access)
    ]
    destinations = [
        _destination(config, draft, access) for draft, access in zip(drafts, destination_access)
    ]
    access_summary = _access_checks(points, destinations, network, checks)
    coverage = _coverage(
        config,
        destinations,
        snapshot_evidence,
        roster is not None,
        medical is not None,
        education is not None,
    )

    failed = [check["check_id"] for check in checks if check["status"] == "fail"]
    warnings = [check["check_id"] for check in checks if check["status"] == "warn"]
    state: AcceptanceState = "quarantined" if failed else ("accepted" if accept else "validated")
    excluded = [cell for cell in cells if cell.status == "excluded"]
    offsets = [point.offset_m for point in points]
    qa_report = {
        "bundle_id": bundle_id,
        "generated_at": now.isoformat(),
        "parser_version": PARSER_VERSION,
        "category_rules": cat.RULES_VERSION,
        "graph_version": network.graph_version,
        "result": "fail" if failed else "pass",
        "failed_checks": failed,
        "warnings": warnings,
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
            "walk_network": network.stats,
            "access_distance_m": access_summary,
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
        valid_from=ANALYSIS_WINDOW[0],
        valid_to=ANALYSIS_WINDOW[1],
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
        graph_version=network.graph_version,
        source_versions=[
            SourceVersion(
                source_id=item.source_id,
                dataset_id=item.dataset_id,
                retrieved_at=item.retrieved_at,
                checksum=item.checksum,
                license=item.license,
                publication_date=item.publication_date,
            )
            for item in source_manifests
        ],
        files={},
        coverage=coverage,
        qa_report="qa_report.json",
        limitations=[
            "Границы — OSM (relation городского округа), не официальный реестр границ МО",
            "Население — модельная оценка GHSL R2023A эпохи 2025, не перепись",
            "Поликлиники, детсады, школы, колледжи и вузы в карантине до сверки с официальным реестром",
            "Ж/д вокзалы, автовокзалы и ТЦ отобраны правилами OSM v1 без сверки со справочником",
            "Вход на граф — проекция на пешеходную сеть OSM v1, не размеченный вход здания",
            "Период применимости 01–30.11.2026 заявлен ML2 для снимка OSM от 07.11.2026",
            "Расписания и грузовые данные в этот bundle не входят",
        ],
    )
    collections: dict[str, Sequence[BaseModel]] = {
        "evidence": book.rows(),
        "territories": territories,
        "population_cells": cells,
        "sample_points": points,
        "destinations": destinations,
        "category_ownership": ownership,
        "access_nodes": access_nodes,
    }
    return publish_bundle(manifest, collections, qa_report)


def _territories(
    config: CityConfig,
    layers: OsmLayers,
    grid: GridResult,
    book: EvidenceBook,
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
    result = [
        Territory(
            territory_id=config.city_id,
            level="city",
            name=config.name,
            parent_id=None,
            timezone=config.timezone,
            boundary_version=f"{config.osm_dataset_id}:relation/{config.osm_boundary_relation}",
            geometry=rounded_geojson(layers.city),
            area_km2=round(city_area, 3),
            area_method="geodesic_wgs84_of_osm_boundary",
            evidence_ids=[book.osm(layers.city_ref)],
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
                evidence_ids=[book.osm(ref)],
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


def _square_bbox(ix: int, iy: int, grid: GridResult) -> tuple[float, float, float, float]:
    size = grid.spacing_m
    square = shapely.box(ix * size, iy * size, (ix + 1) * size, (iy + 1) * size)
    west, south, east, north = to_crs(square, grid.crs, "EPSG:4326").bounds
    return (round(west, 7), round(south, 7), round(east, 7), round(north, 7))


def _cells(
    config: CityConfig, grid: GridResult, ghsl_evidence: str, checks: list[dict[str, Any]]
) -> tuple[list[PopulationCell], list[PointDraft]]:
    cells: list[PopulationCell] = []
    drafts: list[PointDraft] = []
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
                square_bbox=_square_bbox(ix, iy, grid),
                land_area_km2=round(cell.land_area_km2, 6),
                population=population,
                population_status=status,
                population_method=POPULATION_METHOD,
                population_year=POPULATION_YEAR,
                district_shares=shares,
                status="excluded" if cell.point is None else "sampled",
                exclusion_reason=cell.exclusion_reason,  # type: ignore[arg-type]
                evidence_ids=[ghsl_evidence],
            )
        )
        if cell.point is None:
            continue
        points_ok &= bool(cell.clipped.buffer(1e-6).covers(cell.point))
        size = grid.spacing_m
        drafts.append(
            PointDraft(
                point_id=f"{cell_id}-p",
                cell_id=cell_id,
                utm=cell.point,
                center_utm=shapely.Point((ix + 0.5) * size, (iy + 0.5) * size),
                placement=cell.placement or "",
                building_ref=cell.building_ref or "",
                offset_m=cell.offset_m or 0.0,
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
    return cells, drafts


def _destination_drafts(
    config: CityConfig,
    layers: OsmLayers,
    osm_dir: Path,
    airports_manifest: DatasetManifest,
    book: EvidenceBook,
    checks: list[dict[str, Any]],
    roster: tuple[klgd.Roster, DatasetManifest] | None = None,
    medical: tuple[list[trmo.Unit], DatasetManifest] | None = None,
    education: tuple[list[obrnadzor.EduOrg], DatasetManifest] | None = None,
) -> tuple[list[DestinationDraft], list[CategoryOwnership], dict[str, Any]]:
    rows = pq.read_table(osm_dir / "pois.parquet").to_pylist()
    candidates, osm_rule_counts = cat.build_candidates(rows, config.name)
    rule_counts: dict[str, Any] = dict(osm_rule_counts)
    crs = f"EPSG:{config.utm_epsg}"
    city_utm = to_crs(layers.city, "EPSG:4326", crs)
    shapely.prepare(city_utm)

    def in_search_area(candidate: cat.Candidate) -> bool:
        limit = AIRPORT_BUFFER_M if candidate.category == "airport" else SEARCH_BUFFER_M
        return bool(city_utm.distance(to_crs(candidate.point, "EPSG:4326", crs)) <= limit)

    candidates = [c for c in candidates if in_search_area(c)]
    drafts: list[DestinationDraft] = []
    ownership: list[CategoryOwnership] = []
    cache: dict[str, AddressIndex] = {}

    def address_index() -> AddressIndex:
        if "index" not in cache:
            area = to_crs(city_utm.buffer(SEARCH_BUFFER_M), crs, "EPSG:4326")
            cache["index"] = AddressIndex(osm_dir, area, layers.city, config.name)
        return cache["index"]

    if roster is not None:
        registry_categories = set(KIND_TO_CATEGORY.values())
        roster_candidates = [c for c in candidates if c.category in registry_categories]
        others = [c for c in candidates if c.category not in registry_categories]
        registry_drafts, registry_ownership, unmatched, stats = _registry_drafts(
            config, roster, roster_candidates, city_utm, crs, book, checks
        )
        drafts.extend(registry_drafts)
        ownership.extend(registry_ownership)
        candidates = others + unmatched
        rule_counts["registry_match"] = stats
    if medical is not None:
        medical_categories = {"adult_polyclinic", "child_polyclinic"}
        medical_candidates = [c for c in candidates if c.category in medical_categories]
        rest = [c for c in candidates if c.category not in medical_categories]
        medical_drafts, medical_ownership, still_unmatched, medical_stats = _medical_drafts(
            config, medical, medical_candidates, address_index(), city_utm, crs, book, checks
        )
        drafts.extend(medical_drafts)
        ownership.extend(medical_ownership)
        candidates = rest + still_unmatched
        rule_counts["medical_registry"] = medical_stats
    if education is not None:
        college_candidates = [c for c in candidates if c.category == "college_university"]
        rest = [c for c in candidates if c.category != "college_university"]
        college_drafts, college_ownership, college_unmatched, college_stats = _university_drafts(
            config, education, college_candidates, address_index(), city_utm, crs, book, checks
        )
        drafts.extend(college_drafts)
        ownership.extend(college_ownership)
        candidates = rest + college_unmatched
        rule_counts["higher_education_registry"] = college_stats
    airports = {
        row["iata_code"]: row
        for row in csv.DictReader((raw_dir() / airports_manifest.raw_path).open(encoding="utf-8"))
        if row["iata_code"]
    }
    for candidate in sorted(candidates, key=lambda c: (c.category, c.osm_refs[0])):
        point_utm = to_crs(candidate.point, "EPSG:4326", crs)
        distance = city_utm.distance(point_utm)
        evidence_ids = [book.osm(ref) for ref in candidate.osm_refs]
        reasons: list[str] = []
        if candidate.category == "airport":
            record = airports.get(str(candidate.tags.get("iata")))
            if record and record["scheduled_service"] == "yes":
                evidence_ids.append(
                    book.add(
                        Evidence(
                            evidence_id=f"ourairports:{record['ident'].lower()}",
                            source_id="ourairports",
                            dataset_id=airports_manifest.dataset_id,
                            record_id=record["ident"],
                            url=f"https://ourairports.com/airports/{record['ident']}/",
                            fetched_at=airports_manifest.retrieved_at,
                            content_hash=_hash_json(record),
                            verification_state="verified",
                        )
                    )
                )
            else:
                reasons.append("NO_SCHEDULED_SERVICE_CONFIRMATION")
        if candidate.category in OWNERSHIP_REQUIRED:
            reasons.append("OWNERSHIP_UNVERIFIED")
        if candidate.name is None:
            reasons.append("UNNAMED")
        destination_id = (
            f"{config.city_id}-{candidate.category}-{candidate.osm_refs[0].replace('/', '-')}"
        )
        drafts.append(
            DestinationDraft(
                destination_id=destination_id,
                category=candidate.category,
                name=candidate.name,
                utm=point_utm,
                lonlat=candidate.point,
                inside_city_boundary=distance == 0,
                osm_ref=candidate.osm_refs[0],
                reasons=reasons,
                evidence_ids=evidence_ids,
            )
        )
        required = candidate.category in OWNERSHIP_REQUIRED
        ownership.append(
            CategoryOwnership(
                destination_id=destination_id,
                category=candidate.category,  # type: ignore[arg-type]
                ownership=candidate.ownership_hint if required else "unknown",  # type: ignore[arg-type]
                basis="osm_tags" if required else "not_required",
                verification_state="unknown",
                evidence_ids=evidence_ids if required else [],
            )
        )
    accepted_public = [
        d.destination_id
        for d in drafts
        if d.category in OWNERSHIP_REQUIRED and not d.reasons and not d.ownership_verified
    ]
    _check(
        checks,
        "public_categories_need_registry",
        not accepted_public,
        "No medical/education object is accepted without registry confirmation",
        accepted_registry_objects=sum(1 for d in drafts if d.ownership_verified),
    )
    airports_in = [d for d in drafts if d.category == "airport" and not d.reasons]
    _check(
        checks,
        "airport_outside_boundary_kept",
        any(not d.inside_city_boundary for d in airports_in),
        "Scheduled airport outside the city boundary is kept (PRODUCT_SPEC §2)",
        airports=[d.name for d in airports_in],
    )
    return drafts, ownership, rule_counts


def _registry_drafts(
    config: CityConfig,
    roster: tuple[klgd.Roster, DatasetManifest],
    candidates: list[cat.Candidate],
    city_utm: shapely.Geometry,
    crs: str,
    book: EvidenceBook,
    checks: list[dict[str, Any]],
) -> tuple[list[DestinationDraft], list[CategoryOwnership], list[cat.Candidate], dict[str, Any]]:
    """One destination per roster building; matched OSM candidates only add evidence.

    The roster is the founder's list, so these objects are municipal and accepted.
    The location is the roster coordinate: it is the official record, and OSM
    agrees with it in most cases (QA ``registry_osm_distance``).
    """

    data, manifest = roster
    matches, unmatched, stats = match_roster(data.buildings, candidates, crs)
    drafts: list[DestinationDraft] = []
    ownership: list[CategoryOwnership] = []
    outside: list[str] = []
    for match in matches:
        building = match.building
        institution = data.institutions[str(building.institution_id)]
        record = book.add(
            Evidence(
                evidence_id=f"{klgd.SOURCE_ID}:{institution.institution_id}",
                source_id=klgd.SOURCE_ID,
                dataset_id=manifest.dataset_id,
                record_id=institution.institution_id,
                url=institution.detail_url,
                fetched_at=manifest.retrieved_at,
                content_hash=data.record_hash(institution.institution_id),
                verification_state="verified",
            )
        )
        osm_refs = sorted({ref for c in match.candidates for ref in c.osm_refs})
        category = KIND_TO_CATEGORY[building.kind]
        destination_id = f"{config.city_id}-{category}-{klgd.SOURCE_ID}-{building.map_id}"
        inside = city_utm.distance(match.point_utm) == 0
        if not inside:
            outside.append(destination_id)
        drafts.append(
            DestinationDraft(
                destination_id=destination_id,
                category=category,
                name=institution.name,
                utm=match.point_utm,
                lonlat=shapely.Point(building.lon, building.lat),
                inside_city_boundary=inside,
                osm_ref=osm_refs[0] if osm_refs else None,
                reasons=[],
                evidence_ids=[record, *(book.osm(ref) for ref in osm_refs)],
                location_source="registry",
                ownership_verified=True,
            )
        )
        ownership.append(
            CategoryOwnership(
                destination_id=destination_id,
                category=category,  # type: ignore[arg-type]
                ownership="municipal",
                basis="registry",
                verification_state="verified",
                evidence_ids=[record],
            )
        )
    far = sorted(
        (
            (round(max(m.distances_m), 1), m.building.name, m.building.map_id)
            for m in matches
            if m.distances_m and max(m.distances_m) > REGISTRY_WARN_M
        ),
        reverse=True,
    )
    _check(
        checks,
        "registry_roster_complete",
        not data.qa["institutions_without_coordinates"] and not data.qa["non_municipal_names"],
        "Every listed institution has building coordinates and a municipal legal form",
        institutions=data.qa["institutions"],
        buildings=data.qa["buildings"],
    )
    _check(
        checks,
        "registry_points_in_city",
        not outside,
        "Roster buildings lie inside the city boundary",
        outside=outside,
    )
    _check(
        checks,
        "registry_osm_distance",
        not far,
        f"Matched OSM objects lie within {REGISTRY_WARN_M:.0f} m of the roster coordinate",
        severity="warn",
        over_limit=[{"distance_m": d, "name": n, "map_id": i} for d, n, i in far],
    )
    _check(
        checks,
        "registry_map_points_listed",
        not data.qa["map_points_not_in_founder_lists"],
        "Every map point belongs to an institution of the founder's lists",
        severity="warn",
        not_listed=data.qa["map_points_not_in_founder_lists"],
    )
    return drafts, ownership, unmatched, stats


def _medical_drafts(
    config: CityConfig,
    medical: tuple[list[trmo.Unit], DatasetManifest],
    candidates: list[cat.Candidate],
    index: AddressIndex,
    city_utm: shapely.Geometry,
    crs: str,
    book: EvidenceBook,
    checks: list[dict[str, Any]],
) -> tuple[list[DestinationDraft], list[CategoryOwnership], list[cat.Candidate], dict[str, Any]]:
    """City polyclinics from the OMS register, placed at their OSM address.

    Objects of one category that land on the same OSM address object are one
    destination. Registry addresses of the city that OSM does not hold are
    reported in QA and not placed: no coordinates are invented.
    """

    units, manifest = medical
    objects, stats = trmo.polyclinics(units)
    grouped: dict[tuple[str, str], list[tuple[trmo.MedicalObject, Any]]] = {}
    unlocated: list[dict[str, str]] = []
    for item in objects:
        hit = index.locate(item.address)
        if hit is None:
            parsed = parse_address(item.address)
            if parsed is not None and parsed.settlement in index.settlements:
                unlocated.append(
                    {"category": item.category, "name": item.name, "address": item.address}
                )
            continue
        grouped.setdefault((item.category, hit.osm_ref), []).append((item, hit))
    located = [
        (members[0][0], members[0][1], [m for m, _ in members]) for members in grouped.values()
    ]
    located.sort(key=lambda entry: (entry[0].category, entry[0].object_id))
    inputs = [
        (item.object_id, item.category, item.name, to_crs(hit.point, "EPSG:4326", crs))
        for item, hit, _ in located
    ]
    attached, unmatched = match_points(inputs, candidates, crs)
    drafts: list[DestinationDraft] = []
    ownership: list[CategoryOwnership] = []
    approximate: list[dict[str, str]] = []
    for (item, hit, members), (_, _, _, point_utm) in zip(located, inputs):
        records = [
            book.add(
                Evidence(
                    evidence_id=f"{trmo.SOURCE_ID}:{member.object_id}",
                    source_id=trmo.SOURCE_ID,
                    dataset_id=manifest.dataset_id,
                    record_id=";".join(sorted({u.unit_code for u in member.units})),
                    url=str(manifest.source_url),
                    fetched_at=manifest.retrieved_at,
                    content_hash=member.record_hash(),
                    verification_state="verified",
                )
            )
            for member in members
        ]
        osm_refs = sorted({ref for c in attached[item.object_id] for ref in c.osm_refs})
        destination_id = f"{config.city_id}-{item.object_id}"
        if hit.method != "exact":
            approximate.append({"destination_id": destination_id, "address": item.address})
        drafts.append(
            DestinationDraft(
                destination_id=destination_id,
                category=item.category,
                name=item.name,
                utm=point_utm,
                lonlat=hit.point,
                inside_city_boundary=city_utm.distance(point_utm) == 0,
                osm_ref=osm_refs[0] if osm_refs else hit.osm_ref,
                reasons=[],
                evidence_ids=[*records, book.osm(hit.osm_ref), *(book.osm(r) for r in osm_refs)],
                location_source="registry_address",
                ownership_verified=True,
            )
        )
        ownership.append(
            CategoryOwnership(
                destination_id=destination_id,
                category=item.category,  # type: ignore[arg-type]
                ownership=item.ownership,  # type: ignore[arg-type]
                basis="registry",
                verification_state="verified",
                evidence_ids=records,
            )
        )
    _check(
        checks,
        "medical_registry_city_addresses_located",
        not unlocated,
        "Every register polyclinic of a settlement in the search area is found in OSM addresses",
        severity="warn",
        unlocated=unlocated,
    )
    _check(
        checks,
        "medical_registry_exact_addresses",
        not approximate,
        "Polyclinics are placed at the exact house (nearest same-parity number at most ±4 otherwise)",
        severity="warn",
        nearest_number=approximate,
    )
    stats |= {
        "located_destinations": {
            c: sum(1 for d in drafts if d.category == c)
            for c in ("adult_polyclinic", "child_polyclinic")
        },
        "city_addresses_not_in_osm": len(unlocated),
        "osm_candidates_attached": sum(len(v) for v in attached.values()),
        "osm_candidates_unmatched": len(unmatched),
    }
    return drafts, ownership, unmatched, stats


def _org_name(org: obrnadzor.EduOrg) -> str:
    short = org.short_name.strip()
    return short if len(short) > 2 else org.full_name


def _university_drafts(
    config: CityConfig,
    education: tuple[list[obrnadzor.EduOrg], DatasetManifest],
    candidates: list[cat.Candidate],
    index: AddressIndex,
    city_utm: shapely.Geometry,
    crs: str,
    book: EvidenceBook,
    checks: list[dict[str, Any]],
) -> tuple[list[DestinationDraft], list[CategoryOwnership], list[cat.Candidate], dict[str, Any]]:
    """Public colleges and universities: accreditation registry plus their OSM buildings.

    An OSM building linked by name to a public organisation is one of its
    buildings (location from OSM). The registry address adds a destination when
    no linked building of that organisation lies within 100 m. Buildings linked
    to private or departmental organisations stay quarantined with that reason.
    """

    orgs, manifest = education
    local = [
        o
        for o in orgs
        if config.name in o.post_address and obrnadzor.classify(o) != "NOT_COLLEGE_OR_UNIVERSITY"
    ]
    by_id = {o.org_id: o for o in local}
    reasons = {o.org_id: obrnadzor.classify(o) for o in local}
    keys = [obrnadzor.name_key(o) for o in local]

    def org_evidence(org: obrnadzor.EduOrg) -> str:
        return book.add(
            Evidence(
                evidence_id=f"{obrnadzor.SOURCE_ID}:{org.org_id.lower()}",
                source_id=obrnadzor.SOURCE_ID,
                dataset_id=manifest.dataset_id,
                record_id=org.org_id,
                url=obrnadzor.REGISTRY_PAGE,
                fetched_at=manifest.retrieved_at,
                content_hash=obrnadzor.record_hash(org),
                verification_state="verified",
            )
        )

    drafts: list[DestinationDraft] = []
    ownership: list[CategoryOwnership] = []
    unmatched: list[cat.Candidate] = []
    linked_points: dict[str, list[shapely.Point]] = {}
    links: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda c: c.osm_refs[0]):
        found = obrnadzor.link(candidate.name, keys)
        if found.org_id is None:
            unmatched.append(candidate)
            continue
        org = by_id[found.org_id]
        reason = reasons[org.org_id]
        point_utm = to_crs(candidate.point, "EPSG:4326", crs)
        record = org_evidence(org)
        destination_id = (
            f"{config.city_id}-college_university-{candidate.osm_refs[0].replace('/', '-')}"
        )
        drafts.append(
            DestinationDraft(
                destination_id=destination_id,
                category="college_university",
                name=candidate.name,
                utm=point_utm,
                lonlat=candidate.point,
                inside_city_boundary=city_utm.distance(point_utm) == 0,
                osm_ref=candidate.osm_refs[0],
                reasons=[] if reason is None else [f"{reason}_BY_REGISTRY"],
                evidence_ids=[record, *(book.osm(ref) for ref in candidate.osm_refs)],
                location_source="osm",
                ownership_verified=reason is None,
            )
        )
        ownership.append(
            CategoryOwnership(
                destination_id=destination_id,
                category="college_university",
                ownership=obrnadzor.ownership(org.full_name),  # type: ignore[arg-type]
                basis="registry",
                verification_state="verified",
                evidence_ids=[record],
            )
        )
        links.append({"osm": candidate.name, "org": _org_name(org), "score": found.score})
        if reason is None:
            linked_points.setdefault(org.org_id, []).append(point_utm)
    unlocated: list[dict[str, str]] = []
    for org in local:
        if reasons[org.org_id] is not None:
            continue
        hit = index.locate(org.post_address)
        if hit is None:
            parsed = parse_address(org.post_address)
            in_area = parsed is not None and parsed.settlement in index.settlements
            if in_area and not linked_points.get(org.org_id):
                unlocated.append({"org": _org_name(org), "address": org.post_address})
            continue
        point_utm = to_crs(hit.point, "EPSG:4326", crs)
        if any(point_utm.distance(p) <= 100 for p in linked_points.get(org.org_id, [])):
            continue
        record = org_evidence(org)
        destination_id = (
            f"{config.city_id}-college_university-{obrnadzor.SOURCE_ID}-{org.org_id.lower()}"
        )
        drafts.append(
            DestinationDraft(
                destination_id=destination_id,
                category="college_university",
                name=_org_name(org),
                utm=point_utm,
                lonlat=hit.point,
                inside_city_boundary=city_utm.distance(point_utm) == 0,
                osm_ref=hit.osm_ref,
                reasons=[],
                evidence_ids=[record, book.osm(hit.osm_ref)],
                location_source="registry_address",
                ownership_verified=True,
            )
        )
        ownership.append(
            CategoryOwnership(
                destination_id=destination_id,
                category="college_university",
                ownership=obrnadzor.ownership(org.full_name),  # type: ignore[arg-type]
                basis="registry",
                verification_state="verified",
                evidence_ids=[record],
            )
        )
    _check(
        checks,
        "higher_education_registry_located",
        not unlocated,
        "Every public college and university of the city has a building or a located address",
        severity="warn",
        unlocated=unlocated,
    )
    rejected: dict[str, int] = {}
    for reason in reasons.values():
        if reason:
            rejected[reason] = rejected.get(reason, 0) + 1
    stats = {
        "rules": obrnadzor.RULES_VERSION,
        "link_rules": obrnadzor.LINK_RULES_VERSION,
        "local_orgs": len(local),
        "public_orgs": sum(1 for r in reasons.values() if r is None),
        "rejected": rejected,
        "accepted_destinations": sum(1 for d in drafts if not d.reasons),
        "osm_linked": links,
        "osm_unlinked": len(unmatched),
    }
    return drafts, ownership, unmatched, stats


def _lonlat(point_utm: shapely.Point, crs: str) -> tuple[float, float]:
    point = to_crs(point_utm, crs, "EPSG:4326")
    return round(point.x, 7), round(point.y, 7)


def _access_nodes(
    accesses: list[Access], network: WalkNetwork, crs: str, book: EvidenceBook
) -> list[AccessNode]:
    nodes: dict[str, AccessNode] = {}
    for access in accesses:
        if access.node_id in nodes:
            continue
        lon, lat = _lonlat(access.point, crs)
        nodes[access.node_id] = AccessNode(
            access_node_id=access.node_id,
            kind=ACCESS_KIND,
            lon=lon,
            lat=lat,
            graph_version=network.graph_version,
            osm_way_ref=access.way_ref,
            offset_along_way_m=access.offset_m,
            evidence_ids=[book.osm(access.way_ref)],
        )
    return [nodes[key] for key in sorted(nodes)]


def _sample_point(draft: PointDraft, access: Access, crs: str, book: EvidenceBook) -> SamplePoint:
    lon, lat = _lonlat(draft.utm, crs)
    center_lon, center_lat = _lonlat(draft.center_utm, crs)
    return SamplePoint(
        point_id=draft.point_id,
        cell_id=draft.cell_id,
        lon=lon,
        lat=lat,
        initial_center_lon=center_lon,
        initial_center_lat=center_lat,
        placement=draft.placement,  # type: ignore[arg-type]
        building_ref=draft.building_ref,
        offset_m=round(draft.offset_m, 2),
        access_node_id=access.node_id,
        access_distance_m=round(access.distance_m, 2),
        evidence_ids=[book.osm(draft.building_ref)],
    )


def _destination(config: CityConfig, draft: DestinationDraft, access: Access) -> Destination:
    return Destination(
        destination_id=draft.destination_id,
        category=draft.category,  # type: ignore[arg-type]
        serving_city_id=config.city_id,
        name=draft.name,
        lon=round(draft.lonlat.x, 7),
        lat=round(draft.lonlat.y, 7),
        inside_city_boundary=draft.inside_city_boundary,
        osm_ref=draft.osm_ref,
        location_source=draft.location_source,  # type: ignore[arg-type]
        access_node_id=access.node_id,
        access_distance_m=round(access.distance_m, 2),
        status="quarantined" if draft.reasons else "accepted",
        quarantine_reasons=draft.reasons,
        evidence_ids=draft.evidence_ids,
    )


def _distances(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "median": round(statistics.median(ordered), 1),
        "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 1),
        "max": round(ordered[-1], 1),
    }


def _access_checks(
    points: list[SamplePoint],
    destinations: list[Destination],
    network: WalkNetwork,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    far_points = [p.point_id for p in points if p.access_distance_m > ACCESS_WARN_M]
    far_destinations = [
        d.destination_id
        for d in destinations
        if d.status == "accepted" and d.access_distance_m > ACCESS_WARN_M
    ]
    _check(
        checks,
        "walk_network_main_component",
        network.stats["main_component_length_share"] >= 0.9,
        "Largest connected walk component holds at least 90% of walk length",
        share=network.stats["main_component_length_share"],
    )
    _check(
        checks,
        "access_distance_points",
        not far_points,
        f"Sample points join the walk network within {ACCESS_WARN_M:.0f} m",
        severity="warn",
        over_limit=far_points,
    )
    _check(
        checks,
        "access_distance_accepted_destinations",
        not far_destinations,
        f"Accepted destinations join the walk network within {ACCESS_WARN_M:.0f} m",
        severity="warn",
        over_limit=far_destinations,
    )
    return {
        "sample_points": _distances([p.access_distance_m for p in points]),
        "destinations": _distances([d.access_distance_m for d in destinations]),
    }


def _coverage(
    config: CityConfig,
    destinations: list[Destination],
    evidence: dict[str, str],
    has_registry: bool = False,
    has_medical_registry: bool = False,
    has_education_registry: bool = False,
) -> list[CoverageRecord]:
    city = config.city_id
    start, end = ANALYSIS_WINDOW
    osm = [evidence["osm"]]
    records = [
        CoverageRecord(
            collection="territories",
            city_id=city,
            status="complete",
            reason="Городской округ и все три района из OSM",
            evidence_ids=osm,
            valid_from=start,
            valid_to=end,
        ),
        CoverageRecord(
            collection="population_cells",
            city_id=city,
            status="complete",
            reason="Все ячейки сетки 1 км, пересекающие границу; население GHSL по центроидам пикселей",
            evidence_ids=[evidence["ghsl"], evidence["osm"]],
            valid_from=start,
            valid_to=end,
        ),
        CoverageRecord(
            collection="sample_points",
            city_id=city,
            status="complete",
            reason="По одной точке на ячейку со зданием; исключённые ячейки с причиной и населением в QA",
            evidence_ids=osm,
            valid_from=start,
            valid_to=end,
        ),
        CoverageRecord(
            collection="access_nodes",
            city_id=city,
            mode="walk",
            status="complete",
            reason="Вход на главную связную компоненту пешеходной сети OSM v1 у каждой точки и объекта",
            evidence_ids=osm,
            valid_from=start,
            valid_to=end,
        ),
    ]
    registry_categories = set(KIND_TO_CATEGORY.values()) if has_registry else set()
    for category in PRODUCT_CATEGORIES:
        accepted = sum(1 for d in destinations if d.category == category and d.status == "accepted")
        extra: list[str] = []
        if has_education_registry and category == "college_university":
            status = "partial"
            reason = (
                "Государственные колледжи и вузы по реестру аккредитации Рособрнадзора; корпуса — "
                "по названиям в OSM; частные, ведомственные и неаккредитованные не входят"
            )
            extra = [evidence["higher_education_registry"]]
        elif has_medical_registry and category in {"adult_polyclinic", "child_polyclinic"}:
            status = "partial"
            reason = (
                "Городские поликлиники ОМС (ОКФС 13/14) по ТРМО ТФОМС; адреса, которых нет в OSM, "
                "не размещены; частные и ведомственные не входят"
            )
            extra = [evidence["medical_registry"]]
        elif category in registry_categories:
            status = "partial"
            reason = (
                "Муниципальные учреждения подтверждены перечнем учредителя (klgd.ru); "
                "региональные, федеральные и частные не подтверждены"
            )
            extra = [evidence["education_registry"]]
        elif category in OWNERSHIP_REQUIRED:
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
                    "evidence_ids": osm
                    + extra
                    + ([evidence["airports"]] if category == "airport" else []),
                    "valid_from": start,
                    "valid_to": end,
                }
            )
        )
    records.append(
        CoverageRecord(
            collection="category_ownership",
            city_id=city,
            status="partial" if registry_categories or has_medical_registry else "unknown",
            reason=(
                "Школы и детсады — по перечню учредителя (муниципальные), поликлиники — по ТРМО "
                "(ОКФС 13/14), колледжи и вузы — по реестру аккредитации; прочие не подтверждены"
                if registry_categories or has_medical_registry
                else "Официальный реестр учреждений ещё не загружен"
            ),
            evidence_ids=[
                evidence[key]
                for key in ("education_registry", "medical_registry", "higher_education_registry")
                if key in evidence
            ],
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
