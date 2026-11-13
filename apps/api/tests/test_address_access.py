"""Regression coverage for category offsets and missing walking routes."""

import json
import sqlite3
from contextlib import closing

import httpx
import pytest
from a2_api.adapters import address_access as access
from pydantic import ValidationError
from shapely.geometry import box, mapping


def test_batched_walk_scores_stay_bound_to_their_category(monkeypatch):
    def rows(_db, _point, category):
        if category == "school":
            return []
        return [
            {
                "id": f"node/{index}",
                "name": f"{category} {index}",
                "lat": 55.75,
                "lon": 37.61 + index / 1000,
                "location": "object",
                "line": "",
            }
            for index in range(2)
        ]

    def route(request):
        assert request.url.path == "/sources_to_targets"
        return httpx.Response(
            200,
            json={
                "sources_to_targets": [
                    [
                        {"to_index": 0, "time": 500},
                        {"to_index": 1, "time": 300},
                        {"to_index": 2, "time": None},
                        {"to_index": 3, "time": 180},
                    ]
                ]
            },
        )

    monkeypatch.setattr(access, "candidate_rows", rows)
    monkeypatch.setattr(access, "restricted_walk_areas", lambda _db: [])
    with httpx.Client(base_url="http://local", transport=httpx.MockTransport(route)) as client:
        hospital, school, metro = access.calculate_categories(
            None, client, access.Point(lat=55.75, lon=37.61), ["hospital", "school", "metro"], 3600
        )
    assert hospital.destination.name == "hospital 1"
    assert hospital.index == pytest.approx(100 * 2 ** (-300 / 3600))
    assert school.seconds is None
    assert school.index is None
    assert metro.destination.name == "metro 1"
    assert metro.seconds == 180


def test_restricted_walk_rejects_external_targets_and_paths(monkeypatch):
    scope = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
    }
    gate = {"coordinates": [0.7, 0.5]}
    monkeypatch.setattr(
        access, "restricted_walk_areas", lambda _db: [{"geometry": scope, "excluded_gates": [gate]}]
    )
    monkeypatch.setattr(
        access,
        "candidate_rows",
        lambda *_args, **_kwargs: [
            {"id": key, "name": key, "lat": 0.5, "lon": lon, "location": "object", "line": ""}
            for key, lon in (("outside", 1.1), ("detour", 0.9), ("gate", 0.85), ("internal", 0.8))
        ],
    )

    def route(_client, point, destination, gates):
        assert destination.id != "outside"
        assert len(gates) == 1 and gates[0].x == 0.7
        intermediate = {"detour": (1.05, 0.5), "gate": (0.7, 0.5)}.get(destination.id, (0.7, 0.6))
        return (
            240 if destination.id == "internal" else 120,
            [(point.lon, point.lat), intermediate, (destination.lon, destination.lat)],
        )

    monkeypatch.setattr(access, "_walk_route", route)
    (school,) = access.calculate_categories(
        None, None, access.Point(lat=0.5, lon=0.5), ["school"], 1200, geometry=True
    )
    assert school.destination.id == "internal"
    assert school.seconds == 240
    assert school.index == pytest.approx(100 * 2 ** (-240 / 1200))
    assert len(school.path) == 3


@pytest.mark.parametrize(
    ("points", "accepted"),
    [
        ([(0.5, 0.5), (0.9, 0.5), (1.2, 0.5)], True),
        ([(0.5, 0.5), (0.9, 0.5), (1.2, 0.6)], False),
        ([(0.5, 0.5), (0.9, 0.5), (1.1, 0.5), (0.9, 0.6), (1.2, 0.6)], False),
    ],
)
def test_reviewed_hospital_requires_the_checked_exit(monkeypatch, points, accepted):
    entry = {
        "id": "node/1",
        "name": "Hospital",
        "location": "object",
        "coordinates": [1.2, 0.5],
        "pedestrian_exit": {
            "checkpoint_coordinates": [0.9, 0.5],
            "boundary_crossing": [1, 0.5],
        },
    }

    def route(_client, _point, _destination, _gates, *, via):
        assert (via.x, via.y) == (0.9, 0.5)
        return 240, points

    monkeypatch.setattr(access, "_walk_route", route)
    result = access._walk_to_reviewed_hospital(
        None, access.Point(lat=0.5, lon=0.5), entry, box(0, 0, 1, 1), []
    )
    assert (result is not None) is accepted


def test_unmatched_endpoint_keeps_routes_to_other_candidates():
    import json

    def route(request):
        body = json.loads(request.content)
        if request.url.path == "/locate":
            return httpx.Response(200, json=[{"edges": [{}]}, {"edges": None}, {"edges": [{}]}])
        if len(body["targets"]) == 2:
            return httpx.Response(400, json={"error_code": 171})
        return httpx.Response(200, json={"sources_to_targets": [[{"to_index": 0, "time": 240}]]})

    destinations = [
        access.Destination(
            id=f"node/{index}",
            name=str(index),
            lat=55.75,
            lon=37.6 + index / 100,
            location="object",
            line="",
        )
        for index in range(2)
    ]
    with httpx.Client(base_url="http://local", transport=httpx.MockTransport(route)) as client:
        assert access._matrix_times(client, access.Point(lat=55.75, lon=37.6), destinations) == {
            1: 240
        }


@pytest.mark.parametrize(
    "location", [{}, {"lat": 55}, {"address_id": "node/1", "lat": 55, "lon": 37}]
)
def test_address_request_requires_exactly_one_origin(location):
    with pytest.raises(ValidationError):
        access.AddressRequest.model_validate({**location, "categories": ["school"]})


def test_point_stays_at_selected_coordinates_and_uses_published_boundaries(monkeypatch, tmp_path):
    catalogue = tmp_path / "catalog.sqlite"
    catalogue.with_name("localities.json").write_text(
        json.dumps([{"h_seconds": 1200, "geometry": mapping(box(36, 55, 37, 56))}])
    )
    monkeypatch.setattr(access, "catalogue_path", lambda: catalogue)
    monkeypatch.setattr(access, "metadata", lambda _db, _key: "3600")
    monkeypatch.setattr(
        access,
        "district_rows",
        lambda _db: [
            {"id": "oblast-1", "geometry": json.dumps(mapping(box(35, 54, 39, 57)))},
            {"id": "moscow-1", "geometry": json.dumps(mapping(box(37, 55, 38, 56)))},
        ],
    )
    with closing(sqlite3.connect(":memory:")) as db:
        moscow = access.address_at_point(db, 55.5, 37.5)
        assert moscow is not None
        assert (moscow.lat, moscow.lon, moscow.h_seconds) == (55.5, 37.5, 3600)
        oblast = access.address_at_point(db, 55.5, 36.5)
        assert oblast is not None
        assert (oblast.lat, oblast.lon, oblast.h_seconds) == (55.5, 36.5, 1200)
        without_area = access.address_at_point(db, 54.5, 36.5)
        assert without_area is not None and without_area.h_seconds is None
        assert access.address_at_point(db, 59.9, 30.3) is None
