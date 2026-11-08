"""Adapter from an ML2 bundle to BACKEND's canonical ``a2_contracts.v2.AcceptedData``.

The canonical contract is owned by BACKEND (PR #8, ``work/backend``). This module
only maps fields and never relaxes a rule:

- quarantined destinations are left out: v2 cannot hold a public category
  without verified registry evidence, and a quarantined object is not usable;
- districts are not v2 territories (``TerritoryLevel`` is city|region); they are
  exported separately as ``AreaOption`` rows for the product registry;
- a destination category without a coverage scope stays uncovered (unknown);
- ``calendar_version`` is ``not_collected`` while the bundle has no schedules.

``to_accepted_data_v2`` returns plain JSON data; ``load_accepted_data`` validates
it with ``a2_contracts.v2`` when that package is importable.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any

from .bundle import BundleData, CoverageRecord
from .store import load_bundle

CALENDAR_NOT_COLLECTED = "not_collected"
COVERAGE_MODES = ["walk"]
OWNERSHIP_V2 = {
    "municipal": "municipal",
    "federal": "government",
    "regional": "government",
    "state": "government",
    "private": "private",
    "unknown": "unknown",
}


def _lonlat(lon: float, lat: float) -> dict[str, float]:
    return {"lon": lon, "lat": lat}


def _coordinates(node: Any) -> Iterator[tuple[float, float]]:
    if isinstance(node, list | tuple) and node and isinstance(node[0], int | float):
        yield float(node[0]), float(node[1])
        return
    for child in node:
        yield from _coordinates(child)


def _bbox(geometry: dict[str, Any]) -> dict[str, float]:
    points = list(_coordinates(geometry["coordinates"]))
    return {
        "west": min(x for x, _ in points),
        "south": min(y for _, y in points),
        "east": max(x for x, _ in points),
        "north": max(y for _, y in points),
    }


def _period(start: Any, end: Any) -> dict[str, Any]:
    return {"start": start.isoformat() if start else None, "end": end.isoformat() if end else None}


def to_accepted_data_v2(data: BundleData) -> dict[str, Any]:
    manifest = data.manifest
    if manifest.acceptance_state != "accepted" or manifest.accepted_at is None:
        raise ValueError("only an accepted bundle maps to AcceptedData")
    if manifest.graph_version is None:
        raise ValueError("bundle without a graph version has no access nodes")
    licenses = {s.source_id: s.license.name or "unknown" for s in manifest.source_versions}
    versions = {s.source_id: s.dataset_id for s in manifest.source_versions}
    city_ids = set(manifest.geographic_scope)
    cities = [t for t in data.territories if t.level == "city" and t.territory_id in city_ids]
    city_refs = {t.territory_id: t.evidence_ids for t in cities}
    grids = {grid.grid_id: grid for grid in manifest.grids}
    population_versions = sorted(
        {
            f"{c.population_method}:{c.population_year}"
            for c in data.population_cells
            if c.population_method
        }
    )

    evidence = [
        {
            "evidence_id": item.evidence_id,
            "source_id": item.source_id,
            "source_version": versions[item.source_id],
            "url": item.url,
            "retrieved_at": item.fetched_at.isoformat(),
            "valid_period": _period(None, None),
            "license": licenses[item.source_id],
            "content_hash": item.content_hash,
            "verified": item.verification_state == "verified",
        }
        for item in data.evidence
    ]
    territories = [
        {
            "territory_id": t.territory_id,
            "level": "city",
            "name": t.name,
            "timezone": t.timezone,
            "geometry": {
                "geometry_id": f"{t.territory_id}:boundary",
                "version": t.boundary_version,
                "bbox": _bbox(t.geometry),
                "source_refs": t.evidence_ids,
            },
            "area_km2": t.area_km2,
            "source_refs": t.evidence_ids,
        }
        for t in cities
    ]
    population_cells = []
    for cell in data.population_cells:
        grid = grids[cell.grid_id]
        west, south, east, north = cell.square_bbox
        population_cells.append(
            {
                "cell_id": cell.cell_id,
                "territory_id": cell.city_id,
                "grid_version": cell.grid_id,
                "grid_origin_m": [grid.origin_x, grid.origin_y],
                "projection": grid.crs,
                "geometry": {
                    "geometry_id": cell.cell_id,
                    "version": cell.grid_id,
                    "bbox": {"west": west, "south": south, "east": east, "north": north},
                    "source_refs": city_refs[cell.city_id],
                },
                "population": cell.population,
                "population_year": cell.population_year,
                "population_method": cell.population_method,
                "unit": "persons",
                "sample_status": "included" if cell.status == "sampled" else "excluded",
                "exclusion_reason": cell.exclusion_reason,
                "source_refs": [*cell.evidence_ids, *city_refs[cell.city_id]],
            }
        )
    cell_city = {cell.cell_id: cell.city_id for cell in data.population_cells}
    sample_points = [
        {
            "point_id": p.point_id,
            "cell_id": p.cell_id,
            "building_id": p.building_ref,
            "initial_center": _lonlat(p.initial_center_lon, p.initial_center_lat),
            "access_node_id": p.access_node_id,
            "access_distance_m": p.access_distance_m,
            "territory_id": cell_city[p.cell_id],
            "location": _lonlat(p.lon, p.lat),
            "source_refs": p.evidence_ids,
        }
        for p in data.sample_points
    ]
    usable = [d for d in data.destinations if d.status == "accepted"]
    destinations = [
        {
            "destination_id": d.destination_id,
            "categories": [d.category],
            "access_node_id": d.access_node_id,
            "object_identity": f"osm:{d.osm_ref}" if d.osm_ref else d.destination_id,
            "territory_id": d.serving_city_id,
            "location": _lonlat(d.lon, d.lat),
            "source_refs": d.evidence_ids,
        }
        for d in usable
    ]
    registry_evidence, category_ownership = _ownership(data, {d.destination_id for d in usable})
    used_nodes = {p.access_node_id for p in data.sample_points} | {d.access_node_id for d in usable}
    node_city = {p.access_node_id: cell_city[p.cell_id] for p in data.sample_points}
    node_city.update({d.access_node_id: d.serving_city_id for d in usable})
    access_nodes = [
        {
            "node_id": node.access_node_id,
            "kind": node.kind,
            "territory_id": node_city[node.access_node_id],
            "location": _lonlat(node.lon, node.lat),
            "source_refs": node.evidence_ids,
        }
        for node in data.access_nodes
        if node.access_node_id in used_nodes
    ]
    return {
        "schema_version": "2",
        "snapshot_id": manifest.bundle_id,
        "source_versions": versions,
        "geographic_scope": manifest.geographic_scope,
        "valid_period": _period(manifest.valid_from, manifest.valid_to),
        "timezone": manifest.timezone,
        "provenance": {
            "graph_version": manifest.graph_version,
            "geometry_version": "+".join([*(t.boundary_version for t in cities), *sorted(grids)]),
            "calendar_version": CALENDAR_NOT_COLLECTED,
            "population_version": "+".join(population_versions) or None,
        },
        "qa": {
            "acceptance_state": "accepted",
            "accepted_by": manifest.accepted_by,
            "accepted_at": manifest.accepted_at.isoformat(),
            "checks": [
                check["check_id"]
                for check in data.qa_report.get("checks", [])
                if check.get("status") in {"pass", "warn"}
            ],
        },
        "evidence": evidence,
        "registry_evidence": registry_evidence,
        "territories": territories,
        "population_cells": population_cells,
        "sample_points": sample_points,
        "destinations": destinations,
        "access_nodes": access_nodes,
        "scheduled_services": [],
        "transfers": [],
        "freight_sites": [],
        "freight_nodes": [],
        "freight_compatibility": [],
        "pipeline_connections": [],
        "reference_edges": [],
        "minimum_transfers": [],
        "traffic_profiles": [],
        "category_ownership": category_ownership,
        "coverage_by_mode_date_category": _coverage_scopes(manifest.coverage),
        "confirmed_absence_scope": [],
    }


def _ownership(
    data: BundleData, usable: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Registry-verified public ownership of usable destinations, in v2 form."""

    source_of = {item.evidence_id: item.source_id for item in data.evidence}
    registry_evidence: list[dict[str, Any]] = []
    category_ownership: list[dict[str, Any]] = []
    for row in data.category_ownership:
        if row.destination_id not in usable or row.basis != "registry":
            continue
        if row.verification_state != "verified" or not row.evidence_ids:
            continue
        registry_evidence_id = f"registry:{row.destination_id}:{row.category}"
        ownership = OWNERSHIP_V2[row.ownership]
        registry_evidence.append(
            {
                "registry_evidence_id": registry_evidence_id,
                "registry_id": source_of[row.evidence_ids[0]],
                "destination_id": row.destination_id,
                "category": row.category,
                "ownership": ownership,
                "verified": True,
                "source_refs": row.evidence_ids,
            }
        )
        category_ownership.append(
            {
                "destination_id": row.destination_id,
                "category": row.category,
                "ownership": ownership,
                "registry_evidence_ids": [registry_evidence_id],
                "source_refs": row.evidence_ids,
            }
        )
    return registry_evidence, category_ownership


