"""Explicitly synthetic process fixtures. Never auto-loaded by production settings."""

import json
from pathlib import Path

from a2_api.jobs import ClaimedJob, V2JobContext
from a2_api.product_registry import ProductRegistry
from a2_contracts.v2.api import TerritoryOption
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.results import AnalysisResult

FIXTURES = Path(__file__).resolve().parents[3] / "packages/contracts/fixtures/v2"


def registry() -> ProductRegistry:
    snapshot = AcceptedData.model_validate(
        json.loads((FIXTURES / "accepted-data.json").read_text())["payload"]
    )
    return ProductRegistry(
        territories=[
            TerritoryOption(
                territory_id="city-1", level="city", name="Москва", timezone="Europe/Moscow"
            )
        ],
        snapshots=[snapshot],
        ready_kinds=["urban_passenger"],
    )


def success(claim: ClaimedJob) -> AnalysisResult:
    assert isinstance(claim.context, V2JobContext)
    context = claim.context
    payload = json.loads(
        (FIXTURES / f"{context.normalized_request.analysis_kind}-result.json").read_text()
    )["payload"]
    payload = json.loads(
        json.dumps(payload).replace("demo-evidence", "e-1").replace("demo-source", "source-1")
    )
    payload.update(
        analysis_id=context.analysis_id,
        request=context.normalized_request.model_dump(mode="json"),
        snapshot_id=context.snapshot_id,
        versions=context.versions.model_dump(mode="json"),
        config_hash=context.versions.config_hash,
    )
    return AnalysisResult.model_validate(payload)


def with_proposals(claim):
    from a2_api.jobs import CalculationOutput
    from test_v2_storage import proposal

    saved = success(claim)
    return CalculationOutput(analysis=saved, proposals=[proposal(saved)])


def scenario_success(claim):
    from test_v2_storage import scenario_result

    baseline = success(claim)
    command = claim.context.scenario_proposal
    baseline = baseline.model_copy(update={"result_hash": command.baseline_result_hash})
    return scenario_result(baseline, command)
