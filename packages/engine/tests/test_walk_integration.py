"""Real ML2 geography is separate from explicitly synthetic route attestations."""

import copy
import json
import math
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from a2_engine import (
    UrbanDestination,
    UrbanThreshold,
    ValhallaBuild,
    ValhallaCorrelation,
    WalkConnector,
    WalkCoordinate,
    WalkResponseEnvelope,
    analyze_walk_responses,
    load_urban_config,
    load_urban_journey_rules,
    normalize_walk_response,
    plan_walk_routes,
    prepare_walk_inputs,
)
from a2_engine.urban.valhalla import digest
from a2_engine.urban.valhalla.codec import (
    ROUTE_ECHO_TRUNC6_EVIDENCE_REF,
    _valhalla351_route_echo,
)
from a2_engine.urban.walk import WalkInputs, WalkRoutePlan
from a2_engine.urban.walk.observed import assess_observed_walk_route

FIXTURE = Path(__file__).parent / "fixtures" / "kaliningrad_walk_canonical_subset.json"
DEPARTURE = datetime(2026, 11, 8, 12, tzinfo=UTC)


@pytest.fixture
def canonical() -> dict[str, Any]:
    value: dict[str, Any] = json.loads(FIXTURE.read_text())
    return value


def prepare(raw: dict[str, Any], **kwargs: Any) -> WalkInputs:
    return prepare_walk_inputs(
        raw,
        city_id="kaliningrad",
        departure_at=DEPARTURE,
        methodology_version=load_urban_config().methodology_version,
        config_hash="synthetic-config",
        search_parameters_id="test-search",
        **kwargs,
    )


def synthetic_inputs(raw: dict[str, Any]) -> WalkInputs:
    """Keep real geography; all build/connector/search proofs are TEST ONLY."""
    build = ValhallaBuild(
        raw["snapshot_id"],
        raw["provenance"]["graph_version"],
        "synthetic-build",
        "synthetic-valhalla-tiles",
        "3.5.1",
        "test-profile-v1",
        '{"walking_speed":5.1,"type":"foot","shortest":false}',
        ("synthetic-build-proof",),
    )
    base = prepare(raw)
    connectors = []
    correlations = []
    seen = set()
    for ep in (*base.point_endpoints, *base.destination_endpoints):
        seconds = 60 if ep in base.point_endpoints else 120
        connectors.append(
            WalkConnector(
                raw["snapshot_id"],
                base.source_graph_version,
                ep.physical_node_id,
                ep.access_node_id,
                ep.location,
                ep.access_location,
                seconds,
                True,
                "synthetic-measured-seconds-v1",
                ("synthetic-connector-proof",),
            )
        )
        if ep.access_node_id not in seen:
            correlations.append(
                ValhallaCorrelation(
                    build,
                    ep.access_node_id,
                    ep.access_location,
                    ep.access_location,
                    "synthetic-correlation:" + ep.access_node_id,
                    ("synthetic-correlation-proof",),
                )
            )
            seen.add(ep.access_node_id)
    return prepare(raw, build=build, connectors=connectors, correlations=correlations)


def plan(inputs: WalkInputs) -> WalkRoutePlan:
    return plan_walk_routes(
        inputs, point_ids=[p.point_id for p in inputs.points], categories=["airport"]
    )


def response(request: Any, **changes: Any) -> WalkResponseEnvelope:
    summary = {"time": 1620, "length": 2.1, "has_ferry": False}
    body = {
        "id": request.request_id,
        "trip": {
            "status": 0,
            "units": "kilometers",
            "locations": [
                request.origin.correlated_location.location(),
                request.destination.correlated_location.location(),
            ],
            "summary": summary,
            "legs": [
                {
                    "summary": summary,
                    "maneuvers": [
                        {
                            "type": 1,
                            "time": 1620,
                            "length": 2.1,
                            "travel_mode": "pedestrian",
                            "travel_type": "foot",
                        }
                    ],
                }
            ],
        },
    }
    result = WalkResponseEnvelope(
        request.request_id,
        request.point_id,
        request.destination_id,
        request.build,
        digest(request.payload()),
        request.origin.correlation_id,
        request.destination.correlation_id,
        "response",
        json.dumps(body),
        ("synthetic-response-sha256",),
        True,
        ("synthetic-min-time-audit",),
        (),
        ("synthetic-no-ferry-edge-audit",),
    )
    result = replace(
        result,
        matched_origin=request.origin.correlated_location,
        matched_destination=request.destination.correlated_location,
        endpoint_evidence_refs=("synthetic-actual-correlation-proof",),
    )
    return replace(result, **changes)


def mutate(envelope: WalkResponseEnvelope, action: Any) -> WalkResponseEnvelope:
    body = json.loads(envelope.response_json or "{}")
    action(body)
    return replace(envelope, response_json=json.dumps(body))


