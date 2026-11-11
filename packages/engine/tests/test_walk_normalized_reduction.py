"""TEST_ONLY: chunk normalization preserves the original complete reduction."""

import json
from dataclasses import replace
from typing import TypedDict

import pytest
from a2_engine import (
    UrbanConfig,
    UrbanJourneyRules,
    WalkRoutePlan,
    analyze_normalized_walk_routes,
    analyze_walk_responses,
    load_urban_config,
    load_urban_journey_rules,
    normalize_walk_response,
)
from test_walk_integration import FIXTURE, plan, response, synthetic_inputs


class ReductionOptions(TypedDict):
    selected_categories: dict[str, str]
    config: UrbanConfig
    rules: UrbanJourneyRules


def inputs() -> tuple[WalkRoutePlan, ReductionOptions]:
    route_plan = plan(synthetic_inputs(json.loads(FIXTURE.read_text())))
    kwargs: ReductionOptions = {
        "selected_categories": {"airport": "high"},
        "config": load_urban_config(),
        "rules": load_urban_journey_rules(),
    }
    return route_plan, kwargs


@pytest.mark.parametrize("case", ["complete", "missing", "empty", "timeout", "unreachable"])
def test_chunked_normalization_matches_envelope_entry_point_exactly(case: str) -> None:
    route_plan, kwargs = inputs()
    envelopes = [response(request) for request in route_plan.requests]
    if case == "missing":
        envelopes = envelopes[:1]
    elif case == "empty":
        envelopes = []
    elif case == "timeout":
        envelopes[0] = replace(envelopes[0], outcome="timeout", response_json=None)
    elif case == "unreachable":
        envelopes[0] = replace(
            envelopes[0],
            response_json=json.dumps({"id": envelopes[0].request_id, "error_code": 442}),
            unreachable_evidence_refs=("TEST_ONLY_closed_network",),
        )
    requests = {request.request_id: request for request in route_plan.requests}
    normalized = [normalize_walk_response(requests[item.request_id], item) for item in envelopes]
    if case == "unreachable":
        assert normalized[0].state == "confirmed_unreachable"
    elif case == "timeout":
        assert normalized[0].state == "unknown"
    # The worker may finish checkpoints in a different order. Reduction remains
    # in plan order, and missing/unknown observations keep their original states.
    result = analyze_normalized_walk_routes(route_plan, list(reversed(normalized)), **kwargs)
    assert result == analyze_walk_responses(route_plan, envelopes, **kwargs)


@pytest.mark.parametrize(
    "attack", ["duplicate", "foreign", "same_id_changed_request", "categories"]
)
def test_normalized_routes_remain_bound_to_the_exact_request_plan(attack: str) -> None:
    route_plan, kwargs = inputs()
    request = route_plan.requests[0]
    route = normalize_walk_response(request, response(request))
    routes = [route]
    if attack == "duplicate":
        routes.append(route)
    elif attack == "foreign":
        routes[0] = replace(route, request=replace(request, request_id="TEST_ONLY_foreign"))
    elif attack == "same_id_changed_request":
        routes[0] = replace(route, request=replace(request, payload_json='{"changed":true}'))
    else:
        kwargs["selected_categories"] = {"school": "high"}
    with pytest.raises(
        ValueError,
        match="DUPLICATE_OR_UNKNOWN_NORMALIZED_ROUTE|NORMALIZED_ROUTE_PLAN_MISMATCH|PLAN_CATEGORY_SELECTION_MISMATCH",
    ):
        analyze_normalized_walk_routes(route_plan, routes, **kwargs)
