"""TEST_ONLY: expanded wire budget requires frozen, explicitly admitted DEMO scope."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from a2_api.core.jobs import ClaimedJob, V2JobContext
from a2_api.core.scenario_scope import build_scenario_endpoint_scope
from a2_api.workers import calculation
from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.scenarios import ScenarioResult, ScenarioRunRequest
from pydantic import ValidationError
from test_product_scenarios import make_case, with_claim
from test_worker_transport import Capture

LABELS = sorted(calculation._DEMO_LABELS)


def synthetic_demo_claim():
    claim = with_claim(make_case())[0]
    values = claim.model_dump(mode="json")
    context = values["context"]
    snapshot_id = "demo-moscow-TEST_ONLY-transport"
    context["snapshot_id"] = context["versions"]["snapshot_id"] = snapshot_id
    context["snapshot_hash"] = "0" * 64
    context["normalized_request"].update(snapshot_id=snapshot_id, city_id="moscow")
    proposal = context["scenario_proposal"]
    proposal["versions"]["snapshot_id"] = snapshot_id
    proposal["assumptions"] = LABELS
    proposal["limitations"] = sorted(set(proposal["limitations"]) | set(LABELS))
    context["scenario_endpoint_scope"] = {
        "snapshot_id": snapshot_id,
        "snapshot_hash": context["snapshot_hash"],
        "city_id": "moscow",
        "point_origins": {"p1": "physical:point:p1"},
        "point_access_nodes": {"p1": "a1"},
        "destination_nodes": {"d1": "physical:destination:d1"},
        "destination_access_nodes": {"d1": "a2"},
        "destination_categories": {"d1": ["adult_polyclinic"]},
    }
    values["job"]["bundle_id"] = snapshot_id
    return ClaimedJob.model_validate(values)


def test_only_frozen_admitted_demo_scenario_gets_32mib(monkeypatch):
    monkeypatch.setenv("A2_DEMO_TRANSPORT_ENABLED", "true")
    claim = synthetic_demo_claim()
    assert calculation._result_byte_limit(claim) == 32 * 1024 * 1024
    assert calculation._result_byte_limit(with_claim(make_case())[0]) == 16 * 1024 * 1024
    context = claim.context
    assert isinstance(context, V2JobContext)
    for changes in ({"scenario_endpoint_scope": None}, {"scenario_proposal": None}):
        altered = claim.model_copy(update={"context": context.model_copy(update=changes)})
        assert calculation._result_byte_limit(altered) == 16 * 1024 * 1024
    for key in ("assumptions", "limitations"):
        altered = context.scenario_proposal.model_copy(update={key: []})
        copied = claim.model_copy(
            update={"context": context.model_copy(update={"scenario_proposal": altered})}
        )
        assert calculation._result_byte_limit(copied) == 16 * 1024 * 1024
    assert (
        calculation._result_byte_limit(claim.model_copy(update={"kind": "analysis"}))
        == 16 * 1024 * 1024
    )
    monkeypatch.setenv("A2_DEMO_TRANSPORT_ENABLED", "false")
    assert calculation._result_byte_limit(claim) == 16 * 1024 * 1024


@pytest.mark.parametrize("city", ["kaliningrad", "novorossiysk"])
def test_frozen_demo_ipc_allowance_uses_matching_scope_in_each_city(monkeypatch, city):
    monkeypatch.setenv("A2_DEMO_TRANSPORT_ENABLED", "true")
    values = synthetic_demo_claim().model_dump(mode="json")
    context = values["context"]
    identity = "demo-" + city + "-TEST_ONLY"
    context["snapshot_id"] = context["versions"]["snapshot_id"] = identity
    context["normalized_request"].update(city_id=city, snapshot_id=identity)
    context["scenario_endpoint_scope"].update(city_id=city, snapshot_id=identity)
    context["scenario_proposal"]["versions"]["snapshot_id"] = identity
    values["job"]["bundle_id"] = identity
    claim = ClaimedJob.model_validate(values)
    assert calculation._result_byte_limit(claim) == calculation.MAX_DEMO_SCENARIO_BYTES
    for key, change in [("city_id", "foreign-city"), ("snapshot_id", "foreign-snapshot")]:
        request = claim.context.normalized_request.model_copy(update={key: change})
        mutated = claim.model_copy(
            update={"context": claim.context.model_copy(update={"normalized_request": request})}
        )
        assert calculation._result_byte_limit(mutated) == calculation.MAX_RESULT_BYTES
    monkeypatch.delenv("A2_DEMO_TRANSPORT_ENABLED")
    assert calculation._result_byte_limit(claim) == 16 * 1024 * 1024


def actual_case():
    root = Path(
        os.environ.get(
            "A2_TEST_DEMO_SCENARIO_RUNTIME",
            "/Users/kirito/waydex-full-runtime/demo-moscow-transport-v4",
        )
    )
    if not (root / "verified-scenario-result.json").is_file():
        pytest.skip(
            "Restore v4 DEMO and set A2_TEST_DEMO_SCENARIO_RUNTIME for actual22.65MB roundtrip"
        )
    snapshot = AcceptedData.model_validate_json((root / "accepted-data.json").read_text())
    context = V2JobContext.model_validate_json(
        (root / "offline-scenario-job-context.json").read_text()
    )
    context = V2JobContext.model_validate(
        context.model_dump(mode="json")
        | {
            "scenario_endpoint_scope": build_scenario_endpoint_scope(
                snapshot, context.normalized_request
            ).model_dump(mode="json")
        }
    )
    proposal = context.scenario_proposal
    base = synthetic_demo_claim()
    claim = base.model_copy(
        update={
            "context": context,
            "request": ScenarioRunRequest(
                schema_version="2",
                scenario_id=proposal.scenario_id,
                baseline_analysis_id=context.analysis_id,
            ),
        }
    )
    return claim, ScenarioResult.model_validate_json(
        (root / "verified-scenario-result.json").read_text()
    )


def test_actual_scenario_roundtrip_exact_and_required_null_is_not_omitted(monkeypatch):
    monkeypatch.setenv("A2_DEMO_TRANSPORT_ENABLED", "true")
    claim, result = actual_case()
    calculation._validate_extended_result(claim, result)
    capture = Capture()
    monkeypatch.setattr(
        calculation.importlib, "import_module", lambda _: SimpleNamespace(run=lambda _: result)
    )
    calculation._child(capture, "TEST_ONLY:run", claim.model_dump_json(), os.getppid())
    assert calculation.MAX_RESULT_BYTES < len(capture.value) <= calculation.MAX_DEMO_SCENARIO_BYTES
    payload = json.loads(capture.value)
    restored = ScenarioResult.model_validate(payload["result"])
    assert restored == result
    assert (
        canonical_hash(restored.model_dump(mode="json", exclude={"result_hash"}))
        == result.result_hash
    )
    del payload["result"]["after"]["metrics"][0]["reason_code"]
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(payload["result"])
    for key in ("assumptions", "limitations", "baseline_result_hash"):
        with pytest.raises(ValueError, match="Extended DEMO"):
            calculation._validate_extended_result(
                claim,
                result.model_copy(
                    update={key: [] if key != "baseline_result_hash" else "TEST_ONLY-wrong"}
                ),
            )


def test_child_rejects_more_than_32mib_and_ordinary_cap_stays_16mib(monkeypatch):
    monkeypatch.setenv("A2_DEMO_TRANSPORT_ENABLED", "true")
    claim, result = actual_case()
    oversized = result.model_copy(
        update={
            "assumptions": [
                *result.assumptions,
                "TEST_ONLY:" + "x" * calculation.MAX_DEMO_SCENARIO_BYTES,
            ]
        }
    )
    monkeypatch.setattr(
        calculation.importlib, "import_module", lambda _: SimpleNamespace(run=lambda _: oversized)
    )
    capture = Capture()
    calculation._child(capture, "TEST_ONLY:run", claim.model_dump_json(), os.getppid())
    assert json.loads(capture.value)["error"]["code"] == "engine_unavailable"
    assert len(capture.value) < 1024
    monkeypatch.setenv("A2_DEMO_TRANSPORT_ENABLED", "false")
    monkeypatch.setattr(
        calculation.importlib, "import_module", lambda _: SimpleNamespace(run=lambda _: result)
    )
    capture = Capture()
    calculation._child(capture, "TEST_ONLY:run", claim.model_dump_json(), os.getppid())
    assert json.loads(capture.value)["error"]["code"] == "engine_unavailable"
