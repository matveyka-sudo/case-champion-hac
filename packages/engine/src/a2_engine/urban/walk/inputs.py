"""walk / inputs — canonical urban implementation."""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from math import fsum
from typing import Any
from zoneinfo import ZoneInfo

from a2_engine.shared.results import finite_number
from a2_engine.urban.models import (
    UrbanArea,
    UrbanContext,
    UrbanDestination,
    UrbanPoint,
    UrbanPopulationCoverage,
    UrbanVersions,
    references,
    utc_timestamp,
)
from a2_engine.urban.valhalla.codec import digest
from a2_engine.urban.valhalla.models import (
    ValhallaBuild,
    ValhallaCorrelation,
    WalkConnector,
    WalkCoordinate,
)
from a2_engine.urban.walk.models import WalkCatalog, WalkEndpoint, WalkInputs


def _coordinate(row: Mapping[str, Any]) -> WalkCoordinate:
    return WalkCoordinate(row["lon"], row["lat"])


def _table(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, Mapping[str, Any]]:
    table = {row[key]: row for row in rows}
    if len(table) != len(rows):
        raise ValueError("DUPLICATE_CANONICAL_ID:" + key)
    return table


def _active(period: Mapping[str, Any], on: date) -> bool:
    return (period["start"] is None or date.fromisoformat(period["start"]) <= on) and (
        period["end"] is None or on <= date.fromisoformat(period["end"])
    )


