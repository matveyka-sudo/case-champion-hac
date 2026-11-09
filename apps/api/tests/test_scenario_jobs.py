import pytest
from a2_api.job_services import QueuedServices
from a2_api.services import DomainError
from a2_api.storage import JobRepository
from a2_api.worker import Worker
from a2_contracts.common import canonical_hash
from a2_contracts.scenarios import Comparison, ScenarioRequest
from test_storage import context, request


def scenario(base="demo-base"):
    return ScenarioRequest(
        base_run_ids={"regular": base},
        evaluate_profile_ids=["regular"],
        operations=[
            dict(
                type="shift_trip",
                trip_id="demo-trip",
                service_dates=["2026-11-09"],
                delta_seconds=300,
            )
        ],
    )


def scenario_context(base="demo-base"):
    return context().model_copy(update={"baseline_run_ids": {"regular": base}})


def comparison(claim):
    operations = [op.model_dump(mode="json") for op in claim.request.operations]
    return Comparison.model_validate(
        dict(
            run_id="demo-scenario-" + claim.job.job_id,
            scenario_id="demo-scenario",
            bundle_id="demo-synthetic-bundle",
            base_run_ids=claim.request.base_run_ids,
            status="partial",
            operations=operations,
            operations_hash=canonical_hash(operations),
            comparisons=[
                dict(
                    profile_id="regular",
                    profile_version=1,
                    task_set_hash="demo-task-hash",
                    base_run_id=claim.request.base_run_ids["regular"],
                    scenario_run_id="demo-scenario-regular",
                    origin_id="demo-city-1",
                    status="partial",
                    before=[20, 40],
                    after=[30, 50],
                    lower_bound_change=10,
                    upper_bound_change=10,
                    delta_interval_conservative=[-10, 30],
                    confirmed_improved_cases=1,
                    confirmed_worse_cases=0,
                    unresolved_cases=1,
                    worst_worsened_case_ids=[],
                    reason=None,
                )
            ],
            resource_delta={"departures": 0, "vehicle_minutes": None},
            operational_validation="not_verified",
            provenance=[
                dict(
                    bundle_id="demo-synthetic-bundle",
                    profile_id="regular",
                    profile_version=1,
                    task_set_hash="demo-task-hash",
                    code_version="demo-code",
                    completeness_scope="synthetic_only",
                    network_complete=False,
                    search_complete=True,
                )
            ],
            evidence_ids=[],
            assumption_ids=["demo-only"],
            code_version="demo-code",
            params_hash="demo",
            result_hash="demo",
            warnings=["synthetic_example_not_city_results"],
        )
    )


@pytest.mark.asyncio
async def test_scenario_requires_existing_saved_baselines(database):
    pool, _, _ = database

    async def prepare(kind, payload):
        return scenario_context()

    with pytest.raises(DomainError) as exc:
        await QueuedServices(
            JobRepository(pool), prepare=prepare, code_version="demo-code"
        ).scenario(scenario(), "key", "trace")
    assert exc.value.status_code == 404
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["operations", "task", "origin"])
async def test_scenario_result_cannot_change_accepted_command_or_domain_versions(database, changed):
    pool, _, _ = database
    repo = JobRepository(pool)
    await repo.enqueue("scenario", scenario(), scenario_context(), "key", "trace")
    claim = await repo.claim("token")
    output = comparison(claim).model_dump(mode="json")
    if changed == "operations":
        output["operations"][0]["delta_seconds"] = 600
        output["operations_hash"] = canonical_hash(output["operations"])
    elif changed == "task":
        output["comparisons"][0]["task_set_hash"] = "different-task"
        output["provenance"][0]["task_set_hash"] = "different-task"
    else:
        output["comparisons"][0]["origin_id"] = "different-city"
    with pytest.raises(DomainError):
        await repo.succeed(claim, Comparison.model_validate(output))
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0


@pytest.mark.asyncio
async def test_scenario_queue_runs_against_saved_base_without_mutating_it(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    baseline = await repo.enqueue("analysis", request(), context(), "base-key", "trace")
    await Worker(repo, code_version="demo-code", handler="worker_handlers:success").run_once()
    base_id = (await repo.get_job(baseline.job_id)).run_id
    before = (await repo.get_run(base_id)).model_dump_json()

    async def prepared(kind, payload):
        return scenario_context(base_id)

    job = await QueuedServices(repo, prepare=prepared, code_version="demo-code").scenario(
        scenario(base_id), "scenario-key", "trace"
    )
    assert job.status == "queued"
    await Worker(
        repo, code_version="demo-code", handler="worker_handlers:scenario_success"
    ).run_once()
    finished = await repo.get_job(job.job_id)
    assert finished.status == "succeeded"
    after = await repo.get_run(finished.run_id)
    assert after.base_run_ids == {"regular": base_id}
    assert after.comparisons[0].delta_interval_conservative == (0, 50)
    assert (await repo.get_run(base_id)).model_dump_json() == before


@pytest.mark.asyncio
async def test_scenario_cannot_fabricate_the_before_interval_of_a_saved_base(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    baseline = await repo.enqueue("analysis", request(), context(), "base-key", "trace")
    await Worker(repo, code_version="demo-code", handler="worker_handlers:success").run_once()
    base_id = (await repo.get_job(baseline.job_id)).run_id
    await repo.enqueue("scenario", scenario(base_id), scenario_context(base_id), "s", "trace")
    claim = await repo.claim("token")
    output = comparison(claim).model_dump(mode="json")
    output["comparisons"][0].update(
        before=[40, 40],
        after=[50, 100],
        lower_bound_change=10,
        upper_bound_change=60,
        delta_interval_conservative=[10, 60],
    )
    with pytest.raises(DomainError):
        await repo.succeed(claim, Comparison.model_validate(output))
    assert await pool.fetchval("SELECT count(*) FROM runs") == 1
