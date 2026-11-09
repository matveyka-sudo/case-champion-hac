"""Immutable scenario inputs, operation supplements and complete result records."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from a2_engine.comparison.deltas import DeltaResult
from a2_engine.freight.delivery.models import (
    CargoAction,
    CargoDeliveryScope,
    CargoReadiness,
    CargoState,
    IntercityCargoAnalysis,
)
from a2_engine.freight.models import CargoProfile
from a2_engine.freight.truck import TruckRoute, TruckSearchReceipt
from a2_engine.freight.urban_analysis import (
    CargoNode,
    CargoSite,
    UrbanCargoAnalysis,
    UrbanCargoCoverage,
)
from a2_engine.intercity.analysis.models import (
    AcceptedIntercityCar,
    BoardingReadiness,
    IntercityAnalysisScope,
    IntercityPassengerAnalysis,
    PhysicalService,
    TransportModeBinding,
)
from a2_engine.pipeline.analysis import (
    PipelineAnalysisScope,
    PipelineConnectionsAnalysis,
    PipelineObservation,
)
from a2_engine.routing.reference.search import ReferenceSearchResult
from a2_engine.routing.timetable_models import Timetable, TimetableRun
from a2_engine.routing.traffic.models import TrafficSnapshot
from a2_engine.shared.config import (
    CargoDeliveryRules,
    FreightConfig,
    IntercityPassengerRules,
    PassengerConfig,
    UrbanConfig,
    UrbanJourneyRules,
)
from a2_engine.shared.normalized import checked_id, ids, utc
from a2_engine.shared.results import finite_number
from a2_engine.urban.analysis.models import UrbanAnalysis
from a2_engine.urban.districts.models import (
    UrbanDistrict,
    UrbanDistrictAnalysis,
    UrbanDistrictCoverage,
    UrbanDistrictShare,
    UrbanOriginalCell,
)
from a2_engine.urban.models import (
    UrbanArea,
    UrbanCandidate,
    UrbanContext,
    UrbanCoverage,
    UrbanDestination,
    UrbanPoint,
    UrbanPopulationCoverage,
    UrbanThreshold,
)
from a2_engine.urban.tau.models import UrbanTauResult
from a2_engine.urban.transit.models import UrbanLastReturnResult, UrbanTransitResult


@dataclass(frozen=True)
class ScenarioTimetable:
    network_id: str
    timetable: Timetable
    transport_modes: tuple[TransportModeBinding, ...] = ()
    physical_services: tuple[PhysicalService, ...] = ()

    def __post_init__(self) -> None:
        checked_id(self.network_id)
        object.__setattr__(
            self,
            "transport_modes",
            tuple(sorted(self.transport_modes, key=lambda m: m.transport_type)),
        )
        object.__setattr__(
            self,
            "physical_services",
            tuple(sorted(self.physical_services, key=lambda s: s.physical_service_id)),
        )


@dataclass(frozen=True)
class UrbanScenarioInput:
    job_id: str
    network_id: str
    context: UrbanContext
    points: tuple[UrbanPoint, ...]
    destinations: tuple[UrbanDestination, ...]
    coverage: tuple[UrbanCoverage, ...]
    area: UrbanArea
    selected_categories: tuple[tuple[str, str], ...]
    modes: tuple[str, ...]
    population_coverage: UrbanPopulationCoverage
    config: UrbanConfig
    rules: UrbanJourneyRules
    thresholds: tuple[UrbanThreshold, ...] = ()
    tau_results: tuple[UrbanTauResult, ...] = ()
    fixed_candidates: tuple[UrbanCandidate, ...] = ()
    original_cells: tuple[UrbanOriginalCell, ...] = ()
    districts: tuple[UrbanDistrict, ...] = ()
    district_shares: tuple[UrbanDistrictShare, ...] = ()
    district_coverage: UrbanDistrictCoverage | None = None
    return_day: date | None = None
    resource_limit: int | None = None

    def __post_init__(self) -> None:
        checked_id(self.job_id)
        checked_id(self.network_id)
        for key, attr in (
            ("points", "point_id"),
            ("destinations", "destination_id"),
            ("thresholds", "passenger_mode"),
            ("tau_results", "passenger_mode"),
            ("fixed_candidates", "journey_id"),
            ("original_cells", "cell_id"),
            ("districts", "district_id"),
        ):
            object.__setattr__(
                self, key, tuple(sorted(getattr(self, key), key=lambda r: getattr(r, attr)))
            )
        object.__setattr__(
            self,
            "coverage",
            tuple(
                sorted(
                    self.coverage,
                    key=lambda c: (c.scope.point_id, c.scope.category, c.scope.passenger_mode),
                )
            ),
        )
        object.__setattr__(
            self,
            "district_shares",
            tuple(sorted(self.district_shares, key=lambda s: (s.cell_id, s.district_id))),
        )
        object.__setattr__(self, "selected_categories", tuple(sorted(self.selected_categories)))
        object.__setattr__(self, "modes", ids(self.modes))
        if len(dict(self.selected_categories)) != len(self.selected_categories):
            raise ValueError("DUPLICATE_SCENARIO_CATEGORY")
        if any(
            candidate.passenger_mode in ("walk", "public_transport")
            for candidate in self.fixed_candidates
        ):
            raise ValueError("ROUTABLE_CANDIDATES_MUST_BE_RECOMPUTED")
        if self.district_coverage is None and (self.districts or self.district_shares):
            raise ValueError("SCENARIO_DISTRICT_COVERAGE_REQUIRED")
        for tau in self.tau_results:
            if tau.to_threshold() not in self.thresholds:
                raise ValueError("FROZEN_TAU_THRESHOLD_MISMATCH")


@dataclass(frozen=True)
class IntercityScenarioInput:
    job_id: str
    network_id: str
    scope: IntercityAnalysisScope
    requested_at: datetime
    reference: ReferenceSearchResult
    config: PassengerConfig
    rules: IntercityPassengerRules
    frequency_start_at: datetime
    frequency_end_at: datetime
    readiness: tuple[BoardingReadiness, ...] = ()
    car: AcceptedIntercityCar | None = None
    resource_limit: int | None = None

    def __post_init__(self) -> None:
        checked_id(self.job_id)
        checked_id(self.network_id)
        for key in ("requested_at", "frequency_start_at", "frequency_end_at"):
            object.__setattr__(self, key, utc(getattr(self, key)))
        object.__setattr__(
            self, "readiness", tuple(sorted(self.readiness, key=lambda r: r.node_id))
        )


@dataclass(frozen=True)
class CargoExecution:
    execution_id: str
    service_date: date
    action_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        checked_id(self.execution_id)
        if type(self.service_date) is not date:
            raise ValueError("EXPLICIT_CARGO_SERVICE_DATE_REQUIRED")
        object.__setattr__(self, "action_ids", ids(self.action_ids))
        object.__setattr__(self, "evidence_refs", ids(self.evidence_refs))


@dataclass(frozen=True)
class CargoScenarioInput:
    job_id: str
    scope: CargoDeliveryScope
    started_at: datetime
    profile: CargoProfile
    states: tuple[CargoState, ...]
    actions: tuple[CargoAction, ...]
    readiness: tuple[CargoReadiness, ...]
    executions: tuple[CargoExecution, ...]
    config: FreightConfig
    rules: CargoDeliveryRules
    physical_sites: tuple[CargoSite, ...] = ()
    resource_limit: int | None = None

    def __post_init__(self) -> None:
        checked_id(self.job_id)
        object.__setattr__(self, "started_at", utc(self.started_at))
        object.__setattr__(
            self, "physical_sites", tuple(sorted(self.physical_sites, key=lambda s: s.site_id))
        )
        if any(site.context != self.scope.context for site in self.physical_sites):
            raise ValueError("CARGO_SCENARIO_SITE_CONTEXT_MISMATCH")
        for key, attr in (
            ("states", "state_id"),
            ("actions", "action_id"),
            ("readiness", "state_id"),
            ("executions", "execution_id"),
        ):
            object.__setattr__(
                self, key, tuple(sorted(getattr(self, key), key=lambda x: getattr(x, attr)))
            )


@dataclass(frozen=True)
class UrbanCargoScenarioInput:
    job_id: str
    profile: CargoProfile
    requested_at: datetime
    sites: tuple[CargoSite, ...]
    nodes: tuple[CargoNode, ...]
    routes: tuple[TruckRoute, ...]
    receipts: tuple[TruckSearchReceipt, ...]
    coverage: UrbanCargoCoverage
    selected_node_types: tuple[str, ...]
    area_km2: float | None
    config: FreightConfig

    def __post_init__(self) -> None:
        checked_id(self.job_id)
        object.__setattr__(self, "requested_at", utc(self.requested_at))
        for key, attr in (
            ("sites", "site_id"),
            ("nodes", "node_id"),
            ("routes", "route_id"),
            ("receipts", "route_id"),
        ):
            object.__setattr__(
                self, key, tuple(sorted(getattr(self, key), key=lambda x: getattr(x, attr)))
            )
        object.__setattr__(self, "selected_node_types", ids(self.selected_node_types))


@dataclass(frozen=True)
class PipelineScenarioInput:
    job_id: str
    scope: PipelineAnalysisScope
    observations: tuple[PipelineObservation, ...]
    config: FreightConfig

    def __post_init__(self) -> None:
        checked_id(self.job_id)
        object.__setattr__(
            self,
            "observations",
            tuple(sorted(self.observations, key=lambda o: o.connection.connection_id)),
        )


@dataclass(frozen=True)
class ScenarioInputs:
    timetables: tuple[ScenarioTimetable, ...] = ()
    urban: tuple[UrbanScenarioInput, ...] = ()
    intercity: tuple[IntercityScenarioInput, ...] = ()
    cargo: tuple[CargoScenarioInput, ...] = ()
    urban_cargo: tuple[UrbanCargoScenarioInput, ...] = ()
    pipelines: tuple[PipelineScenarioInput, ...] = ()
    traffic_snapshots: tuple[TrafficSnapshot, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "timetables", tuple(sorted(self.timetables, key=lambda t: t.network_id))
        )
        ids(tuple(t.network_id for t in self.timetables), required=False)
        job_ids: list[str] = []
        for key in ("urban", "intercity", "cargo", "urban_cargo", "pipelines"):
            rows = tuple(sorted(getattr(self, key), key=lambda j: j.job_id))
            object.__setattr__(self, key, rows)
            job_ids.extend(j.job_id for j in rows)
        ids(tuple(job_ids))
        from a2_engine.scenario.identity import scenario_hash

        object.__setattr__(
            self, "traffic_snapshots", tuple(sorted(self.traffic_snapshots, key=scenario_hash))
        )


@dataclass(frozen=True)
class ScenarioOperation:
    baseline_id: str
    operation_id: str
    template_id: str
    target_kind: Literal["timetable", "cargo"]
    target_id: str
    execution_id: str
    service_date: date
    event_id: str
    expected_execution_hash: str
    shift_seconds: int
    incoming_execution_id: str
    incoming_event_id: str
    evidence_refs: tuple[str, ...]
    timing_model: Literal["whole_execution", "departure_tail"] = "whole_execution"
    new_run: TimetableRun | None = None
    new_physical_service: PhysicalService | None = None
    demand_evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for key in (
            "baseline_id",
            "operation_id",
            "template_id",
            "target_id",
            "execution_id",
            "event_id",
            "expected_execution_hash",
            "incoming_execution_id",
            "incoming_event_id",
        ):
            checked_id(getattr(self, key))
        if (
            self.target_kind not in ("timetable", "cargo")
            or type(self.service_date) is not date
            or type(self.shift_seconds) is not int
            or self.shift_seconds == 0
        ):
            raise ValueError("INVALID_SCENARIO_OPERATION")
        if self.timing_model not in ("whole_execution", "departure_tail"):
            raise ValueError("EXPLICIT_SCENARIO_TIMING_MODEL_REQUIRED")
        object.__setattr__(self, "evidence_refs", ids(self.evidence_refs))
        object.__setattr__(
            self, "demand_evidence_refs", ids(self.demand_evidence_refs, required=False)
        )


@dataclass(frozen=True)
class ScenarioMeasure:
    key: tuple[str, ...]  # semantic roster: job, subject, category/mode/name/period
    unit: Literal["seconds", "index", "count", "utc_seconds", "percent"]
    prefer_higher: bool
    value: float | None
    state: Literal["known", "absent", "unknown"]
    proven: bool
    scope_hash: str
    route_id: str | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("SEMANTIC_METRIC_KEY_REQUIRED")
        object.__setattr__(self, "key", tuple(self.key))
        for part in self.key:
            checked_id(part)
        checked_id(self.scope_hash)
        if (
            self.unit not in ("seconds", "index", "count", "utc_seconds", "percent")
            or self.state not in ("known", "absent", "unknown")
            or type(self.prefer_higher) is not bool
            or type(self.proven) is not bool
        ):
            raise ValueError("INVALID_SCENARIO_MEASURE")
        if self.value is not None:
            object.__setattr__(self, "value", finite_number(self.value, "scenario metric"))
        if (self.state == "known" and self.value is None) or (
            self.state == "unknown" and self.value is not None
        ):
            raise ValueError("SCENARIO_METRIC_STATE_MISMATCH")
        if self.unit == "index" and self.value is not None and self.value > 100:
            raise ValueError("INVALID_SCENARIO_INDEX")

    @property
    def metric_id(self) -> str:
        from a2_engine.scenario.identity import scenario_hash

        return "metric:" + scenario_hash((self.key, self.unit, self.prefer_higher, self.scope_hash))


@dataclass(frozen=True)
class ScenarioEffect:
    metric_id: str
    key: tuple[str, ...]
    delta: DeltaResult
    outcome: Literal[
        "improved", "worsened", "unchanged", "new_connection", "lost_connection", "unknown"
    ]


@dataclass(frozen=True)
class UrbanScenarioResult:
    job_id: str
    analysis: UrbanAnalysis
    searches: tuple[UrbanTransitResult, ...]
    districts: UrbanDistrictAnalysis | None
    returns: tuple[tuple[tuple[str, str, str], UrbanLastReturnResult | None], ...]


@dataclass(frozen=True)
class ScenarioComputation:
    urban: tuple[UrbanScenarioResult, ...]
    intercity: tuple[tuple[str, IntercityPassengerAnalysis], ...]
    cargo: tuple[tuple[str, IntercityCargoAnalysis], ...]
    urban_cargo: tuple[tuple[str, UrbanCargoAnalysis], ...]
    pipelines: tuple[tuple[str, PipelineConnectionsAnalysis], ...]
    metrics: tuple[ScenarioMeasure, ...]


@dataclass(frozen=True)
class ScenarioBaseline:
    baseline_id: str
    input_hash: str
    result_hash: str
    inputs: ScenarioInputs
    before: ScenarioComputation


@dataclass(frozen=True)
class ScenarioEventChange:
    network_id: str
    execution_id: str
    event_id: str
    before_arrival: datetime | None
    before_departure: datetime | None
    after_arrival: datetime
    after_departure: datetime


@dataclass(frozen=True)
class ScenarioOverlay:
    overlay_id: str
    baseline_id: str
    operation_id: str
    inputs: ScenarioInputs
    changed_events: tuple[ScenarioEventChange, ...]
    affected_execution_ids: tuple[str, ...]


@dataclass(frozen=True)
class ScenarioResourceLink:
    previous_run_id: str
    next_run_id: str
    minimum_seconds: float
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        checked_id(self.previous_run_id)
        checked_id(self.next_run_id)
        object.__setattr__(
            self, "minimum_seconds", finite_number(self.minimum_seconds, "accepted turnaround")
        )
        object.__setattr__(self, "evidence_refs", ids(self.evidence_refs))


@dataclass(frozen=True)
class ScenarioResourceBlock:
    resource_id: str
    network_id: str
    run_ids: tuple[str, ...]
    available_from: datetime
    available_to: datetime
    links: tuple[ScenarioResourceLink, ...]
    evidence_refs: tuple[str, ...]
    minimum_before_seconds: float | None = None
    minimum_after_seconds: float | None = None

    def __post_init__(self) -> None:
        for key in ("minimum_before_seconds", "minimum_after_seconds"):
            value = getattr(self, key)
            if value is not None:
                object.__setattr__(self, key, finite_number(value, key))
        checked_id(self.resource_id)
        checked_id(self.network_id)
        object.__setattr__(self, "run_ids", ids(self.run_ids))
        for key in ("available_from", "available_to"):
            object.__setattr__(self, key, utc(getattr(self, key)))
        if self.available_from >= self.available_to:
            raise ValueError("RESOURCE_AVAILABILITY_PERIOD_REQUIRED")
        object.__setattr__(
            self,
            "links",
            tuple(sorted(self.links, key=lambda link: (link.previous_run_id, link.next_run_id))),
        )
        object.__setattr__(self, "evidence_refs", ids(self.evidence_refs))


@dataclass(frozen=True)
class ScenarioFeasibilityInputs:
    baseline_id: str
    operation_hash: str
    blocks: tuple[ScenarioResourceBlock, ...]
    covered_execution_ids: tuple[str, ...]
    resources_complete: bool
    calendar_confirmed: bool
    infrastructure_confirmed: bool
    constraints_valid_from: datetime
    constraints_valid_to: datetime
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        checked_id(self.baseline_id)
        checked_id(self.operation_hash)
        for key in ("resources_complete", "calendar_confirmed", "infrastructure_confirmed"):
            if type(getattr(self, key)) is not bool:
                raise ValueError("EXPLICIT_FEASIBILITY_COVERAGE_REQUIRED")
        object.__setattr__(
            self, "blocks", tuple(sorted(self.blocks, key=lambda b: (b.network_id, b.resource_id)))
        )
        object.__setattr__(self, "covered_execution_ids", ids(self.covered_execution_ids))
        object.__setattr__(self, "evidence_refs", ids(self.evidence_refs))
        for key in ("constraints_valid_from", "constraints_valid_to"):
            object.__setattr__(self, key, utc(getattr(self, key)))
        if self.constraints_valid_from >= self.constraints_valid_to:
            raise ValueError("FEASIBILITY_PERIOD_REQUIRED")


@dataclass(frozen=True)
class ScenarioFeasibility:
    state: Literal["feasible", "infeasible", "unverified"]
    reasons: tuple[str, ...]
    resource_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ScenarioImpact:
    known_affected_population: float
    affected_cell_ids: tuple[tuple[str, str, str], ...]
    unknown_cell_ids: tuple[tuple[str, str, str], ...]
    population_complete: bool
    affected_site_ids: tuple[str, ...]
    unknown_site_ids: tuple[str, ...]
    sites_complete: bool
    unmapped_site_job_ids: tuple[str, ...]
    scope_job_ids: tuple[str, ...]


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    baseline_id: str
    operation: ScenarioOperation
    overlay: ScenarioOverlay
    before: ScenarioComputation
    after: ScenarioComputation
    effects: tuple[ScenarioEffect, ...]
    worsened_metric_ids: tuple[str, ...]
    feasibility: ScenarioFeasibility
    impact: ScenarioImpact
    complete: bool
    label: str
    diagnostics: tuple[str, ...]
    evidence_refs: tuple[str, ...]
