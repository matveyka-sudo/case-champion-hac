"""TEST_ONLY: found-route estimates never replace canonical minimum indices."""

import importlib.util
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from a2_api.adapters.product_projection import Projection, project_urban
from a2_api.adapters.walk_observed import project_observed_walk
from a2_api.core.jobs import V2JobContext
from a2_api.core.registry import ProductRegistry
from a2_contracts.v2.base import VersionRefs
from a2_contracts.v2.requests import UrbanPassengerRequest
from a2_contracts.v2.results import EvidenceRef
from a2_engine import analyze_walk_responses, load_urban_config, load_urban_journey_rules
from a2_engine.scenario.identity import collect_evidence
from a2_engine.urban.walk.observed import assess_observed_walk_route

ROOT = Path(__file__).resolve().parents[3]


def setup(*, network_times=None, failed=False):
    source = ROOT / "packages/engine/tests/test_walk_integration.py"
    spec = importlib.util.spec_from_file_location("TEST_ONLY_walk_observed_fixture", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raw = json.loads(module.FIXTURE.read_text())
    inputs = module.synthetic_inputs(raw)
    config = ProductRegistry()
    context = replace(
        inputs.context, versions=replace(inputs.context.versions, config_hash=config.config_hash)
    )
    inputs = replace(inputs, context=context)
    if network_times is not None:
        destination = inputs.destinations[0]
        endpoint = inputs.destination_endpoints[0]
        other_id = "AAA-TEST_ONLY-second-airport"
        other_physical = "physical:destination:" + other_id
        inputs = replace(
            inputs,
            destinations=(
                *inputs.destinations,
                replace(
                    destination,
                    destination_id=other_id,
                    object_identity=other_id,
                    physical_node_id=other_physical,
                ),
            ),
            destination_endpoints=(
                *inputs.destination_endpoints,
                replace(
                    endpoint,
                    entity_id=other_id,
                    physical_node_id=other_physical,
                    connector=replace(endpoint.connector, physical_node_id=other_physical),
                ),
            ),
        )
    plan = module.plan(inputs)
    envelopes = []
    first_point = plan.requests[0].point_id
    for planned in plan.requests:
        if planned.point_id != first_point:
            continue
        envelope = replace(
            module.response(planned), search_complete=False, minimum_time_evidence_refs=()
        )
        if network_times is not None:
            seconds = network_times[planned.destination_id == "AAA-TEST_ONLY-second-airport"]
            body = json.loads(envelope.response_json)
            body["trip"]["summary"]["time"] = seconds
            body["trip"]["legs"][0]["summary"]["time"] = seconds
            body["trip"]["legs"][0]["maneuvers"][0]["time"] = seconds
            envelope = replace(envelope, response_json=json.dumps(body))
        if failed:
            envelope = replace(
                envelope,
                outcome="http_error",
                response_json=None,
                matched_origin=None,
                matched_destination=None,
                endpoint_evidence_refs=(),
                pure_walk_evidence_refs=(),
            )
        envelopes.append(envelope)
    calculated = analyze_walk_responses(
        plan,
        envelopes,
        selected_categories={"airport": "high"},
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
    )
    request = UrbanPassengerRequest(
        schema_version="2",
        analysis_kind="urban_passenger",
        city_id="kaliningrad",
        snapshot_id=raw["snapshot_id"],
        departure_at=context.requested_departure_at,
        passenger_mode="walk",
        traffic_mode="static",
        categories=["airport"],
        priorities={"airport": "high"},
        methodology=config.methodology,
    )
    job = V2JobContext(
        schema_version="2",
        snapshot_id=raw["snapshot_id"],
        snapshot_hash="TEST_ONLY",
        versions=VersionRefs(
            snapshot_id=raw["snapshot_id"],
            graph_version=inputs.source_graph_version,
            calendar_version=raw["provenance"]["calendar_version"],
            population_version=raw["provenance"]["population_version"],
            methodology_version=config.methodology.version,
            config_hash=config.config_hash,
            code_version="TEST_ONLY",
        ),
        analysis_id="TEST_ONLY",
        normalized_request=request,
        access_scope="TEST_ONLY",
    )
    refs = [
        EvidenceRef(
            evidence_id=identity,
            source_id="TEST_ONLY",
            url="https://example.test/TEST_ONLY",
            retrieved_at=datetime.now(UTC),
            observed_at=None,
            section="TEST_ONLY",
        )
        for identity in collect_evidence(calculated)
    ]
    out = Projection(job, refs)
    project_urban(out, calculated.analysis)
    return out, calculated


def test_separate_estimate_maps_engine_value_and_preserves_unknown_minimum():
    out, calculated = setup()
    canonical = [score.model_dump(mode="json") for score in out.scores]
    assessment = assess_observed_walk_route(
        calculated.plan.inputs,
        calculated.routes[0],
        category="airport",
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
    )
    project_observed_walk(out, calculated)
    result = out.finish()
    assert [score.model_dump(mode="json") for score in result.scores[: len(canonical)]] == canonical
    estimates = [score for score in result.scores if score.name == "observed_route_accessibility"]
    assert len(estimates) == len(calculated.plan.inputs.points) == 2
    known = [score for score in estimates if score.value is not None]
    assert len(known) == 1 and known[0].value == assessment.observed_route_accessibility.value
    assert all(
        score.completeness == "partial" and not score.ranking_eligible for score in estimates
    )
    assert known[0].reason_code == "MINIMUM_NOT_PROVEN"
    assert all(
        score.value is None
        for score in result.scores
        if score.name in {"point_index", "category_index", "territory_index"}
    )
    time = next(
        metric for metric in result.metrics if metric.metric_id == known[0].components[0].id
    )
    assert time.name == "best_observed_time" and time.value == assessment.observed_total_seconds
    assert time.journey_id in {journey.journey_id for journey in result.journeys}
    assert known[0].components[1].id == "urban-h"
    assert "NOT_A_CITY_RANKING" in result.limitations


def test_projection_rejects_changed_source_context():
    out, calculated = setup()
    out.context = out.context.model_copy(update={"snapshot_id": "TEST_ONLY-other-snapshot"})
    with pytest.raises(ValueError, match="SOURCE_CONTEXT"):
        project_observed_walk(out, calculated)


@pytest.mark.parametrize("network_times", [(1620.0, 900.0), (1620.0, 1620.0), (900.0, 1620.0)])
def test_multiple_successful_candidates_use_exact_selected_journey(network_times):
    out, calculated = setup(network_times=network_times)
    before = [score.model_dump(mode="json") for score in out.scores]
    candidates = [
        assess_observed_walk_route(
            calculated.plan.inputs,
            route,
            category="airport",
            config=load_urban_config(),
            rules=load_urban_journey_rules(),
        )
        for route in calculated.routes
    ]
    expected = min(candidates, key=lambda item: (item.observed_total_seconds, item.destination_id))
    project_observed_walk(out, calculated)
    result = out.finish()
    score = next(
        score
        for score in result.scores
        if score.name == "observed_route_accessibility" and score.value is not None
    )
    time = next(metric for metric in result.metrics if metric.metric_id == score.components[0].id)
    journey = next(journey for journey in result.journeys if journey.journey_id == time.journey_id)
    assert score.value == expected.observed_route_accessibility.value
    assert time.value == expected.observed_total_seconds
    assert journey.journey_id == expected.request_id + ":airport"
    assert journey.destination_object_id == expected.destination_id
    assert [score.model_dump(mode="json") for score in result.scores[: len(before)]] == before


def test_unknown_or_disconnected_observation_stays_unknown_without_exception():
    out, calculated = setup(failed=True)
    project_observed_walk(out, calculated)
    result = out.finish()
    estimates = [score for score in result.scores if score.name == "observed_route_accessibility"]
    assert estimates and all(
        score.value is None and score.status == "unknown" for score in estimates
    )
    assert not result.journeys
