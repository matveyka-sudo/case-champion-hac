"""TEST_ONLY route responses over accepted geography; no external routing calls."""

import json
from dataclasses import replace

import pytest
from a2_api.adapters.product_projection import Projection, project_urban
from a2_api.adapters.walk_map import attach_walk_map
from a2_api.core.registry import ProductRegistry
from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.journeys import LineGeometry
from a2_contracts.v2.results import AnalysisResult, EvidenceRef
from a2_engine import (
    UrbanGeometry,
    WalkResponseEnvelope,
    analyze_walk_responses,
    load_urban_config,
    load_urban_journey_rules,
    plan_walk_routes,
    prepare_walk_inputs,
    scenario_hash,
)
from test_walk_pilot import POINT, SCHOOL, claim_for, route_body
from test_walk_pilot import configured as configured


def polyline(coordinates):
    encoded, previous = [], [0, 0]
    for lon, lat in coordinates:
        for index, number in enumerate((lat, lon)):
            value = round(number * 1e6)
            difference = value - previous[index]
            previous[index] = value
            delta = ~(difference << 1) if difference < 0 else difference << 1
            while delta >= 32:
                encoded.append(chr((32 | (delta & 31)) + 63))
                delta >>= 5
            encoded.append(chr(delta + 63))
    return "".join(encoded)


def calculate(configured, *, shape=True, unknown_population=False):
    from a2_api.adapters.walk_pilot import load_runtime

    snapshot = configured[2]
    if unknown_population:
        payload = snapshot.model_dump(mode="json")
        payload["population_cells"][0].update(
            population=None, population_year=None, population_method=None
        )
        snapshot = AcceptedData.model_validate(payload)
    runtime = load_runtime()
    context = claim_for(snapshot).context
    current = ProductRegistry()
    inputs = prepare_walk_inputs(
        snapshot.model_dump(mode="json"),
        city_id=context.normalized_request.city_id,
        departure_at=context.normalized_request.departure_at,
        methodology_version=current.methodology.version,
        config_hash=current.config_hash,
        search_parameters_id="TEST_ONLY-map",
        build=runtime.build,
        connectors=runtime.connectors,
        correlations=runtime.correlations,
    )
    plan = plan_walk_routes(inputs, point_ids=[POINT], categories=["school"])
    request = next(row for row in plan.requests if row.destination_id == SCHOOL)
    body = route_body(request.payload())
    if shape:
        body["trip"]["legs"][0]["shape"] = polyline(
            [
                (request.origin.correlated_location.lon, request.origin.correlated_location.lat),
                (
                    request.destination.correlated_location.lon,
                    request.destination.correlated_location.lat,
                ),
            ]
        )
    envelope = WalkResponseEnvelope(
        request.request_id,
        request.point_id,
        request.destination_id,
        request.build,
        request.payload_hash,
        request.origin.correlation_id,
        request.destination.correlation_id,
        "response",
        json.dumps(body),
        ("TEST_ONLY-response-proof",),
        pure_walk_evidence_refs=("TEST_ONLY-response-proof",),
        matched_origin=request.origin.correlated_location,
        matched_destination=request.destination.correlated_location,
        endpoint_evidence_refs=("TEST_ONLY-response-proof",),
    )
    calculated = analyze_walk_responses(
        plan,
        [envelope],
        selected_categories={"school": "medium"},
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
    )
    evidence = [
        EvidenceRef(
            evidence_id=row.evidence_id,
            source_id=row.source_id,
            url=str(row.url),
            retrieved_at=row.retrieved_at,
            observed_at=None,
            section="accepted-snapshot",
        )
        for row in snapshot.evidence
    ]
    evidence.extend(runtime.evidence)
    evidence.append(evidence[-1].model_copy(update={"evidence_id": "TEST_ONLY-response-proof"}))
    output = Projection(context, evidence)
    project_urban(output, calculated.analysis)
    return snapshot, calculated, output.finish(), {SCHOOL: "TEST_ONLY accepted school name"}


def test_all_canonical_points_and_original_population_remain_visible(configured):
    snapshot, calculated, before, names = calculate(configured, unknown_population=True)
    original = before.model_dump_json(), snapshot.model_dump_json(), scenario_hash(calculated)
    result = attach_walk_map(before, snapshot, calculated, names)
    points = {
        row.entity_id: row for row in result.map_data.features if row.entity_kind == "sample_point"
    }
    assert set(points) == {
        row.point_id for row in snapshot.sample_points if row.territory_id == before.request.city_id
    }
    assert len(points) > len(calculated.plan.point_ids)
    cells = {row.cell_id: row for row in snapshot.population_cells}
    for point in snapshot.sample_points:
        if point.point_id in points:
            assert points[point.point_id].population == cells[point.cell_id].population
    assert any(
        row.population is None for row in result.map_data.features if row.entity_kind == "cell"
    )
    assert all(
        row.geometry is None for row in result.map_data.features if row.entity_kind == "cell"
    )
    assert all(
        row.geometry.type == "Point" for row in result.map_data.features if row.geometry is not None
    )
    destinations = [row for row in result.map_data.features if row.entity_kind == "destination"]
    assert {row.entity_id for row in destinations} == {
        row.destination_id
        for row in snapshot.destinations
        if row.territory_id == before.request.city_id and "school" in row.categories
    }
    assert next(row.name for row in destinations if row.entity_id == SCHOOL) == names[SCHOOL]
    assert all(row.name is None for row in destinations if row.entity_id not in names)
    assert result.metrics == before.metrics and result.scores == before.scores
    assert all(row.value is None for row in result.scores)
    assert (
        before.model_dump_json(),
        snapshot.model_dump_json(),
        scenario_hash(calculated),
    ) == original
    assert result.result_hash == canonical_hash(
        result.model_dump(mode="json", exclude={"result_hash"})
    )
    assert (
        attach_walk_map(result, snapshot, calculated, names).model_dump_json()
        == result.model_dump_json()
    )


