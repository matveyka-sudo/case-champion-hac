"""Synthetic controls for preserving road facts without certifying freight service."""

from dataclasses import replace

import pytest
from a2_api.adapters.cargo_map import accepted_cargo_map
from a2_api.adapters.product_projection import project_urban_cargo
from a2_contracts.v2 import AnalysisResult
from a2_engine import CargoNode, CargoSite, analyze_urban_cargo, load_freight_config
from test_product_projection import example, projection
from test_product_runtime import transport_facts as transport_facts
from test_product_source_guards import cargo_inputs


@pytest.mark.parametrize("compatibility", ["unknown", "forbidden", "confirmed"])
@pytest.mark.parametrize("direction", ["inbound", "outbound"])
def test_road_observation_does_not_become_access_or_delivery_index(compatibility, direction):
    m = example("urban_cargo")
    route, _ = m.truck("site1", "port", 1200, inbound=direction == "inbound")
    route = replace(route, compatibility=replace(route.compatibility, state=compatibility))
    original = m.run_demo()
    analysis = analyze_urban_cargo(
        m.PROFILE,
        m.START,
        (CargoSite("site1", m.CONTEXT, m.PROOF),),
        (CargoNode("port", "port", "port", m.CONTEXT, m.PROOF),),
        (route,),
        (),
        replace(
            original.coverage,
            expected_site_ids=("site1",),
            direction=direction,
            roster_complete=False,
            complete_node_types=(),
        ),
        ("port",),
        100,
        config=load_freight_config(),
    )
    out = projection(
        "urban_cargo",
        m.PROOF,
        city_id="city",
        cargo_profile_id=m.PROFILE.profile_id,
        direction=direction,
    )
    project_urban_cargo(out, analysis)
    result = out.finish()
    observed = [x for x in result.metrics if x.name == "observed_truck_network_seconds"]
    assert len(observed) == 1
    assert observed[0].value == 1140  # approaches are not road movement
    assert observed[0].completeness == "partial"
    assert observed[0].time_kind == "movement"
    assert observed[0].subject.node_id == "port"
    assert observed[0].reason_code == "TRUCK_NETWORK_ONLY_NOT_DELIVERY"
    assert all(s.value is None and not s.ranking_eligible for s in result.scores)
    assert all(observed[0].metric_id != c.id for s in result.scores for c in s.components)
    assert AnalysisResult.model_validate_json(result.model_dump_json()) == result


def test_cargo_map_keeps_real_site_coordinates_without_population(transport_facts):
    snapshot, facts, _, _ = cargo_inputs(transport_facts)
    out = projection(
        "urban_cargo",
        ("e-1",),
        city_id="city-1",
        cargo_profile_id=facts.profile.profile_id,
        direction="outbound",
    )
    request = out.context.normalized_request.model_copy(
        update={"snapshot_id": snapshot.snapshot_id}
    )
    result = accepted_cargo_map(snapshot, request, facts)
    assert result.completeness == "partial"
    assert len(result.features) == len(facts.coverage.expected_site_ids)
    source = {s.site_id: s for s in snapshot.freight_sites}
    for feature in result.features:
        site = source[feature.entity_id]
        assert feature.geometry.coordinates == (site.location.lon, site.location.lat)
        assert feature.population is None
        assert feature.source_refs == sorted(set(site.source_refs))
    with pytest.raises(ValueError, match="SCOPE_MISMATCH"):
        accepted_cargo_map(snapshot, request.model_copy(update={"city_id": "other-city"}), facts)
