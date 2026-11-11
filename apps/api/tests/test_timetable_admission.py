"""TEST_ONLY: partial timetable admission retains unknown physical boundaries."""

from dataclasses import replace
from datetime import timedelta

import pytest
from a2_api.adapters import product
from a2_api.adapters.product_runtime import load_runtime, validate_inputs
from a2_api.core.errors import DomainError
from a2_api.core.registry import ProductRegistry
from a2_contracts.common import canonical_hash
from a2_engine import (
    Timetable,
    TimetableFootpath,
    UrbanCoverage,
    UrbanSearchScope,
    prepare_walk_inputs,
)
from test_product_projection import example
from test_product_runtime import bind_fingerprints, claim_for, configure, request_for
from test_product_runtime import transport_facts as transport_facts


@pytest.fixture
def partial_runtime(tmp_path, monkeypatch, transport_facts):
    snapshot, facts, profile = transport_facts
    snapshot = type(snapshot).model_validate(
        snapshot.model_dump(mode="json")
        | {
            "sample_points": [
                *snapshot.model_dump(mode="json")["sample_points"],
                snapshot.sample_points[0].model_dump(mode="json")
                | {"point_id": "TEST_ONLY-unknown-point", "cell_id": "TEST_ONLY-cell"},
            ],
            "population_cells": [
                *snapshot.model_dump(mode="json")["population_cells"],
                snapshot.population_cells[0].model_dump(mode="json")
                | {"cell_id": "TEST_ONLY-cell"},
            ],
            "evidence": [
                *snapshot.model_dump(mode="json")["evidence"],
                snapshot.evidence[0].model_dump(mode="json")
                | {"evidence_id": "TEST_ONLY-unused-proof"},
            ],
        }
    )
    config = ProductRegistry()
    raw = prepare_walk_inputs(
        snapshot.model_dump(mode="json"),
        city_id=profile.city_id,
        departure_at=facts.context.requested_departure_at,
        methodology_version=config.methodology.version,
        config_hash=config.config_hash,
        search_parameters_id="TEST_ONLY-search",
    )
    facts = facts.model_copy(
        update={
            "points": raw.points,
            "population_coverage": raw.population_coverage,
            "coverage": tuple(
                UrbanCoverage(
                    UrbanSearchScope(
                        raw.context, point.point_id, "adult_polyclinic", "walk", 14400
                    ),
                    False,
                    False,
                    point.source_refs,
                    frozenset(),
                )
                for point in raw.points
            ),
        }
    )
    profile = bind_fingerprints(
        profile.model_copy(
            update={"snapshot_hash": canonical_hash(snapshot.model_dump(mode="json"))}
        ),
        facts,
    )
    configure(tmp_path, monkeypatch, (snapshot, facts, profile))
    runtime = load_runtime()
    point, destination = facts.points[0], facts.destinations[0]
    _, seed = example("intercity").demo_inputs()
    scope = replace(
        seed.scope,
        snapshot_id=snapshot.snapshot_id,
        graph_version=snapshot.provenance.graph_version,
        calendar_version=snapshot.provenance.calendar_version,
        start_at=facts.context.requested_departure_at - timedelta(days=8),
        end_at=facts.context.requested_departure_at + timedelta(days=21),
        node_ids=frozenset(
            {
                point.origin_node_id,
                point.access_node_id,
                destination.access_node_id,
                destination.physical_node_id,
            }
        ),
        evidence_refs=point.source_refs,
        schedule_complete=False,
        calendar_complete=False,
        footpaths_complete=False,
        transfers_complete=False,
        continuations_complete=False,
    )
    facts = facts.model_copy(
        update={
            "timetable": Timetable(scope, (), (), ()),
            "timetable_service_clock": "local_service_midnight_v1",
            "coverage": tuple(
                replace(row, scope=replace(row.scope, passenger_mode="public_transport"))
                for row in facts.coverage
            ),
        }
    )
    profile = bind_fingerprints(
        profile.model_copy(
            update={
                "passenger_modes": ["public_transport"],
                "max_route_pairs": 1,
            }
        ),
        facts,
    )
    validate_inputs(profile, facts, snapshot, ProductRegistry())
    runtime.profiles[profile.profile_id] = profile, facts
    monkeypatch.setattr(product, "load_runtime", lambda **kwargs: runtime)
    return runtime, snapshot, facts, profile


