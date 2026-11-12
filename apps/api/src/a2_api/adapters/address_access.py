"""Static pedestrian accessibility on a versioned local OSM catalogue."""

import json
import os
import sqlite3
from collections.abc import Sequence
from typing import Any, Literal, Self

import httpx
from a2_contracts.v2.base import Priority
from a2_contracts.v2.map_data import MultiPolygonGeometry, PolygonGeometry
from a2_engine.shared.config import load_urban_config
from a2_engine.shared.scoring import category_index
from a2_engine.urban.valhalla.codec import _polyline6
from pydantic import BaseModel, Field, model_validator
from shapely.geometry import LineString, shape  # type: ignore[import-untyped]
from shapely.geometry import Point as ShapePoint
from shapely.geometry.base import BaseGeometry  # type: ignore[import-untyped]

from a2_api.db.repositories.address_catalog import (
    MAX_CANDIDATES,
    candidate_rows,
    catalogue,
    catalogue_path,
    district_rows,
    find_address,
    metadata,
    restricted_walk_areas,
    search_rows,
)

Category = Literal[
    "hospital", "school", "kindergarten", "college", "university", "metro", "mcd", "mck"
]
CATEGORIES = ("hospital", "school", "kindergarten", "college", "university", "metro", "mcd", "mck")
WalkRoute = tuple[float, list[tuple[float, float]]]
ROUTER = "http://valhalla:8002"
OPTIONS = {
    "costing": "pedestrian",
    "costing_options": {"pedestrian": {"walking_speed": 5, "use_ferry": 0}},
}


class Point(BaseModel):
    lat: float
    lon: float


class Address(Point):
    id: str
    label: str
    region: str
    h_seconds: float | None


class Destination(Point):
    id: str
    name: str
    location: str
    line: str


class CategoryResult(BaseModel):
    category: Category
    seconds: float | None = None
    index: float | None = None
    destination: Destination | None = None
    path: list[Point] = Field(default_factory=list)


class AddressQuery(BaseModel):
    query: str = Field(min_length=3, max_length=150)


class AddressRequest(BaseModel):
    address_id: str = Field(pattern=r"^(node|way|relation)/[0-9]+$")
    categories: list[Category] = Field(min_length=1, max_length=len(CATEGORIES))
    priorities: dict[Category, Priority] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_categories(self) -> Self:
        if len(set(self.categories)) != len(self.categories):
            raise ValueError("Categories must be unique")
        if not self.priorities.keys() <= set(self.categories):
            raise ValueError("Priorities require selected categories")
        return self


class AddressResult(BaseModel):
    address: Address
    index: float | None
    categories: list[CategoryResult]
    source: str


class District(BaseModel):
    id: str
    name: str


class DistrictResult(BaseModel):
    district: District
    boundary: PolygonGeometry | MultiPolygonGeometry | None = None
    index: float | None
    categories: dict[Category, float | None]
    population_covered: dict[Category, float]
    population: float
    overall_population_covered: float | None = Field(default=None, ge=0, le=1)
    sample_count: int | None = Field(default=None, ge=0)
    source: str


def address_search(query: str) -> list[Address]:
    tokens = "".join(
        char if char.isalnum() or char in "/-" else " " for char in query.lower().replace("ё", "е")
    ).split()
    tokens = [token for token in tokens if token not in {"ул", "улица", "д", "дом", "г"}]
    if not tokens:
        return []
    parts = [f" {token} " if any(c.isdigit() for c in token) else f" {token}" for token in tokens]
    with catalogue() as db:
        return [Address.model_validate(dict(row)) for row in search_rows(db, parts)]


def _router_post(client: httpx.Client, path: str, body: dict[str, Any]) -> Any:
    response = client.post(path, json={**OPTIONS, **body})
    if response.status_code == 400 and response.json().get("error_code") in {170, 171, 442}:
        return None
    response.raise_for_status()
    return response.json()


def _location(point: Point) -> dict[str, float]:
    return {"lat": point.lat, "lon": point.lon, "search_cutoff": 100}