def analyze(p: WalkRoutePlan, envelopes: Any = None, **kwargs: Any) -> Any:
    return analyze_walk_responses(
        p,
        [response(r) for r in p.requests] if envelopes is None else envelopes,
        selected_categories={"airport": "high"},
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
        **kwargs,
    )


def test_real_canonical_payload_readiness(canonical: dict[str, Any]) -> None:
    original = copy.deepcopy(canonical)
    inputs = prepare(canonical)
    assert canonical == original
    assert len(inputs.points) == 2 and len(inputs.destinations) == 1
    assert inputs.context.versions.graph_version == canonical["provenance"]["graph_version"]
    assert inputs.context.versions.calendar_version == "not_collected"
    assert (
        inputs.population_coverage.excluded_population
        and inputs.population_coverage.excluded_population > 0
    )
    first = next(
        p for p in inputs.points if p.point_id == canonical["sample_points"][0]["point_id"]
    )
    assert first.access_distance_m == 12.53
    assert inputs.point_endpoints[0].location != inputs.point_endpoints[0].access_location
    p = plan(inputs)
    assert not p.requests and len(p.issues) == 2
    assert all("CONNECTOR_UNCONFIRMED" in issue.reasons for issue in p.issues)
    result = analyze(p, [])
    assert result.analysis.h_seconds.value == 1800
    assert result.analysis.modes[0].index.status == "unknown"


def test_full_time_counts_both_connectors_once(canonical: dict[str, Any]) -> None:
    inputs = synthetic_inputs(canonical)
    p = plan(inputs)
    result = analyze(p)
    assert len(p.requests) == 2 and not p.issues
    for journey in result.analysis.journeys:
        assert [leg.duration_seconds for leg in journey.candidate.legs] == [60, 1620, 120]
        assert journey.total_seconds == 1800
        assert journey.candidate.geometry is None
        assert journey.candidate.origin_node_id.startswith("physical:point:")
        assert journey.candidate.destination_node_id.startswith("physical:destination:")
    assert result.analysis.h_seconds.value == 1800
    assert all(point.index.value == 50 for point in result.analysis.modes[0].points)
    assert all(
        point.categories[0].count.reason_code == "TAU_UNAVAILABLE"
        for point in result.analysis.modes[0].points
    )
    assert not result.analysis.modes[0].ranking_eligible  # Real excluded population retained.


def test_observed_estimate_is_separate_from_unproven_category_index(
    canonical: dict[str, Any],
) -> None:
    inputs = synthetic_inputs(canonical)
    request = plan(inputs).requests[0]
    envelope = response(request, search_complete=False, minimum_time_evidence_refs=())
    route = normalize_walk_response(request, envelope)
    result = assess_observed_walk_route(
        inputs,
        route,
        category="airport",
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
    )
    assert not route.minimum_proven
    assert result.observed_total_seconds == 60 + 1620 + 120
    assert result.h_seconds.value == 1800
    assert result.observed_route_accessibility.value == 50
    assert result.observed_route_accessibility.reason_code == "MINIMUM_NOT_PROVEN"
    assert result.canonical_category_index.value is None
    assert result.canonical_category_index.reason_code == "MINIMUM_NOT_PROVEN"
    assert not result.minimum_proven and not result.ranking_eligible
    assert not result.certified_index_lower_bound and result.completeness == "partial"
    assert result.projection_offset_seconds == 0
    assert "SINGLE_FOUND_OBJECT_NOT_CATEGORY_MINIMUM" in result.limitations
    assert "REPORTED_DURATION_NOT_CERTIFIED_UPPER_BOUND" in result.limitations
    assert analyze(plan(inputs), [envelope]).analysis.modes[0].points[0].index.value is None


@pytest.mark.parametrize("change", ["missing", "passability", "seconds", "zero"])
def test_observed_estimate_never_fills_an_unknown_physical_approach(
    canonical: dict[str, Any], change: str
) -> None:
    inputs = synthetic_inputs(canonical)
    request = plan(inputs).requests[0]
    route = normalize_walk_response(request, response(request))
    endpoint = inputs.destination_endpoints[0]
    assert endpoint.connector
    changes: dict[str, Any] = {
        "passability": {"traversable": None},
        "seconds": {"seconds": None},
        "zero": {"seconds": 0},
    }
    connector = None if change == "missing" else replace(endpoint.connector, **changes[change])
    inputs = replace(inputs, destination_endpoints=(replace(endpoint, connector=connector),))
    result = assess_observed_walk_route(
        inputs,
        route,
        category="airport",
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
    )
    assert result.observed_total_seconds is None
    assert result.observed_route_accessibility.value is None
    assert result.projection_offset_seconds is None


