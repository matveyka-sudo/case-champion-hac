"""Shared semantic validation must follow nested score operands on every path."""

from copy import deepcopy

import pytest
from a2_api.jobs import V2JobContext
from a2_api.services import DomainError
from a2_api.storage import JobRepository
from a2_contracts.v2.journeys import ReferenceResult
from a2_contracts.v2.results import ComponentRef
from a2_contracts.v2.scenarios import ScenarioRunRequest
from test_v2_storage import context, proposal, request, result, scenario_result


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign", [False, True])
async def test_nested_explicit_reference_has_the_same_proof_at_publish_read_and_cache(
    database, foreign
):
    pool, _, _ = database
    repository = JobRepository(pool)
    baseline = result("intercity_passenger")
    reference = deepcopy(baseline.references[0].model_dump(mode="json"))
    reference.update(
        reference_id="another-pair",
        boundary_node_sets=dict(origin_node_ids=["foreign-a"], destination_node_ids=["foreign-b"]),
    )
    baseline.references.append(ReferenceResult.model_validate(reference))
    # Add an aggregate whose explicit denominator is proven by its child journey.
    aggregate = baseline.scores[0].model_copy(deep=True)
    aggregate.score_id = "nested-index"
    aggregate.calculation_kind = "aggregate"
    aggregate.components = [ComponentRef(kind="score", id=baseline.scores[0].score_id)]
    aggregate = type(aggregate).model_validate(aggregate.model_dump(mode="json"))
    baseline.scores.append(aggregate)
    await repository.enqueue(
        "analysis",
        request("intercity_passenger"),
        context("intercity_passenger"),
        "baseline",
        "trace",
        scope="owner",
    )
    await repository.succeed(
        await repository.claim("baseline-claim"), baseline, proposals=[proposal(baseline)]
    )
    saved = await repository.get_run(baseline.analysis_id, "owner")
    command = (await repository.get_proposals(saved.analysis_id, "owner"))[0]
    parameters = await repository.get_run_parameters(saved.analysis_id, "owner")
    prepared = V2JobContext.model_validate(parameters["context"]).model_copy(
        update={"scenario_proposal": command}
    )
    command_request = ScenarioRunRequest(
        schema_version="2", scenario_id=command.scenario_id, baseline_analysis_id=saved.analysis_id
    )
    await repository.enqueue(
        "scenario", command_request, prepared, "scenario", "trace", scope="owner"
    )
    candidate = scenario_result(saved, command)
    candidate.after.metrics[0].value = candidate.before.metrics[0].value
    candidate.deltas[0].delta = 0.0
    candidate.deltas[0].outcome = "unchanged"
    candidate.after.scores = [item.model_copy(deep=True) for item in candidate.after.scores]
    candidate.after.scores[-1].reference_ids = [
        "another-pair" if foreign else baseline.references[0].reference_id
    ]
    claim = await repository.claim("scenario-claim")
    if foreign:
        with pytest.raises(DomainError):
            await repository.succeed(claim, candidate)
        assert await pool.fetchval("SELECT count(*) FROM runs WHERE result_kind='scenario_v2'") == 0
    else:
        await repository.succeed(claim, candidate)
        assert (await repository.get_run(candidate.run_id, "owner")).after.scores[
            -1
        ].reference_ids == [baseline.references[0].reference_id]
        assert (
            await repository.enqueue(
                "scenario", command_request, prepared, "retry", "trace", scope="owner"
            )
        ).status == "succeeded"
    assert (await repository.get_run(saved.analysis_id, "owner")).result_hash == saved.result_hash


@pytest.mark.asyncio
async def test_full_aggregate_cannot_erase_its_own_incomplete_frozen_reference(database):
    from a2_contracts.v2.scenarios import ScenarioDelta

    pool, _, _ = database
    repository = JobRepository(pool)
    baseline = result("intercity_passenger")
    partial = baseline.references[0].model_copy(
        deep=True, update={"reference_id": "incomplete-operand", "completeness": "partial"}
    )
    baseline.references.append(partial)
    aggregate = baseline.scores[0].model_copy(
        deep=True,
        update={
            "score_id": "nested-index",
            "calculation_kind": "aggregate",
            "components": [ComponentRef(kind="score", id=baseline.scores[0].score_id)],
            "reference_ids": [partial.reference_id],
            "completeness": "partial",
            "ranking_eligible": False,
        },
    )
    baseline.scores.append(aggregate)
    baseline.status = "partial"
    await repository.enqueue(
        "analysis",
        request("intercity_passenger"),
        context("intercity_passenger"),
        "baseline",
        "trace",
        scope="owner",
    )
    await repository.succeed(
        await repository.claim("baseline-claim"), baseline, proposals=[proposal(baseline)]
    )
    saved = await repository.get_run(baseline.analysis_id, "owner")
    command = (await repository.get_proposals(saved.analysis_id, "owner"))[0]
    parameters = await repository.get_run_parameters(saved.analysis_id, "owner")
    prepared = V2JobContext.model_validate(parameters["context"]).model_copy(
        update={"scenario_proposal": command}
    )
    submitted = ScenarioRunRequest(
        schema_version="2", scenario_id=command.scenario_id, baseline_analysis_id=saved.analysis_id
    )
    await repository.enqueue("scenario", submitted, prepared, "scenario", "trace", scope="owner")
    template = saved.model_copy(deep=True)
    template.status = "available"
    template.scores[-1].completeness = "full"
    template.scores[-1].reference_ids = []
    candidate = scenario_result(template, command)
    candidate.status = "partial"
    candidate.before = type(candidate.before)(metrics=saved.metrics, scores=saved.scores)
    candidate.after.metrics[0].value = candidate.before.metrics[0].value
    candidate.deltas[0].delta, candidate.deltas[0].outcome = 0.0, "unchanged"
    candidate.after.scores = [item.model_copy(deep=True) for item in candidate.after.scores]
    after = candidate.after.scores[-1]
    after.completeness, after.ranking_eligible, after.reference_ids = "full", True, []
    candidate.deltas.append(
        ScenarioDelta(
            effect_id="completed-aggregate",
            kind="score",
            before_id=aggregate.score_id,
            after_id=aggregate.score_id,
            unit="points_0_100",
            delta=None,
            sign_convention="after_minus_before",
            outcome="unknown",
            reason_code="COMPLETENESS_CHANGED",
            scope="transport",
        )
    )
    candidate = type(candidate).model_validate(candidate.model_dump(mode="json"))
    with pytest.raises(DomainError):
        await repository.succeed(await repository.claim("scenario-claim"), candidate)
    assert await pool.fetchval("SELECT count(*) FROM runs WHERE result_kind='scenario_v2'") == 0
    assert (await repository.get_run(saved.analysis_id, "owner")).result_hash == saved.result_hash
