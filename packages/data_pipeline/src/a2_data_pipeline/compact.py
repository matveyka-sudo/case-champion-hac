"""Deterministic selections from accepted Waydex snapshots."""

from collections.abc import Iterable
from math import cos, hypot, radians
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData

from .gtfs_inputs import _records

# Moscow anchor is the corridor of the two lines verified in the November handoff.
# The other two anchors are the city-centre areas of their accepted networks.
ZONES = {
    "moscow": (37.727, 55.606),
    "kaliningrad": (20.510, 54.711),
    "novorossiysk": (37.772, 44.724),
}


def distance(location: Any, centre: tuple[float, float]) -> float:
    """Local distance in metres for selection only."""
    lon, lat = centre
    return 111_195 * hypot((location.lon - lon) * cos(radians(lat)), location.lat - lat)


def nearest(rows: Iterable[Any], identity: str, city: str, count: int) -> list[Any]:
    return sorted(
        rows, key=lambda row: (distance(row.location, ZONES[city]), getattr(row, identity))
    )[:count]


def passenger_selection(
    parent: AcceptedData,
    city: str,
    traversable: set[str],
    categories: Iterable[str],
) -> tuple[list[str], dict[str, list[str]]]:
    # Stratify by proximity to the chosen corridor/centre. Keep ten actual cells
    # and a spread of inner/outer access distances instead of ten adjacent points.
    local = nearest(
        (p for p in parent.sample_points if "physical:point:" + p.point_id in traversable),
        "point_id",
        city,
        24,
    )
    if len(local) < 10:
        raise ValueError(f"{city}: ten points with walking connectors are required")
    points = [local[i * (len(local) - 1) // 9].point_id for i in range(10)]
    destinations = {
        category: [
            d.destination_id
            for d in nearest(
                (d for d in parent.destinations if category in d.categories),
                "destination_id",
                city,
                5,
            )
        ]
        for category in sorted(categories)
    }
    return points, destinations


def compact_snapshot(
    parent: AcceptedData,
    *,
    point_ids: Iterable[str] = (),
    destination_ids: dict[str, list[str]] | None = None,
    site_ids: Iterable[str] = (),
    freight_node_ids: Iterable[str] = (),
) -> AcceptedData:
    point_set, site_set, freight_node_set = set(point_ids), set(site_ids), set(freight_node_ids)
    by_category = {key: set(value) for key, value in (destination_ids or {}).items()}
    doc = parent.model_dump(mode="json")
    doc["sample_points"] = [row for row in doc["sample_points"] if row["point_id"] in point_set]
    cell_ids = {row["cell_id"] for row in doc["sample_points"]}
    doc["population_cells"] = [row for row in doc["population_cells"] if row["cell_id"] in cell_ids]
    doc["destinations"] = [
        {**row, "categories": categories}
        for row in doc["destinations"]
        if (
            categories := [
                c for c in row["categories"] if row["destination_id"] in by_category.get(c, set())
            ]
        )
    ]
    ownership = {
        (row["destination_id"], c) for row in doc["destinations"] for c in row["categories"]
    }
    doc["category_ownership"] = [
        row
        for row in doc["category_ownership"]
        if (row["destination_id"], row["category"]) in ownership
    ]
    doc["registry_evidence"] = [
        row
        for row in doc["registry_evidence"]
        if (row["destination_id"], row["category"]) in ownership
    ]
    doc["freight_sites"] = [row for row in doc["freight_sites"] if row["site_id"] in site_set]
    doc["freight_nodes"] = [
        row for row in doc["freight_nodes"] if row["freight_node_id"] in freight_node_set
    ]
    access_nodes = {
        row["access_node_id"]
        for name in ("sample_points", "destinations", "freight_sites", "freight_nodes")
        for row in doc[name]
    } | {
        event["node_id"] for service in doc["scheduled_services"] for event in service["stop_times"]
    }
    doc["access_nodes"] = [row for row in doc["access_nodes"] if row["node_id"] in access_nodes]
    for name in (
        "confirmed_absence_scope",
        "coverage_by_mode_date_category",
        "freight_compatibility",
        "pipeline_connections",
        "reference_edges",
        "minimum_transfers",
        "traffic_profiles",
        "transfers",
    ):
        doc[name] = []
    evidence_ids: set[str] = set()
    for value in doc.values():
        if isinstance(value, list):
            for row in value:
                if isinstance(row, dict):
                    evidence_ids.update(row.get("source_refs", []))
                    evidence_ids.update((row.get("geometry") or {}).get("source_refs", []))
    doc["evidence"] = [e for e in doc["evidence"] if e["evidence_id"] in evidence_ids]
    doc["qa"]["checks"].append("bounded_selected_original_records")
    digest = canonical_hash(doc)
    doc["snapshot_id"] = f"{parent.snapshot_id}:compact:{digest[:16]}"
    return AcceptedData.model_validate(doc)


def gtfs_lineage(archive_path: Path, route_ids: tuple[str, ...]) -> dict[str, Any]:
    """Retain source route, direction and service-calendar metadata for the slice."""
    with ZipFile(archive_path) as archive:
        trips = [row for row in _records(archive, "trips.txt") if row["route_id"] in route_ids]
        services = {row["service_id"] for row in trips}
        return {
            "routes": [
                row for row in _records(archive, "routes.txt") if row["route_id"] in route_ids
            ],
            "trips": trips,
            "calendar": [
                row for row in _records(archive, "calendar.txt") if row["service_id"] in services
            ],
            "calendar_dates": [
                row
                for row in _records(archive, "calendar_dates.txt")
                if row["service_id"] in services
            ],
        }