def _coverage_scopes(records: list[CoverageRecord]) -> list[dict[str, Any]]:
    """One scope per (city, completeness, period) over the covered destination categories."""

    groups: dict[tuple[str, str, Any, Any], dict[str, Any]] = {}
    for record in records:
        if record.collection != "destinations" or record.category is None:
            continue
        if record.status not in {"complete", "partial"} or record.city_id is None:
            continue
        completeness = "full" if record.status == "complete" else "partial"
        key = (record.city_id, completeness, record.valid_from, record.valid_to)
        scope = groups.setdefault(
            key,
            {
                "territory_ids": [record.city_id],
                "modes": COVERAGE_MODES,
                "categories": [],
                "valid_period": _period(record.valid_from, record.valid_to),
                "completeness": completeness,
                "source_refs": [],
            },
        )
        scope["categories"].append(record.category)
        for evidence_id in record.evidence_ids:
            if evidence_id not in scope["source_refs"]:
                scope["source_refs"].append(evidence_id)
    return [groups[key] for key in sorted(groups, key=str)]


def registry_options(data: BundleData) -> dict[str, list[dict[str, Any]]]:
    """TerritoryOption and AreaOption rows for BACKEND's ProductRegistry."""

    territories = [
        {
            "territory_id": t.territory_id,
            "level": "city",
            "name": t.name,
            "timezone": t.timezone,
            "parent_region_id": None,
            "capital_city_id": None,
        }
        for t in data.territories
        if t.level == "city"
    ]
    areas = [
        {"area_id": t.territory_id, "city_id": t.parent_id, "name": t.name}
        for t in data.territories
        if t.level == "district"
    ]
    return {"territories": territories, "areas": areas}


def load_accepted_data(bundle_id: str) -> Any:
    """``load_bundle`` + mapping + validation by BACKEND's ``a2_contracts.v2.AcceptedData``.

    Raises ``ModuleNotFoundError`` while the canonical contracts are not installed.
    """

    contracts = importlib.import_module("a2_contracts.v2")
    return contracts.AcceptedData.model_validate(to_accepted_data_v2(load_bundle(bundle_id)))