def test_only_addressable_searches_consume_budget_without_dropping_catalog(partial_runtime):
    runtime, snapshot, facts, profile = partial_runtime
    request = request_for(snapshot, passenger_mode="public_transport")
    assert len(facts.points) * len(facts.destinations) > profile.max_route_pairs
    assert runtime.select(request, snapshot) == (profile, facts)
    result = product.calculate(claim_for(snapshot, request))
    assert "PHYSICAL_BOUNDARY_CONNECTOR_UNAVAILABLE" in result.limitations
    assert {score.subject.point_id for score in result.scores if score.name == "point_index"} == {
        point.point_id for point in facts.points
    }
    assert all(score.value is None and not score.ranking_eligible for score in result.scores)
    assert all(
        metric.value is None
        for metric in result.metrics
        if metric.name == "minimum_journey_seconds"
    )


def test_every_addressable_physical_pair_still_obeys_limit(partial_runtime):
    runtime, snapshot, facts, profile = partial_runtime
    table = facts.timetable
    nodes = table.scope.node_ids | {point.origin_node_id for point in facts.points}
    facts = facts.model_copy(
        update={"timetable": replace(table, scope=replace(table.scope, node_ids=nodes))}
    )
    runtime.profiles[profile.profile_id] = profile, facts
    with pytest.raises(DomainError, match="APPROVED_ROUTE_PAIR_BUDGET_EXCEEDED"):
        runtime.select(request_for(snapshot, passenger_mode="public_transport"), snapshot)


def test_one_destination_in_two_categories_consumes_two_actual_searches(partial_runtime):
    runtime, snapshot, facts, profile = partial_runtime
    destination = replace(
        facts.destinations[0], categories=frozenset({"adult_polyclinic", "school"})
    )
    facts = facts.model_copy(update={"destinations": (destination,)})
    profile = profile.model_copy(update={"categories": ["adult_polyclinic", "school"]})
    runtime.profiles[profile.profile_id] = profile, facts
    request = request_for(
        snapshot,
        passenger_mode="public_transport",
        categories=["adult_polyclinic", "school"],
        priorities={"adult_polyclinic": "high", "school": "medium"},
    )
    with pytest.raises(DomainError, match="APPROVED_ROUTE_PAIR_BUDGET_EXCEEDED"):
        runtime.select(request, snapshot)


def test_physical_boundary_cannot_point_to_a_different_access_node(partial_runtime):
    _, snapshot, facts, profile = partial_runtime
    point = facts.points[0]
    wrong = facts.destinations[0].access_node_id
    assert wrong != point.access_node_id
    table = replace(
        facts.timetable,
        footpaths=(
            TimetableFootpath(
                "TEST_ONLY-wrong-access",
                point.origin_node_id,
                wrong,
                1,
                True,
                point.source_refs,
            ),
        ),
    )
    facts = facts.model_copy(update={"timetable": table})
    with pytest.raises(ValueError, match="PHYSICAL_ACCESS_LINK_MISMATCH"):
        validate_inputs(bind_fingerprints(profile, facts), facts, snapshot, ProductRegistry())


def test_dated_result_prunes_only_unused_definitions_after_projection(partial_runtime):
    runtime, snapshot, facts, profile = partial_runtime
    # Canonical source evidence remains unchanged; only the saved-result
    # definition table is reduced once every public result edge exists.
    before = snapshot.model_dump(mode="json")
    result = product.calculate(
        claim_for(snapshot, request_for(snapshot, passenger_mode="public_transport"))
    )
    assert snapshot.model_dump(mode="json") == before
    assert len(result.evidence_refs) < len(snapshot.evidence)
    assert result.result_hash == canonical_hash(
        result.model_dump(mode="json", exclude={"result_hash"})
    )
