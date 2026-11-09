"""Typed snapshot boundary for PRODUCT_SPEC; validation never performs ingest or GIS repair.

All source_refs resolve to Evidence.evidence_id in the same snapshot. Registry
records add field-specific institutional confirmation, not a medical-hours model.
"""

from collections.abc import Sequence
from datetime import date
from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, Field, HttpUrl, StrictBool, field_validator, model_validator

from a2_contracts.common import valid_timezone

from .base import (
    Category,
    Completeness,
    ContractModel,
    Count,
    Identifier,
    LonLat,
    Mode,
    NonNegative,
    PipelineProduct,
    TerritoryLevel,
    Timestamp,
    TrafficMode,
)

PublicOwnership = Literal["government", "municipal", "private", "unknown"]
PUBLIC_CATEGORIES = {
    "adult_polyclinic",
    "child_polyclinic",
    "kindergarten",
    "school",
    "college_university",
}


def _confirmed(value: object) -> object:
    if value is not True:
        raise ValueError("Confirmation must be explicitly true, without boolean coercion")
    return value


Confirmed = Annotated[Literal[True], BeforeValidator(_confirmed)]


class Period(ContractModel):
    start: date | None
    end: date | None

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.start and self.end and self.end < self.start:
            raise ValueError("Reversed validity period")
        return self


class SourceLinked(ContractModel):
    source_refs: list[Identifier] = Field(min_length=1)


class Evidence(ContractModel):
    evidence_id: Identifier
    source_id: Identifier
    source_version: Identifier
    url: HttpUrl
    retrieved_at: Timestamp
    valid_period: Period
    license: Identifier
    content_hash: Identifier
    verified: StrictBool


class Bounds(ContractModel):
    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-90, le=90)
    east: float = Field(ge=-180, le=180)
    north: float = Field(ge=-90, le=90)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.west > self.east or self.south > self.north:
            raise ValueError("Bounds must be ordered; split antimeridian geometries upstream")
        return self

    def contains(self, point: LonLat) -> bool:
        return self.west <= point.lon <= self.east and self.south <= point.lat <= self.north


class GeometryRef(SourceLinked):
    geometry_id: Identifier
    version: Identifier
    bbox: Bounds | None = None  # EPSG:4326; no geometry is synthesized from a bbox.


class SnapshotProvenance(ContractModel):
    graph_version: Identifier
    geometry_version: Identifier
    calendar_version: Identifier
    population_version: Identifier | None


class QAReport(ContractModel):
    acceptance_state: Literal["accepted"]
    accepted_by: Identifier
    accepted_at: Timestamp
    checks: list[Identifier] = Field(min_length=1)


class Territory(SourceLinked):
    territory_id: Identifier
    level: TerritoryLevel
    name: Identifier
    timezone: Identifier
    geometry: GeometryRef
    area_km2: NonNegative | None
    _timezone = field_validator("timezone")(valid_timezone)


class Located(SourceLinked):
    territory_id: Identifier
    location: LonLat


class PopulationCell(SourceLinked):
    cell_id: Identifier
    territory_id: Identifier
    grid_version: Identifier
    grid_origin_m: tuple[float, float]
    projection: Identifier
    geometry: GeometryRef
    population: NonNegative | None
    population_year: Annotated[int, Field(strict=True, ge=1)] | None
    population_method: Identifier | None
    unit: Literal["persons"]
    sample_status: Literal["included", "excluded"]
    exclusion_reason: Identifier | None

    @model_validator(mode="after")
    def known_population(self) -> Self:
        if self.population is not None and (
            self.population_year is None or not self.population_method
        ):
            raise ValueError("Known population requires year and method")
        if (self.sample_status == "excluded") != (self.exclusion_reason is not None):
            raise ValueError("Excluded cells require a reason; included cells have none")
        return self


class SamplePoint(Located):
    point_id: Identifier
    cell_id: Identifier
    building_id: Identifier
    initial_center: LonLat
    access_node_id: Identifier
    access_distance_m: NonNegative


class Destination(Located):
    destination_id: Identifier
    categories: list[Category] = Field(min_length=1)
    access_node_id: Identifier
    object_identity: Identifier


class AccessNode(Located):
    node_id: Identifier
    kind: Identifier


class ServiceStopTime(ContractModel):
    node_id: Identifier
    sequence: Count
    arrival_seconds: Count
    departure_seconds: Count
    pickup: StrictBool
    dropoff: StrictBool


