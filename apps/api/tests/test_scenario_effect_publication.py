"""A mutated worker DTO cannot publish a scenario that omits deterioration."""

import pytest
from a2_api.jobs import V2JobContext
from a2_api.storage import JobRepository
from a2_contracts.v2.scenarios import ScenarioDelta, ScenarioRunRequest
from pydantic import ValidationError
from test_v2_storage import context, proposal, request, result, scenario_result


@pytest.mark.asyncio
async def test_hidden_worsening_is_rejected_before_postgresql_scenario_insert(database):
    pool, _, _ = database
    repository = JobRepository(pool)
    baseline = result()
    baseline.metrics.append(
        baseline.metrics[0].model_copy(update={"metric_id": "route-B", "value": 3600.0})
    )
    await repository.enqueue("analysis", request(), context(), "baseline", "trace", scope="owner")
    claim = await repository.claim("baseline-claim")
    await repository.succeed(claim, baseline, proposals=[proposal(baseline)])
    saved = await repository.get_run(baseline.analysis_id, scope="owner")
    command = (await repository.get_proposals(baseline.analysis_id, "owner"))[0]
    parameters = await repository.get_run_parameters(baseline.analysis_id, scope="owner")
    scenario_context = V2JobContext.model_validate(parameters["context"]).model_copy(
        update={"scenario_proposal": command}
    )
    accepted = await repository.enqueue(
        "scenario",
        ScenarioRunRequest(
            schema_version="2",
            scenario_id=command.scenario_id,
            baseline_analysis_id=baseline.analysis_id,
        ),
        scenario_context,
        "scenario",
        "scenario-trace",
        scope="owner",
    )
    scenario_claim = await repository.claim("scenario-claim")
    # A correctly validated object becomes malicious after construction. succeed must revalidate it.
    forged = scenario_result(saved, command)
    forged.after.metrics[0].value = 1900.0
    forged.after.metrics[1].value = 3400.0
    forged.deltas = [
        ScenarioDelta(
            effect_id="only-B",
            kind="metric",
            before_id="route-B",
            after_id="route-B",
            unit="seconds",
            delta=200.0,
            sign_convention="before_minus_after",
            outcome="improved",
            reason_code=None,
            scope="transport",
        )
    ]
    forged.worsened_metric_ids = []
    with pytest.raises(ValidationError):
        await repository.succeed(scenario_claim, forged)
    assert await pool.fetchval("SELECT count(*) FROM runs WHERE result_kind='scenario_v2'") == 0
    assert await pool.fetchval("SELECT count(*) FROM runs") == 1
    assert (await repository.get_job(accepted.job_id, scope="owner")).status == "running"
    assert (
        await repository.get_run(baseline.analysis_id, scope="owner")
    ).result_hash == saved.result_hash