def test_observed_quantized_endpoint_is_an_uncertain_estimate_not_a_zero_offset(
    canonical: dict[str, Any],
) -> None:
    inputs = synthetic_inputs(canonical)

    def quantized(endpoint: Any) -> Any:
        correlation = endpoint.correlation
        assert correlation
        return replace(
            endpoint,
            correlation=replace(
                correlation,
                correlated_location=WalkCoordinate(
                    round(correlation.source_location.lon, 6),
                    round(correlation.source_location.lat, 6),
                ),
                coordinate_precision_digits=6,
                serialization_evidence_refs=("synthetic-pinned-serialization-audit",),
            ),
        )

    inputs = replace(
        inputs,
        point_endpoints=tuple(quantized(ep) for ep in inputs.point_endpoints),
        destination_endpoints=tuple(quantized(ep) for ep in inputs.destination_endpoints),
    )
    request = plan(inputs).requests[0]
    route = normalize_walk_response(request, response(request))
    result = assess_observed_walk_route(
        inputs,
        route,
        category="airport",
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
    )
    assert result.observed_route_accessibility.value == 50
    assert result.projection_offset_seconds is None
    assert not result.complete_physical_time_confirmed
    assert not result.certified_index_lower_bound
    assert "COORDINATE_QUANTIZATION_UNCERTAINTY" in result.limitations
    assert "PHYSICAL_CORRELATION_OFFSET_UNMEASURED" in result.limitations


@pytest.mark.parametrize("eligible", [False, None])
def test_observed_estimate_requires_an_accepted_eligible_object(
    canonical: dict[str, Any], eligible: bool | None
) -> None:
    inputs = synthetic_inputs(canonical)
    request = plan(inputs).requests[0]
    route = normalize_walk_response(request, response(request))
    inputs = replace(inputs, destinations=(replace(inputs.destinations[0], eligible=eligible),))
    result = assess_observed_walk_route(
        inputs,
        route,
        category="airport",
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
    )
    assert result.observed_route_accessibility.value is None
    assert result.observed_route_accessibility.reason_code == "DESTINATION_ELIGIBILITY_UNCONFIRMED"


def test_observed_estimate_rejects_foreign_request_and_preserves_city_h(
    canonical: dict[str, Any],
) -> None:
    inputs = synthetic_inputs(canonical)
    request = plan(inputs).requests[0]
    route = normalize_walk_response(request, response(request))
    inputs = replace(inputs, area=replace(inputs.area, area_km2=2562.625))
    result = assess_observed_walk_route(
        inputs,
        route,
        category="airport",
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
    )
    assert result.h_seconds.value == 3600
    assert result.observed_route_accessibility.value == 100 * 2 ** (-1800 / 3600)
    with pytest.raises(ValueError, match="OBSERVED_ROUTE_REQUEST_SOURCE_MISMATCH"):
        assess_observed_walk_route(
            inputs,
            replace(route, request=replace(request, search_parameters_id="foreign-search")),
            category="airport",
            config=load_urban_config(),
            rules=load_urban_journey_rules(),
        )


def test_legacy_destination_constructor_remains_valid() -> None:
    d = UrbanDestination(
        "d", "object", frozenset({"airport"}), "access", "city", True, True, ("proof",)
    )
    assert d.physical_node_id is None
    assert replace(d, physical_node_id="physical:d").access_node_id == "access"


def test_missing_final_connector_cannot_pass_existing_analysis(canonical: dict[str, Any]) -> None:
    from a2_engine.urban.analysis import analyze_urban_candidates

    result = analyze(plan(synthetic_inputs(canonical)))
    original = result.analysis
    candidate = original.journeys[0].candidate
    with pytest.raises(ValueError):
        analyze_urban_candidates(
            original.context,
            [p.point for p in original.modes[0].points],
            result.plan.inputs.destinations,
            [replace(candidate, legs=candidate.legs[:-1])],
            [],
            area=original.area,
            selected_categories=original.selected_categories,
            modes=("walk",),
            population_coverage=original.population_coverage,
            config=load_urban_config(),
            rules=load_urban_journey_rules(),
        )


def test_unknown_connector_is_not_zero(canonical: dict[str, Any]) -> None:
    inputs = synthetic_inputs(canonical)
    endpoint = inputs.destination_endpoints[0]
    assert endpoint.connector
    inputs = replace(
        inputs,
        destination_endpoints=(
            replace(endpoint, connector=replace(endpoint.connector, seconds=None)),
        ),
    )
    p = plan(inputs)
    assert not p.requests and len(p.issues) == 2
    assert not analyze(p, []).analysis.journeys


@pytest.mark.parametrize(
    "field",
    [
        "point_id",
        "destination_id",
        "request_id",
        "request_payload_hash",
        "origin_correlation_id",
        "destination_correlation_id",
    ],
)
def test_foreign_pair_direction_and_request_are_rejected(
    canonical: dict[str, Any], field: str
) -> None:
    request = plan(synthetic_inputs(canonical)).requests[0]
    with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
        changes: dict[str, Any] = {field: "foreign"}
        normalize_walk_response(request, replace(response(request), **changes))


