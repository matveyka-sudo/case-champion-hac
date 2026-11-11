"""Scenario publication must resolve implicit baseline reference operands too."""

import json
from hashlib import sha256
from pathlib import Path

import pytest
from a2_api.api.application import create_app
from a2_api.core.jobs import V2JobContext
from a2_api.db.repositories.jobs import JobRepository
from a2_api.services import DomainError
from a2_api.services.analyses import AnalysisService
from a2_contracts.v2.results import AnalysisResult
from a2_contracts.v2.scenarios import ScenarioResult, ScenarioRunRequest
from httpx import ASGITransport, AsyncClient
from test_v2_storage import context, proposal, request, scenario_result


@pytest.mark.asyncio
async def test_scenario_full_after_cannot_hide_partial_reference_by_omitting_direct_ids(database):
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
    full = AnalysisResult.model_validate(raw)
    command = proposal(full)
    candidate = scenario_result(full, command).model_dump(mode="json")
    raw["references"][0]["completeness"] = "partial"
    raw["status"] = "partial"
    for item in raw["scores"]:
        item.update(completeness="partial", ranking_eligible=False, reference_ids=[])
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
    with pytest.raises(DomainError):
        await repository.succeed(claim, forged)
    assert await pool.fetchval("SELECT count(*) FROM runs WHERE result_kind='scenario_v2'") == 0
    assert (await repository.get_job(accepted.job_id, scope=scope)).status != "succeeded"
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
    assert retried.status == "queued"
    async with AsyncClient(
        transport=ASGITransport(app=create_app(AnalysisService(repository=repository))),
        base_url="http://test",
        cookies={"a2_session": token},
    ) as api:
        response = await api.get("/api/v1/scenario-runs/" + forged.run_id)
        assert response.status_code == 404
        assert "Traceback" not in response.text
