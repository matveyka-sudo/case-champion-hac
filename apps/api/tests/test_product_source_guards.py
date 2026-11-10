"""TEST_ONLY: omission and schedule replay cannot manufacture better accessibility."""

from dataclasses import replace
from datetime import timedelta

import pytest
from a2_api.adapters import product
from a2_api.adapters.product_inputs import UrbanCargoInputs, candidate_is_static
from a2_api.adapters.product_runtime import Profile, validate_inputs
from a2_api.core.registry import ProductRegistry
from a2_contracts.common import canonical_hash
from a2_contracts.v2.requests import DeliveryCargoProfile
from a2_engine import (
    CargoContext,
    CargoSite,
    Timetable,
    TimetableEvent,
    TimetableFootpath,
    TimetableRun,
    UrbanCandidate,
    UrbanCargoCoverage,
    UrbanLeg,
)
from test_product_projection import example
from test_product_runtime import (
    bind_fingerprints,
    claim_for,
    configure,
    request_for,
)
from test_product_runtime import transport_facts as transport_facts


@pytest.mark.parametrize("field", ["eligible", "in_service_scope"])
def test_accepted_destination_eligibility_cannot_remove_closest_object(transport_facts, field):
    snapshot, facts, profile = transport_facts
    facts = facts.model_copy(
        update={"destinations": (replace(facts.destinations[0], **{field: False}),)}
    )
    with pytest.raises(ValueError, match="CANNOT_BE_OVERRIDDEN"):
        validate_inputs(bind_fingerprints(profile, facts), facts, snapshot, ProductRegistry())


def test_static_flag_cannot_retime_scheduled_public_transport(transport_facts):
    snapshot, facts, profile = transport_facts
    at = facts.context.requested_departure_at
    p, d = facts.points[0], facts.destinations[0]
    ride = UrbanLeg(
        "public_transport",
        "ride",
        p.origin_node_id,
        d.physical_node_id,
        at,
        at + timedelta(minutes=10),
        600,
        "TEST_ONLY-dated-service",
        p.source_refs,
    )
    candidate = UrbanCandidate(
        "TEST_ONLY-trip",
        p.point_id,
        d.destination_id,
        "adult_polyclinic",
        "public_transport",
        facts.context,
        p.origin_node_id,
        d.physical_node_id,
        at,
        at + timedelta(minutes=10),
        (ride,),
        p.source_refs,
    )
    assert not candidate_is_static(candidate)
    facts = facts.model_copy(update={"candidates": (candidate,)})
    profile = profile.model_copy(
        update={"passenger_modes": ["public_transport"], "static_observations": True}
    )
    with pytest.raises(ValueError, match="DATED_TIMETABLE_RECOMPUTATION"):
        validate_inputs(bind_fingerprints(profile, facts), facts, snapshot, ProductRegistry())


def test_departure_after_sole_dated_bus_does_not_create_another_run(
    tmp_path, monkeypatch, transport_facts
):
    snapshot, facts, profile = transport_facts
    at = facts.context.requested_departure_at
    p, d = facts.points[0], facts.destinations[0]
    _, seed = example("intercity").demo_inputs()
    refs = p.source_refs
    bus = TimetableRun(
        snapshot.snapshot_id,
        "source-1",
        "TEST_ONLY-sole-bus",
        at.date(),
        "UTC",
        "bus",
        (
            TimetableEvent(
                0,
                p.access_node_id,
                at + timedelta(minutes=5),
                at + timedelta(minutes=5),
                True,
                True,
                refs,
            ),
            TimetableEvent(
                1,
                d.access_node_id,
                at + timedelta(minutes=10),
                at + timedelta(minutes=10),
                True,
                True,
                refs,
            ),
        ),
        True,
        refs,
    )
    scope = replace(
        seed.scope,
        snapshot_id=snapshot.snapshot_id,
        graph_version=snapshot.provenance.graph_version,
        calendar_version=snapshot.provenance.calendar_version,
        start_at=at - timedelta(days=1),
        end_at=at + timedelta(days=7),
        node_ids=frozenset(
            {p.origin_node_id, p.access_node_id, d.access_node_id, d.physical_node_id}
        ),
        schedule_complete=False,
        footpaths_complete=False,
        transfers_complete=False,
        continuations_complete=False,
        evidence_refs=refs,
    )
    table = Timetable(
        scope,
        (bus,),
        (
            TimetableFootpath(
                "TEST_ONLY-entry", p.origin_node_id, p.access_node_id, 60, True, refs
            ),
            TimetableFootpath(
                "TEST_ONLY-exit", d.access_node_id, d.physical_node_id, 60, True, refs
            ),
        ),
        (),
    )
    facts = facts.model_copy(
        update={
            "timetable": table,
            "coverage": tuple(
                replace(c, scope=replace(c.scope, passenger_mode="public_transport"))
                for c in facts.coverage
            ),
        }
    )
    profile = bind_fingerprints(
        profile.model_copy(update={"passenger_modes": ["public_transport"]}), facts
    )
    configure(tmp_path, monkeypatch, (snapshot, facts, profile))
    first = product.calculate(
        claim_for(snapshot, request_for(snapshot, passenger_mode="public_transport"))
    )
    missed = product.calculate(
        claim_for(
            snapshot,
            request_for(
                snapshot, passenger_mode="public_transport", departure_at=at + timedelta(minutes=15)
            ),
        )
    )
    assert first.journeys and first.journeys[0].total_seconds == 660
    assert missed.journeys == []
    assert all(score.value is None for score in missed.scores)


