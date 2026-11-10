"""Versioned, server-owned transport facts for the production dispatcher.

These models are deliberately separate from public requests. They contain inputs
to the public engine, never client-supplied metrics, scores or executable hooks.
"""

from typing import Annotated, Literal

from a2_contracts.v2.base import ContractModel, Timestamp
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
    car: AcceptedIntercityCar | None = None
    car_departure_at: Timestamp | None = None
    resource_limit: int | None = Field(default=None, gt=0)


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
