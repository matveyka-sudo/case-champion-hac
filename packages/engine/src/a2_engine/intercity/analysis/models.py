"""Immutable supplemental identity, analysis scope and coverage, outside canonical DTO."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from a2_engine.intercity.passenger import CarResult, PassengerResult
from a2_engine.routing.reference.models import ReferenceScope, identifier, identifiers
from a2_engine.routing.scheduled import ScheduledChain, ScheduledSearchResult
from a2_engine.routing.timetable_models import utc
from a2_engine.shared.results import finite_number

PublicMode = Literal["road", "rail", "air", "water"]


@dataclass(frozen=True)
class TransportModeBinding:
    transport_type: str
    mode: PublicMode
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        identifier(self.transport_type)
        if self.mode not in ("road", "rail", "air", "water"):
            raise ValueError("INVALID_INTERCITY_PUBLIC_MODE")
        object.__setattr__(self, "evidence_refs", identifiers(self.evidence_refs))


@dataclass(frozen=True)
class PhysicalDeparture:
    event_sequence: int
    departure_id: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.event_sequence) is not int or self.event_sequence < 0:
            raise ValueError("INVALID_PHYSICAL_EVENT_SEQUENCE")
        identifier(self.departure_id)
        object.__setattr__(self, "evidence_refs", identifiers(self.evidence_refs))


@dataclass(frozen=True)
class PhysicalService:
    physical_service_id: str
    run_ids: tuple[str, ...]
    departures: tuple[PhysicalDeparture, ...]
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        identifier(self.physical_service_id)
        object.__setattr__(self, "run_ids", identifiers(self.run_ids))
        rows = tuple(sorted(self.departures, key=lambda r: r.event_sequence))
        if len({r.event_sequence for r in rows}) != len(rows):
            raise ValueError("DUPLICATE_PHYSICAL_EVENT_SEQUENCE")
        identifiers(tuple(r.departure_id for r in rows), nonempty=False)
        object.__setattr__(self, "departures", rows)
        object.__setattr__(self, "evidence_refs", identifiers(self.evidence_refs))


@dataclass(frozen=True)
class CapitalBoundary:
    region_id: str
    capital_city_id: str
    node_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        identifier(self.region_id)
        identifier(self.capital_city_id)
        object.__setattr__(self, "node_ids", identifiers(self.node_ids))
        object.__setattr__(self, "evidence_refs", identifiers(self.evidence_refs))


@dataclass(frozen=True)
class IntercityAnalysisScope:
    comparison_scope: ReferenceScope
    applicable_modes: tuple[PublicMode, ...]
    modes_complete: bool
    evidence_refs: tuple[str, ...]
    territory_kind: Literal["cities", "regions"] = "cities"
    capital_boundaries: tuple[CapitalBoundary, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.comparison_scope, ReferenceScope)
            or type(self.modes_complete) is not bool
        ):
            raise ValueError("INVALID_INTERCITY_SCOPE")
        values = identifiers(self.applicable_modes)
        if set(values) - set(self.comparison_scope.allowed_modes):
            raise ValueError("PUBLIC_MODES_OUTSIDE_COMMON_REFERENCE_AREA")
        object.__setattr__(self, "applicable_modes", values)
        object.__setattr__(self, "evidence_refs", identifiers(self.evidence_refs))
        capitals = tuple(sorted(self.capital_boundaries, key=lambda c: c.region_id))
        if self.territory_kind not in ("cities", "regions"):
            raise ValueError("INVALID_INTERCITY_TERRITORY_KIND")
        if self.territory_kind == "regions":
            expected = {
                self.comparison_scope.origin_territory_id: self.comparison_scope.origin_node_ids,
                self.comparison_scope.destination_territory_id: self.comparison_scope.destination_node_ids,
            }
            if len(capitals) != 2 or {c.region_id: c.node_ids for c in capitals} != expected:
                raise ValueError("ACCEPTED_CAPITAL_BOUNDARIES_REQUIRED")
        elif capitals:
            raise ValueError("CAPITAL_BINDING_REQUIRES_REGION_ANALYSIS")
        object.__setattr__(self, "capital_boundaries", capitals)


@dataclass(frozen=True)
class BoardingReadiness:
    node_id: str
    ready_at: datetime
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        identifier(self.node_id)
        object.__setattr__(self, "ready_at", utc(self.ready_at))
        object.__setattr__(self, "evidence_refs", identifiers(self.evidence_refs))


@dataclass(frozen=True)
class AcceptedIntercityCar:
    chain_id: str
    comparison_scope: ReferenceScope
    origin_node_id: str
    destination_node_id: str
    path_seconds: float
    complete: bool
    evidence_refs: tuple[str, ...]
    traffic_mode: Literal["static", "typical", "live", "historical"] = "static"

    def __post_init__(self) -> None:
        for value in (self.chain_id, self.origin_node_id, self.destination_node_id):
            identifier(value)
        if not isinstance(self.comparison_scope, ReferenceScope) or type(self.complete) is not bool:
            raise ValueError("INVALID_ACCEPTED_CAR_CONTEXT")
        if self.traffic_mode not in ("static", "typical", "live", "historical"):
            raise ValueError("INVALID_ACCEPTED_CAR_TRAFFIC")
        object.__setattr__(
            self, "path_seconds", finite_number(self.path_seconds, "car path", positive=True)
        )
        object.__setattr__(self, "evidence_refs", identifiers(self.evidence_refs))


@dataclass(frozen=True)
class IntercityChainResult:
    route: ScheduledChain
    evaluation: PassengerResult
    initial_wait_seconds: float
    path_seconds: float
    total_seconds: float
    riding_seconds: float
    walking_seconds: float
    transfer_wait_seconds: float
    boardings: int
    transfers: int
    category: str
    first_physical_departure_id: str | None
    physical_boardings_confirmed: bool
    chain_complete: bool


@dataclass(frozen=True)
class IntercityFrequency:
    start_at: datetime
    end_at: datetime
    category: str
    count: int | None
    known_departure_ids: tuple[str, ...]
    complete: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class IntercityModeResult:
    category: str
    best_known: IntercityChainResult | None
    optimum_proven: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class IntercityPassengerAnalysis:
    analysis_id: str
    scope: IntercityAnalysisScope
    transport_modes: tuple[TransportModeBinding, ...]
    physical_services: tuple[PhysicalService, ...]
    requested_at: datetime
    departure_window_end_at: datetime
    candidates: tuple[IntercityChainResult, ...]
    by_mode: tuple[IntercityModeResult, ...]
    public: IntercityModeResult
    connection_state: Literal["reachable", "confirmed_absent", "unknown"]
    search: ScheduledSearchResult
    frequency_search: ScheduledSearchResult
    reference_completeness: Literal["full", "partial"]
    timetable_complete: bool
    modes_complete: bool
    frequency: tuple[IntercityFrequency, ...]
    car: CarResult | None
    car_complete: bool
    full_ranking_eligible: bool
    reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...]
