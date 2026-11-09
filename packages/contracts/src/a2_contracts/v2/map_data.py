"""Saved map metadata supplied by a trusted adapter, never inferred from a bbox.

Coordinates are GeoJSON longitude/latitude. Ring checks are structural validation,
not geometric repair, building containment checks or proof of accepted GIS data.
Routes retain the existing JourneyResult geometry/evidence contract.
"""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .base import Category, Completeness, ContractModel, Identifier, NonNegative

MapEntityKind = Literal[
    "cell",
    "sample_point",
    "destination",
    "freight_site",
    "freight_node",
    "access_node",
    "territory",
]
Coordinate = tuple[
    Annotated[float, Field(strict=True, ge=-180, le=180)],
    Annotated[float, Field(strict=True, ge=-90, le=90)],
]
Ring = Annotated[list[Coordinate], Field(min_length=4)]
PolygonCoordinates = Annotated[list[Ring], Field(min_length=1)]


def _closed_rings(rings: list[list[Coordinate]]) -> None:
    for ring in rings:
        if ring[0] != ring[-1] or len(set(ring[:-1])) < 3:
            raise ValueError("Polygon rings must close and contain distinct vertices")


class PointGeometry(ContractModel):
    type: Literal["Point"]
    coordinates: Coordinate


class PolygonGeometry(ContractModel):
    type: Literal["Polygon"]
    coordinates: PolygonCoordinates

    @model_validator(mode="after")
    def closed_rings(self) -> Self:
        _closed_rings(self.coordinates)
        return self


class MultiPolygonGeometry(ContractModel):
    type: Literal["MultiPolygon"]
    coordinates: list[PolygonCoordinates] = Field(min_length=1)

    @model_validator(mode="after")
    def closed_rings(self) -> Self:
        for polygon in self.coordinates:
            _closed_rings(polygon)
        return self


MapGeometry = Annotated[
    PointGeometry | PolygonGeometry | MultiPolygonGeometry, Field(discriminator="type")
]


class MapFeature(ContractModel):
    entity_kind: MapEntityKind
    entity_id: Identifier
    territory_id: Identifier
    point_id: Identifier | None = None
    cell_id: Identifier | None = None
    categories: list[Category] = Field(default_factory=list)
    name: Identifier | None = None
    geometry: MapGeometry | None
    geometry_kind: Literal["actual", "schematic", "none"]
    geometry_ref: Identifier | None = None
    reason_code: Identifier | None = None
    population: NonNegative | None = None
    population_year: Annotated[int, Field(strict=True, ge=1900, le=2200)] | None = None
    population_method: Identifier | None = None
    source_refs: list[Identifier] = Field(min_length=1)

    @model_validator(mode="after")
    def explicit_metadata(self) -> Self:
        if (self.geometry is None) != (self.geometry_kind == "none"):
            raise ValueError("Map geometry must be explicitly actual, schematic or absent")
        if self.geometry is None and not (self.geometry_ref or self.reason_code):
            raise ValueError("Absent geometry requires its immutable reference or reason")
        if self.population is not None and (
            self.population_year is None or self.population_method is None
        ):
            raise ValueError("Known population requires year and method")
        if self.entity_kind == "cell" and self.cell_id not in (None, self.entity_id):
            raise ValueError("Cell feature identity differs from its cell link")
        if self.entity_kind == "sample_point" and self.point_id not in (None, self.entity_id):
            raise ValueError("Sample feature identity differs from its point link")
        if len(set(self.categories)) != len(self.categories) or len(set(self.source_refs)) != len(
            self.source_refs
        ):
            raise ValueError("Map categories and evidence references must be unique")
        return self


class AnalysisMapData(ContractModel):
    status: Literal["available", "unknown"] = "unknown"
    completeness: Completeness = "partial"
    reason_code: Identifier | None = "MAP_DATA_UNAVAILABLE"
    features: list[MapFeature] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent_inventory(self) -> Self:
        if self.status == "unknown":
            if not self.reason_code or self.features or self.completeness != "partial":
                raise ValueError(
                    "Unknown map requires a reason, partial scope and no invented features"
                )
            return self
        if not self.features:
            raise ValueError("Available map requires explicit saved features")
        identities = {(feature.entity_kind, feature.entity_id) for feature in self.features}
        if len(identities) != len(self.features):
            raise ValueError("Duplicate map entity identity")
        for feature in self.features:
            if (
                feature.point_id is not None
                and ("sample_point", feature.point_id) not in identities
            ):
                raise ValueError("Unresolved map point reference")
            if feature.cell_id is not None and ("cell", feature.cell_id) not in identities:
                raise ValueError("Unresolved map cell reference")
            if self.completeness == "full" and feature.geometry is None:
                raise ValueError("Missing feature geometry requires a partial map")
        return self
