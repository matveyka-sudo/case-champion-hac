"""TEST_ONLY closed synthetic schedules; production closure guard stays closed."""

from dataclasses import replace
from datetime import timedelta

import pytest
from a2_api.adapters.demo_transport import (
    DEMO_LIMITATIONS,
    DEMO_SOURCE,
    DemoTransportReceipt,
    verify_demo_model,
)
from a2_api.adapters.timetable_source import service_timestamp, verify_timetable_source
from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData
from a2_engine import (
    Timetable,
    TimetableEvent,
    TimetableFootpath,
    TimetableRun,
    TimetableScope,
    TimetableTransfer,
)
from test_product_runtime import transport_facts as transport_facts

CLOCK = "local_service_midnight_v1"


@pytest.fixture
def demo(transport_facts):
    snapshot = transport_facts[0]
    payload = snapshot.model_dump(mode="json")
    proof = "TEST_ONLY-DEMO-proof"
    payload["snapshot_id"] = "demo-moscow-TEST_ONLY"
    payload["qa"]["checks"].append(DEMO_SOURCE)
    payload["source_versions"][DEMO_SOURCE] = "TEST_ONLY-v1"
    payload["evidence"].append(
        payload["evidence"][0]
        | {
            "evidence_id": proof,
            "source_id": DEMO_SOURCE,
            "source_version": "TEST_ONLY-v1",
            "verified": True,
        }
    )
    payload["scheduled_services"] = [payload["scheduled_services"][0] | {"source_refs": [proof]}]
    snapshot = AcceptedData.model_validate(payload)
    service = snapshot.scheduled_services[0]
    runs = tuple(
        TimetableRun(
            snapshot.snapshot_id,
            DEMO_SOURCE,
            service.trip_id,
            day,
            service.timezone,
            "bus" if service.mode == "public_transport" else service.mode,
            tuple(
                TimetableEvent(
                    stop.sequence,
                    stop.node_id,
                    service_timestamp(day, stop.arrival_seconds, service.timezone, CLOCK),
                    service_timestamp(day, stop.departure_seconds, service.timezone, CLOCK),
                    stop.pickup,
                    stop.dropoff,
                    (proof,),
                )
                for stop in service.stop_times
            ),
            True,
            (proof,),
        )
        for day in service.service_dates
    )
    nodes = frozenset(stop.node_id for stop in service.stop_times)
    first, last = service.stop_times[0].node_id, service.stop_times[-1].node_id
    table = Timetable(
        TimetableScope(
            snapshot.snapshot_id,
            snapshot.provenance.graph_version,
            snapshot.provenance.calendar_version,
            "TEST_ONLY-timetable",
            "TEST_ONLY-footpaths",
            "TEST_ONLY-transfers",
            service.timezone,
            min(run.events[0].arrival_at for run in runs) - timedelta(days=1),
            max(run.events[-1].departure_at for run in runs) + timedelta(days=1),
            nodes,
            True,
            True,
            True,
            True,
            True,
            (proof,),
            (),
        ),
        runs,
        (TimetableFootpath("TEST_ONLY-path", first, last, 30, True, (proof,)),),
        (TimetableTransfer("TEST_ONLY-transfer", first, first, 0, True, (proof,)),),
    )
    receipt = DemoTransportReceipt(
        schema_version="waydex-demo-closed-transport/1",
        snapshot_hash=canonical_hash(snapshot.model_dump(mode="json")),
        model=table,
        limitations=tuple(sorted(DEMO_LIMITATIONS)),
    )
    return snapshot, table, receipt


def test_explicit_exact_demo_receipt_does_not_change_production_guard(demo, monkeypatch):
    snapshot, table, receipt = demo
    monkeypatch.setenv("A2_DEMO_TRANSPORT_ENABLED", "true")
    assert verify_demo_model(snapshot, table, CLOCK, receipt) == receipt
    assert table.scope.limitations == ()
    assert set(receipt.limitations) == DEMO_LIMITATIONS
    assert DemoTransportReceipt.model_validate_json(receipt.model_dump_json()) == receipt
    with pytest.raises(ValueError, match="EXHAUSTIVE_CONNECTOR"):
        verify_timetable_source(snapshot, table, CLOCK)


@pytest.mark.parametrize("flag", [None, "false", "1", "yes"])
def test_demo_disabled_by_default(demo, monkeypatch, flag):
    monkeypatch.delenv("A2_DEMO_TRANSPORT_ENABLED", raising=False)
    if flag is not None:
        monkeypatch.setenv("A2_DEMO_TRANSPORT_ENABLED", flag)
    with pytest.raises(ValueError, match="DEPLOYMENT_DISABLED"):
        verify_demo_model(demo[0], demo[1], CLOCK, demo[2])


