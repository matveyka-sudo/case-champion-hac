"""Build the urban bundle of one city: territories, grid, points, population,
destination candidates, access nodes, ownership status, coverage and QA; for a
city with ``freight`` also freight nodes and physical sites with road access.

Usage (geo dependencies as an overlay until DEVOPS adds them, see README)::

    python -m a2_data_pipeline.build_city kaliningrad --bundle-id <id> --accept
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, get_args

import pyarrow.parquet as pq  # type: ignore[import-untyped]
import shapely  # type: ignore[import-untyped]
from pydantic import BaseModel

from . import admnvrsk, freight, gorono, gortrans39, klgd, mgfoms, obrnadzor, trmo
from . import categories as cat
from .access import Access, WalkNetwork, load_road_network, load_walk_network, snap
from .addresses import AddressIndex, parse_address, street_tokens
from .bundle import (
    OWNERSHIP_REQUIRED,
    PRODUCT_CATEGORIES,
    AcceptanceState,
    AccessNode,
    BundleManifest,
    CategoryOwnership,
    CoverageRecord,
    CoverageStatus,
    Destination,
    DistrictShare,
    Evidence,
    FreightKind,
    FreightNode,
    FreightSite,
    GridSpec,
    PopulationCell,
    SamplePoint,
    SourceVersion,
    StopFrequency,
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
OURAIRPORTS_DATASET = "ourairports.airports-20261108"
SEARCH_BUFFER_M = 10_000
SCHOOL_BUILDING_RULES = "school-building-link-v1"
SCHOOL_CAMPUS_M = 5_000.0
SCHOOL_NAME_NUMBER = re.compile(
    r"(?:школ|лице|гимнази|образовани)[а-яё]*\s*(?:№|N)\s*(\d{1,4})(?!\d)", re.I
)
AIRPORT_BUFFER_M = 60_000
ACCESS_WARN_M = 500.0
REGISTRY_WARN_M = 150.0
# Declared analysis window of the snapshot (ML2 assumption): geometry and
# population of an OSM state from 2026-11-07 are used for dates in November 2026.
# Not a guarantee that objects are unchanged; schedules have their own periods.
ANALYSIS_WINDOW = (date(2026, 11, 1), date(2026, 11, 30))
PUBLIC_CATEGORY_NAMES = {
    "adult_polyclinic": "взрослые поликлиники",
    "child_polyclinic": "детские поликлиники",
    "kindergarten": "детсады",
    "school": "школы",
    "college_university": "колледжи и вузы",
}
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


def _osm_hashes(osm_dir: Path, networks: Sequence[WalkNetwork]) -> dict[str, str]:
    """Record hash per OSM reference; the first table that holds a reference wins."""

    hashes: dict[str, str] = {}
    for network in networks:
        for ref, value in network.hashes.items():
            hashes.setdefault(ref, value)
    for name in ("boundaries", "pois", "buildings", "addresses", "freight", "stops"):
        table = pq.read_table(osm_dir / f"{name}.parquet", columns=["osm_ref", "content_hash"])
        for ref, value in zip(
            table.column("osm_ref").to_pylist(), table.column("content_hash").to_pylist()
        ):
            hashes.setdefault(ref, value)
    return hashes


def build(config: CityConfig, bundle_id: str, accept: bool) -> Path:
    now = datetime.now(UTC)
    osm_manifest = load_dataset_manifest(config.osm_dataset_id)
    ghsl_manifest = load_dataset_manifest(config.ghsl_dataset_id)
    airports_manifest = load_dataset_manifest(OURAIRPORTS_DATASET)
    osm_dir = processed_dir() / config.city_id / "osm"
    boundary_ids = {config.osm_boundary_relation, *config.osm_district_relations}
    if not extract_is_current(osm_dir, boundary_ids, config.extract_bbox, config.poi_bbox):
        extract(
            raw_dir() / osm_manifest.raw_path,
            boundary_ids,
            osm_dir,
            config.extract_bbox,
            config.poi_bbox,
        )
    layers = load_osm_layers(osm_dir, config.osm_boundary_relation, config.osm_district_relations)
    grid = build_grid(layers, config.utm_epsg)
    assign_population(grid, raw_dir() / ghsl_manifest.raw_path, config.ghsl_tif)
    network = load_walk_network(osm_dir, config.utm_epsg, config.osm_dataset_id)
    road = (
        load_road_network(osm_dir, config.utm_epsg, config.osm_dataset_id)
        if config.freight
        else None
    )

    checks: list[dict[str, Any]] = []
    book = EvidenceBook(
        osm_manifest, _osm_hashes(osm_dir, [network] if road is None else [network, road])
    )
    ghsl_evidence = book.add(
        _dataset_evidence(ghsl_manifest, "ghsl:" + config.ghsl_dataset_id, config.ghsl_tif)
    )
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
        medical = (*_medical_objects(medical_manifest), medical_manifest)
        snapshot_evidence["medical_registry"] = book.add(
            _dataset_evidence(
                medical_manifest,
                f"{medical_manifest.source_id}:dataset:{medical_manifest.dataset_id}",
            )
        )
    kindergartens = None
    if config.kindergarten_registry_dataset_id:
        kindergarten_manifest = load_dataset_manifest(config.kindergarten_registry_dataset_id)
        source_manifests.append(kindergarten_manifest)
        records, kindergarten_stats = gorono.load(raw_dir() / kindergarten_manifest.raw_path)
        kindergartens = (records, kindergarten_stats, kindergarten_manifest)
        snapshot_evidence["kindergarten_registry"] = book.add(
            _dataset_evidence(
                kindergarten_manifest,
                f"{kindergarten_manifest.source_id}:dataset:{kindergarten_manifest.dataset_id}",
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

    seaport = None
    if config.seaport_dataset_id:
        seaport_manifest = load_dataset_manifest(config.seaport_dataset_id)
        source_manifests.append(seaport_manifest)
        record = freight.load_seaport(
            raw_dir() / seaport_manifest.raw_path, config.seaport_name or config.name
        )
        seaport = (record, seaport_manifest)
        snapshot_evidence["seaport"] = book.add(
            _dataset_evidence(
                seaport_manifest,
                f"{seaport_manifest.source_id}:dataset:{seaport_manifest.dataset_id}",
            )
        )

    territories = _territories(config, layers, grid, book, checks)
    cells, point_drafts = _cells(config, grid, ghsl_evidence, checks)
    drafts, ownership, rule_counts = _destination_drafts(
        config,
        layers,
        osm_dir,
        airports_manifest,
        book,
        checks,
        roster,
        medical,
        education,
        kindergartens,
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
    freight_nodes: list[FreightNode] = []
    freight_sites: list[FreightSite] = []
    freight_coverage: list[CoverageRecord] = []
    freight_summary: dict[str, Any] | None = None
    if road is not None:
        freight_nodes, freight_sites, road_nodes, freight_summary = _freight(
            config, osm_dir, layers, road, seaport, book, checks
        )
        access_nodes = sorted([*access_nodes, *road_nodes], key=lambda node: node.access_node_id)
        freight_coverage = _freight_coverage(
            config,
            freight_nodes,
            freight_sites,
            seaport[0] if seaport else None,
            snapshot_evidence,
        )
    frequencies: list[StopFrequency] = []
    timetable_coverage: list[CoverageRecord] = []
    timetable_summary: dict[str, Any] | None = None
    if config.timetable_dataset_id:
        timetable_manifest = load_dataset_manifest(config.timetable_dataset_id)
        source_manifests.append(timetable_manifest)
        snapshot_evidence["timetables"] = book.add(
            _dataset_evidence(
                timetable_manifest,
                f"{timetable_manifest.source_id}:dataset:{timetable_manifest.dataset_id}",
            )
        )
        frequencies, timetable_summary, timetable_record = _stop_frequencies(
            config, osm_dir, layers, timetable_manifest, book, checks, snapshot_evidence
        )
        timetable_coverage = [timetable_record]
    registry_coverage: dict[str, tuple[str, str]] = {}
    if roster is not None:
        for category in KIND_TO_CATEGORY.values():
            registry_coverage[category] = (
                "Муниципальные учреждения подтверждены перечнем учредителя; региональные, "
                "федеральные и частные не подтверждены",
                "education_registry",
            )
    if medical is not None:
        medical_reason = {
            trmo.SOURCE_ID: "Городские поликлиники ОМС (ОКФС 13/14) по ТРМО ТФОМС; адреса, которых "
            "нет в OSM, не размещены; частные и ведомственные не входят",
            admnvrsk.SOURCE_ID: "Городские поликлиники по перечню учреждений здравоохранения на "
            "сайте администрации (ОПФ ГБУЗ … МЗ КК); головные адреса, не все отделения",
            mgfoms.SOURCE_ID: "Поликлиники с прикреплением, подведомственные ДЗМ, по справочнику "
            "МГФОМС с координатами фонда; ведомственные и частные не входят",
        }[medical[2].source_id]
        for category in ("adult_polyclinic", "child_polyclinic"):
            registry_coverage[category] = (medical_reason, "medical_registry")
    if kindergartens is not None:
        registry_coverage["kindergarten"] = (
            "Муниципальные детсады по перечню учредителя (Управление образования, gorono.ru), "
            "адрес учреждения; другие корпуса и частные детсады не подтверждены",
            "kindergarten_registry",
        )
    if education is not None:
        registry_coverage["college_university"] = (
            "Государственные колледжи и вузы по реестру аккредитации Рособрнадзора; корпуса — по "
            "названиям в OSM; частные, ведомственные и неаккредитованные не входят",
            "higher_education_registry",
        )
        if roster is None and config.school_buildings_by_name:
            registry_coverage["school"] = (
                "Государственные школы по реестру аккредитации: адрес организации и корпуса OSM с "
                "номером этой школы в названии (до 5 км); корпуса без номера не подтверждены",
                "higher_education_registry",
            )
            registry_coverage["kindergarten"] = (
                "Только дошкольные отделения государственных школ, названные в OSM по номеру "
                "школы; отдельные детсады не подтверждены",
                "higher_education_registry",
            )
        elif roster is None:
            registry_coverage["school"] = (
                "Государственные и муниципальные школы по реестру аккредитации; только адрес "
                "организации, другие корпуса не подтверждены",
                "higher_education_registry",
            )
    coverage = _coverage(
        config,
        destinations,
        snapshot_evidence,
        registry_coverage,
        len(layers.districts),
        [*freight_coverage, *timetable_coverage],
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
        "road_graph_version": road.graph_version if road else None,
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
            **({"freight": freight_summary} if freight_summary else {}),
            **({"timetables": timetable_summary} if timetable_summary else {}),
        },
    }
    limitations = [
        "Границы — OSM (relation городского округа), не официальный реестр границ МО",
        "Население — модельная оценка GHSL R2023A эпохи 2025, не перепись",
        _public_limitation(registry_coverage),
        "Ж/д вокзалы, автовокзалы и ТЦ отобраны правилами OSM v1 без сверки со справочником",
        "Вход на граф — проекция на пешеходную сеть OSM v1, не размеченный вход здания",
        "Период применимости 01–30.11.2026 заявлен ML2 для снимка OSM от 07.11.2026",
    ]
    if timetable_summary:
        limitations.append(
            "Расписаний (рейсов) нет: остановочные таблицы gortrans39 без trip_id дают только "
            f"справку частоты, {len(timetable_summary['accepted_routes'])} из "
            f"{timetable_summary['routes']} маршрутов муниципального перевозчика; направления и "
            "даты действия не подтверждены"
        )
    else:
        limitations.append("Расписания в этот bundle не входят")
    if road is None:
        limitations.append("Грузовые данные в этот bundle не входят")
    else:
        limitations += [
            "Грузовые узлы: порт — портовые территории OSM и объекты со словом «порт» в названии в "
            "границе города с зарегистрированным морским портом; объекты, найденные только по "
            "скрытому в разметке перечню операторов Росморпорта, в карантине; нефтяные терминалы — "
            "по названиям OSM; ж/д станции в карантине: грузовые операции не подтверждены",
            "Узлы за границей города (терминалы порта в соседних муниципалитетах) не включены",
            f"Грузовые площадки — промзоны и склады OSM по правилам {freight.RULES_VERSION}, равные "
            "веса; реестра предприятий и объёмов нет",
            "Совместимость груза, услуги и подъезда неизвестна; въезд — проекция внутренней точки "
            "контура на дорожную сеть OSM v1, не размеченные ворота; ограничения массы не учтены",
        ]
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
        road_graph_version=road.graph_version if road else None,
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
        limitations=limitations,
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
    if road is not None:
        collections |= {"freight_nodes": freight_nodes, "freight_sites": freight_sites}
    if config.timetable_dataset_id:
        collections |= {"stop_frequencies": frequencies}
    return publish_bundle(manifest, collections, qa_report)


def _public_limitation(registry_coverage: dict[str, tuple[str, str]]) -> str:
    missing = [
        PUBLIC_CATEGORY_NAMES[c]
        for c in PRODUCT_CATEGORIES
        if c in OWNERSHIP_REQUIRED and c not in registry_coverage
    ]
    if len(missing) == len(OWNERSHIP_REQUIRED):
        return "Поликлиники, детсады, школы, колледжи и вузы в карантине до сверки с официальным реестром"
    if not missing:
        return (
            "Поликлиники, детсады, школы, колледжи и вузы приняты только по официальным реестрам; "
            "объекты OSM без записи реестра — в карантине"
        )
    return (
        "Без реестра собственности, в карантине: "
        + ", ".join(missing)
        + "; остальные публичные категории приняты только по официальным реестрам"
    )


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
    medical: tuple[list[RegistryObject], dict[str, Any], DatasetManifest] | None = None,
    education: tuple[list[obrnadzor.EduOrg], DatasetManifest] | None = None,
    kindergartens: KindergartenInput | None = None,
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
    if kindergartens is not None:
        kindergarten_candidates = [c for c in candidates if c.category == "kindergarten"]
        rest = [c for c in candidates if c.category != "kindergarten"]
        kg_drafts, kg_ownership, kg_unmatched, kg_stats = _founder_kindergartens(
            config, kindergartens, kindergarten_candidates, address_index(), city_utm, crs, book
        )
        drafts.extend(kg_drafts)
        ownership.extend(kg_ownership)
        candidates = rest + kg_unmatched
        rule_counts["kindergarten_registry"] = kg_stats
        _check(
            checks,
            "kindergarten_registry_located",
            not kg_stats["unlocated"],
            "Every kindergarten of the founder's list is found in OSM addresses",
            severity="warn",
            unlocated=kg_stats["unlocated"],
        )
    if education is not None and roster is None:
        linked = {"school", "kindergarten"} if config.school_buildings_by_name else {"school"}
        school_candidates = [c for c in candidates if c.category in linked]
        rest = [c for c in candidates if c.category not in linked]
        school_drafts, school_ownership, school_unmatched, school_stats = _accredited_schools(
            config, education, school_candidates, address_index(), city_utm, crs, book, checks
        )
        drafts.extend(school_drafts)
        ownership.extend(school_ownership)
        candidates = rest + school_unmatched
        rule_counts["accredited_schools"] = school_stats
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
    identities: dict[tuple[str, str], list[str]] = {}
    for draft in drafts:
        if not draft.reasons:
            key = (draft.category, draft.osm_ref or draft.destination_id)
            identities.setdefault(key, []).append(draft.destination_id)
    repeated = sorted(ids for ids in identities.values() if len(ids) > 1)
    _check(
        checks,
        "destination_identity_unique",
        not repeated,
        "An accepted object (OSM object or registry record) occurs once per category",
        repeated=repeated[:20],
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
        severity="warn",
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
    medical: tuple[list[RegistryObject], dict[str, Any], DatasetManifest],
    candidates: list[cat.Candidate],
    index: AddressIndex,
    city_utm: shapely.Geometry,
    crs: str,
    book: EvidenceBook,
    checks: list[dict[str, Any]],
) -> tuple[list[DestinationDraft], list[CategoryOwnership], list[cat.Candidate], dict[str, Any]]:
    """City polyclinics from an official register.

    A register with coordinates (MGFOMS) places the object at its own point. A
    register without them is placed at the OSM address: objects of one category
    that land on the same OSM address object are one destination, and city
    addresses OSM does not hold are reported in QA and not placed — no
    coordinates are invented. Register points beyond the search area are dropped.
    """

    objects, registry_stats, manifest = medical
    stats = dict(registry_stats)
    grouped: dict[tuple[str, str], list[tuple[RegistryObject, Any]]] = {}
    unlocated: list[dict[str, str]] = []
    beyond = 0
    for item in objects:
        if item.lon is not None and item.lat is not None:
            point = shapely.Point(item.lon, item.lat)
            if city_utm.distance(to_crs(point, "EPSG:4326", crs)) > SEARCH_BUFFER_M:
                beyond += 1
                continue
            own = RegistryPoint(point=point)
            grouped.setdefault((item.category, f"registry:{item.object_id}"), []).append(
                (item, own)
            )
            continue
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
                    evidence_id=f"{manifest.source_id}:{member.object_id}",
                    source_id=manifest.source_id,
                    dataset_id=manifest.dataset_id,
                    record_id=member.record_id,
                    url=str(manifest.source_url),
                    fetched_at=manifest.retrieved_at,
                    content_hash=member.content_hash,
                    verification_state="verified",
                )
            )
            for member in members
        ]
        osm_refs = sorted({ref for c in attached[item.object_id] for ref in c.osm_refs})
        destination_id = f"{config.city_id}-{item.object_id}"
        if hit.method not in {"exact", "registry"}:
            approximate.append({"destination_id": destination_id, "address": item.address})
        address_refs = [book.osm(hit.osm_ref)] if hit.osm_ref else []
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
                evidence_ids=[*records, *address_refs, *(book.osm(r) for r in osm_refs)],
                location_source="registry" if hit.method == "registry" else "registry_address",
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
        "registry_points_beyond_search_area": beyond,
        "osm_candidates_attached": sum(len(v) for v in attached.values()),
        "osm_candidates_unmatched": len(unmatched),
    }
    return drafts, ownership, unmatched, stats


@dataclass(frozen=True)
class RegistryObject:
    """One registry object of a public category, whatever register it comes from."""

    object_id: str
    category: str
    ownership: str
    address: str
    name: str
    record_id: str
    content_hash: str
    lon: float | None = None  # the register's own coordinate, when it has one
    lat: float | None = None


@dataclass(frozen=True)
class RegistryPoint:
    """Location from the register itself (no OSM address object)."""

    point: shapely.Point
    osm_ref: str | None = None
    method: str = "registry"


def _medical_objects(manifest: DatasetManifest) -> tuple[list[RegistryObject], dict[str, Any]]:
    raw = raw_dir() / manifest.raw_path
    if manifest.source_id == trmo.SOURCE_ID:
        objects, stats = trmo.polyclinics(trmo.read_units(raw))
        return [
            RegistryObject(
                object_id=o.object_id,
                category=o.category,
                ownership=o.ownership,
                address=o.address,
                name=o.name,
                record_id=";".join(sorted({u.unit_code for u in o.units})),
                content_hash=o.record_hash(),
            )
            for o in objects
        ], stats
    if manifest.source_id == admnvrsk.SOURCE_ID:
        rows, stats = admnvrsk.load(raw)
        return [
            RegistryObject(
                object_id=r.object_id,
                category=r.category,
                ownership=r.ownership,
                address=r.address,
                name=r.unit or r.name,
                record_id=f"row-{r.row_number}",
                content_hash=r.record_hash(),
            )
            for r in rows
        ], stats
    if manifest.source_id == mgfoms.SOURCE_ID:
        clinics, stats = mgfoms.load(raw)
        return [
            RegistryObject(
                object_id=c.object_id,
                category=c.category,
                ownership=c.ownership,
                address=c.address,
                name=c.name,
                record_id=c.record_id,
                content_hash=c.record_hash(),
                lon=c.lon,
                lat=c.lat,
            )
            for c in clinics
        ], stats
    raise ValueError(f"no polyclinic reader for source {manifest.source_id}")


def _accredited_schools(
    config: CityConfig,
    education: tuple[list[obrnadzor.EduOrg], DatasetManifest],
    candidates: list[cat.Candidate],
    index: AddressIndex,
    city_utm: shapely.Geometry,
    crs: str,
    book: EvidenceBook,
    checks: list[dict[str, Any]],
) -> tuple[list[DestinationDraft], list[CategoryOwnership], list[cat.Candidate], dict[str, Any]]:
    """Public schools of the accreditation registry, for a city without a founder's roster.

    The registry address is the school; OSM candidates join it by the roster rules
    (equal numbers within 300 m, unnamed or type-only names within 60 m).
    """

    orgs, manifest = education
    schools = [
        o
        for o in orgs
        if _names_city(o.post_address, config.name) and obrnadzor.school_reason(o) is None
    ]
    placed: dict[str, tuple[obrnadzor.EduOrg, Any]] = {}
    unlocated: list[dict[str, str]] = []
    for org in schools:
        hit = index.locate(org.post_address)
        if hit is None:
            parsed = parse_address(org.post_address)
            if parsed is not None and parsed.settlement in index.settlements:
                unlocated.append({"org": _org_name(org), "address": org.post_address})
            continue
        placed.setdefault(hit.osm_ref, (org, hit))
    entries = sorted(placed.values(), key=lambda item: item[0].org_id)
    buildings = [
        klgd.Building(
            map_id=org.org_id.lower(),
            institution_id=org.org_id.lower(),
            kind="school",
            name=_org_name(org),
            lat=hit.point.y,
            lon=hit.point.x,
            address_short=None,
            licence=None,
            accreditation=None,
        )
        for org, hit in entries
    ]
    matches, unmatched, stats = match_roster(buildings, candidates, crs)
    drafts: list[DestinationDraft] = []
    ownership: list[CategoryOwnership] = []
    for (org, hit), match in zip(entries, matches):
        record = book.add(
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
        osm_refs = sorted({ref for c in match.candidates for ref in c.osm_refs})
        destination_id = f"{config.city_id}-school-{obrnadzor.SOURCE_ID}-{org.org_id.lower()}"
        drafts.append(
            DestinationDraft(
                destination_id=destination_id,
                category="school",
                name=_org_name(org),
                utm=match.point_utm,
                lonlat=hit.point,
                inside_city_boundary=city_utm.distance(match.point_utm) == 0,
                osm_ref=osm_refs[0] if osm_refs else hit.osm_ref,
                reasons=[],
                evidence_ids=[record, book.osm(hit.osm_ref), *(book.osm(r) for r in osm_refs)],
                location_source="registry_address",
                ownership_verified=True,
            )
        )
        ownership.append(
            CategoryOwnership(
                destination_id=destination_id,
                category="school",
                ownership=obrnadzor.ownership(org.full_name),  # type: ignore[arg-type]
                basis="registry",
                verification_state="verified",
                evidence_ids=[record],
            )
        )
    if config.school_buildings_by_name:
        unmatched, building_stats = _school_buildings(
            config, schools, entries, unmatched, manifest, city_utm, crs, book, drafts, ownership
        )
        stats["school_buildings"] = building_stats
    _check(
        checks,
        "accredited_schools_located",
        not unlocated,
        "Every public school of the accreditation registry in the city is found in OSM addresses",
        severity="warn",
        unlocated=unlocated,
    )
    stats |= {
        "registry_schools": len(schools),
        "placed": len(entries),
        "unlocated": len(unlocated),
    }
    return drafts, ownership, unmatched, stats


def _school_number(*names: str | None) -> str | None:
    """The number of a school named by type: «Школа № 1514», «Лицей № 1535» (not «Детский сад № 5»)."""

    numbers = {str(int(n)) for name in names if name for n in SCHOOL_NAME_NUMBER.findall(name)}
    return numbers.pop() if len(numbers) == 1 else None


def _school_buildings(
    config: CityConfig,
    schools: list[obrnadzor.EduOrg],
    entries: list[tuple[obrnadzor.EduOrg, Any]],
    candidates: list[cat.Candidate],
    manifest: DatasetManifest,
    city_utm: shapely.Geometry,
    crs: str,
    book: EvidenceBook,
    drafts: list[DestinationDraft],
    ownership: list[CategoryOwnership],
) -> tuple[list[cat.Candidate], dict[str, Any]]:
    """Other buildings of school complexes (rule ``school-building-link-v1``).

    An OSM school or kindergarten whose name gives one school number by type
    («Школа № 1514, корпус 3», «Школа № 1514, дошкольное отделение») is a building
    of the accredited public school with that number, when the number belongs to
    exactly one registry school of the city and the building lies within 5 km of
    its registry address. «Детский сад № 1234» is never linked: old kindergarten
    numbers repeat school numbers. Location: the OSM object.
    """

    counts: dict[str, int] = {}
    for org in schools:
        number = _school_number(org.short_name, org.full_name)
        if number:
            counts[number] = counts.get(number, 0) + 1
    by_number: dict[str, tuple[obrnadzor.EduOrg, shapely.Point]] = {}
    for org, hit in entries:
        number = _school_number(org.short_name, org.full_name)
        if number and counts.get(number) == 1:
            by_number[number] = (org, to_crs(hit.point, "EPSG:4326", crs))
    rest: list[cat.Candidate] = []
    linked: dict[str, int] = {}
    too_far = 0
    occupied_skipped = 0
    # an OSM object already placed for a school (its registry address) is not relinked
    occupied = {(d.category, d.osm_ref) for d in drafts if d.osm_ref}
    for candidate in sorted(candidates, key=lambda c: c.osm_refs[0]):
        if any((candidate.category, ref) in occupied for ref in candidate.osm_refs):
            occupied_skipped += 1
            rest.append(candidate)
            continue
        tags = candidate.tags
        number = _school_number(
            candidate.name, tags.get("name"), tags.get("official_name"), tags.get("short_name")
        )
        if number is None or number not in by_number:
            rest.append(candidate)
            continue
        org, registry_point = by_number[number]
        point_utm = to_crs(candidate.point, "EPSG:4326", crs)
        if point_utm.distance(registry_point) > SCHOOL_CAMPUS_M:
            too_far += 1
            rest.append(candidate)
            continue
        record = book.add(
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
                inside_city_boundary=city_utm.distance(point_utm) == 0,
                osm_ref=candidate.osm_refs[0],
                reasons=[],
                evidence_ids=[record, *(book.osm(ref) for ref in candidate.osm_refs)],
                location_source="osm",
                ownership_verified=True,
            )
        )
        ownership.append(
            CategoryOwnership(
                destination_id=destination_id,
                category=candidate.category,  # type: ignore[arg-type]
                ownership=obrnadzor.ownership(org.full_name),  # type: ignore[arg-type]
                basis="registry",
                verification_state="verified",
                evidence_ids=[record],
            )
        )
        linked[candidate.category] = linked.get(candidate.category, 0) + 1
        occupied.add((candidate.category, candidate.osm_refs[0]))
    stats = {
        "rules": SCHOOL_BUILDING_RULES,
        "numbered_registry_schools": len(by_number),
        "repeated_numbers": sum(1 for n in counts.values() if n > 1),
        "linked_buildings": linked,
        "beyond_campus_distance": too_far,
        "occupied_by_another_school": occupied_skipped,
    }
    return rest, stats


KindergartenInput = tuple[list[gorono.Kindergarten], dict[str, Any], DatasetManifest]


def _founder_kindergartens(
    config: CityConfig,
    kindergartens: KindergartenInput,
    candidates: list[cat.Candidate],
    index: AddressIndex,
    city_utm: shapely.Geometry,
    crs: str,
    book: EvidenceBook,
) -> tuple[list[DestinationDraft], list[CategoryOwnership], list[cat.Candidate], dict[str, Any]]:
    """Municipal kindergartens of the founder's list, placed at their OSM address.

    Kindergartens that land on one OSM address object are one destination with
    the evidence of each. OSM candidates join by the roster rules (equal numbers
    within 300 m, unnamed or type-only names within 60 m) and add evidence.
    """

    records, registry_stats, manifest = kindergartens
    grouped: dict[str, list[tuple[gorono.Kindergarten, Any]]] = {}
    unlocated: list[dict[str, str]] = []
    for record in records:
        # the founder's list is the city's own: an address without a settlement is in the city
        hit = index.locate(record.address, default_settlement=config.name)
        if hit is None:
            unlocated.append({"number": record.number, "address": record.address})
            continue
        grouped.setdefault(hit.osm_ref, []).append((record, hit))
    entries = sorted(grouped.values(), key=lambda members: int(members[0][0].number))
    buildings = [
        klgd.Building(
            map_id=f"gorono-{members[0][0].number}",
            institution_id=members[0][0].number,
            kind="kindergarten",
            name=members[0][0].name,
            lat=members[0][1].point.y,
            lon=members[0][1].point.x,
            address_short=None,
            licence=None,
            accreditation=None,
        )
        for members in entries
    ]
    matches, unmatched, stats = match_roster(buildings, candidates, crs)
    drafts: list[DestinationDraft] = []
    ownership: list[CategoryOwnership] = []
    approximate = 0
    for members, match in zip(entries, matches):
        first, hit = members[0]
        records_ids = [
            book.add(
                Evidence(
                    evidence_id=f"{manifest.source_id}:{record.number}",
                    source_id=manifest.source_id,
                    dataset_id=manifest.dataset_id,
                    record_id=f"ДОУ № {record.number}",
                    url=gorono.CARD_URL.format(number=record.number),
                    fetched_at=manifest.retrieved_at,
                    content_hash=record.record_hash(),
                    verification_state="verified",
                )
            )
            for record, _ in members
        ]
        approximate += hit.method != "exact"
        osm_refs = sorted({ref for c in match.candidates for ref in c.osm_refs})
        destination_id = f"{config.city_id}-{first.object_id}"
        drafts.append(
            DestinationDraft(
                destination_id=destination_id,
                category="kindergarten",
                name=first.name,
                utm=match.point_utm,
                lonlat=hit.point,
                inside_city_boundary=city_utm.distance(match.point_utm) == 0,
                osm_ref=osm_refs[0] if osm_refs else hit.osm_ref,
                reasons=[],
                evidence_ids=[
                    *records_ids,
                    book.osm(hit.osm_ref),
                    *(book.osm(r) for r in osm_refs),
                ],
                location_source="registry_address",
                ownership_verified=first.ownership == "municipal",
            )
        )
        ownership.append(
            CategoryOwnership(
                destination_id=destination_id,
                category="kindergarten",
                ownership=first.ownership,  # type: ignore[arg-type]
                basis="registry",
                verification_state="verified",
                evidence_ids=records_ids,
            )
        )
    stats |= dict(registry_stats) | {
        "placed_records": sum(len(m) for m in entries),
        "destinations": len(drafts),
        "nearest_number_placements": approximate,
        "unlocated": unlocated,
    }
    return drafts, ownership, unmatched, stats


def _names_city(address: str, city: str) -> bool:
    """The city as a whole word: «г. Новороссийск» yes, «ул. Новороссийская» no."""

    return bool(re.search(rf"(?<![а-яё]){re.escape(city.lower())}(?![а-яё])", address.lower()))


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
        if _names_city(o.post_address, config.name)
        and obrnadzor.classify(o) != "NOT_COLLEGE_OR_UNIVERSITY"
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
    # one place per OSM object: organisations at one address share the destination
    by_ref = {d.osm_ref: d for d in drafts if d.osm_ref and not d.reasons}
    shared_address = 0
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
        if hit.osm_ref in by_ref:
            shared = by_ref[hit.osm_ref]
            if record not in shared.evidence_ids:
                shared.evidence_ids.append(record)
            shared_address += 1
            continue
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
        by_ref[hit.osm_ref] = drafts[-1]
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
        "organisations_sharing_an_address": shared_address,
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
            kind=network.kind,
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


STOP_CLUSTER_M = 300.0


def _stop_key(name: str) -> frozenset[str]:
    """Words of a stop name without street types and notes: «ул. Олега Кошевого (конечная)»."""

    text = re.sub(r"\([^)]*\)", " ", name).replace("«", " ").replace("»", " ")
    return street_tokens(text)


def _compact(points: list[shapely.Point]) -> bool:
    return all(a.distance(b) <= STOP_CLUSTER_M for a in points for b in points)


def _stop_frequencies(
    config: CityConfig,
    osm_dir: Path,
    layers: OsmLayers,
    manifest: DatasetManifest,
    book: EvidenceBook,
    checks: list[dict[str, Any]],
    evidence: dict[str, str],
) -> tuple[list[StopFrequency], dict[str, Any], CoverageRecord]:
    """Frequency reference from stop timetables (``gortrans39-frequency-rules-v1``).

    A timetable stop gets the location of the OSM stops with the same name words
    within 10 km of the city when they lie within 300 m of each other; otherwise
    the stops whose ``route_ref`` names the route, under the same condition;
    otherwise no location. Departures are never joined into trips.
    """

    crs = f"EPSG:{config.utm_epsg}"
    area = to_crs(layers.city, "EPSG:4326", crs).buffer(SEARCH_BUFFER_M)
    shapely.prepare(area)
    index: dict[frozenset[str], list[tuple[str, shapely.Point, set[str]]]] = {}
    for row in pq.read_table(osm_dir / "stops.parquet").to_pylist():
        tags = json.loads(row["tags"])
        utm = to_crs(shapely.from_wkb(row["wkb"]), "EPSG:4326", crs)
        key = _stop_key(tags["name"])
        if key and area.covers(utm):
            route_refs = {
                r.strip() for r in re.split(r"[;,]", tags.get("route_ref") or "") if r.strip()
            }
            index.setdefault(key, []).append((row["osm_ref"], utm, route_refs))
    routes = gortrans39.load(raw_dir() / manifest.raw_path)
    rows: list[StopFrequency] = []
    unlocated: list[str] = []
    located = 0
    empty_days: list[str] = []
    duplicates = 0
    for route in routes:
        number = re.sub(r"^\D+", "", route.route_id)
        for stop in route.stops:
            candidates = index.get(_stop_key(stop.name), [])
            chosen = candidates if candidates and _compact([c[1] for c in candidates]) else []
            if candidates and not chosen:
                served = [c for c in candidates if number in c[2]]
                chosen = served if served and _compact([c[1] for c in served]) else []
            refs = sorted(c[0] for c in chosen)
            lonlat = (
                _lonlat(shapely.MultiPoint([c[1] for c in chosen]).centroid, crs)
                if chosen
                else None
            )
            if not route.reasons:
                if chosen:
                    located += 1
                else:
                    unlocated.append(f"{route.route_id}: {stop.name}")
            record = book.add(
                Evidence(
                    evidence_id=f"{gortrans39.SOURCE_ID}:{route.route_id}:{stop.sequence}",
                    source_id=manifest.source_id,
                    dataset_id=manifest.dataset_id,
                    record_id=f"{route.route_id}/{stop.name}",
                    url=gortrans39.PAGE_URL.format(route_id=route.route_id),
                    fetched_at=manifest.retrieved_at,
                    content_hash=gortrans39.record_hash(route, stop),
                    verification_state="verified",
                )
            )
            stop_evidence = [record, *(book.osm(ref) for ref in refs)]
            for day in gortrans39.DAY_TYPES:
                value = gortrans39.frequency(stop.days.get(day, []))
                reasons = list(route.reasons)
                if value.departures is None:
                    reasons.append("EMPTY_DAY_LIST")
                    if not route.reasons and stop.sequence == 0:
                        empty_days.append(f"{route.route_id}: {day}")
                if not route.reasons:
                    duplicates += value.duplicate_times
                rows.append(
                    StopFrequency(
                        frequency_id=(
                            f"{config.city_id}-{gortrans39.SOURCE_ID}-{route.route_id}-"
                            f"{stop.sequence}-{day}"
                        ),
                        city_id=config.city_id,
                        route_id=route.route_id,
                        route_name=route.name,
                        mode=route.mode,  # type: ignore[arg-type]
                        stop_name=stop.name,
                        stop_list_position=stop.sequence,
                        day_type=day,  # type: ignore[arg-type]
                        unified_timetable=route.unified,
                        departures=value.departures,
                        first_departure=value.first,
                        last_departure=value.last,
                        max_hourly_departures=value.max_hourly,
                        median_gap_min=value.median_gap_min,
                        duplicate_times=value.duplicate_times,
                        lon=lonlat[0] if lonlat else None,
                        lat=lonlat[1] if lonlat else None,
                        osm_stop_refs=refs,
                        status="quarantined" if reasons else "accepted",
                        quarantine_reasons=reasons,
                        evidence_ids=stop_evidence,
                    )
                )
    accepted_routes = [r for r in routes if not r.reasons]
    constant = [
        r.route_id
        for r in accepted_routes
        if all(
            len({b - a for a, b in zip(lst, lst[1:])}) == 1
            for lst in r.first_lists()
            if len(lst) > 2
        )
    ]
    mismatched = [
        r.route_id
        for r in routes
        if [gortrans39.normal_name(n) for n in r.listed_stops]
        != [gortrans39.normal_name(s.name) for s in r.stops]
    ]
    _check(
        checks,
        "timetable_template_routes",
        not any(r.reasons for r in routes),
        "Every published route timetable is a schedule, not a shared template or a 30-minute grid",
        severity="warn",
        quarantined={r.route_id: r.reasons for r in routes if r.reasons},
    )
    _check(
        checks,
        "timetable_constant_headway",
        not constant,
        "Accepted routes do not run at one constant headway all day (an interval plan)",
        severity="warn",
        routes=constant,
    )
    _check(
        checks,
        "timetable_empty_day_lists",
        not empty_days,
        "Accepted routes publish departures for every day type (empty is unknown, not zero)",
        severity="warn",
        empty=empty_days,
    )
    _check(
        checks,
        "timetable_duplicate_times",
        not duplicates,
        "No departure time repeats at one stop of an accepted route (unique times are counted)",
        severity="warn",
        duplicates=duplicates,
    )
    _check(
        checks,
        "timetable_stops_located",
        not unlocated,
        "Every stop of an accepted route is found among named OSM stops",
        severity="warn",
        located=located,
        unlocated=unlocated,
    )
    _check(
        checks,
        "timetable_stop_lists_match",
        not mismatched,
        "The page's stop list equals the stops of its timetable (no overwritten names)",
        severity="warn",
        routes=mismatched,
    )
    summary = gortrans39.stats(routes) | {
        "rows": len(rows),
        "accepted_rows": sum(1 for r in rows if r.status == "accepted"),
        "accepted_stops_located": located,
        "accepted_stops_unlocated": len(unlocated),
    }
    coverage = CoverageRecord(
        collection="stop_frequencies",
        city_id=config.city_id,
        mode="public_transport",
        status="partial" if accepted_routes else "unknown",
        reason=(
            "Справка частоты по остановочным таблицам gortrans39: "
            f"{len(accepted_routes)} из {len(routes)} маршрутов АО «Калининград-ГорТранс», у "
            "остальных шаблон вместо расписания; частные перевозчики не входят; рейсы, "
            "направления и даты действия не подтверждены"
        ),
        evidence_ids=[evidence["timetables"]],
    )
    return rows, summary, coverage


SeaportInput = tuple[freight.SeaportRecord, DatasetManifest]


def _freight(
    config: CityConfig,
    osm_dir: Path,
    layers: OsmLayers,
    road: WalkNetwork,
    seaport: SeaportInput | None,
    book: EvidenceBook,
    checks: list[dict[str, Any]],
) -> tuple[list[FreightNode], list[FreightSite], list[AccessNode], dict[str, Any]]:
    """Freight nodes and physical sites inside the city, joined to the road network.

    An area is located at its representative point (always inside the contour);
    the access node is the projection of that point onto the main road component,
    a proxy for the gate, not a mapped one. A port node carries the seaport
    record and, when linked by name, its operator's record from the same page;
    an operator record from a list the page does not show is ``unknown``, not
    ``verified``. ``operator`` is that operator for ports and the OSM tag otherwise.
    """

    crs = road.crs
    city_utm = to_crs(layers.city, "EPSG:4326", crs)
    rows = pq.read_table(osm_dir / "freight.parquet").to_pylist()
    stations = [
        row
        for row in pq.read_table(osm_dir / "pois.parquet").to_pylist()
        if freight.is_rail_station(json.loads(row["tags"]))
    ]
    record = seaport[0] if seaport else None
    facts: dict[str, Any] = record.facts if record else {}
    node_drafts, site_drafts, counts = freight.classify(rows, stations, city_utm, crs, record)
    seaport_evidence: str | None = None
    operator_evidence: dict[str, str] = {}
    if seaport is not None:
        record, manifest = seaport
        seaport_evidence = book.add(
            Evidence(
                evidence_id=f"{manifest.source_id}:seaport-{config.city_id}",
                source_id=manifest.source_id,
                dataset_id=manifest.dataset_id,
                record_id=f"Морской порт {record.port_name}, рег. № {record.registration_number}",
                url=str(manifest.source_url),
                fetched_at=manifest.retrieved_at,
                content_hash=_hash_json(record.facts),
                verification_state="verified",
            )
        )
        for operator in record.operators:
            digest = hashlib.sha256(operator.name.encode("utf-8")).hexdigest()
            operator_evidence[operator.name] = (
                f"{manifest.source_id}:seaport-{config.city_id}:operator-{digest[:12]}"
            )

    def port_operator(name: str) -> str:
        assert seaport is not None
        source, manifest = seaport
        return book.add(
            Evidence(
                evidence_id=operator_evidence[name],
                source_id=manifest.source_id,
                dataset_id=manifest.dataset_id,
                record_id=name,
                url=str(manifest.source_url),
                fetched_at=manifest.retrieved_at,
                content_hash=hashlib.sha256(name.encode("utf-8")).hexdigest(),
                verification_state="verified" if source.operators_published else "unknown",
            )
        )

    geometries = [d.geometry for d in node_drafts] + [d.geometry for d in site_drafts]
    points = [to_crs(g, "EPSG:4326", crs).representative_point() for g in geometries]
    accesses = snap(points, road)
    road_nodes = _access_nodes(accesses, road, crs, book)
    split = len(node_drafts)
    nodes: list[FreightNode] = []
    for draft, point, access in zip(node_drafts, points[:split], accesses[:split]):
        evidence_ids = [book.osm(ref) for ref in draft.osm_refs]
        if seaport_evidence is not None and draft.kind == "port":
            if draft.operator:
                evidence_ids.append(port_operator(draft.operator))
            evidence_ids.append(seaport_evidence)
        # the seaport record names the CPC offshore berths: support for CPC terminals
        cpc = bool(draft.name and freight.CPC_NAME.search(draft.name))
        if seaport_evidence and draft.kind == "pipeline_terminal" and cpc:
            if facts.get("mentions_ktk_berths"):
                evidence_ids.append(seaport_evidence)
        reasons = list(draft.reasons)
        if draft.name is None:
            reasons.append("UNNAMED")
        lon, lat = _lonlat(point, crs)
        nodes.append(
            FreightNode(
                freight_node_id=f"{config.city_id}-{draft.kind}-{draft.osm_refs[0].replace('/', '-')}",
                kind=draft.kind,  # type: ignore[arg-type]
                city_id=config.city_id,
                name=draft.name,
                operator=draft.operator,
                lon=lon,
                lat=lat,
                osm_refs=draft.osm_refs,
                rule=f"{freight.RULES_VERSION}:{draft.rule}",
                access_node_id=access.node_id,
                access_distance_m=round(access.distance_m, 2),
                status="quarantined" if reasons else "accepted",
                quarantine_reasons=reasons,
                evidence_ids=evidence_ids,
            )
        )
    sites: list[FreightSite] = []
    for site, point, access in zip(site_drafts, points[split:], accesses[split:]):
        lon, lat = _lonlat(point, crs)
        sites.append(
            FreightSite(
                site_id=f"{config.city_id}-site-{site.osm_ref.replace('/', '-')}",
                kind=site.kind,  # type: ignore[arg-type]
                city_id=config.city_id,
                name=site.name,
                lon=lon,
                lat=lat,
                area_m2=round(site.area_m2, 1),
                osm_ref=site.osm_ref,
                access_node_id=access.node_id,
                access_distance_m=round(access.distance_m, 2),
                evidence_ids=[book.osm(site.osm_ref)],
            )
        )
    nodes.sort(key=lambda node: node.freight_node_id)
    sites.sort(key=lambda site: site.site_id)
    accepted = [node for node in nodes if node.status == "accepted"]
    _check(
        checks,
        "road_network_main_component",
        road.stats["main_component_length_share"] >= 0.9,
        "Largest connected road component holds at least 90% of drivable length",
        share=road.stats["main_component_length_share"],
    )
    far = [
        {"id": row_id, "name": name, "distance_m": distance}
        for row_id, name, distance in [
            *((n.freight_node_id, n.name, n.access_distance_m) for n in accepted),
            *((s.site_id, s.name, s.access_distance_m) for s in sites),
        ]
        if distance > ACCESS_WARN_M
    ]
    _check(
        checks,
        "freight_access_distance",
        not far,
        f"Accepted freight nodes and sites join the road network within {ACCESS_WARN_M:.0f} m "
        "of their inner point",
        severity="warn",
        over_limit=far,
    )
    linked = sorted({n.operator for n in nodes if n.kind == "port" and n.operator})
    if record is not None:
        _check(
            checks,
            "seaport_operator_list_published",
            record.operators_published,
            "The seaport page shows its stevedore list (not only inside an HTML comment)",
            severity="warn",
            operators=len(record.operators),
        )
        _check(
            checks,
            "seaport_operators_located",
            len(linked) == len(record.operators),
            "Every stevedoring operator of the seaport record has a named OSM object in the city",
            severity="warn",
            unlinked=[op.name for op in record.operators if op.name not in linked],
        )
    summary = {
        "rules": freight.RULES_VERSION,
        "road_network": road.stats,
        "rule_counts": counts,
        "nodes": {
            kind: {
                status: sum(1 for n in nodes if n.kind == kind and n.status == status)
                for status in ("accepted", "quarantined")
            }
            for kind in get_args(FreightKind)
        },
        "sites": {
            "industrial": sum(1 for s in sites if s.kind == "industrial"),
            "warehouse": sum(1 for s in sites if s.kind == "warehouse"),
            "area_ha": round(sum(s.area_m2 for s in sites) / 1e4, 1),
        },
        "access_distance_m": {
            "nodes": _distances([n.access_distance_m for n in accepted]) if accepted else None,
            "sites": _distances([s.access_distance_m for s in sites]) if sites else None,
        },
        "seaport": (
            {
                "record": facts,
                "operators": len(record.operators),
                "operators_published": record.operators_published,
                "linked_operators": linked,
            }
            if record is not None
            else None
        ),
    }
    return nodes, sites, road_nodes, summary


def _freight_coverage(
    config: CityConfig,
    nodes: list[FreightNode],
    sites: list[FreightSite],
    record: freight.SeaportRecord | None,
    evidence: dict[str, str],
) -> list[CoverageRecord]:
    """Freight coverage by node kind: an index for a kind without accepted nodes is unknown."""

    city = config.city_id
    start, end = ANALYSIS_WINDOW
    osm = [evidence["osm"]]
    port_refs = osm + ([evidence["seaport"]] if "seaport" in evidence else [])

    def found(kind: str) -> bool:
        return any(n.kind == kind and n.status == "accepted" for n in nodes)

    linked = len({n.operator for n in nodes if n.kind == "port" and n.operator})
    if record is None:
        port_reason = "Портовые территории OSM без записи о морском порте: в карантине"
    else:
        listed = (
            "перечень операторов опубликован"
            if record.operators_published
            else "перечень операторов на странице скрыт в разметке, объекты только по нему в "
            "карантине"
        )
        port_reason = (
            "Портовые территории OSM и объекты со словом «порт» в названии в границе города; "
            f"морской порт {record.port_name} (рег. № {record.registration_number}) по "
            f"Росморпорту; {listed}; подписанные объекты в OSM есть у {linked} из "
            f"{len(record.operators)} операторов"
        )
    pipelines = found("pipeline_terminal")
    kinds: dict[FreightKind, tuple[CoverageStatus, str, list[str]]] = {
        "port": ("partial" if found("port") else "unknown", port_reason, port_refs),
        "pipeline_terminal": (
            "partial" if pipelines else "unknown",
            "Терминалы магистральных трубопроводов по названиям OSM (КТК, Шесхарис, ПНБ); "
            "реестра операторов нет"
            if pipelines
            else "Терминалы магистральных трубопроводов по правилам OSM не найдены; отсутствие "
            "не подтверждено",
            osm,
        ),
        "rail_terminal": (
            "unknown",
            "Ж/д станции OSM в карантине: грузовые операции не подтверждены открытым реестром",
            osm,
        ),
        "logistics_terminal": (
            "not_collected",
            "Логистические центры не собраны; пустой список не означает отсутствие",
            [],
        ),
        "airport": (
            "not_collected",
            "Грузовые аэропорты не собраны; пустой список не означает отсутствие",
            [],
        ),
    }
    records = [
        CoverageRecord(
            collection="access_nodes",
            city_id=city,
            mode="road",
            status="complete",
            reason="Вход на главную связную компоненту дорожной сети OSM v1 (грузовой подъезд) "
            "у каждого грузового узла и площадки",
            evidence_ids=osm,
            valid_from=start,
            valid_to=end,
        )
    ]
    for kind, (status, reason, refs) in kinds.items():
        records.append(
            CoverageRecord(
                collection="freight_nodes",
                city_id=city,
                freight_kind=kind,
                status=status,
                reason=reason,
                evidence_ids=refs,
                valid_from=start,
                valid_to=end,
            )
        )
    records += [
        CoverageRecord(
            collection="freight_sites",
            city_id=city,
            status="partial" if sites else "unknown",
            reason="Промзоны OSM от 0,5 га, склады и производственные здания от 1000 м² вне "
            f"промзон и узлов ({freight.RULES_VERSION}); реестра предприятий нет, веса равные",
            evidence_ids=osm,
            valid_from=start,
            valid_to=end,
        ),
        CoverageRecord(
            collection="freight_compatibility",
            city_id=city,
            status="unknown",
            reason="Совместимость груза, услуги и подъезда не подтверждена открытыми данными "
            "операторов; строки не созданы",
            evidence_ids=port_refs,
        ),
        CoverageRecord(
            collection="pipeline_connections",
            city_id=city,
            status="not_collected",
            reason="Только межгород; соединения и направления не собраны, терминалы города — "
            "в freight_nodes",
        ),
    ]
    return records


def _coverage(
    config: CityConfig,
    destinations: list[Destination],
    evidence: dict[str, str],
    registry_coverage: dict[str, tuple[str, str]],
    district_count: int,
    freight_records: Sequence[CoverageRecord] = (),
) -> list[CoverageRecord]:
    """Coverage records. ``registry_coverage`` maps a category confirmed by a register
    to (reason, evidence key); other public categories stay unknown. Freight
    records replace the ``not_collected`` rows of their collections."""

    city = config.city_id
    start, end = ANALYSIS_WINDOW
    osm = [evidence["osm"]]
    records = [
        CoverageRecord(
            collection="territories",
            city_id=city,
            status="complete",
            reason=f"Городской округ и {district_count} района(ов) из OSM",
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
    for category in PRODUCT_CATEGORIES:
        accepted = sum(1 for d in destinations if d.category == category and d.status == "accepted")
        extra: list[str] = []
        if category in registry_coverage:
            reason, key = registry_coverage[category]
            status = "partial"
            extra = [evidence[key]]
        elif category in OWNERSHIP_REQUIRED:
            status = "unknown"
            reason = "Кандидаты OSM в карантине: форма собственности не подтверждена реестром"
        elif category == "airport":
            if config.airport_service_unverified:
                status = "partial" if accepted else "unknown"
                reason = (
                    "Аэродромы с IATA в 60 км по OurAirports; регулярность рейсов не проверена "
                    "по расписаниям (ограничения полётов на юге России)"
                )
            else:
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
    confirmed = sorted(registry_coverage)
    records.append(
        CoverageRecord(
            collection="category_ownership",
            city_id=city,
            status="partial" if confirmed else "unknown",
            reason=(
                "Подтверждены реестрами: " + ", ".join(confirmed)
                if confirmed
                else "Официальный реестр учреждений не загружен"
            ),
            evidence_ids=sorted({evidence[key] for _, key in registry_coverage.values()}),
        )
    )
    records.extend(freight_records)
    covered = {record.collection for record in freight_records}
    for collection in NOT_COLLECTED:
        if collection in covered:
            continue
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