def test_only_network_leg_gets_actual_geometry_without_approach_or_gap_join(configured):
    snapshot, calculated, before, names = calculate(configured)
    result = attach_walk_map(before, snapshot, calculated, names)
    assert result.journeys
    for previous, journey in zip(before.journeys, result.journeys, strict=True):
        assert journey.geometry is None and journey.geometry_kind == "none"
        assert journey.model_dump(exclude={"legs"}) == previous.model_dump(exclude={"legs"})
        assert [leg.geometry is not None for leg in journey.legs] == [False, True, False]
        assert journey.legs[1].geometry.coordinates == list(
            calculated.routes[0].network_geometry.coordinates
        )
        assert all(
            leg.model_dump(exclude={"geometry"}) == old.model_dump(exclude={"geometry"})
            for old, leg in zip(previous.legs, journey.legs, strict=True)
        )
    assert AnalysisResult.model_validate_json(result.model_dump_json()) == result


def test_absent_shape_remains_absent_and_existing_whole_geometry_is_preserved(configured):
    snapshot, calculated, before, names = calculate(configured, shape=False)
    result = attach_walk_map(before, snapshot, calculated, names)
    assert all(leg.geometry is None for journey in result.journeys for leg in journey.legs)
    snapshot, calculated, before, names = calculate(configured)
    geometry = LineGeometry(
        type="LineString",
        coordinates=list(calculated.routes[0].network_geometry.coordinates),
        evidence_refs=list(calculated.routes[0].network_geometry.evidence_refs),
    )
    before = before.model_copy(
        update={
            "journeys": [
                row.model_copy(update={"geometry": geometry, "geometry_kind": "actual"})
                for row in before.journeys
            ]
        }
    )
    result = attach_walk_map(before, snapshot, calculated, names)
    assert all(
        row.geometry == geometry and row.geometry_kind == "actual" for row in result.journeys
    )


@pytest.mark.parametrize(
    "attack",
    ["request", "point", "destination", "category", "leg", "duration", "evidence", "unresolved"],
)
def test_swapped_route_or_journey_identity_never_attaches_geometry(configured, attack):
    snapshot, calculated, before, names = calculate(configured)
    route = calculated.routes[0]
    if attack in {"request", "point", "destination"}:
        field = {"request": "request_id", "point": "point_id", "destination": "destination_id"}[
            attack
        ]
        calculated = replace(
            calculated,
            routes=(
                replace(route, request=replace(route.request, **{field: "TEST_ONLY-foreign"})),
            ),
        )
    elif attack == "category":
        calculated = replace(calculated, plan=replace(calculated.plan, categories=("airport",)))
    elif attack in {"leg", "duration"}:
        journey = before.journeys[0]
        legs = list(journey.legs)
        legs[1] = legs[1].model_copy(
            update={"evidence_refs": ["TEST_ONLY-response-proof"]}
            if attack == "leg"
            else {"duration_seconds": legs[1].duration_seconds + 1}
        )
        before = before.model_copy(update={"journeys": [journey.model_copy(update={"legs": legs})]})
    else:
        refs = ("TEST_ONLY-response-proof",) if attack == "evidence" else ("TEST_ONLY-unresolved",)
        calculated = replace(
            calculated,
            routes=(
                replace(
                    route, network_geometry=UrbanGeometry(route.network_geometry.coordinates, refs)
                ),
            ),
        )
    with pytest.raises(ValueError):
        attach_walk_map(before, snapshot, calculated, names)


def test_changed_snapshot_population_is_not_silently_projected(configured):
    snapshot, calculated, before, names = calculate(configured)
    cell = snapshot.population_cells[0].model_copy(
        update={
            "population": 999.0,
            "population_year": 2026,
            "population_method": "TEST_ONLY-changed",
        }
    )
    changed = snapshot.model_copy(
        update={"population_cells": [cell, *snapshot.population_cells[1:]]}
    )
    with pytest.raises(ValueError, match="SOURCE_SCOPE"):
        attach_walk_map(before, changed, calculated, names)