@pytest.mark.parametrize(
    "attack",
    [
        "prefix",
        "snapshot",
        "nodes",
        "run",
        "edge",
        "transfer",
        "clock",
        "label",
        "proof",
        "calendar",
        "receipt_labels",
    ],
)
def test_demo_receipt_never_accepts_mutated_or_unlabelled_inventory(demo, monkeypatch, attack):
    snapshot, table, receipt = demo
    monkeypatch.setenv("A2_DEMO_TRANSPORT_ENABLED", "true")
    clock = CLOCK
    if attack == "prefix":
        snapshot = snapshot.model_copy(update={"snapshot_id": "ordinary-moscow-snapshot"})
    elif attack == "snapshot":
        receipt = receipt.model_copy(update={"snapshot_hash": "TEST_ONLY-foreign"})
    elif attack == "nodes":
        table = replace(
            table, scope=replace(table.scope, node_ids=table.scope.node_ids | {"TEST_ONLY-extra"})
        )
    elif attack == "run":
        table = replace(table, runs=())
    elif attack == "edge":
        table = replace(table, footpaths=())
    elif attack == "transfer":
        table = replace(table, transfers=())
    elif attack == "clock":
        clock = None
    elif attack == "label":
        snapshot = snapshot.model_copy(
            update={"qa": snapshot.qa.model_copy(update={"checks": ["TEST_ONLY"]})}
        )
        receipt = receipt.model_copy(
            update={"snapshot_hash": canonical_hash(snapshot.model_dump(mode="json"))}
        )
    elif attack == "proof":
        table = replace(
            table, scope=replace(table.scope, evidence_refs=(snapshot.evidence[0].evidence_id,))
        )
        receipt = receipt.model_copy(update={"model": table})
    elif attack == "calendar":
        table = replace(table, runs=())
        receipt = receipt.model_copy(update={"model": table})
    elif attack == "receipt_labels":
        receipt = receipt.model_copy(update={"limitations": ()})
    with pytest.raises(ValueError):
        verify_demo_model(snapshot, table, clock, receipt)


def test_demo_verifier_preserves_actual_algorithm_limitations(demo, monkeypatch):
    snapshot, table, receipt = demo
    monkeypatch.setenv("A2_DEMO_TRANSPORT_ENABLED", "true")
    table = replace(
        table, scope=replace(table.scope, limitations=("TEST_ONLY_INCOMPLETE_PATH_SEARCH",))
    )
    receipt = receipt.model_copy(update={"model": table})
    original = receipt.model_dump_json()
    assert verify_demo_model(snapshot, table, CLOCK, receipt).model.scope.limitations == (
        "TEST_ONLY_INCOMPLETE_PATH_SEARCH",
    )
    assert receipt.model_dump_json() == original
    assert table.scope.limitations == ("TEST_ONLY_INCOMPLETE_PATH_SEARCH",)


@pytest.mark.parametrize(
    "identity", ["demo-kaliningrad-v1", "demo-novorossiysk-v1", "registered-model-v1"]
)
def test_exact_synthetic_receipt_is_independent_of_city_name(demo, monkeypatch, identity):
    snapshot, table, receipt = demo
    monkeypatch.setenv("A2_DEMO_TRANSPORT_ENABLED", "true")
    snapshot = snapshot.model_copy(update={"snapshot_id": identity})
    table = replace(
        table,
        scope=replace(table.scope, snapshot_id=identity),
        runs=tuple(replace(run, snapshot_id=identity) for run in table.runs),
    )
    receipt = receipt.model_copy(
        update={"snapshot_hash": canonical_hash(snapshot.model_dump(mode="json")), "model": table}
    )
    assert verify_demo_model(snapshot, table, CLOCK, receipt) == receipt


@pytest.mark.parametrize("attack", ["missing_version", "foreign_version", "unverified"])
def test_model_provenance_must_match_the_declared_source_version(demo, monkeypatch, attack):
    snapshot, table, receipt = demo
    monkeypatch.setenv("A2_DEMO_TRANSPORT_ENABLED", "true")
    payload = snapshot.model_dump(mode="json")
    if attack == "missing_version":
        del payload["source_versions"][DEMO_SOURCE]
    elif attack == "foreign_version":
        payload["source_versions"][DEMO_SOURCE] = "foreign-model-version"
    else:
        for row in payload["evidence"]:
            if row["source_id"] == DEMO_SOURCE:
                row["verified"] = False
    snapshot = snapshot.model_copy(
        update={
            "source_versions": payload["source_versions"],
            "evidence": [
                type(snapshot.evidence[0]).model_validate(row) for row in payload["evidence"]
            ],
        }
    )
    receipt = receipt.model_copy(
        update={"snapshot_hash": canonical_hash(snapshot.model_dump(mode="json"))}
    )
    with pytest.raises(ValueError):
        verify_demo_model(snapshot, table, CLOCK, receipt)