class ScheduledService(SourceLinked):
    service_id: Identifier
    trip_id: Identifier
    mode: Mode
    timezone: Identifier
    service_dates: list[date] = Field(min_length=1)
    trip_identity_confirmed: Confirmed
    stop_times: list[ServiceStopTime] = Field(min_length=2)
    _timezone = field_validator("timezone")(valid_timezone)

    @model_validator(mode="after")
    def ordered_chain(self) -> Self:
        if len(set(self.service_dates)) != len(self.service_dates):
            raise ValueError(
                "Service dates must be unique explicit dates after calendar exceptions"
            )
        previous = None
        for event in self.stop_times:
            if event.arrival_seconds > event.departure_seconds or (
                previous
                and (
                    event.sequence <= previous.sequence
                    or event.arrival_seconds < previous.departure_seconds
                )
            ):
                raise ValueError("Service chain must preserve sequence and service-day time")
            previous = event
        return self


class Transfer(SourceLinked):
    transfer_id: Identifier
    from_node_id: Identifier
    to_node_id: Identifier
    seconds: NonNegative
    verification_state: Literal["verified", "absent", "unknown"]


class FreightSite(Located):
    site_id: Identifier
    access_node_id: Identifier


class FreightNode(Located):
    freight_node_id: Identifier
    access_node_id: Identifier
    kind: Literal["port", "rail_terminal", "logistics_terminal", "airport", "pipeline_terminal"]


class FreightCompatibility(SourceLinked):
    compatibility_id: Identifier
    site_id: Identifier
    freight_node_id: Identifier
    direction: Literal["inbound", "outbound"]
    food_classes: list[Literal["food", "nonfood"]]
    temperature_requirements: list[Identifier]
    handling_requirements: list[Identifier]
    vehicle_constraints: list[Identifier]
    compatible: StrictBool | None


class Quantity(ContractModel):
    value: NonNegative
    unit: Identifier
    period: Period
    product: PipelineProduct


class PipelineConnection(SourceLinked):
    connection_id: Identifier
    product: PipelineProduct
    from_node_id: Identifier
    to_node_id: Identifier
    connection_confirmed: Confirmed
    direction_confirmed: Confirmed
    volume: Quantity | None
    capacity: Quantity | None
    quantity_status: Literal["available", "unknown"]

    @model_validator(mode="after")
    def comparable_quantities(self) -> Self:
        if self.from_node_id == self.to_node_id:
            raise ValueError("Pipeline connection requires distinct confirmed boundary nodes")
        if self.quantity_status == "available":
            v, q = self.volume, self.capacity
            if not v or not q or q.value <= 0 or v.unit != q.unit or v.period != q.period:
                raise ValueError(
                    "Pipeline quantities require comparable period/unit and positive capacity"
                )
            if (
                v.product != self.product
                or q.product != self.product
                or not v.period.start
                or not v.period.end
            ):
                raise ValueError("Pipeline product and complete period must match")
        return self


class ReferenceEdge(SourceLinked):
    edge_id: Identifier
    from_node_id: Identifier
    to_node_id: Identifier
    mode: Mode
    length_m: NonNegative | None
    running_time_seconds: Annotated[float, Field(gt=0)] | None
    calibration_method: Identifier
    geometry: GeometryRef | None


class MinimumTransfer(SourceLinked):
    minimum_id: Identifier
    from_node_id: Identifier
    to_node_id: Identifier
    seconds: NonNegative
    applicability: list[Identifier]


class TrafficObservation(ContractModel):
    date: date
    weekday_type: Identifier
    season: Identifier
    graph_version: Identifier


