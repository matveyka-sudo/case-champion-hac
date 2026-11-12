"""Versioned, server-owned transport facts for the production dispatcher.

These models are deliberately separate from public requests. They contain inputs
to the public engine, never client-supplied metrics, scores or executable hooks.
"""

from typing import Annotated, Literal

from a2_contracts.v2.base import ContractModel, Timestamp
from a2_contracts.v2.journeys import LineGeometry
from a2_contracts.v2.requests import IntercityPassengerRequest
from a2_engine import (
    AcceptedIntercityCar,
    BoardingReadiness,
    CargoAction,
    CargoDeliveryScope,
    CargoNode,
    CargoProfile,
    CargoReadiness,
    CargoSite,
    CargoState,
    IntercityAnalysisScope,
    PhysicalService,
    PipelineAnalysisScope,
    PipelineObservation,
    ReferenceGraph,
    ReferenceScope,
    Timetable,
    TransportModeBinding,
    TruckRoute,
    TruckSearchReceipt,
    UrbanArea,
    UrbanCandidate,
    UrbanCargoCoverage,
    UrbanContext,
    UrbanCoverage,
    UrbanDestination,
    UrbanPoint,
    UrbanPopulationCoverage,
    UrbanThreshold,
)
from pydantic import Field, TypeAdapter

from a2_api.adapters.timetable_source import ServiceClock


class UrbanInputs(ContractModel):
    kind: Literal["urban_passenger"]
    context: UrbanContext
    points: tuple[UrbanPoint, ...]
    destinations: tuple[UrbanDestination, ...]
    candidates: tuple[UrbanCandidate, ...] = ()
    coverage: tuple[UrbanCoverage, ...]
    area: UrbanArea
    population_coverage: UrbanPopulationCoverage
    thresholds: tuple[UrbanThreshold, ...] = ()
    timetable: Timetable | None = None
    timetable_service_clock: ServiceClock | None = None
    resource_limit: int | None = Field(default=None, gt=0)


class IntercityInputs(ContractModel):
    kind: Literal["intercity_passenger"]
    scope: IntercityAnalysisScope
    timetable: Timetable
    timetable_service_clock: ServiceClock | None = None
    transport_modes: tuple[TransportModeBinding, ...]
    physical_services: tuple[PhysicalService, ...]
    reference_graph: ReferenceGraph
    reference_scope: ReferenceScope
    frequency_start_at: Timestamp
    frequency_end_at: Timestamp
    readiness: tuple[BoardingReadiness, ...] = ()
    initial_airport_checkin_seconds: int | None = Field(default=None, gt=0, le=86400)
    mixed_only: bool = False
    aviation_with_ground_access: bool = False
    car: AcceptedIntercityCar | None = None
    car_departure_at: Timestamp | None = None
    car_geometry: LineGeometry | None = None
    resource_limit: int | None = Field(default=None, gt=0)

    @property
    def public_timetable_unavailable(self) -> bool:
        """An explicitly incomplete empty timetable is not a scheduled network."""
        return (
            not self.transport_modes
            and not self.physical_services
            and not self.timetable.runs
            and not self.timetable.scope.schedule_complete
        )


def car_observation_matches(
    inputs: IntercityInputs, request: IntercityPassengerRequest, *, static_observations: bool
) -> bool:
    return (
        inputs.car is not None
        and inputs.car.traffic_mode == request.traffic_mode
        and (
            inputs.car_departure_at == request.departure_at
            or (static_observations and request.traffic_mode == "static")
        )
    )


class UrbanCargoInputs(ContractModel):
    kind: Literal["urban_cargo"]
    profile: CargoProfile
    sites: tuple[CargoSite, ...]
    nodes: tuple[CargoNode, ...]
    routes: tuple[TruckRoute, ...]
    receipts: tuple[TruckSearchReceipt, ...]
    coverage: UrbanCargoCoverage
    area_km2: float | None


class DeliveryInputs(ContractModel):
    kind: Literal["intercity_cargo"]
    scope: CargoDeliveryScope
    profile: CargoProfile
    states: tuple[CargoState, ...]
    actions: tuple[CargoAction, ...]
    readiness: tuple[CargoReadiness, ...]
    resource_limit: int | None = Field(default=None, gt=0)


class PipelineInputs(ContractModel):
    kind: Literal["pipeline"]
    scope: PipelineAnalysisScope
    observations: tuple[PipelineObservation, ...]


EngineInputs = Annotated[
    UrbanInputs | IntercityInputs | UrbanCargoInputs | DeliveryInputs | PipelineInputs,
    Field(discriminator="kind"),
]
INPUT_ADAPTER = TypeAdapter(EngineInputs)


def candidate_is_static(candidate: UrbanCandidate) -> bool:
    """A declared static cost model cannot turn a scheduled ride into a new run."""
    return candidate.passenger_mode in ("walk", "private_car") and all(
        leg.kind in ("walk", "ride")
        and leg.service_id is None
        and (leg.kind != "ride" or leg.mode == "private_car")
        for leg in candidate.legs
    )


class InputDocument(ContractModel):
    schema_version: Literal["waydex-engine-inputs/1"]
    inputs: EngineInputs