@pytest.mark.parametrize(
    "field",
    [
        "snapshot_id",
        "source_graph_version",
        "graph_version",
        "build_id",
        "profile_version",
        "pedestrian_options_json",
    ],
)
def test_foreign_versions_and_profiles_rejected(canonical: dict[str, Any], field: str) -> None:
    request = plan(synthetic_inputs(canonical)).requests[0]
    value = '{"walking_speed":4}' if field == "pedestrian_options_json" else "foreign"
    changes: dict[str, Any] = {field: value}
    envelope = response(request, build=replace(request.build, **changes))
    with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
        normalize_walk_response(request, envelope)


def test_payload_coords_are_named_and_directional(canonical: dict[str, Any]) -> None:
    request = plan(synthetic_inputs(canonical)).requests[0]
    data = request.payload()
    assert data["locations"][0]["lon"] == request.origin.source_location.lon
    assert data["locations"][0]["lat"] == request.origin.source_location.lat
    assert data["costing"] == "pedestrian" and data["units"] == "kilometers"
    assert "date_time" not in data  # static, never current server time.
    bad = mutate(response(request), lambda b: b["trip"]["locations"].reverse())
    with pytest.raises(ValueError, match="ENDPOINT_MISMATCH"):
        normalize_walk_response(request, bad)


@pytest.mark.parametrize("units,multiplier", [("kilometers", 1000), ("miles", 1609.344)])
def test_documented_units_are_converted(
    canonical: dict[str, Any], units: str, multiplier: float
) -> None:
    request = plan(synthetic_inputs(canonical)).requests[0]
    e = mutate(response(request), lambda b: b["trip"].update(units=units))
    result = normalize_walk_response(request, e)
    assert result.network_seconds == 1620
    assert result.network_distance_m == pytest.approx(2.1 * multiplier)


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), True, "1620"])
def test_invalid_elapsed_time_rejected(canonical: dict[str, Any], bad: Any) -> None:
    request = plan(synthetic_inputs(canonical)).requests[0]
    e = mutate(response(request), lambda b: b["trip"]["summary"].update(time=bad))
    with pytest.raises(ValueError):
        normalize_walk_response(request, e)


@pytest.mark.parametrize("outcome", ["timeout", "http_error", "limit", "no_correlation"])
def test_provider_failures_are_unknown(canonical: dict[str, Any], outcome: Any) -> None:
    request = plan(synthetic_inputs(canonical)).requests[0]
    result = normalize_walk_response(request, response(request, outcome=outcome))
    assert result.state == "unknown" and result.network_seconds is None


def test_no_path_code_needs_independent_complete_proof(canonical: dict[str, Any]) -> None:
    request = plan(synthetic_inputs(canonical)).requests[0]
    e = response(request, response_json=json.dumps({"id": request.request_id, "error_code": 442}))
    assert normalize_walk_response(request, e).state == "unknown"
    assert (
        normalize_walk_response(
            request, replace(e, unreachable_evidence_refs=("test-exhaustion",))
        ).state
        == "confirmed_unreachable"
    )
    assert (
        normalize_walk_response(
            request,
            replace(e, search_complete=False, unreachable_evidence_refs=("test-exhaustion",)),
        ).state
        == "unknown"
    )


def test_response_does_not_prove_minimum_by_itself(canonical: dict[str, Any]) -> None:
    p = plan(synthetic_inputs(canonical))
    e = [response(r, minimum_time_evidence_refs=()) for r in p.requests]
    result = analyze(p, e)
    assert all(r.network_seconds == 1620 and not r.minimum_proven for r in result.routes)
    assert all(
        point.categories[0].best is not None and point.index.status == "unknown"
        for point in result.analysis.modes[0].points
    )


def test_partial_catalog_stays_partial(canonical: dict[str, Any]) -> None:
    raw = copy.deepcopy(canonical)
    for scope in raw["coverage_by_mode_date_category"]:
        scope["completeness"] = "partial"
    result = analyze(plan(synthetic_inputs(raw)))
    assert all(
        p.categories[0].best and p.index.status == "unknown"
        for p in result.analysis.modes[0].points
    )


@pytest.mark.parametrize(
    "mode,kind,ferry",
    [("drive", "car", False), ("transit", "ferry", True), ("pedestrian", "foot", True)],
)
def test_non_walk_paths_rejected(
    canonical: dict[str, Any], mode: str, kind: str, ferry: bool
) -> None:
    request = plan(synthetic_inputs(canonical)).requests[0]
    e = mutate(
        response(request),
        lambda b: b["trip"]["legs"][0]["maneuvers"][0].update(
            travel_mode=mode, travel_type=kind, ferry=ferry
        ),
    )
    assert normalize_walk_response(request, e).state == "unknown"
    assert (
        normalize_walk_response(request, response(request, pure_walk_evidence_refs=())).state
        == "unknown"
    )


