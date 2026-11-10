"""TEST_ONLY: canonical dated executions cannot disappear behind closure flags."""

from dataclasses import replace
from datetime import timedelta

import pytest
from a2_api.adapters.product_runtime import validate_inputs
from a2_api.adapters.timetable_source import verify_timetable_source
from a2_api.core.registry import ProductRegistry
from a2_contracts.common import canonical_hash
from a2_engine import Timetable
from test_product_projection import example
from test_product_runtime import bind_fingerprints
from test_product_runtime import transport_facts as transport_facts


@pytest.mark.parametrize("assert_graph_closure", [True, False])
def test_empty_declared_full_timetable_cannot_establish_absence(
    transport_facts, assert_graph_closure
):
    snapshot, facts, profile = transport_facts
    scopes = [
        scope.model_copy(update={"completeness": "full", "modes": ["public_transport"]})
        for scope in snapshot.coverage_by_mode_date_category
    ]
    snapshot = snapshot.model_copy(update={"coverage_by_mode_date_category": scopes})
    _, seed = example("intercity").demo_inputs()
    p, d = facts.points[0], facts.destinations[0]
    table = Timetable(
        replace(
            seed.scope,
            snapshot_id=snapshot.snapshot_id,
            graph_version=snapshot.provenance.graph_version,
            calendar_version=snapshot.provenance.calendar_version,
            start_at=facts.context.requested_departure_at - timedelta(days=8),
            end_at=facts.context.requested_departure_at + timedelta(days=21),
            node_ids=frozenset(
                {p.origin_node_id, p.access_node_id, d.access_node_id, d.physical_node_id}
            ),
            evidence_refs=p.source_refs,
            footpaths_complete=assert_graph_closure,
            transfers_complete=assert_graph_closure,
            continuations_complete=assert_graph_closure,
        ),
        (),
        (),
        (),
    )
    facts = facts.model_copy(
        update={
            "timetable": table,
            "timetable_service_clock": "local_service_midnight_v1",
            "coverage": tuple(
                replace(
                    c,
                    catalog_complete=True,
                    search_complete=False,
                    scope=replace(c.scope, passenger_mode="public_transport"),
                )
                for c in facts.coverage
            ),
        }
    )
    profile = profile.model_copy(
        update={
            "passenger_modes": ["public_transport"],
            "snapshot_hash": canonical_hash(snapshot.model_dump(mode="json")),
        }
    )
    with pytest.raises(ValueError, match="EXHAUSTIVE_CONNECTOR|OMITS_CANONICAL_DATED"):
        validate_inputs(bind_fingerprints(profile, facts), facts, snapshot, ProductRegistry())


def test_partial_materialization_receipt_records_omissions_without_proving_absence(transport_facts):
    snapshot, facts, _ = transport_facts
    _, seed = example("intercity").demo_inputs()
    scope = replace(
        seed.scope,
        snapshot_id=snapshot.snapshot_id,
        graph_version=snapshot.provenance.graph_version,
        calendar_version=snapshot.provenance.calendar_version,
        start_at=facts.context.requested_departure_at - timedelta(days=8),
        end_at=facts.context.requested_departure_at + timedelta(days=21),
        schedule_complete=False,
        footpaths_complete=False,
        transfers_complete=False,
        continuations_complete=False,
    )
    receipt = verify_timetable_source(
        snapshot, Timetable(scope, (), (), ()), "local_service_midnight_v1"
    )
    assert receipt.expected_execution_count > 0
    assert receipt.bindings == [] and not receipt.materialization_complete