class TrafficProfile(SourceLinked):
    profile_id: Identifier
    directed_edge_id: Identifier
    graph_version: Identifier
    traffic_mode: TrafficMode
    status: Literal["available", "unknown"]
    speed_kph: Annotated[float, Field(gt=0)] | None
    is_closed: StrictBool | None
    weekday_type: Identifier
    local_15min_bin: Annotated[int, Field(strict=True, ge=0, lt=96)]
    season: Identifier
    observed_period: Period
    observations: list[TrafficObservation]
    sample_count: Count
    observed_at: Timestamp | None
    valid_until: Timestamp | None
    method: Identifier
    confidence: Annotated[float, Field(ge=0, le=1)] | None

    @model_validator(mode="after")
    def comparable_observations(self) -> Self:
        if self.sample_count < len(self.observations):
            raise ValueError("Sample count cannot be smaller than supplied observations")
        if self.status == "unknown" and self.speed_kph is not None:
            raise ValueError("Unknown traffic must not assert speed")
        if self.is_closed and self.speed_kph is not None:
            raise ValueError("Closure is explicit, never represented as a speed")
        if (
            self.status == "available"
            and not self.is_closed
            and (self.speed_kph is None or self.is_closed is None)
        ):
            raise ValueError("Available open edge requires a positive speed")
        if self.traffic_mode == "typical" and self.status == "available":
            if len({o.date for o in self.observations}) < 5:
                raise ValueError("Typical traffic requires at least five distinct comparable dates")
            for observation in self.observations:
                if any(
                    getattr(observation, field) != getattr(self, field)
                    for field in ("weekday_type", "season", "graph_version")
                ):
                    raise ValueError("Incomparable traffic observations")
                if (
                    self.observed_period.start and observation.date < self.observed_period.start
                ) or (self.observed_period.end and observation.date > self.observed_period.end):
                    raise ValueError("Traffic observation outside declared period")
        if self.traffic_mode == "live" and self.status == "available":
            if not self.observed_at or not self.valid_until or self.valid_until <= self.observed_at:
                raise ValueError("Available live traffic requires explicit observation and expiry")
        return self


class RegistryEvidence(SourceLinked):
    registry_evidence_id: Identifier
    registry_id: Identifier
    destination_id: Identifier
    category: Category
    ownership: PublicOwnership
    verified: StrictBool


class CategoryOwnership(SourceLinked):
    destination_id: Identifier
    category: Category
    ownership: PublicOwnership
    registry_evidence_ids: list[Identifier]


class CoverageScope(SourceLinked):
    territory_ids: list[Identifier] = Field(min_length=1)
    modes: list[Mode] = Field(min_length=1)
    categories: list[Category]
    valid_period: Period
    completeness: Completeness


