"""Synthetic controls for preserving road facts without certifying freight service."""

from dataclasses import replace

import pytest
from a2_api.adapters.cargo_map import accepted_cargo_map
from a2_api.adapters.product_projection import project_urban_cargo
from a2_contracts.v2 import AnalysisResult
from a2_contracts.v2.evidence import EvidenceResolver
from a2_engine import CargoNode, CargoSite, analyze_urban_cargo, load_freight_config
from test_product_projection import example, projection
from test_product_runtime import transport_facts as transport_facts
from test_product_source_guards import cargo_inputs


def test_large_cargo_catalogue_preserves_all_sources_without_repeating_every_leaf():
    m = example("urban_cargo")
    original = m.run_demo()
    refs = tuple(
        sorted(set(original.evidence_refs) | {f"TEST_ONLY-source-{i}" for i in range(5000)})
    )
    analysis = replace(original, evidence_refs=refs)
    out = projection(
        "urban_cargo",
        refs,
        city_id="city",
        cargo_profile_id=m.PROFILE.profile_id,
        direction="outbound",
    )
    project_urban_cargo(out, analysis)
    result = out.finish()
    resolver = EvidenceResolver(
        (e.evidence_id for e in result.evidence_refs), result.evidence_groups
    )
    assert len(result.evidence_groups) == 2
    for score in result.scores:
        assert set(resolver.expand(score.evidence_refs)) == set(refs)
        assert len(score.evidence_refs) == 2
    assert AnalysisResult.model_validate_json(result.model_dump_json()) == result
    assert [s.value for s in result.scores] == [
        s.value for s in _project_control(original, refs).scores
    ]


def _project_control(analysis, refs):
    out = projection(
        "urban_cargo",
        refs,
        city_id="city",
        cargo_profile_id=analysis.profile.profile_id,
        direction="outbound",
    )
    project_urban_cargo(out, analysis)
    return out.finish()


@pytest.mark.parametrize("compatibility", ["unknown", "forbidden", "confirmed"])
@pytest.mark.parametrize("direction", ["inbound", "outbound"])
@pytest.mark.parametrize("unknown_access", [False, True])
def test_road_observation_does_not_become_access_or_delivery_index(
    compatibility, direction, unknown_access
):
    m = example("urban_cargo")
    route, _ = m.truck("site1", "port", 1200, inbound=direction == "inbound")
    route = replace(route, compatibility=replace(route.compatibility, state=compatibility))
    if unknown_access:
        route = replace(route, origin_access_seconds=None, destination_access_seconds=None)
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


def test_inventory_without_accepted_nodes_returns_unknown_instead_of_failing(
    transport_facts, monkeypatch
):
    from a2_api.adapters import product
    from a2_api.adapters.product_runtime import Manifest, Runtime
    from a2_contracts.v2.requests import UrbanCargoRequest
    from test_product_runtime import claim_for

    snapshot, inputs, profile, registry = cargo_inputs(transport_facts)
    profile = profile.model_copy(update={"static_observations": True})
    runtime = Runtime(
        "d" * 64,
        Manifest(
            schema_version="waydex-product-runtime/1",
            snapshots=[],
            territories=[],
            profiles=[profile],
        ),
        {snapshot.snapshot_id: snapshot},
        {profile.profile_id: (profile, inputs)},
        frozenset(),
    )
    monkeypatch.setattr(product, "load_runtime", lambda: runtime)
    monkeypatch.setenv("A2_CODE_VERSION", "TEST_ONLY-" + runtime.digest)
    request = UrbanCargoRequest(
        schema_version="2",
        analysis_kind="urban_cargo",
        city_id="city-1",
        departure_at=profile.valid_from,
        traffic_mode="static",
        snapshot_id=snapshot.snapshot_id,
        cargo_profile_id=inputs.profile.profile_id,
        direction="outbound",
        methodology=registry.methodology,
    )
    result = product.calculate(claim_for(snapshot, request))
    assert result.map_data.features
    assert result.scores and all(score.value is None for score in result.scores)
    assert not result.journeys


def test_static_road_facts_can_retime_but_minimum_receipts_cannot(transport_facts):
    from a2_api.adapters.product_runtime import Manifest, Runtime
    from a2_api.core.errors import DomainError
    from a2_contracts.v2.requests import UrbanCargoRequest

    snapshot, inputs, profile, _ = cargo_inputs(transport_facts)
    route, receipt = example("urban_cargo").truck("site1", "port", 1200)
    inputs = inputs.model_copy(update={"routes": (route,)})
    profile = profile.model_copy(update={"static_observations": True})
    runtime = Runtime(
        "d" * 64,
        Manifest(
            schema_version="waydex-product-runtime/1",
            snapshots=[],
            territories=[],
            profiles=[profile],
        ),
        {snapshot.snapshot_id: snapshot},
        {profile.profile_id: (profile, inputs)},
        frozenset(),
    )
    request = UrbanCargoRequest(
        schema_version="2",
        analysis_kind="urban_cargo",
        city_id="city-1",
        departure_at=profile.valid_from,
        traffic_mode="static",
        snapshot_id=snapshot.snapshot_id,
        cargo_profile_id=inputs.profile.profile_id,
        direction="outbound",
    )
    assert runtime.select(request, snapshot)[0] == profile
    runtime.profiles[profile.profile_id] = (
        profile,
        inputs.model_copy(update={"receipts": (receipt,)}),
    )
    with pytest.raises(DomainError, match="STATIC_TRUCK"):
        runtime.select(request, snapshot)
