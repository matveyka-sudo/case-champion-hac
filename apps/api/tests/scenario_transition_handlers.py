"""Opt-in synthetic handlers exercising scoped connectivity transitions."""

import os

from a2_api.jobs import CalculationOutput
from a2_contracts.v2.results import AnalysisResult
from a2_contracts.v2.scenarios import ScenarioResult
from test_v2_storage import proposal, scenario_result
from v2_handlers import success


def baseline(claim):
    saved = success(claim)
    payload = saved.model_dump(mode="json")
    first = payload["metrics"][0]
    first["value"] = 30.0
    second = dict(
        first,
        metric_id="B",
        value=60.0,
        subject=dict(
            kind="connection", origin_id="synthetic-A", destination_id="synthetic-B", mode="rail"
        ),
    )
    payload["metrics"] = [first, second]
    payload["scores"] = []
    return AnalysisResult.model_validate(payload)


def baseline_with_proposal(claim):
    saved = baseline(claim)
    return CalculationOutput(analysis=saved, proposals=[proposal(saved)])


def scenario(claim):
    saved = baseline(claim)
    command = claim.context.scenario_proposal
    saved.result_hash = command.baseline_result_hash
    # Build a known valid unchanged task first, then serialize the forged worker output.
    payload = scenario_result(saved, command).model_dump(mode="json")
    first_id = payload["before"]["metrics"][0]["metric_id"]
    payload["after"]["metrics"][0]["value"] = 35.0
    payload["after"]["metrics"][1]["value"] = 40.0
    payload["deltas"][0].update(delta=-5.0, outcome="worsened")
    payload["deltas"].append(
        dict(
            payload["deltas"][0],
            effect_id="B",
            before_id="B",
            after_id="B",
            delta=20.0,
            outcome="improved",
        )
    )
    payload["worsened_metric_ids"] = [first_id]
    mode = os.environ.get("A2_SYNTHETIC_TRANSITION", "control")
    if mode == "deleted":
        payload["after"]["metrics"].pop(0)
    elif mode in ("no-route", "loss", "unknown"):
        payload["after"]["metrics"][0].update(
            value=None,
            status="unknown" if mode == "unknown" else "not_applicable",
            reason_code="DATA_UNAVAILABLE" if mode == "unknown" else "NO_FEASIBLE_ROUTE",
        )
    if mode in ("deleted", "no-route"):
        payload["deltas"] = payload["deltas"][1:]
        payload["worsened_metric_ids"] = []
    elif mode == "loss":
        payload["deltas"][0].update(
            delta=None, outcome="lost_connection", reason_code="LOST_CONNECTION"
        )
    elif mode == "unknown":
        payload["status"] = "partial"
        payload["deltas"][0].update(delta=None, outcome="unknown", reason_code="DATA_UNAVAILABLE")
        payload["worsened_metric_ids"] = []
    # Skip only initial construction to prove parent-side DTO validation is enforced.
    # Nested data will be reconstructed and checked by calculate() before repository.succeed.
    return ScenarioResult.model_construct(**payload)
