"""Accepted data bundle, schema version 2 (packages/contracts/specs/dataset-snapshot.md).

This is ML2's producer-side model. It is a proposal for BACKEND's canonical
``AcceptedData`` v2; BACKEND owns the canonical contract. Rules enforced here:
unknown stays ``None`` (never 0), confirmed absence needs an explicit scope,
every reference resolves, and an empty collection without a coverage record
means "not collected", not "no objects".
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .manifest import Checksum, Identifier, License

SCHEMA_VERSION = 2
BUNDLE_FORMAT = "a2-ml2-bundle"

PRODUCT_CATEGORIES = (
    "adult_polyclinic",
    "child_polyclinic",
    "kindergarten",
    "school",
    "college_university",
    "rail_terminal",
    "bus_terminal",
    "airport",
    "shopping_center",
)
# Categories whose ownership must be confirmed by an official registry (PRODUCT_SPEC §2).
OWNERSHIP_REQUIRED = frozenset(
    {"adult_polyclinic", "child_polyclinic", "kindergarten", "school", "college_university"}
)

Lon = Annotated[float, Field(ge=-180, le=180)]
Lat = Annotated[float, Field(ge=-90, le=90)]
NonNegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Category = Literal[
    "adult_polyclinic",
    "child_polyclinic",
    "kindergarten",
    "school",
    "college_university",
    "rail_terminal",
    "bus_terminal",
    "airport",
    "shopping_center",
]
FreightKind = Literal["port", "rail_terminal", "logistics_terminal", "airport", "pipeline_terminal"]
VerificationState = Literal["verified", "absent", "unknown"]
AcceptanceState = Literal["draft", "validated", "accepted", "quarantined"]
CoverageStatus = Literal["complete", "partial", "unknown", "not_collected"]
Collection = Literal[
    "territories",
    "population_cells",
    "sample_points",
    "destinations",
    "category_ownership",
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
    "stop_frequencies",
    "walk_connectors",
    "transit_stops",
]
PRODUCED_COLLECTIONS: tuple[Collection, ...] = (
    "territories",
    "population_cells",
    "sample_points",
    "destinations",
    "category_ownership",
    "access_nodes",
    "freight_nodes",
    "freight_sites",
    "stop_frequencies",
    "walk_connectors",
    "transit_stops",
)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


GeoJSON = dict[str, Any]


class Evidence(Model):
    evidence_id: Identifier
    source_id: Identifier
    dataset_id: Identifier | None
    record_id: str
    url: str | None
    fetched_at: datetime
    content_hash: str | None
    verification_state: VerificationState
    valid_from: date | None = None
    valid_to: date | None = None

    @model_validator(mode="after")
    def ordered_validity(self) -> Self:
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("evidence valid_to precedes valid_from")
        return self


class SourceVersion(Model):
    source_id: Identifier
    dataset_id: Identifier
    retrieved_at: datetime
    checksum: Checksum
    license: License
    publication_date: date | None = None


class GridSpec(Model):
    grid_id: Identifier
    city_id: Identifier
    crs: Annotated[str, Field(pattern=r"^EPSG:\d+$")]
    spacing_m: Annotated[int, Field(gt=0)]
    origin_x: float
    origin_y: float
    version: Annotated[int, Field(ge=1)]


class Territory(Model):
    territory_id: Identifier
    level: Literal["city", "district"]
    name: str
    parent_id: Identifier | None
    timezone: str
    boundary_version: str
    geometry: GeoJSON
    area_km2: NonNegative | None
    area_method: str | None
    evidence_ids: list[Identifier]

    @model_validator(mode="after")
    def area_has_method(self) -> Self:
        if (self.area_km2 is None) != (self.area_method is None):
            raise ValueError("area_km2 and area_method are set together")
        return self


class DistrictShare(Model):
    territory_id: Identifier
    area_share: Annotated[float, Field(ge=0, le=1)]
    population: NonNegative | None


class PopulationCell(Model):
    """Grid cell clipped to the city boundary; population belongs to the cell."""

    cell_id: Identifier
    city_id: Identifier
    grid_id: Identifier
    ix: int
    iy: int
    geometry: GeoJSON
    square_bbox: tuple[Lon, Lat, Lon, Lat] = Field(
        description="EPSG:4326 bbox of the original unclipped square: west, south, east, north"
    )
    land_area_km2: NonNegative
    population: NonNegative | None
    population_status: Literal["estimated", "zero", "unknown"]
    population_method: str | None
    population_year: int | None
    district_shares: list[DistrictShare]
    status: Literal["sampled", "excluded"]
    exclusion_reason: Literal["NO_BUILDING", "WATER_ONLY"] | None
    evidence_ids: Annotated[list[Identifier], Field(min_length=1)]
    # raster coverage, kept apart from the sum; absent in bundles before 2026-11-10
    raster_pixels_total: Annotated[int, Field(ge=0)] | None = None
    raster_pixels_valid: Annotated[int, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.population_status == "unknown" and self.population is not None:
            raise ValueError("unknown population must be null")
        if self.population_status == "zero" and self.population != 0:
            raise ValueError("zero population must be exactly 0")
        if self.population_status == "estimated" and self.population is None:
            raise ValueError("estimated population requires a value")
        if self.population_status != "unknown" and not (
            self.population_method and self.population_year
        ):
            raise ValueError("population requires method and year")
        if (self.status == "excluded") != (self.exclusion_reason is not None):
            raise ValueError("exclusion_reason is set exactly for excluded cells")
        if (self.raster_pixels_total is None) != (self.raster_pixels_valid is None):
            raise ValueError("raster coverage needs both pixel counts")
        if self.raster_pixels_total is not None and self.raster_pixels_valid is not None:
            if self.raster_pixels_valid > self.raster_pixels_total:
                raise ValueError("more valid pixels than pixels")
            if self.raster_pixels_valid < self.raster_pixels_total and self.population is not None:
                raise ValueError("a cell with nodata pixels has unknown population, not a sum")
        if self.population is not None:
            shared = sum(share.population or 0.0 for share in self.district_shares)
            if shared > self.population * (1 + 1e-9) + 1e-9:
                raise ValueError("district shares exceed cell population")
        return self


class AccessNode(Model):
    """Where a point or destination joins the routing graph; not a graph vertex itself."""

    access_node_id: Identifier
    kind: Literal["walk_network_projection", "road_network_projection"]
    lon: Lon
    lat: Lat
    graph_version: str
    osm_way_ref: str
    offset_along_way_m: NonNegative
    evidence_ids: Annotated[list[Identifier], Field(min_length=1)]


class SamplePoint(Model):
    point_id: Identifier
    cell_id: Identifier
    lon: Lon
    lat: Lat
    initial_center_lon: Lon
    initial_center_lat: Lat
    placement: Literal["cell_center", "nearest_building_point"]
    building_ref: str
    offset_m: NonNegative
    access_node_id: Identifier
    access_distance_m: NonNegative
    evidence_ids: Annotated[list[Identifier], Field(min_length=1)]


class Destination(Model):
    destination_id: Identifier
    category: Category
    serving_city_id: Identifier
    name: str | None
    lon: Lon
    lat: Lat
    inside_city_boundary: bool
    osm_ref: str | None
    location_source: Literal["osm", "registry", "registry_address"] = "osm"
    access_node_id: Identifier
    access_distance_m: NonNegative
    status: Literal["accepted", "quarantined"]
    quarantine_reasons: list[str]
    evidence_ids: list[Identifier]

    @model_validator(mode="after")
    def quarantine_reasoned(self) -> Self:
        if (self.status == "quarantined") != bool(self.quarantine_reasons):
            raise ValueError("quarantine reasons are set exactly for quarantined objects")
        return self


class FreightNode(Model):
    """Port, rail freight station, logistics or pipeline terminal (road access for lorries)."""

    freight_node_id: Identifier
    kind: FreightKind
    city_id: Identifier
    name: str | None
    operator: str | None
    lon: Lon
    lat: Lat
    osm_refs: list[str]
    rule: str
    access_node_id: Identifier
    access_distance_m: NonNegative
    status: Literal["accepted", "quarantined"]
    quarantine_reasons: list[str]
    evidence_ids: Annotated[list[Identifier], Field(min_length=1)]

    @model_validator(mode="after")
    def quarantine_reasoned(self) -> Self:
        if (self.status == "quarantined") != bool(self.quarantine_reasons):
            raise ValueError("quarantine reasons are set exactly for quarantined nodes")
        return self


class FreightSite(Model):
    """Physical production site or warehouse; equal weight, no volumes."""

    site_id: Identifier
    kind: Literal["industrial", "warehouse"]
    city_id: Identifier
    name: str | None
    lon: Lon
    lat: Lat
    area_m2: NonNegative
    osm_ref: str
    access_node_id: Identifier
    access_distance_m: NonNegative
    evidence_ids: Annotated[list[Identifier], Field(min_length=1)]


class StopFrequency(Model):
    """Departures of one route at one named stop on one day type.

    A frequency reference from a stop timetable without trip ids: it is never
    joined into trips. ``departures`` counts unique published times; an empty
    published list is unknown (quarantined), never zero.
    """

    frequency_id: Identifier
    city_id: Identifier
    route_id: str
    route_name: str
    mode: Literal["bus", "trolleybus", "tram", "unknown"]
    stop_name: str
    stop_list_position: Annotated[int, Field(ge=0)]
    day_type: Literal["weekdays", "saturday", "weekend"]
    unified_timetable: bool
    departures: Annotated[int, Field(ge=1)] | None
    first_departure: Annotated[str, Field(pattern=r"^\d{2}:\d{2}$")] | None
    last_departure: Annotated[str, Field(pattern=r"^\d{2}:\d{2}$")] | None
    max_hourly_departures: Annotated[int, Field(ge=1)] | None
    median_gap_min: NonNegative | None
    duplicate_times: Annotated[int, Field(ge=0)]
    lon: Lon | None
    lat: Lat | None
    osm_stop_refs: list[str]
    status: Literal["accepted", "quarantined"]
    quarantine_reasons: list[str]
    evidence_ids: Annotated[list[Identifier], Field(min_length=1)]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.status == "quarantined") != bool(self.quarantine_reasons):
            raise ValueError("quarantine reasons are set exactly for quarantined rows")
        if self.status == "accepted" and self.departures is None:
            raise ValueError("an accepted frequency needs known departures")
        if (self.departures is None) != (self.first_departure is None):
            raise ValueError("departures and first departure are known together")
        if (self.lon is None) != (self.lat is None):
            raise ValueError("lon and lat are set together")
        return self


class CategoryOwnership(Model):
    destination_id: Identifier
    category: Category
    ownership: Literal["federal", "regional", "municipal", "state", "private", "unknown"]
    basis: Literal["registry", "osm_tags", "not_required"]
    verification_state: VerificationState
    evidence_ids: list[Identifier]


class WalkConnector(Model):
    """The straight link between a point or destination and its walk access node.

    Rule set ``walk-connector-geometry-v1``. ``traversable=True`` when the link
    crosses no other building, water, railway or motorway and is at most 500 m;
    a crossing is ``None`` (unknown: a bridge or a gate may exist), never ``False``.
    Seconds are not stored: they come from the walking speed of the accepted
    pedestrian profile (``supplements.walk_connectors``).
    """

    physical_node_id: Identifier
    entity_kind: Literal["point", "destination"]
    entity_id: Identifier
    access_node_id: Identifier
    length_m: NonNegative
    traversable: Literal[True] | None
    reasons: list[str]
    evidence_ids: Annotated[list[Identifier], Field(min_length=1)]

    @model_validator(mode="after")
    def state_has_reasons(self) -> Self:
        if self.physical_node_id != f"physical:{self.entity_kind}:{self.entity_id}":
            raise ValueError("physical node id must be physical:<kind>:<entity id>")
        if (self.traversable is None) != bool(self.reasons):
            raise ValueError("an unknown connector needs reasons; a traversable one has none")
        return self


class TransitStop(Model):
    """A timetable stop joined to the walk network like points and destinations."""

    stop_id: Identifier
    timetable_id: Identifier
    gtfs_stop_id: str
    name: str | None
    lon: Lon
    lat: Lat
    access_node_id: Identifier
    access_distance_m: NonNegative
    status: Literal["accepted", "quarantined"]
    quarantine_reasons: list[str]
    evidence_ids: Annotated[list[Identifier], Field(min_length=1)]


class TimetableRef(Model):
    """A timetable kept outside the bundle (a GTFS archive), bound by its sha256."""

    timetable_id: Identifier
    format: Literal["gtfs"]
    path: str
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    dataset_ids: Annotated[list[str], Field(min_length=1)]
    valid_from: date
    valid_to: date
    unknown_dates: list[date]
    modes: list[str]
    qa_report: str
    limitations: list[str]


class CoverageRecord(Model):
    """Completeness of one collection in one scope; absence only with an explicit scope."""

    collection: Collection
    city_id: Identifier | None
    category: Category | None = None
    freight_kind: FreightKind | None = None
    mode: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    status: CoverageStatus
    confirmed_absence: bool = False
    reason: str
    evidence_ids: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def absence_needs_complete_scope(self) -> Self:
        if self.confirmed_absence and self.status != "complete":
            raise ValueError("confirmed absence requires complete coverage of its scope")
        if self.freight_kind is not None and self.collection != "freight_nodes":
            raise ValueError("freight_kind scopes only freight_nodes coverage")
        return self


class FileRef(Model):
    path: str
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    rows: Annotated[int, Field(ge=0)]


class BundleManifest(Model):
    format: Literal["a2-ml2-bundle"]
    schema_version: Literal[2]
    bundle_id: Identifier
    created_at: datetime
    parser_version: str
    acceptance_state: AcceptanceState
    accepted_by: str | None
    accepted_at: datetime | None
    synthetic: bool
    geographic_scope: list[Identifier]
    valid_from: date | None
    valid_to: date | None
    timezone: str
    grids: list[GridSpec]
    graph_version: str | None
    road_graph_version: str | None = None
    timetables: list[TimetableRef] = Field(default_factory=list)
    source_versions: list[SourceVersion]
    files: dict[str, FileRef]
    coverage: list[CoverageRecord]
    qa_report: str
    limitations: list[str]

    @model_validator(mode="after")
    def explicit_acceptance(self) -> Self:
        if self.acceptance_state == "accepted" and (
            self.accepted_by is None or self.accepted_at is None
        ):
            raise ValueError("accepted bundle requires accepted_by and accepted_at")
        return self


class BundleData(Model):
    """Validated collections of one bundle with resolved references."""

    manifest: BundleManifest
    evidence: list[Evidence]
    territories: list[Territory]
    population_cells: list[PopulationCell]
    sample_points: list[SamplePoint]
    destinations: list[Destination]
    category_ownership: list[CategoryOwnership]
    access_nodes: list[AccessNode]
    freight_nodes: list[FreightNode] = Field(default_factory=list)
    freight_sites: list[FreightSite] = Field(default_factory=list)
    stop_frequencies: list[StopFrequency] = Field(default_factory=list)
    walk_connectors: list[WalkConnector] = Field(default_factory=list)
    transit_stops: list[TransitStop] = Field(default_factory=list)
    qa_report: dict[str, Any]

    @model_validator(mode="after")
    def references(self) -> Self:
        def unique(values: list[Any], name: str) -> set[Any]:
            seen = set(values)
            if len(seen) != len(values):
                raise ValueError(f"duplicate key in {name}")
            return seen

        evidence_ids = unique([row.evidence_id for row in self.evidence], "evidence")
        territory_ids = unique([row.territory_id for row in self.territories], "territories")
        cell_ids = unique([row.cell_id for row in self.population_cells], "population_cells")
        unique([row.point_id for row in self.sample_points], "sample_points")
        destination_ids = unique([row.destination_id for row in self.destinations], "destinations")
        unique(
            [(row.destination_id, row.category) for row in self.category_ownership],
            "category_ownership",
        )
        access_ids = unique([row.access_node_id for row in self.access_nodes], "access_nodes")
        grid_ids = {grid.grid_id for grid in self.manifest.grids}
        source_ids = {source.source_id for source in self.manifest.source_versions}
        scope = set(self.manifest.geographic_scope)

        def resolve(value: str | None, allowed: set[Any], name: str) -> None:
            if value is not None and value not in allowed:
                raise ValueError(f"unresolved {name}: {value}")

        for item in self.evidence:
            resolve(item.source_id, source_ids, "evidence.source_id")
        graphs = {self.manifest.graph_version, self.manifest.road_graph_version} - {None}
        access_kinds = {node.access_node_id: node.kind for node in self.access_nodes}

        def joins(value: str, kind: str, name: str) -> None:
            resolve(value, access_ids, f"{name}.access_node_id")
            if access_kinds[value] != kind:
                raise ValueError(f"{name} must join the {kind}: {value}")

        for node in self.access_nodes:
            if node.graph_version not in graphs:
                raise ValueError(f"access node from another graph version: {node.access_node_id}")
            for evidence_id in node.evidence_ids:
                resolve(evidence_id, evidence_ids, "access_nodes.evidence_ids")
        for territory in self.territories:
            resolve(territory.parent_id, territory_ids, "territories.parent_id")
            for evidence_id in territory.evidence_ids:
                resolve(evidence_id, evidence_ids, "territories.evidence_ids")
        for cell in self.population_cells:
            for evidence_id in cell.evidence_ids:
                resolve(evidence_id, evidence_ids, "population_cells.evidence_ids")
            resolve(cell.city_id, scope, "population_cells.city_id")
            resolve(cell.grid_id, grid_ids, "population_cells.grid_id")
            for share in cell.district_shares:
                resolve(share.territory_id, territory_ids, "district_shares.territory_id")
        sampled = {cell.cell_id for cell in self.population_cells if cell.status == "sampled"}
        squares = {cell.cell_id: cell.square_bbox for cell in self.population_cells}
        points_per_cell: dict[str, int] = {}
        for point in self.sample_points:
            resolve(point.cell_id, cell_ids, "sample_points.cell_id")
            joins(point.access_node_id, "walk_network_projection", "sample_points")
            for evidence_id in point.evidence_ids:
                resolve(evidence_id, evidence_ids, "sample_points.evidence_ids")
            if point.cell_id not in sampled:
                raise ValueError(f"sample point in excluded cell: {point.cell_id}")
            west, south, east, north = squares[point.cell_id]
            for lon, lat in (
                (point.lon, point.lat),
                (point.initial_center_lon, point.initial_center_lat),
            ):
                if not (west <= lon <= east and south <= lat <= north):
                    raise ValueError(f"sample point leaves its original cell: {point.point_id}")
            points_per_cell[point.cell_id] = points_per_cell.get(point.cell_id, 0) + 1
        if any(count > 1 for count in points_per_cell.values()):
            raise ValueError("at most one sample point per cell")
        if set(points_per_cell) != sampled:
            raise ValueError("every sampled cell needs exactly one sample point")
        unique([row.freight_node_id for row in self.freight_nodes], "freight_nodes")
        unique([row.site_id for row in self.freight_sites], "freight_sites")
        freight_rows: list[FreightNode | FreightSite] = [*self.freight_nodes, *self.freight_sites]
        for freight_row in freight_rows:
            resolve(freight_row.city_id, scope, "freight.city_id")
            joins(freight_row.access_node_id, "road_network_projection", "freight")
            for evidence_id in freight_row.evidence_ids:
                resolve(evidence_id, evidence_ids, "freight.evidence_ids")
        unique([row.frequency_id for row in self.stop_frequencies], "stop_frequencies")
        entity_access = {
            **{("point", p.point_id): p.access_node_id for p in self.sample_points},
            **{("destination", d.destination_id): d.access_node_id for d in self.destinations},
        }
        unique([row.physical_node_id for row in self.walk_connectors], "walk_connectors")
        for connector in self.walk_connectors:
            entity = (connector.entity_kind, connector.entity_id)
            if entity not in entity_access:
                raise ValueError(f"connector of an unknown {entity[0]}: {entity[1]}")
            if entity_access[entity] != connector.access_node_id:
                raise ValueError(f"connector access node differs: {connector.physical_node_id}")
            for evidence_id in connector.evidence_ids:
                resolve(evidence_id, evidence_ids, "walk_connectors.evidence_ids")
        timetable_ids = {t.timetable_id for t in self.manifest.timetables}
        unique([row.stop_id for row in self.transit_stops], "transit_stops")
        for stop in self.transit_stops:
            resolve(stop.timetable_id, timetable_ids, "transit_stops.timetable_id")
            joins(stop.access_node_id, "walk_network_projection", "transit_stops")
            for evidence_id in stop.evidence_ids:
                resolve(evidence_id, evidence_ids, "transit_stops.evidence_ids")
        for frequency in self.stop_frequencies:
            resolve(frequency.city_id, scope, "stop_frequencies.city_id")
            for evidence_id in frequency.evidence_ids:
                resolve(evidence_id, evidence_ids, "stop_frequencies.evidence_ids")
        for destination in self.destinations:
            resolve(destination.serving_city_id, scope, "destinations.serving_city_id")
            joins(destination.access_node_id, "walk_network_projection", "destinations")
            for evidence_id in destination.evidence_ids:
                resolve(evidence_id, evidence_ids, "destinations.evidence_ids")
        categories = {row.destination_id: row.category for row in self.destinations}
        for row in self.category_ownership:
            resolve(row.destination_id, destination_ids, "category_ownership.destination_id")
            if categories[row.destination_id] != row.category:
                raise ValueError("ownership category differs from destination category")
            for evidence_id in row.evidence_ids:
                resolve(evidence_id, evidence_ids, "category_ownership.evidence_ids")
        owned = {(row.destination_id, row.category) for row in self.category_ownership}
        for destination in self.destinations:
            if destination.category in OWNERSHIP_REQUIRED and destination.status == "accepted":
                key = (destination.destination_id, destination.category)
                verified = any(
                    row.destination_id == key[0]
                    and row.verification_state == "verified"
                    and row.basis == "registry"
                    and row.ownership != "private"
                    for row in self.category_ownership
                )
                if key not in owned or not verified:
                    raise ValueError(
                        f"accepted {destination.category} needs registry-verified public ownership: "
                        f"{destination.destination_id}"
                    )
        return self


def read_collection(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path.name}: collection file must be a JSON array")
    return payload