def encode_shape(points: list[tuple[float, float]]) -> str:
    encoded = ""
    previous = (0, 0)
    for lon, lat in points:
        current = (round(lat * 1e6), round(lon * 1e6))
        for value, old in zip(current, previous, strict=True):
            delta = value - old
            delta = ~(delta << 1) if delta < 0 else delta << 1
            while delta >= 32:
                encoded += chr((32 | (delta & 31)) + 63)
                delta >>= 5
            encoded += chr(delta + 63)
        previous = current
    return encoded


def test_network_geometry_is_validated_but_never_claimed_full(canonical: dict[str, Any]) -> None:
    p = plan(synthetic_inputs(canonical))
    envelopes = []
    for r in p.requests:
        coords = [
            (c.correlated_location.lon, c.correlated_location.lat)
            for c in (r.origin, r.destination)
        ]
        envelopes.append(
            mutate(response(r), lambda b: b["trip"]["legs"][0].update(shape=encode_shape(coords)))
        )
    result = analyze(p, envelopes)
    assert all(r.network_geometry and r.network_geometry.evidence_refs for r in result.routes)
    assert all(j.candidate.geometry is None for j in result.analysis.journeys)
    bad = mutate(envelopes[0], lambda b: b["trip"]["legs"][0].update(shape="!"))
    with pytest.raises(ValueError):
        normalize_walk_response(p.requests[0], bad)


def test_shared_physical_object_does_not_inflate_count(canonical: dict[str, Any]) -> None:
    raw = copy.deepcopy(canonical)
    raw["destinations"].append(dict(raw["destinations"][0], destination_id="test-second-entrance"))
    p = plan(synthetic_inputs(raw))
    context = p.inputs.context
    threshold = UrbanThreshold(
        context.city_id,
        "walk",
        context.versions,
        "static",
        context.search_parameters_id,
        1800,
        ("synthetic-tau",),
    )
    result = analyze(p, thresholds=(threshold,))
    assert len(p.requests) == 4
    assert all(point.categories[0].count.value == 1 for point in result.analysis.modes[0].points)


def test_missing_response_cannot_complete_search(canonical: dict[str, Any]) -> None:
    p = plan(synthetic_inputs(canonical))
    result = analyze(p, [response(p.requests[0])])
    assert sum(point.index.status == "unknown" for point in result.analysis.modes[0].points) == 1
    with pytest.raises(ValueError):
        analyze(p, [response(p.requests[0])] * 2)


def test_inputs_and_replay_are_deterministic(canonical: dict[str, Any]) -> None:
    original = copy.deepcopy(canonical)
    first = plan(synthetic_inputs(canonical))
    canonical["sample_points"].reverse()
    second = plan(synthetic_inputs(canonical))
    assert first.requests == second.requests
    responses = [response(r) for r in first.requests]
    saved = copy.deepcopy(responses)
    assert analyze(first, responses) == analyze(first, list(reversed(responses)))
    assert responses == saved
    canonical["sample_points"].reverse()
    assert canonical == original


def test_binding_and_correlation_must_match_actual_source(canonical: dict[str, Any]) -> None:
    inputs = synthetic_inputs(canonical)
    assert inputs.build
    assert inputs.context.versions.graph_version == inputs.build.graph_version
    assert inputs.source_graph_version != inputs.context.versions.graph_version
    with pytest.raises(ValueError, match="BUILD_SOURCE_MISMATCH"):
        prepare(canonical, build=replace(inputs.build, snapshot_id="foreign"))
    ep = inputs.point_endpoints[0]
    assert ep.correlation
    with pytest.raises(ValueError, match="REVISED_ACCESS_CONNECTOR_REQUIRED"):
        replace(ep.correlation, correlated_location=WalkCoordinate(20, 54))


def test_invalid_coordinates_and_profile() -> None:
    for coord in [(True, 54), (20, float("nan")), (181, 54), (20, 91)]:
        with pytest.raises(ValueError):
            WalkCoordinate(*coord)
    with pytest.raises(ValueError):
        ValhallaBuild(
            "s", "source", "build", "graph", "3.5.1", "p", '{"walking_speed":0}', ("proof",)
        )


def test_actual_correlation_is_distinct_from_response_location_echo(
    canonical: dict[str, Any],
) -> None:
    request = plan(synthetic_inputs(canonical)).requests[0]
    assert (
        normalize_walk_response(request, response(request, matched_origin=None)).state == "unknown"
    )
    with pytest.raises(ValueError, match="ACTUAL_ENDPOINT_MISMATCH"):
        normalize_walk_response(request, response(request, matched_origin=WalkCoordinate(20, 54)))