class AcceptedData(ContractModel):
    schema_version: Literal["2"]
    snapshot_id: Identifier
    source_versions: dict[Identifier, Identifier]
    geographic_scope: list[Identifier]
    valid_period: Period
    timezone: Identifier
    provenance: SnapshotProvenance
    qa: QAReport
    evidence: list[Evidence]
    registry_evidence: list[RegistryEvidence]
    territories: list[Territory]
    population_cells: list[PopulationCell]
    sample_points: list[SamplePoint]
    destinations: list[Destination]
    access_nodes: list[AccessNode]
    scheduled_services: list[ScheduledService]
    transfers: list[Transfer]
    freight_sites: list[FreightSite]
    freight_nodes: list[FreightNode]
    freight_compatibility: list[FreightCompatibility]
    pipeline_connections: list[PipelineConnection]
    reference_edges: list[ReferenceEdge]
    minimum_transfers: list[MinimumTransfer]
    traffic_profiles: list[TrafficProfile]
    category_ownership: list[CategoryOwnership]
    coverage_by_mode_date_category: list[CoverageScope]
    confirmed_absence_scope: list[CoverageScope]
    _timezone = field_validator("timezone")(valid_timezone)

    @model_validator(mode="after")
    def intact_references(self) -> Self:
        tables: tuple[tuple[Sequence[ContractModel], str], ...] = (
            (self.evidence, "evidence_id"),
            (self.registry_evidence, "registry_evidence_id"),
            (self.territories, "territory_id"),
            (self.population_cells, "cell_id"),
            (self.sample_points, "point_id"),
            (self.destinations, "destination_id"),
            (self.access_nodes, "node_id"),
            (self.scheduled_services, "service_id"),
            (self.transfers, "transfer_id"),
            (self.freight_sites, "site_id"),
            (self.freight_nodes, "freight_node_id"),
            (self.freight_compatibility, "compatibility_id"),
            (self.pipeline_connections, "connection_id"),
            (self.reference_edges, "edge_id"),
            (self.minimum_transfers, "minimum_id"),
            (self.traffic_profiles, "profile_id"),
        )
        keys: dict[str, set[str]] = {}
        for rows, field in tables:
            values = [str(getattr(row, field)) for row in rows]
            if len(values) != len(set(values)):
                raise ValueError(f"Duplicate {field}")
            keys[field] = set(values)

        def require(value: str, field: str) -> None:
            if value not in keys[field]:
                raise ValueError(f"Unresolved {field}: {value}")

        for evidence in self.evidence:
            if self.source_versions.get(evidence.source_id) != evidence.source_version:
                raise ValueError("Evidence source/version missing from snapshot")
        for territory_id in self.geographic_scope:
            require(territory_id, "territory_id")
        linked = [row for rows, _ in tables for row in rows if isinstance(row, SourceLinked)]
        linked += [
            *self.category_ownership,
            *self.coverage_by_mode_date_category,
            *self.confirmed_absence_scope,
        ]
        linked += [t.geometry for t in self.territories] + [
            c.geometry for c in self.population_cells
        ]
        linked += [e.geometry for e in self.reference_edges if e.geometry]
        for row in linked:
            for evidence_id in row.source_refs:
                require(evidence_id, "evidence_id")
            if isinstance(row, Located | PopulationCell):
                require(row.territory_id, "territory_id")
            if isinstance(row, SamplePoint | Destination | FreightSite | FreightNode):
                require(row.access_node_id, "node_id")
            if isinstance(row, Transfer | MinimumTransfer | ReferenceEdge):
                require(row.from_node_id, "node_id")
                require(row.to_node_id, "node_id")
            if isinstance(row, RegistryEvidence | CategoryOwnership):
                require(row.destination_id, "destination_id")
        for scope in [*self.coverage_by_mode_date_category, *self.confirmed_absence_scope]:
            for territory_id in scope.territory_ids:
                require(territory_id, "territory_id")
        cells = {cell.cell_id: cell for cell in self.population_cells}
        if len({p.cell_id for p in self.sample_points}) != len(self.sample_points):
            raise ValueError("At most one sample point per original grid cell")
        if {p.cell_id for p in self.sample_points} != {
            c.cell_id for c in self.population_cells if c.sample_status == "included"
        }:
            raise ValueError(
                "Every included cell requires one sample; excluded population stays recorded"
            )
        for point in self.sample_points:
            require(point.cell_id, "cell_id")
            cell = cells[point.cell_id]
            if cell.sample_status != "included" or point.territory_id != cell.territory_id:
                raise ValueError("Sample point requires an included cell of the same territory")
            if cell.geometry.bbox and not all(
                cell.geometry.bbox.contains(p) for p in (point.location, point.initial_center)
            ):
                raise ValueError("Sample point cannot leave its original cell")
        for service in self.scheduled_services:
            for event in service.stop_times:
                require(event.node_id, "node_id")
        if len({s.trip_id for s in self.scheduled_services}) != len(self.scheduled_services):
            raise ValueError("Duplicate trip identity")
        for compatibility in self.freight_compatibility:
            require(compatibility.site_id, "site_id")
            require(compatibility.freight_node_id, "freight_node_id")
        for pipe in self.pipeline_connections:
            require(pipe.from_node_id, "freight_node_id")
            require(pipe.to_node_id, "freight_node_id")
        for traffic in self.traffic_profiles:
            require(traffic.directed_edge_id, "edge_id")
            if traffic.graph_version != self.provenance.graph_version:
                raise ValueError("Traffic profile belongs to another graph version")
        ownerships = {(o.destination_id, o.category): o for o in self.category_ownership}
        if len(ownerships) != len(self.category_ownership):
            raise ValueError("Duplicate category ownership record")
        registry = {r.registry_evidence_id: r for r in self.registry_evidence}
        evidence_by_id = {e.evidence_id: e for e in self.evidence}
        for scope in self.confirmed_absence_scope:
            if scope.completeness != "full" or not all(
                evidence_by_id[e].verified for e in scope.source_refs
            ):
                raise ValueError("Confirmed absence requires a full, verified scope")
        object_categories = [
            (d.object_identity, c) for d in self.destinations for c in d.categories
        ]
        if len(object_categories) != len(set(object_categories)):
            raise ValueError("An object may occur only once per category, regardless of entrances")
        for ownership in self.category_ownership:
            for registry_id in ownership.registry_evidence_ids:
                require(registry_id, "registry_evidence_id")
        for destination in self.destinations:
            if len(set(destination.categories)) != len(destination.categories):
                raise ValueError("Destination categories must be unique")
            for category in set(destination.categories) & PUBLIC_CATEGORIES:
                matching_ownership = ownerships.get((destination.destination_id, category))
                if not matching_ownership or matching_ownership.ownership not in {
                    "government",
                    "municipal",
                }:
                    raise ValueError("Public medical/education category requires public ownership")
                confirmed = [registry[r] for r in matching_ownership.registry_evidence_ids]
                if not any(
                    r.verified
                    and r.destination_id == destination.destination_id
                    and r.category == category
                    and r.ownership == matching_ownership.ownership
                    and all(evidence_by_id[e].verified for e in r.source_refs)
                    for r in confirmed
                ):
                    raise ValueError("Public category requires matching verified registry evidence")
        return self
