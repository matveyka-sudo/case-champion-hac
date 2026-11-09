"""Scenario references are resolved against the immutable accepted baseline."""

import pytest
from a2_api.jobs import V2JobContext
from a2_api.services import DomainError
from a2_api.storage import JobRepository
from a2_contracts.v2.scenarios import ScenarioRunRequest
from test_v2_storage import context, proposal, request, result, scenario_result


@pytest.mark.asyncio
async def test_scenario_cannot_publish_a_full_score_with_a_foreign_reference(database):
    pool, _, _ = database
    repository = JobRepository(pool)
    baseline = result()
    await repository.enqueue("analysis", request(), context(), "baseline", "trace", scope="owner")
    await repository.succeed(
        await repository.claim("baseline-claim"), baseline, proposals=[proposal(baseline)]
    )
    saved = await repository.get_run(baseline.analysis_id, scope="owner")
    command = (await repository.get_proposals(baseline.analysis_id, "owner"))[0]
    parameters = await repository.get_run_parameters(baseline.analysis_id, scope="owner")
    scenario_context = V2JobContext.model_validate(parameters["context"]).model_copy(
        update={"scenario_proposal": command}
    )
    await repository.enqueue(
        "scenario",
        ScenarioRunRequest(
            schema_version="2",
            scenario_id=command.scenario_id,
            baseline_analysis_id=baseline.analysis_id,
        ),
        scenario_context,
        "scenario",
        "trace",
        scope="owner",
    )
    forged = scenario_result(saved, command)
    forged.after.scores = [item.model_copy(deep=True) for item in forged.after.scores]
    forged.after.scores[0].reference_ids = ["nonexistent-reference"]
    with pytest.raises(DomainError):
        await repository.succeed(await repository.claim("scenario-claim"), forged)
    assert await pool.fetchval("SELECT count(*) FROM runs WHERE result_kind='scenario_v2'") == 0
    assert (
        await repository.get_run(baseline.analysis_id, scope="owner")
    ).result_hash == saved.result_hash