@pytest.mark.parametrize(
    "mutation",
    ["leg_time", "leg_length", "missing_leg", "units", "warnings", "malformed_json", "missing_id"],
)
def test_incomplete_inconsistent_or_clamped_response_is_unknown(
    canonical: dict[str, Any], mutation: str
) -> None:
    request = plan(synthetic_inputs(canonical)).requests[0]
    envelope = response(request)
    body = json.loads(envelope.response_json or "{}")
    if mutation == "leg_time":
        body["trip"]["legs"][0]["summary"]["time"] = 1
    if mutation == "leg_length":
        body["trip"]["legs"][0]["summary"]["length"] = 1
    if mutation == "missing_leg":
        body["trip"]["legs"] = []
    if mutation == "units":
        body["trip"]["units"] = "seconds"
    if mutation == "warnings":
        body["trip"]["warnings"] = [{"code": 1}]
    if mutation == "missing_id":
        body.pop("id")
    envelope = replace(
        envelope, response_json="{" if mutation == "malformed_json" else json.dumps(body)
    )
    assert normalize_walk_response(request, envelope).state == "unknown"


def test_cost_and_recosting_do_not_replace_elapsed_seconds(canonical: dict[str, Any]) -> None:
    request = plan(synthetic_inputs(canonical)).requests[0]
    envelope = mutate(
        response(request), lambda b: b["trip"]["summary"].update(cost=9000, time_other=1)
    )
    result = normalize_walk_response(request, envelope)
    assert result.network_seconds == 1620
    assert result.response_hash is not None and len(result.response_hash) == 64
    assert not normalize_walk_response(
        request, replace(envelope, minimum_time_evidence_refs=())
    ).minimum_proven


def test_fractional_durations_normalize_at_timestamp_boundary(canonical: dict[str, Any]) -> None:
    base = synthetic_inputs(canonical)
    endpoints = (*base.point_endpoints, *base.destination_endpoints)
    connectors = [
        replace(ep.connector, seconds=60.123456789 if ep in base.point_endpoints else 120.345678912)
        for ep in endpoints
        if ep.connector
    ]
    correlations = tuple(
        {
            ep.correlation.access_node_id: ep.correlation for ep in endpoints if ep.correlation
        }.values()
    )
    inputs = prepare(canonical, build=base.build, connectors=connectors, correlations=correlations)
    p = plan(inputs)
    envelopes = [
        mutate(
            response(r),
            lambda b: (
                b["trip"]["summary"].update(time=1620.234567891),
                b["trip"]["legs"][0]["summary"].update(time=1620.234567891),
            ),
        )
        for r in p.requests
    ]
    result = analyze(p, envelopes)
    assert inputs.point_endpoints[0].connector == connectors[0]
    assert connectors[0].seconds == 60.123456789
    assert result.routes[0].network_seconds == 1620.234567891
    assert inputs.destination_endpoints[0].connector
    assert inputs.destination_endpoints[0].connector.seconds == 120.345678912
    for timing in result.analysis.journeys:
        candidate = timing.candidate
        assert [leg.duration_seconds for leg in candidate.legs] == [
            60.123457,
            1620.234568,
            120.345679,
        ]
        assert timing.total_seconds == pytest.approx(
            (candidate.arrival_at - candidate.departure_at).total_seconds(), abs=1e-12
        )
        for before, after in zip(candidate.legs, candidate.legs[1:]):
            assert before.end_at == after.start_at and before.end_at.tzinfo == UTC
        assert all(
            leg.duration_seconds == (leg.end_at - leg.start_at).total_seconds()
            for leg in candidate.legs
        )
    assert all(point.index.status == "available" for point in result.analysis.modes[0].points)


@pytest.mark.parametrize("part", ["origin", "network", "destination"])
def test_positive_submicrosecond_duration_never_silently_becomes_zero(
    canonical: dict[str, Any], part: str
) -> None:
    inputs = synthetic_inputs(canonical)
    if part != "network":
        field = "point_endpoints" if part == "origin" else "destination_endpoints"
        endpoints = getattr(inputs, field)
        endpoint = endpoints[0]
        changes: dict[str, Any] = {
            field: (
                replace(endpoint, connector=replace(endpoint.connector, seconds=1e-7)),
                *endpoints[1:],
            )
        }
        inputs = replace(inputs, **changes)
    p = plan(inputs)
    envelopes = [response(r) for r in p.requests]
    if part == "network":
        envelopes = [
            mutate(
                e,
                lambda b: (
                    b["trip"]["summary"].update(time=1e-7),
                    b["trip"]["legs"][0]["summary"].update(time=1e-7),
                ),
            )
            for e in envelopes
        ]
    with pytest.raises(ValueError, match="WALK_DURATION_BELOW_TIMESTAMP_RESOLUTION"):
        analyze(p, envelopes)


