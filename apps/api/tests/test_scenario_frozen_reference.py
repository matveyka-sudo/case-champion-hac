"""Scenario publication must resolve implicit baseline reference operands too."""

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest
from a2_api.analysis_service import AnalysisService
from a2_api.api_v2 import create_app
from a2_api.jobs import V2JobContext
from a2_api.services import DomainError
from a2_api.storage import JobRepository
from a2_contracts.v2.results import AnalysisResult
from a2_contracts.v2.scenarios import ScenarioResult, ScenarioRunRequest
from httpx import ASGITransport, AsyncClient
from test_v2_storage import context, proposal, request, scenario_result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "foreign_journey",
        "same_boundary",
        "direct_reference",
        "legacy_direct_reference",
        "renamed_control",
    ],
)
async def test_scenario_reference_cannot_be_rebound_to_another_baseline_task(database, mode):
    pool, _, _ = database
    repository = JobRepository(pool)
    token = "A" * 43
    scope = sha256(token.encode()).hexdigest()
    raw = json.loads(
        (
            Path(__file__).resolve().parents[3]
            / "packages/contracts/fixtures/v2/required-dependencies-synthetic.json"
        ).read_text()
    )["payload"]
    raw["analysis_id"] = "partial-baseline"
    foreign_ref = deepcopy(raw["references"][0])
    foreign_ref.update(
        reference_id="foreign-reference",
        boundary_node_sets=dict(
            origin_node_ids=["elsewhere-a"], destination_node_ids=["elsewhere-b"]
        ),
    )
    if mode == "same_boundary":
        foreign_ref["boundary_node_sets"] = deepcopy(raw["references"][0]["boundary_node_sets"])
    raw["references"].append(foreign_ref)
    foreign_journey = deepcopy(raw["journeys"][0])
    foreign_journey.update(
        journey_id="foreign-journey",
        reference_id="foreign-reference",
        origin_node_id="elsewhere-a",
        destination_node_id="elsewhere-b",
    )
    foreign_journey["legs"][0].update(from_node_id="elsewhere-a", to_node_id="elsewhere-b")
    if mode == "same_boundary":
        foreign_journey["origin_node_id"] = raw["journeys"][0]["origin_node_id"]
        foreign_journey["destination_node_id"] = raw["journeys"][0]["destination_node_id"]
        foreign_journey["legs"] = deepcopy(raw["journeys"][0]["legs"])
    raw["journeys"].append(foreign_journey)
    if mode in {"direct_reference", "legacy_direct_reference"}:
        raw["metrics"][0]["journey_id"] = None
        raw["scores"][0]["reference_ids"] = ["reference"]
    full = AnalysisResult.model_validate(raw)
    command = proposal(full)
    candidate = scenario_result(full, command).model_dump(mode="json")
    if mode == "legacy_direct_reference":
        raw["scores"][0].pop("calculation_kind", None)
    good = mode == "renamed_control"
    raw["references"][0]["completeness"] = "full" if good else "partial"
    raw["status"] = "partial"
    for item in raw["scores"]:
        item.update(completeness="partial", ranking_eligible=False)
    baseline = AnalysisResult.model_validate(raw)
    await repository.enqueue(
        "analysis",
        request("intercity_passenger"),
        context("intercity_passenger", analysis_id=baseline.analysis_id, scope=scope),
        "partial-baseline",
        "trace",
        scope=scope,
    )
    await repository.succeed(
        await repository.claim("baseline-claim"), baseline, proposals=[proposal(baseline)]
    )
    saved = await repository.get_run(baseline.analysis_id, scope=scope)
    saved_command = (await repository.get_proposals(saved.analysis_id, scope))[0]
    parameters = await repository.get_run_parameters(saved.analysis_id, scope=scope)
    scenario_context = V2JobContext.model_validate(parameters["context"]).model_copy(
        update={"scenario_proposal": saved_command}
    )
    candidate.update(
        baseline_result_hash=saved.result_hash,
        scenario_id=saved_command.scenario_id,
        status="partial",
    )
    candidate["before"] = {
        "metrics": [m.model_dump(mode="json") for m in saved.metrics],
        "scores": [s.model_dump(mode="json") for s in saved.scores],
    }
    for score in candidate["after"]["scores"]:
        score["reference_ids"] = []
        candidate["deltas"].append(
            dict(
                effect_id="score-" + score["score_id"],
                kind="score",
                before_id=score["score_id"],
                after_id=score["score_id"],
                unit="points_0_100",
                delta=None,
                sign_convention="after_minus_before",
                outcome="unknown",
                reason_code="CHANGED_COMPLETENESS",
                scope="transport",
            )
        )
    # A metadata-only completion has unchanged numeric values; no formula claim changes.
    candidate["after"]["metrics"][0]["value"] = candidate["before"]["metrics"][0]["value"]
    candidate["deltas"][0].update(delta=0.0, outcome="unchanged")
    if mode in {"direct_reference", "legacy_direct_reference"}:
        candidate["after"]["scores"][0]["reference_ids"] = ["foreign-reference"]
    else:
        candidate["after"]["metrics"][0]["journey_id"] = (
            "new-scenario-journey" if good else "foreign-journey"
        )
    forged = ScenarioResult.model_validate(candidate)
    accepted = await repository.enqueue(
        "scenario",
        ScenarioRunRequest(
            schema_version="2",
            scenario_id=saved_command.scenario_id,
            baseline_analysis_id=saved.analysis_id,
        ),
        scenario_context,
        "scenario",
        "trace",
        scope=scope,
    )
    claim = await repository.claim("scenario-claim")
    if good:
        await repository.succeed(claim, forged)
    else:
        with pytest.raises(DomainError):
            await repository.succeed(claim, forged)
    assert await pool.fetchval("SELECT count(*) FROM runs WHERE result_kind='scenario_v2'") == int(
        good
    )
    assert ((await repository.get_job(accepted.job_id, scope=scope)).status == "succeeded") == good
    assert (
        await repository.get_run(saved.analysis_id, scope=scope)
    ).result_hash == saved.result_hash
    retried = await repository.enqueue(
        "scenario",
        ScenarioRunRequest(
            schema_version="2",
            scenario_id=saved_command.scenario_id,
            baseline_analysis_id=saved.analysis_id,
        ),
        scenario_context,
        "scenario-retry",
        "trace",
        scope=scope,
    )
    assert retried.status == ("succeeded" if good else "queued")
    async with AsyncClient(
        transport=ASGITransport(app=create_app(AnalysisService(repository=repository))),
        base_url="http://test",
        cookies={"a2_session": token},
    ) as api:
        response = await api.get("/api/v1/scenario-runs/" + forged.run_id)
        assert response.status_code == (200 if good else 404)
        assert "Traceback" not in response.text