def _matrix_times(
    client: httpx.Client, point: Point, destinations: list[Destination], *, recover: bool = True
) -> dict[int, float] | None:
    if not destinations:
        return {}
    matrix = _router_post(
        client,
        "/sources_to_targets",
        {"sources": [_location(point)], "targets": [_location(item) for item in destinations]},
    )
    if matrix is not None:
        return {
            item["to_index"]: float(item["time"])
            for item in matrix["sources_to_targets"][0]
            if item["time"] is not None
        }
    if not recover:
        return None

    # Valhalla rejects the whole matrix when a single endpoint cannot be
    # correlated. Keep the matched candidates and preserve their original IDs.
    locations = [_location(point), *map(_location, destinations)]
    matched: list[int] = []
    for offset in range(0, len(locations), 40):
        batch = locations[offset : offset + 40]
        matches = _router_post(client, "/locate", {"locations": batch})
        if not isinstance(matches, list) or len(matches) != len(batch):
            return None
        matched.extend(offset + index for index, row in enumerate(matches) if row.get("edges"))
        if not matched or matched[0] != 0:
            return {}
    indexes = [index - 1 for index in matched[1:]]
    if len(indexes) == len(destinations):
        return None
    filtered = _matrix_times(
        client, point, [destinations[index] for index in indexes], recover=False
    )
    return (
        {indexes[index]: value for index, value in filtered.items()}
        if filtered is not None
        else None
    )


def _walk_route(
    client: httpx.Client,
    point: Point,
    destination: Destination,
    excluded_gates: Sequence[ShapePoint] = (),
    *,
    via: ShapePoint | None = None,
) -> WalkRoute | None:
    locations = [_location(point)]
    if via is not None:
        locations.append({"lon": via.x, "lat": via.y, "search_cutoff": 15})
    locations.append(_location(destination))
    body: dict[str, Any] = {"locations": locations}
    if excluded_gates:
        body["exclude_locations"] = [
            {"lon": gate.x, "lat": gate.y, "search_cutoff": 15} for gate in excluded_gates
        ]
    route = _router_post(client, "/route", body)
    if route is None:
        return None
    trip = route["trip"]
    coordinates = [
        (lon, lat)
        for leg in trip["legs"]
        for lon, lat in _polyline6(leg["shape"], ("valhalla",)).coordinates
    ]
    return float(trip["summary"]["time"]), coordinates


def _walk_to_reviewed_hospital(
    client: httpx.Client,
    point: Point,
    entry: dict[str, Any],
    scope: BaseGeometry,
    gates: Sequence[ShapePoint],
) -> tuple[Destination, float, list[tuple[float, float]]] | None:
    lon, lat = entry["coordinates"]
    destination = Destination(
        id=entry["id"], name=entry["name"], lon=lon, lat=lat, location=entry["location"], line=""
    )
    exit_data = entry.get("pedestrian_exit")
    checkpoint = ShapePoint(*exit_data["checkpoint_coordinates"]) if exit_data else None
    gates = [*gates, *(ShapePoint(*coordinates) for coordinates in entry.get("avoid_gates", []))]
    route = (
        _walk_route(client, point, destination, gates)
        if checkpoint is None
        else _walk_route(client, point, destination, gates, via=checkpoint)
    )
    if route is None or len(route[1]) < 2:
        return None
    seconds, coordinates = route
    line = LineString(coordinates)
    if any(line.distance(gate) <= 2e-6 for gate in gates):
        return None
    if exit_data is None:
        if shape(entry["medical_walk_geometry"]).covers(line):
            return destination, seconds, coordinates
        return None
    crossing = line.intersection(scope.boundary)
    if (
        not scope.covers(ShapePoint(*coordinates[0]))
        or scope.covers(ShapePoint(*coordinates[-1]))
        or crossing.geom_type != "Point"
        or crossing.is_empty
        or crossing.distance(ShapePoint(*exit_data["boundary_crossing"])) > 2.5e-5
        or line.distance(checkpoint) > 2.5e-5
    ):
        return None
    return destination, seconds, coordinates


def _internal_walk(
    client: httpx.Client,
    point: Point,
    grouped: dict[Category, list[Destination]],
    scope: BaseGeometry,
    gates: Sequence[ShapePoint],
    h_seconds: float | None,
    geometry: bool,
    reviewed_hospitals: list[dict[str, Any]],
) -> list[CategoryResult]:
    results = []
    for category, destinations in grouped.items():
        row = CategoryResult(category=category)
        results.append(row)
        for destination in destinations:
            route = _walk_route(client, point, destination, gates)
            if route is None:
                continue
            seconds, coordinates = route
            if len(coordinates) < 2:
                continue
            line = LineString(coordinates)
            # Valhalla polylines are rounded to six decimal places.
            if not scope.covers(line) or any(line.distance(gate) <= 2e-6 for gate in gates):
                continue
            if row.seconds is not None and seconds >= row.seconds:
                continue
            row.destination = destination
            row.seconds = seconds
            row.index = category_index(seconds, h_seconds).value
            if geometry:
                row.path = [Point(lon=lon, lat=lat) for lon, lat in coordinates]
        if category == "hospital":
            for entry in reviewed_hospitals:
                walk = _walk_to_reviewed_hospital(client, point, entry, scope, gates)
                if walk is None or (row.seconds is not None and walk[1] >= row.seconds):
                    continue
                row.destination, row.seconds, coordinates = walk
                row.index = category_index(row.seconds, h_seconds).value
                if geometry:
                    row.path = [Point(lon=lon, lat=lat) for lon, lat in coordinates]
    return results