def prepare_walk_inputs(
    accepted: Mapping[str, Any],
    *,
    city_id: str,
    departure_at: datetime,
    methodology_version: str,
    config_hash: str,
    search_parameters_id: str,
    build: ValhallaBuild | None = None,
    connectors: Sequence[WalkConnector] = (),
    correlations: Sequence[ValhallaCorrelation] = (),
) -> WalkInputs:
    """Consume model_dump(mode='json') of BACKEND-validated AcceptedData v2.

    ML2 owns bundle conversion. This projection checks engine invariants and never
    re-implements the canonical wire validator or infers connector passability.
    """
    if accepted["schema_version"] != "2" or accepted["qa"]["acceptance_state"] != "accepted":
        raise ValueError("ACCEPTED_DATA_V2_REQUIRED")
    territories = _table(accepted["territories"], "territory_id")
    city = territories[city_id]
    departure = utc_timestamp(departure_at)
    local_day = departure.astimezone(ZoneInfo(city["timezone"])).date()
    if not _active(accepted["valid_period"], local_day):
        raise ValueError("SNAPSHOT_OUTSIDE_VALID_PERIOD")
    provenance = accepted["provenance"]
    snapshot = accepted["snapshot_id"]
    source_graph = provenance["graph_version"]
    if build is not None and (
        build.snapshot_id != snapshot or build.source_graph_version != source_graph
    ):
        raise ValueError("BUILD_SOURCE_MISMATCH")
    versions = UrbanVersions(
        snapshot,
        build.graph_version if build else source_graph,
        provenance["calendar_version"],
        provenance["population_version"],
        provenance["geometry_version"],
        methodology_version,
        config_hash,
        "static:" + (build.build_id if build else "unbound"),
    )
    context = UrbanContext(city_id, city_id, departure, "static", versions, search_parameters_id)
    cells = _table(accepted["population_cells"], "cell_id")
    nodes = _table(accepted["access_nodes"], "node_id")
    _table(accepted["sample_points"], "point_id")
    _table(accepted["destinations"], "destination_id")
    connector_map = {c.physical_node_id: c for c in connectors}
    correlation_map = {c.access_node_id: c for c in correlations}
    if len(connector_map) != len(connectors) or len(correlation_map) != len(correlations):
        raise ValueError("DUPLICATE_CONNECTOR_OR_CORRELATION")
    used_connectors: set[str] = set()
    used_correlations: set[str] = set()

    def endpoint(row: Mapping[str, Any], entity_id: str, kind: str) -> WalkEndpoint:
        physical = f"physical:{kind}:{entity_id}"
        access_id = row["access_node_id"]
        node = nodes[access_id]
        if node["territory_id"] != city_id:
            raise ValueError("ACCESS_TERRITORY_MISMATCH")
        location, access = _coordinate(row["location"]), _coordinate(node["location"])
        connector = connector_map.get(physical)
        if connector is not None:
            used_connectors.add(physical)
            if (
                connector.snapshot_id != snapshot
                or connector.source_graph_version != source_graph
                or connector.access_node_id != access_id
                or connector.physical_location != location
                or connector.access_location != access
            ):
                raise ValueError("CONNECTOR_SOURCE_MISMATCH")
        correlation = correlation_map.get(access_id)
        if correlation is not None:
            used_correlations.add(access_id)
            if build is None or correlation.build != build or correlation.source_location != access:
                raise ValueError("CORRELATION_SOURCE_MISMATCH")
        return WalkEndpoint(
            entity_id,
            physical,
            access_id,
            location,
            access,
            connector,
            correlation,
            references(tuple(row["source_refs"]) + tuple(node["source_refs"])),
        )

    points, point_endpoints = [], []
    used_cells: set[str] = set()
    for row in sorted(accepted["sample_points"], key=lambda row: row["point_id"]):
        if row["territory_id"] != city_id:
            continue
        cell = cells[row["cell_id"]]
        if (
            cell["cell_id"] in used_cells
            or cell["territory_id"] != city_id
            or cell["sample_status"] != "included"
        ):
            raise ValueError("INVALID_POINT_POPULATION_BASIS")
        used_cells.add(cell["cell_id"])
        ep = endpoint(row, row["point_id"], "point")
        point_endpoints.append(ep)
        points.append(
            UrbanPoint(
                row["point_id"],
                cell["cell_id"],
                ep.physical_node_id,
                ep.access_node_id,
                row["access_distance_m"],
                city_id,
                cell["population"],
                cell["population_year"],
                cell["population_method"],
                references(tuple(row["source_refs"]) + tuple(cell["source_refs"])),
            )
        )
    destinations, destination_endpoints = [], []
    for row in sorted(accepted["destinations"], key=lambda row: row["destination_id"]):
        if row["territory_id"] != city_id:
            continue
        ep = endpoint(row, row["destination_id"], "destination")
        destination_endpoints.append(ep)
        # Public institutional ownership is validated by AcceptedData, not guessed here.
        destinations.append(
            UrbanDestination(
                row["destination_id"],
                row["object_identity"],
                frozenset(row["categories"]),
                ep.access_node_id,
                city_id,
                True,
                True,
                tuple(row["source_refs"]),
                physical_node_id=ep.physical_node_id,
            )
        )
    if set(connector_map) != used_connectors or set(correlation_map) != used_correlations:
        raise ValueError("UNKNOWN_SUPPLEMENT_ENDPOINT")
    relevant_cells = [cell for cell in cells.values() if cell["territory_id"] == city_id]
    missing = [cell for cell in relevant_cells if cell["cell_id"] not in used_cells]
    excluded = (
        None
        if any(cell["population"] is None for cell in missing)
        else fsum(finite_number(cell["population"], "excluded_population") for cell in missing)
    )
    evidence = references(tuple(ref for cell in relevant_cells for ref in cell["source_refs"]))
    population = UrbanPopulationCoverage(
        context,
        all(cell["population"] is not None for cell in relevant_cells)
        and used_cells
        == {cell["cell_id"] for cell in relevant_cells if cell["sample_status"] == "included"},
        excluded,
        evidence,
        frozenset(p.point_id for p in points),
    )
    catalogs = []
    for category in sorted({c for d in destinations for c in d.categories}):
        scopes = [
            s
            for s in accepted["coverage_by_mode_date_category"]
            if city_id in s["territory_ids"]
            and "walk" in s["modes"]
            and category in s["categories"]
            and _active(s["valid_period"], local_day)
        ]
        catalogs.append(
            WalkCatalog(
                category,
                any(s["completeness"] == "full" for s in scopes),
                references(tuple(ref for s in scopes for ref in s["source_refs"])),
            )
        )
    limitations = set()
    if build is None:
        limitations.add("VALHALLA_BUILD_UNAVAILABLE")
    for ep in (*point_endpoints, *destination_endpoints):
        if ep.connector is None or not ep.connector.confirmed:
            limitations.add("CONNECTOR_UNCONFIRMED")
        if ep.correlation is None:
            limitations.add("VALHALLA_CORRELATION_UNAVAILABLE")
    if excluded != 0:
        limitations.add("INCOMPLETE_POPULATION_COVERAGE")
    return WalkInputs(
        digest(accepted),
        source_graph,
        context,
        build,
        tuple(points),
        tuple(destinations),
        tuple(point_endpoints),
        tuple(destination_endpoints),
        tuple(catalogs),
        UrbanArea(city_id, city["area_km2"], versions.geometry_version, tuple(city["source_refs"])),
        population,
        tuple(sorted(limitations)),
    )
