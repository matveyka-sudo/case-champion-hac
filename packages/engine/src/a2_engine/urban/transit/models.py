"""transit / models — canonical urban implementation."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from a2_engine.routing.timetable_models import Timetable, TimetablePath, TimetableSearchResult
from a2_engine.shared.config import UrbanJourneyRules
from a2_engine.shared.results import MetricResult
from a2_engine.urban.journeys import UrbanJourneyTiming, UrbanLegTotals
from a2_engine.urban.models import UrbanContext, UrbanLeg, references


@dataclass(frozen=True)
class UrbanTransitNetwork:
    context: UrbanContext  # All config/geometry/population/traffic/search identities retained.
    timetable: Timetable
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        scope = self.timetable.scope
        versions = self.context.versions
        if (scope.snapshot_id, scope.graph_version, scope.calendar_version) != (
            versions.snapshot_id,
            versions.graph_version,
            versions.calendar_version,
        ):
            raise ValueError("URBAN_TIMETABLE_VERSION_MISMATCH")
        object.__setattr__(self, "evidence_refs", references(self.evidence_refs, required=True))


@dataclass(frozen=True)
class UrbanReturnJourney:
    point_id: str
    destination_id: str
    leave_at: datetime
    first_departure_at: datetime
    arrival_at: datetime
    legs: tuple[UrbanLeg, ...]
    totals: UrbanLegTotals
    source_path: TimetablePath


@dataclass(frozen=True)
class UrbanTransitResult:
    network: UrbanTransitNetwork
    point_id: str
    destination_id: str
    category: str
    passenger_mode: str
    search: TimetableSearchResult
    best_known: UrbanJourneyTiming | None
    seconds: MetricResult

    @property
    def search_complete(self) -> bool:
        return self.search.proven


@dataclass(frozen=True)
class UrbanLastReturnResult:
    network: UrbanTransitNetwork
    point_id: str
    destination_id: str
    local_day: date
    timezone: str
    status: Literal["available", "unknown", "not_applicable"]
    state: Literal["reachable", "confirmed_unreachable", "unknown", "walk_only"]
    proven: bool
    best_known: UrbanReturnJourney | None
    reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    expanded_states: int
    interrupted: bool
    rules: UrbanJourneyRules
    lower_leave_at: datetime | None
    resource_limit: int | None

    @property
    def leave_at(self) -> datetime | None:
        return self.best_known.leave_at if self.status == "available" and self.best_known else None

    @property
    def first_departure_at(self) -> datetime | None:
        return (
            self.best_known.first_departure_at
            if self.status == "available" and self.best_known
            else None
        )

    @property
    def arrival_at(self) -> datetime | None:
        return (
            self.best_known.arrival_at if self.status == "available" and self.best_known else None
        )