def cargo_inputs(transport_facts):
    snapshot, _, _ = transport_facts
    normalized = example("urban_cargo").PROFILE
    refs = ("e-1",)
    context = CargoContext(
        snapshot.snapshot_id,
        snapshot.provenance.graph_version,
        "TEST_ONLY-build",
        snapshot.provenance.calendar_version,
        "TEST_ONLY-rules",
        "TEST_ONLY-truck-profile",
        "a2-methodology-v3",
    )
    canonical = DeliveryCargoProfile(
        profile_id=normalized.profile_id,
        kind="delivery",
        food_class=normalized.goods,
        urgency=normalized.urgency,
        temperature_requirements=dict(controlled=False, min_c=None, max_c=None),
        handling_requirements=list(normalized.handling_requirements),
        vehicle_constraints=dict(max_height_m=None, max_weight_tonnes=None, restrictions=[]),
    )
    registry = ProductRegistry(cargo_profiles=[canonical])
    facts = UrbanCargoInputs(
        kind="urban_cargo",
        profile=normalized,
        sites=tuple(CargoSite(s.site_id, context, refs) for s in snapshot.freight_sites),
        nodes=(),
        routes=(),
        receipts=(),
        coverage=UrbanCargoCoverage(
            context,
            normalized.requirements_hash,
            "outbound",
            tuple(s.site_id for s in snapshot.freight_sites),
            (),
            False,
            refs,
        ),
        area_km2=80,
    )
    profile = Profile(
        profile_id="TEST_ONLY-cargo",
        analysis_kind="urban_cargo",
        city_id="city-1",
        cargo_profile_id=normalized.profile_id,
        direction="outbound",
        valid_from="2026-11-01T00:00:00Z",
        valid_to="2026-11-30T20:00:00Z",
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=canonical_hash(snapshot.model_dump(mode="json")),
        graph_fingerprint="0" * 64,
        routing_profile_fingerprint="0" * 64,
        max_route_pairs=100,
    )
    return snapshot, facts, profile, registry


@pytest.mark.parametrize("mutation", ["site", "node", "area"])
def test_cargo_cannot_omit_inventory_or_invent_administrative_area(transport_facts, mutation):
    snapshot, facts, profile, registry = cargo_inputs(transport_facts)
    if mutation == "site":
        facts = facts.model_copy(
            update={
                "sites": (),
                "coverage": replace(facts.coverage, expected_site_ids=(), roster_complete=True),
            }
        )
    elif mutation == "node":
        port = snapshot.freight_nodes[0].model_copy(
            update={"freight_node_id": "TEST_ONLY-port", "kind": "port"}
        )
        snapshot = snapshot.model_copy(update={"freight_nodes": [*snapshot.freight_nodes, port]})
        facts = facts.model_copy(
            update={"coverage": replace(facts.coverage, complete_node_types=("port",))}
        )
    else:
        facts = facts.model_copy(update={"area_km2": 99999})
    with pytest.raises(ValueError, match="OMITTED_ACCEPTED|ADMINISTRATIVE_AREA"):
        validate_inputs(bind_fingerprints(profile, facts), facts, snapshot, registry)