def calculate_categories(
    db: sqlite3.Connection,
    client: httpx.Client,
    point: Point,
    categories: Sequence[Category],
    h_seconds: float | None,
    *,
    geometry: bool = False,
) -> list[CategoryResult]:
    # The public walking graph does not establish permission to cross a ZATO checkpoint.
    origin = ShapePoint(point.lon, point.lat)
    areas = [
        (
            shape(area["geometry"]),
            [ShapePoint(*gate["coordinates"]) for gate in area["excluded_gates"]],
            area.get("reviewed_hospitals", []),
        )
        for area in restricted_walk_areas(db)
    ]
    scope, gates, reviewed_hospitals = next(
        (item for item in areas if item[0].covers(origin)), (None, [], [])
    )

    def candidates(category: Category) -> list[sqlite3.Row]:
        if not areas:
            return candidate_rows(db, point.lat, point.lon, category)
        rows = candidate_rows(db, point.lat, point.lon, category, limit=-1)
        if scope is not None:
            return [item for item in rows if scope.covers(ShapePoint(item["lon"], item["lat"]))]
        return [
            item
            for item in rows
            if not any(area.covers(ShapePoint(item["lon"], item["lat"])) for area, _, _ in areas)
        ][:MAX_CANDIDATES]

    grouped = {
        category: [Destination.model_validate(dict(item)) for item in candidates(category)]
        for category in categories
    }
    if scope is not None:
        return _internal_walk(
            client, point, grouped, scope, gates, h_seconds, geometry, reviewed_hospitals
        )
    matrix = _matrix_times(client, point, [item for items in grouped.values() for item in items])
    results = []
    offset = 0
    for category, destinations in grouped.items():
        row = CategoryResult(category=category)
        results.append(row)
        # If the router cannot correlate a batch, retry its categories separately.
        if matrix is None:
            times = _matrix_times(client, point, destinations) or {}
        else:
            times = {
                index: matrix[offset + index]
                for index in range(len(destinations))
                if offset + index in matrix
            }
        offset += len(destinations)
        if not times:
            continue
        best = min(times, key=lambda index: (times[index], index))
        row.destination = destinations[best]
        row.seconds = times[best]
        if geometry:
            route = _walk_route(client, point, row.destination)
            if route is not None:
                row.path = [Point(lon=lon, lat=lat) for lon, lat in route[1]]
        row.index = category_index(row.seconds, h_seconds).value
    return results


def address_result(request: AddressRequest) -> AddressResult | None:
    with (
        catalogue() as db,
        httpx.Client(base_url=os.getenv("A2_ADDRESS_ROUTER_URL", ROUTER), timeout=12) as client,
    ):
        row = find_address(db, request.address_id)
        if row is None:
            return None
        address = Address.model_validate(dict(row))
        categories = calculate_categories(
            db, client, address, request.categories, address.h_seconds, geometry=True
        )
        weights = load_urban_config().priority_weights
        values = [
            (item.index, weights[request.priorities.get(item.category, "medium")])
            for item in categories
            if item.index is not None
        ]
        index = (
            sum(value * weight for value, weight in values) / sum(weight for _, weight in values)
            if len(values) == len(categories)
            else None
        )
        source = metadata(db, "source")
        return AddressResult(address=address, index=index, categories=categories, source=source)


def districts() -> list[District]:
    with catalogue() as db:
        return [District(id=row["id"], name=row["name"]) for row in district_rows(db)]


def district_result(district_id: str) -> DistrictResult | None:
    path = catalogue_path().with_name("districts.json")
    if not path.is_file():
        return None
    reports = json.loads(path.read_text())
    report = reports.get(district_id)
    if report is None:
        return None
    with catalogue() as db:
        district = next((row for row in district_rows(db) if row["id"] == district_id), None)
    if district is None:
        return None
    return DistrictResult.model_validate({**report, "boundary": json.loads(district["geometry"])})