def test_explicit_six_decimal_serialization_preserves_physical_connectors(
    canonical: dict[str, Any],
) -> None:
    base = synthetic_inputs(canonical)
    assert base.build is not None
    endpoints = (*base.point_endpoints, *base.destination_endpoints)
    connectors = tuple(ep.connector for ep in endpoints if ep.connector)
    original = {ep.correlation.access_node_id: ep.correlation for ep in endpoints if ep.correlation}
    correlations = tuple(
        replace(
            correlation,
            correlated_location=WalkCoordinate(
                round(correlation.source_location.lon, 6),
                round(correlation.source_location.lat, 6),
            ),
            coordinate_precision_digits=6,
            serialization_evidence_refs=("synthetic-pinned-locate-serializer",),
        )
        for correlation in original.values()
    )
    inputs = prepare(canonical, build=base.build, connectors=connectors, correlations=correlations)
    assert "ENDPOINT_SERIALIZATION_PRECISION_6" in inputs.limitations
    p = plan(inputs)
    for request in p.requests:
        assert request.payload()["locations"][0]["lon"] == request.origin.source_location.lon
        assert "synthetic-pinned-locate-serializer" in request.evidence_refs
    result = analyze(p)
    assert all(r.state == "reachable" and not r.minimum_proven for r in result.routes)
    assert all(r.reason_code == "MINIMUM_TIME_UNPROVEN" for r in result.routes)
    assert not result.analysis.modes[0].ranking_eligible
    assert result.analysis.modes[0].index.value is None
    assert result.analysis.journeys
    for journey in result.analysis.journeys:
        assert [leg.duration_seconds for leg in journey.candidate.legs] == [60, 1620, 120]
        assert journey.total_seconds == 1800
    with pytest.raises(ValueError, match="SOURCE_MISMATCH"):
        prepare(canonical, build=replace(base.build, source_graph_version="foreign"))
    request = p.requests[0]
    with pytest.raises(ValueError, match="ACTUAL_ENDPOINT_MISMATCH"):
        normalize_walk_response(
            request, response(request, matched_origin=request.origin.source_location)
        )
    no_route = response(
        request,
        response_json=json.dumps({"id": request.request_id, "error_code": 442}),
        unreachable_evidence_refs=("synthetic-global-no-route-proof",),
    )
    assert normalize_walk_response(request, no_route).state == "unknown"


@pytest.mark.parametrize(
    "changes",
    [
        {"coordinate_precision_digits": 6},
        {"coordinate_precision_digits": 5, "serialization_evidence_refs": ("proof",)},
        {"coordinate_precision_digits": True, "serialization_evidence_refs": ("proof",)},
    ],
)
def test_coordinate_precision_requires_supported_explicit_evidence(
    canonical: dict[str, Any], changes: dict[str, Any]
) -> None:
    correlation = plan(synthetic_inputs(canonical)).requests[0].origin
    with pytest.raises(ValueError, match="SERIALIZATION_EVIDENCE_REQUIRED"):
        replace(correlation, **changes)


def test_serialization_uncertainty_does_not_accept_real_resnap(canonical: dict[str, Any]) -> None:
    correlation = plan(synthetic_inputs(canonical)).requests[0].origin
    source = WalkCoordinate(20.4955871, 54.7087877)
    rendered = WalkCoordinate(20.495587, 54.708788)
    with pytest.raises(ValueError, match="REVISED_ACCESS_CONNECTOR_REQUIRED"):
        replace(correlation, source_location=source, correlated_location=rendered)
    precise = replace(
        correlation,
        source_location=source,
        correlated_location=rendered,
        coordinate_precision_digits=6,
        serialization_evidence_refs=("synthetic-pinned-serializer",),
    )
    assert precise.correlated_location == rendered and precise.source_location == source
    for shifted in (WalkCoordinate(20.495588, 54.708788), WalkCoordinate(20.4955871, 54.708788)):
        with pytest.raises(ValueError, match="REVISED_ACCESS_CONNECTOR_REQUIRED"):
            replace(precise, correlated_location=shifted)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (37.7356494, 37.735649),
        (55.6927005, 55.6927),
        (37.7196439, 37.719643),
        (55.6916808, 55.69168),
        (-37.7196439, -37.719643),
        (-55.6916808, -55.69168),
        (1.000001, None),
        (-1.000001, None),
        (-47.204542, None),
        (0.000000125, 0.000000125),
        (-0.000000125, -0.000000125),
        (0.00000001, 0.0),
        (-0.00000001, -0.0),
        (-0.0, -0.0),
    ],
)
def test_pinned_route_writer_decimal_truncation_and_signed_zero(
    source: float, expected: float | None
) -> None:
    # Pinned writer expectations; ambiguous six-place boundary inputs fail closed.
    actual = _valhalla351_route_echo(source)
    assert actual == expected
    if expected == 0:
        assert actual is not None
        assert math.copysign(1, actual) == math.copysign(1, expected)


