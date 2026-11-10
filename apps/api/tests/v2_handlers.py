"""Explicitly synthetic process fixtures. Never auto-loaded by production settings."""

import json
from pathlib import Path

from a2_api.core.jobs import ClaimedJob, V2JobContext
from a2_api.core.registry import ProductRegistry
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
    from a2_api.core.jobs import CalculationOutput
    from test_v2_storage import proposal

    saved = success(claim)
    return CalculationOutput(analysis=saved, proposals=[proposal(saved)])


def scenario_success(claim):
    from test_v2_storage import scenario_result

    baseline = success(claim)
    command = claim.context.scenario_proposal
    baseline = baseline.model_copy(update={"result_hash": command.baseline_result_hash})
    return scenario_result(baseline, command)


def with_map(claim):
    """Explicit synthetic map example; never selected by production defaults."""
    result = success(claim)
    payload = result.model_dump(mode="json")
    common = dict(territory_id="city-1", geometry_kind="actual", source_refs=["e-1"])
    payload["map_data"] = dict(
        status="available",
        completeness="partial",
        reason_code="SYNTHETIC_ONLY",
        features=[
            common
            | dict(
                entity_kind="cell",
                entity_id="synthetic-cell",
                point_id="demo-point",
                population=None,
                geometry=dict(
                    type="Polygon",
                    coordinates=[[[37.0, 55.0], [37.01, 55.0], [37.01, 55.01], [37.0, 55.0]]],
                ),
            ),
            common
            | dict(
                entity_kind="sample_point",
                entity_id="demo-point",
                cell_id="synthetic-cell",
                geometry=dict(type="Point", coordinates=[37.005, 55.001]),
            ),
            common
            | dict(
                entity_kind="destination",
                entity_id="synthetic-school",
                categories=["school"],
                name="Synthetic school",
                geometry=dict(type="Point", coordinates=[37.006, 55.002]),
            ),
        ],
    )
    return AnalysisResult.model_validate(payload)
