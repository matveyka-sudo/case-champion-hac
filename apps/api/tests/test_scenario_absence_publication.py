"""TEST_ONLY: persist confirmed absence without inventing a time or reference."""

import pytest
from a2_api.core.jobs import V2JobContext
from a2_api.db.repositories.jobs import JobRepository
from a2_contracts.v2.results import AnalysisResult
from a2_contracts.v2.scenarios import ScenarioResult, ScenarioRunRequest
from test_v2_storage import context, proposal, request, result, scenario_result


def absent_baseline(kind):
    value = result(kind).model_dump(mode="json")
    subject = {
        "kind": "connection",
        "origin_id": value["request"]["origin_id"],
        "destination_id": value["request"]["destination_id"],
        "mode": "rail",
    }
    refs = [item["evidence_id"] for item in value["evidence_refs"]]
    value.update(
        metrics=[
            dict(
                metric_id="absent-total-time",
                name="total_seconds",
                value=None,
                unit="seconds",
                status="not_applicable",
                completeness="full",
                reason_code="NO_FEASIBLE_ROUTE",
                time_kind="complete_chain",
                source_refs=refs,
                subject=subject,
            )
        ],
        scores=[
            dict(
                score_id="absent-index",
                name="index",
                value=0.0,
                status="available",
                completeness="full",
                methodology_version=value["methodology_version"],
                reason_code="NO_FEASIBLE_ROUTE",
                calculation_kind="aggregate"
                if kind == "intercity_passenger"
                else "delivery_deadline",
                components=[],
                evidence_refs=refs,
                subject=subject,
                ranking_eligible=False,
            )
        ],
        journeys=[],
        references=[],
        counts=[],
        last_returns=[],
    )
    return AnalysisResult.model_validate(value)


def unchanged_absence(saved, command, kind):
    seed = result(kind)
    value = scenario_result(seed, proposal(seed)).model_dump(mode="json")
    values = {
        "metrics": [m.model_dump(mode="json") for m in saved.metrics],
        "scores": [s.model_dump(mode="json") for s in saved.scores],
    }
    value.update(
        scenario_id=command.scenario_id,
        baseline_analysis_id=saved.analysis_id,
        baseline_result_hash=saved.result_hash,
        before=values,
        after=values,
        deltas=[
            dict(
                effect_id="same-confirmed-absence",
                kind="score",
                before_id="absent-index",
                after_id="absent-index",
                unit="points_0_100",
                delta=0.0,
                sign_convention="after_minus_before",
                outcome="unchanged",
                reason_code=None,
                scope="transport",
            )
        ],
    )
    return ScenarioResult.model_validate(value)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["intercity_passenger", "intercity_cargo"])
async def test_absent_scenario_persists_scoped_null_chain_and_zero_boundary(database, kind):
    pool, _, _ = database
    repo = JobRepository(pool)
    baseline = absent_baseline(kind)
    await repo.enqueue("analysis", request(kind), context(kind), "baseline", "trace", scope="owner")
    await repo.succeed(await repo.claim("worker"), baseline, proposals=[proposal(baseline)])
    saved = await repo.get_run(baseline.analysis_id, scope="owner")
    command = (await repo.get_proposals(saved.analysis_id, "owner"))[0]
    parameters = await repo.get_run_parameters(saved.analysis_id, scope="owner")
    queued_context = V2JobContext.model_validate(parameters["context"]).model_copy(
        update={"scenario_proposal": command}
    )
    await repo.enqueue(
        "scenario",
        ScenarioRunRequest(
            schema_version="2",
            scenario_id=command.scenario_id,
            baseline_analysis_id=saved.analysis_id,
        ),
        queued_context,
        "scenario",
        "trace",
        scope="owner",
    )
    candidate = unchanged_absence(saved, command, kind)
    await repo.succeed(await repo.claim("scenario-worker"), candidate)
    stored = await repo.get_run(candidate.run_id, scope="owner")
    assert stored.after.metrics[0].value is None
    assert stored.after.metrics[0].reason_code == "NO_FEASIBLE_ROUTE"
    assert stored.after.scores[0].value == 0
    assert stored.after.scores[0].components == []
    assert (await repo.get_run(saved.analysis_id, scope="owner")).result_hash == saved.result_hash