def test_gp109_echo_requires_exact_versioned_evidence_and_keeps_minimum_unknown(
    canonical: dict[str, Any],
) -> None:
    request = plan(synthetic_inputs(canonical)).requests[0]

    def correlation(original: ValhallaCorrelation, lon: float, lat: float) -> ValhallaCorrelation:
        return replace(
            original,
            source_location=WalkCoordinate(lon, lat),
            correlated_location=WalkCoordinate(round(lon, 6), round(lat, 6)),
            coordinate_precision_digits=6,
            serialization_evidence_refs=("synthetic-locate-source", ROUTE_ECHO_TRUNC6_EVIDENCE_REF),
        )

    request = replace(
        request,
        origin=correlation(request.origin, 37.7356494, 55.6927005),
        destination=correlation(request.destination, 37.7196439, 55.6916808),
    )
    payload = request.payload()
    for row, correlated in zip(
        payload["locations"], (request.origin, request.destination), strict=True
    ):
        row.update(correlated.source_location.location())
    request = replace(request, payload_json=json.dumps(payload, separators=(",", ":")))
    envelope = response(request)
    body = json.loads(envelope.response_json or "{}")
    body["trip"]["locations"] = [
        {"lon": 37.735649, "lat": 55.6927},
        {"lon": 37.719643, "lat": 55.69168},
    ]
    envelope = replace(envelope, response_json=json.dumps(body))
    observed = normalize_walk_response(request, envelope)
    assert observed.state == "reachable" and observed.minimum_proven is False
    assert observed.reason_code == "MINIMUM_TIME_UNPROVEN"
    assert request.payload()["locations"][1]["lon"] == 37.7196439
    assert request.destination.source_location == WalkCoordinate(37.7196439, 55.6916808)
    assert request.destination.correlated_location == WalkCoordinate(37.719644, 55.691681)
    for marker in (
        "synthetic-locate-source",
        "valhalla-3.5.1-route-echo-trunc5-v1",
        "valhalla-3.5.2-route-echo-trunc6-v1",
    ):
        unapproved = replace(
            request, destination=replace(request.destination, serialization_evidence_refs=(marker,))
        )
        assert not unapproved.destination.matches_response_location(
            WalkCoordinate(37.719643, 55.69168)
        )
    for wrong in (
        WalkCoordinate(37.719642, 55.69168),
        WalkCoordinate(37.71964, 55.69168),
        WalkCoordinate(37.719643, 55.691681),
    ):
        assert not request.destination.matches_response_location(wrong)
    with pytest.raises(ValueError, match="ACTUAL_ENDPOINT_MISMATCH"):
        normalize_walk_response(
            request, replace(envelope, matched_destination=request.destination.source_location)
        )
    with pytest.raises(ValueError, match="UNSUPPORTED_VALHALLA_VERSION"):
        replace(request.build, engine_version="3.5.2")
    with pytest.raises(ValueError, match="SERIALIZATION_EVIDENCE_REQUIRED"):
        replace(request.destination, coordinate_precision_digits=5)
    boundary = correlation(request.destination, -47.204542, 55.6916808)
    # Native Grisu2 can serialize this exact six-place source below its decimal
    # boundary. Do not invent either new mixed-coordinate echo as a fallback.
    assert not boundary.matches_response_location(WalkCoordinate(-47.204542, 55.69168))
    assert not boundary.matches_response_location(WalkCoordinate(-47.204541, 55.69168))
    assert boundary.matches_response_location(boundary.source_location)
    assert boundary.matches_response_location(boundary.correlated_location)


def test_missing_route_for_positive_population_never_renormalizes_known_points(
    canonical: dict[str, Any],
) -> None:
    # SYNTHETIC coverage and weights isolate route uncertainty from real excluded residents.
    inputs = synthetic_inputs(canonical)
    inputs = replace(
        inputs,
        points=tuple(
            replace(p, population=100, population_year=2026, population_method="synthetic")
            for p in inputs.points
        ),
        population_coverage=replace(
            inputs.population_coverage, complete=True, excluded_population=0
        ),
    )
    p = plan(inputs)
    assert len(p.requests) == 2
    result = analyze(p, [response(p.requests[0])])
    territory = result.analysis.modes[0]
    assert sorted(point.index.status for point in territory.points) == ["available", "unknown"]
    assert territory.index.value is None and territory.index.status == "unknown"
    assert not territory.ranking_eligible and territory.completeness == "partial"


@pytest.mark.parametrize(
    "changes",
    [{"matched_origin": None}, {"matched_destination": None}, {"endpoint_evidence_refs": ()}],
)
def test_successful_route_with_missing_actual_endpoint_remains_unknown(
    canonical: dict[str, Any], changes: dict[str, Any]
) -> None:
    p = plan(synthetic_inputs(canonical))
    result = analyze(p, [response(r, **changes) for r in p.requests])
    assert all(r.state == "unknown" and not r.minimum_proven for r in result.routes)
    assert all(r.reason_code == "ACTUAL_ENDPOINTS_UNCONFIRMED" for r in result.routes)
    assert all(point.index.value is None for point in result.analysis.modes[0].points)
