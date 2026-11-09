"""Canonical requests for the four analyses in docs/PRODUCT_SPEC.md."""

from typing import Annotated, Literal, Self

from pydantic import Field, TypeAdapter, field_validator, model_validator

from .base import (
    CargoDirection,
    Category,
    ContractModel,
    Identifier,
    MethodologyRef,
    PipelineProduct,
    Priority,
    TerritoryLevel,
    Timestamp,
    TrafficMode,
)


class RequestBase(ContractModel):
    schema_version: Literal["2"]
    departure_at: Timestamp
    traffic_mode: TrafficMode
    methodology: MethodologyRef | None = None
    snapshot_id: Identifier | None = None

    @field_validator("passenger_modes", "cargo_modes", "freight_node_types", check_fields=False)
    @classmethod
    def selected_filters(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and (not value or len(value) != len(set(value))):
            raise ValueError("Explicit filters must be nonempty and unique")
        return value

    @model_validator(mode="after")
    def time_grid(self) -> Self:
        if (
            self.departure_at.minute % 15
            or self.departure_at.second
            or self.departure_at.microsecond
        ):
            raise ValueError("Analysis departure must align with the 15-minute input grid")
        return self


class UrbanBase(RequestBase):
    city_id: Identifier
    area_id: Identifier | None = None


class UrbanPassengerRequest(UrbanBase):
    analysis_kind: Literal["urban_passenger"]
    passenger_mode: Literal["walk", "public_transport", "private_car"]
    categories: list[Category] = Field(min_length=1)
    priorities: dict[Category, Priority]

    @model_validator(mode="after")
    def selected_categories(self) -> Self:
        if len(set(self.categories)) != len(self.categories):
            raise ValueError("Selected categories must be unique")
        if set(self.priorities) != set(self.categories):
            raise ValueError("Each selected category requires exactly one named priority")
        return self


class IntercityBase(RequestBase):
    territory_level: TerritoryLevel
    origin_id: Identifier
    destination_id: Identifier
    origin_node_ids: list[Identifier] | None = None
    destination_node_ids: list[Identifier] | None = None

    @model_validator(mode="after")
    def directed_pair(self) -> Self:
        if self.origin_id == self.destination_id:
            raise ValueError("Origin and destination must be different territories")
        if (self.origin_node_ids is None) != (self.destination_node_ids is None):
            raise ValueError("Fixed boundary node sets must be specified together")
        for nodes in (self.origin_node_ids, self.destination_node_ids):
            if nodes is not None and (not nodes or len(nodes) != len(set(nodes))):
                raise ValueError("Boundary node sets must be nonempty and unique")
        return self


class IntercityPassengerRequest(IntercityBase):
    analysis_kind: Literal["intercity_passenger"]
    passenger_modes: (
        list[Literal["road", "rail", "air", "water", "mixed", "private_car"]] | None
    ) = None


class UrbanCargoRequest(UrbanBase):
    analysis_kind: Literal["urban_cargo"]
    cargo_profile_id: Identifier
    direction: CargoDirection
    freight_node_types: (
        list[Literal["port", "rail_freight_station", "terminal", "logistics_center"]] | None
    ) = None


class IntercityCargoRequest(IntercityBase):
    analysis_kind: Literal["intercity_cargo"]
    cargo_profile_id: Identifier
    cargo_transport: Literal["multimodal", "pipeline"]
    pipeline_product: PipelineProduct | None = None
    cargo_modes: list[Literal["road", "rail", "air", "water", "mixed", "pipeline"]] | None = None

    @model_validator(mode="after")
    def separate_pipeline(self) -> Self:
        if self.cargo_transport == "pipeline":
            if self.pipeline_product is None:
                raise ValueError("Pipeline analysis requires an explicit product")
            if self.cargo_modes not in (None, ["pipeline"]):
                raise ValueError("Pipeline cannot be mixed into delivery transport modes")
        elif self.pipeline_product is not None or (
            self.cargo_modes is not None and "pipeline" in self.cargo_modes
        ):
            raise ValueError("Delivery analysis cannot include a pipeline product or mode")
        return self


AnalysisRequest = Annotated[
    UrbanPassengerRequest | IntercityPassengerRequest | UrbanCargoRequest | IntercityCargoRequest,
    Field(discriminator="analysis_kind"),
]
REQUEST_ADAPTER = TypeAdapter(AnalysisRequest)


def parse_analysis_request(value: object) -> AnalysisRequest:
    return REQUEST_ADAPTER.validate_python(value)


class TemperatureRequirements(ContractModel):
    controlled: bool = Field(strict=True)
    min_c: float | None
    max_c: float | None

    @model_validator(mode="after")
    def ordered_temperature(self) -> Self:
        if self.min_c is not None and self.max_c is not None and self.min_c > self.max_c:
            raise ValueError("Minimum temperature exceeds maximum")
        if self.controlled and (self.min_c is None or self.max_c is None):
            raise ValueError("Controlled temperature needs an explicit range")
        return self


class VehicleConstraints(ContractModel):
    max_height_m: Annotated[float, Field(gt=0)] | None
    max_weight_tonnes: Annotated[float, Field(gt=0)] | None
    restrictions: list[Identifier]


class DeliveryCargoProfile(ContractModel):
    profile_id: Identifier
    kind: Literal["delivery"]
    food_class: Literal["food", "nonfood"]
    urgency: Literal["urgent", "ordinary", "nonurgent"]
    temperature_requirements: TemperatureRequirements
    handling_requirements: list[Identifier]
    vehicle_constraints: VehicleConstraints


class PipelineCargoProfile(ContractModel):
    profile_id: Identifier
    kind: Literal["pipeline"]
    product: PipelineProduct


CargoProfile = Annotated[DeliveryCargoProfile | PipelineCargoProfile, Field(discriminator="kind")]
