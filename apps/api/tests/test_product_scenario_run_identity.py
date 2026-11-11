"""TEST_ONLY: one physical scenario can have distinct durable calculation jobs."""

import pytest
from a2_api.adapters import product_scenarios as bridge
from a2_api.core.errors import DomainError
from a2_api.db.repositories.jobs import JobRepository
from a2_contracts.common import canonical_hash
from a2_contracts.v2.scenarios import ScenarioRunRequest
from a2_engine import simulate_scenario
from test_product_scenarios import make_case, trusted_urban_scope, with_claim


def test_run_identity_is_stable_for_job_retry_and_distinct_for_another_job():
    claim, facts, document, baseline, frozen = with_claim(make_case())
    first = bridge.calculate(
        claim, baseline=baseline, inputs=facts, document=document, profile_id=document.profile_id
    )
    retried = bridge.calculate(
        claim.model_copy(update={"claim_token": "TEST_ONLY-reclaimed-lease"}),
        baseline=baseline,
        inputs=facts,
        document=document,
        profile_id=document.profile_id,
    )
    other = bridge.calculate(
        claim.model_copy(
            update={"job": claim.job.model_copy(update={"job_id": "TEST_ONLY-other-job"})}
        ),
        baseline=baseline,
        inputs=facts,
        document=document,
        profile_id=document.profile_id,
    )
    assert retried == first
    assert other.run_id != first.run_id
    assert other.model_dump(exclude={"run_id", "result_hash"}) == first.model_dump(
        exclude={"run_id", "result_hash"}
    )
    engine = simulate_scenario(frozen, document.plans[0].operation)
    assert first.overlay_graph_id == other.overlay_graph_id == engine.overlay.overlay_id
    assert first.scenario_id == other.scenario_id == claim.context.scenario_proposal.scenario_id


async def queue_same_model(pool, *, label, scope, code_version):
    context, facts, document, baseline, frozen = make_case()
    versions = context.versions.model_copy(update={"code_version": code_version})
    context = context.model_copy(
        update={
            "analysis_id": "TEST_ONLY-baseline-" + label,
            "access_scope": scope,
            "versions": versions,
        }
    )
    baseline = baseline.model_copy(
        update={"analysis_id": context.analysis_id, "versions": versions}
    )
    baseline = baseline.model_copy(
        update={
            "result_hash": canonical_hash(baseline.model_dump(mode="json", exclude={"result_hash"}))
        }
    )
    endpoint_scope = trusted_urban_scope((context, facts, document, baseline, frozen))
    context = context.model_copy(update={"snapshot_hash": endpoint_scope.snapshot_hash})
    document = document.model_copy(update={"snapshot_hash": endpoint_scope.snapshot_hash})
    commands = bridge.propose(context, facts, document, baseline, profile_id=document.profile_id)
    repository = JobRepository(pool)
    await repository.enqueue(
        "analysis", context.normalized_request, context, "base-" + label, "trace", scope=scope
    )
    await repository.succeed(
        await repository.claim("baseline-worker"), baseline, proposals=commands
    )
    saved = await repository.get_run(baseline.analysis_id, scope)
    command = (await repository.get_proposals(saved.analysis_id, scope))[0]
    context = context.model_copy(
        update={"scenario_proposal": command, "scenario_endpoint_scope": endpoint_scope}
    )
    request = ScenarioRunRequest(
        schema_version="2", scenario_id=command.scenario_id, baseline_analysis_id=saved.analysis_id
    )
    await repository.enqueue(
        "scenario", request, context, "scenario-" + label, "trace", scope=scope
    )
    claim = await repository.claim("scenario-worker")
    candidate = bridge.calculate(
        claim, baseline=saved, inputs=facts, document=document, profile_id=document.profile_id
    )
    engine = simulate_scenario(frozen, document.plans[0].operation)
    return repository, claim, candidate, saved, engine.scenario_id


@pytest.mark.asyncio
async def test_same_physical_model_publishes_for_distinct_baselines_versions_and_sessions(database):
    pool, _, _ = database
    published, engine_ids = [], []
    cases = [
        ("old", "TEST_ONLY-owner-A", "TEST_ONLY-old-code"),
        ("new", "TEST_ONLY-owner-A", "TEST_ONLY-new-code"),
        ("other-session", "TEST_ONLY-owner-B", "TEST_ONLY-new-code"),
    ]
    for label, scope, code_version in cases:
        repository, claim, candidate, baseline, engine_id = await queue_same_model(
            pool, label=label, scope=scope, code_version=code_version
        )
        baseline_bytes = baseline.model_dump_json()
        # This is the original production failure seam: a second valid job must
        # publish even when the engine computes the identical physical scenario.
        await repository.succeed(claim, candidate)
        stored = await repository.get_run(candidate.run_id, scope)
        stored_bytes = stored.model_dump_json()
        assert (await repository.get_run(candidate.run_id, scope)).model_dump_json() == stored_bytes
        assert stored.before.metrics == baseline.metrics and stored.before.scores == baseline.scores
        assert (
            await repository.get_run(baseline.analysis_id, scope)
        ).model_dump_json() == baseline_bytes
        replay = await repository.replay("scenario", claim.request, "scenario-" + label, scope)
        assert replay.job_id == claim.job.job_id and replay.status == "succeeded"
        cached = await repository.enqueue(
            "scenario", claim.request, claim.context, "cached-" + label, "trace", scope=scope
        )
        assert cached.status == "succeeded"
        assert (await repository.get_job(cached.job_id, scope)).run_id == stored.run_id
        for reader in (repository.get_run, repository.get_run_parameters):
            with pytest.raises(DomainError) as denied:
                await reader(stored.run_id, "TEST_ONLY-foreign-owner")
            assert denied.value.status_code == 404
        published.append(stored)
        engine_ids.append(engine_id)
    assert len(set(engine_ids)) == 1
    assert len({result.run_id for result in published}) == len(cases)
    assert len({result.baseline_analysis_id for result in published}) == len(cases)
    assert all(result.after == published[0].after for result in published)
    assert all(result.deltas == published[0].deltas for result in published)
    assert await pool.fetchval("SELECT count(*) FROM runs WHERE result_kind='scenario_v2'") == 3
