"""Final PRODUCT_SPEC wire primitives. Legacy profile enums are intentionally absent."""

from typing import Literal

from pydantic import Field

from a2_contracts.common import ContractModel, Count, Identifier, NonNegative, Timestamp

AnalysisKind = Literal["urban_passenger", "intercity_passenger", "urban_cargo", "intercity_cargo"]
TrafficMode = Literal["static", "typical", "live", "historical"]
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
Priority = Literal["low", "medium", "high"]
Availability = Literal["available", "unknown", "not_applicable", "methodology_pending"]
Completeness = Literal["full", "partial"]
TerritoryLevel = Literal["city", "region"]
CargoDirection = Literal["outbound", "inbound"]
PipelineProduct = Literal["oil", "oil_products", "gas"]
MethodologyVersion = Literal["a2-methodology-v3"]
Mode = Literal[
    "walk", "public_transport", "private_car", "road", "rail", "air", "water", "mixed", "pipeline"
]


class MethodologyRef(ContractModel):
    version: MethodologyVersion
    config_hash: Identifier
    reference_profile: Literal["edge-calibrated-v1"] = "edge-calibrated-v1"


class LonLat(ContractModel):
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)


class VersionRefs(ContractModel):
    snapshot_id: Identifier
    graph_version: Identifier
    calendar_version: Identifier
    population_version: Identifier | None
    methodology_version: MethodologyVersion
    config_hash: Identifier
    code_version: Identifier


__all__ = [
    "ContractModel",
    "Count",
    "Identifier",
    "NonNegative",
    "Timestamp",
    "AnalysisKind",
    "TrafficMode",
    "Category",
    "Priority",
    "Availability",
    "Completeness",
    "TerritoryLevel",
    "CargoDirection",
    "PipelineProduct",
    "MethodologyVersion",
    "Mode",
    "MethodologyRef",
    "LonLat",
    "VersionRefs",
]
