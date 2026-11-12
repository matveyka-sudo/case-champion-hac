"""Immutable normalized engine boundary; not a second set of wire DTOs."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from a2_engine.shared.results import finite_number

Completeness = Literal["full", "partial"]


BOUNDED_URBAN_SEARCH = "selected-original-cells-v1"


def identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty identifier")


def references(values: Iterable[str], *, required: bool = False) -> tuple[str, ...]:
    if isinstance(values, str):
        raise ValueError("References must be a collection of identifiers")
    result = tuple(values)
    for value in result:
        identifier(value, "reference")
    if required and not result:
        raise ValueError("Evidence/source references are required")
    return tuple(sorted(set(result)))


def utc_timestamp(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("AWARE_DATETIME_REQUIRED")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class UrbanVersions:
    snapshot_id: str
    graph_version: str
    calendar_version: str
    population_version: str | None
    geometry_version: str
    methodology_version: str
    config_hash: str
    traffic_version: str

    def __post_init__(self) -> None:
        for name in (
            "snapshot_id",
            "graph_version",
            "calendar_version",
            "geometry_version",
            "methodology_version",
            "config_hash",
            "traffic_version",
        ):
            identifier(getattr(self, name), name)
        if self.population_version is not None:
            identifier(self.population_version, "population_version")


@dataclass(frozen=True)
class UrbanContext:
    city_id: str
    territory_id: str
    requested_departure_at: datetime
    traffic_mode: Literal["static", "typical", "live", "historical"]
    versions: UrbanVersions
    search_parameters_id: str

    def __post_init__(self) -> None:
        for name in ("city_id", "territory_id", "search_parameters_id"):
            identifier(getattr(self, name), name)
        if self.traffic_mode not in ("static", "typical", "live", "historical"):
            raise ValueError("Unsupported traffic mode")
        if not isinstance(self.versions, UrbanVersions):
            raise ValueError("UrbanVersions are required")
        object.__setattr__(
            self, "requested_departure_at", utc_timestamp(self.requested_departure_at)
        )


@dataclass(frozen=True)
class UrbanPoint:
    point_id: str
    cell_id: str
    origin_node_id: str
    access_node_id: str
    access_distance_m: float
    territory_id: str
    population: float | None
    population_year: int | None
    population_method: str | None
    source_refs: tuple[str, ...]
    population_share_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("point_id", "cell_id", "origin_node_id", "access_node_id", "territory_id"):
            identifier(getattr(self, name), name)
        object.__setattr__(
            self, "access_distance_m", finite_number(self.access_distance_m, "access_distance_m")
        )
        if self.access_distance_m > 0 and self.origin_node_id == self.access_node_id:
            raise ValueError("ACCESS_CONNECTOR_REQUIRED")
        if self.population_share_id is not None:
            identifier(self.population_share_id, "population_share_id")
        if self.population is not None:
            object.__setattr__(self, "population", finite_number(self.population, "population"))
            if (
                isinstance(self.population_year, bool)
                or not isinstance(self.population_year, int)
                or self.population_year < 1
                or self.population_method is None
            ):
                raise ValueError("Known population requires its year and method")
        if self.population_method is not None:
            identifier(self.population_method, "population_method")
        object.__setattr__(self, "source_refs", references(self.source_refs, required=True))


@dataclass(frozen=True)
class UrbanArea:
    city_id: str
    area_km2: float | None
    geometry_version: str
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        identifier(self.city_id, "city_id")
        identifier(self.geometry_version, "geometry_version")
        if self.area_km2 is not None:
            object.__setattr__(
                self, "area_km2", finite_number(self.area_km2, "area_km2", positive=True)
            )
        object.__setattr__(
            self, "source_refs", references(self.source_refs, required=self.area_km2 is not None)
        )


@dataclass(frozen=True)
class UrbanDestination:
    destination_id: str
    object_identity: str
    categories: frozenset[str]
    access_node_id: str
    territory_id: str
    eligible: bool | None
    in_service_scope: bool
    source_refs: tuple[str, ...]
    physical_node_id: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        for name in ("destination_id", "object_identity", "access_node_id", "territory_id"):
            identifier(getattr(self, name), name)
        if isinstance(self.categories, str) or not self.categories:
            raise ValueError("Destination categories must be nonempty")
        for category in self.categories:
            identifier(category, "category")
        object.__setattr__(self, "categories", frozenset(self.categories))
        if self.eligible is not None and not isinstance(self.eligible, bool):
            raise ValueError("eligible must be an explicit boolean or None")
        if not isinstance(self.in_service_scope, bool):
            raise ValueError("in_service_scope must be an explicit boolean")
        object.__setattr__(self, "source_refs", references(self.source_refs, required=True))
        if self.physical_node_id is not None:
            identifier(self.physical_node_id, "physical_node_id")


@dataclass(frozen=True)
class UrbanGeometry:
    coordinates: tuple[tuple[float, float], ...]
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        coordinates = tuple(tuple(pair) for pair in self.coordinates)
        if len(coordinates) < 2:
            raise ValueError("A supplied LineString requires at least two coordinates")
        for pair in coordinates:
            if (
                len(pair) != 2
                or any(
                    isinstance(value, bool) or not isinstance(value, (int, float)) for value in pair
                )
                or not (-180 <= pair[0] <= 180 and -90 <= pair[1] <= 90)
            ):
                raise ValueError("Invalid supplied geographic coordinates")
            for value in pair:
                finite_number(abs(value), "coordinate")
        object.__setattr__(self, "coordinates", coordinates)
        object.__setattr__(self, "evidence_refs", references(self.evidence_refs, required=True))


@dataclass(frozen=True)
class UrbanLeg:
    mode: str
    kind: Literal["walk", "ride", "wait_initial", "wait_transfer"]
    from_node_id: str
    to_node_id: str
    start_at: datetime
    end_at: datetime
    duration_seconds: float
    service_id: str | None
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("mode", "from_node_id", "to_node_id"):
            identifier(getattr(self, name), name)
        if self.kind not in ("walk", "ride", "wait_initial", "wait_transfer"):
            raise ValueError("Unsupported urban passenger leg kind")
        object.__setattr__(self, "start_at", utc_timestamp(self.start_at))
        object.__setattr__(self, "end_at", utc_timestamp(self.end_at))
        object.__setattr__(
            self, "duration_seconds", finite_number(self.duration_seconds, "duration_seconds")
        )
        if self.service_id is not None:
            identifier(self.service_id, "service_id")
        object.__setattr__(self, "evidence_refs", references(self.evidence_refs))


@dataclass(frozen=True)
class UrbanCandidate:
    journey_id: str
    point_id: str
    destination_id: str
    category: str
    passenger_mode: str
    context: UrbanContext
    origin_node_id: str
    destination_node_id: str
    departure_at: datetime
    arrival_at: datetime
    legs: tuple[UrbanLeg, ...]
    evidence_refs: tuple[str, ...]
    geometry: UrbanGeometry | None = None

    def __post_init__(self) -> None:
        for name in (
            "journey_id",
            "point_id",
            "destination_id",
            "category",
            "passenger_mode",
            "origin_node_id",
            "destination_node_id",
        ):
            identifier(getattr(self, name), name)
        if not isinstance(self.context, UrbanContext):
            raise ValueError("UrbanContext is required")
        if self.passenger_mode not in ("walk", "public_transport", "private_car"):
            raise ValueError("Unsupported urban passenger mode")
        object.__setattr__(self, "departure_at", utc_timestamp(self.departure_at))
        object.__setattr__(self, "arrival_at", utc_timestamp(self.arrival_at))
        legs = tuple(self.legs)
        if not legs or any(not isinstance(leg, UrbanLeg) for leg in legs):
            raise ValueError("A candidate requires an explicit leg sequence")
        object.__setattr__(self, "legs", legs)
        object.__setattr__(self, "evidence_refs", references(self.evidence_refs, required=True))
        if self.geometry is not None and not isinstance(self.geometry, UrbanGeometry):
            raise ValueError("Only supplied actual UrbanGeometry is accepted")

    @property
    def geometry_kind(self) -> Literal["actual", "none"]:
        return "none" if self.geometry is None else "actual"


@dataclass(frozen=True)
class UrbanSearchScope:
    context: UrbanContext
    point_id: str
    category: str
    passenger_mode: str
    max_total_wait_seconds: float

    def __post_init__(self) -> None:
        for name in ("point_id", "category", "passenger_mode"):
            identifier(getattr(self, name), name)
        if not isinstance(self.context, UrbanContext):
            raise ValueError("UrbanContext is required")
        object.__setattr__(
            self,
            "max_total_wait_seconds",
            finite_number(self.max_total_wait_seconds, "max_total_wait_seconds"),
        )


@dataclass(frozen=True)
class UrbanCoverage:
    scope: UrbanSearchScope
    catalog_complete: bool
    search_complete: bool
    evidence_refs: tuple[str, ...]
    catalog_destination_ids: frozenset[str]
    provider_error: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, UrbanSearchScope):
            raise ValueError("UrbanSearchScope is required")
        if not isinstance(self.catalog_complete, bool) or not isinstance(
            self.search_complete, bool
        ):
            raise ValueError("Coverage requires explicit booleans")
        if self.provider_error is not None:
            identifier(self.provider_error, "provider_error")
            if self.search_complete:
                raise ValueError("Provider error cannot certify complete search")
        object.__setattr__(
            self,
            "evidence_refs",
            references(self.evidence_refs, required=self.catalog_complete or self.search_complete),
        )
        object.__setattr__(
            self,
            "catalog_destination_ids",
            frozenset(references(self.catalog_destination_ids)),
        )


@dataclass(frozen=True)
class UrbanPopulationCoverage:
    context: UrbanContext
    complete: bool
    excluded_population: float | None
    evidence_refs: tuple[str, ...]
    point_ids: frozenset[str]

    def __post_init__(self) -> None:
        if not isinstance(self.context, UrbanContext) or not isinstance(self.complete, bool):
            raise ValueError("Explicit population coverage/context is required")
        if self.excluded_population is not None:
            object.__setattr__(
                self,
                "excluded_population",
                finite_number(self.excluded_population, "excluded_population"),
            )
        object.__setattr__(
            self, "evidence_refs", references(self.evidence_refs, required=self.complete)
        )
        object.__setattr__(self, "point_ids", frozenset(references(self.point_ids)))


@dataclass(frozen=True)
class UrbanThreshold:
    city_id: str
    passenger_mode: str
    versions: UrbanVersions
    traffic_mode: str
    search_parameters_id: str
    seconds: float | None
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("city_id", "passenger_mode", "traffic_mode", "search_parameters_id"):
            identifier(getattr(self, name), name)
        if not isinstance(self.versions, UrbanVersions):
            raise ValueError("UrbanVersions are required")
        if self.seconds is not None:
            object.__setattr__(self, "seconds", finite_number(self.seconds, "tau seconds"))
        object.__setattr__(
            self, "evidence_refs", references(self.evidence_refs, required=self.seconds is not None)
        )
