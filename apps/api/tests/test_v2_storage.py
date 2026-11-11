"""Synthetic v2 storage/worker lifecycle checks against an isolated PostgreSQL schema."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from a2_api.core import jobs
from a2_api.db.repositories.jobs import JobRepository
from a2_api.services import DomainError
from a2_contracts.common import canonical_hash
from a2_contracts.v2.requests import parse_analysis_request
from a2_contracts.v2.results import AnalysisResult
from a2_contracts.v2.scenarios import ScenarioProposal, ScenarioResult, ScenarioRunRequest
from pydantic import ValidationError


def result(kind="urban_passenger", analysis_id="synthetic-analysis"):
    path = (
        Path(__file__).resolve().parents[3]
        / "packages/contracts/fixtures/v2"
        / (kind + "-result.json")
    )
    payload = json.loads(path.read_text())["payload"]
    payload["analysis_id"] = analysis_id
    return AnalysisResult.model_validate(payload)


def request(kind="urban_passenger"):
    payload = result(kind).request.model_dump(mode="json")
    payload["snapshot_id"] = None
    payload["methodology"] = None
    return parse_analysis_request(payload)


def context(kind="urban_passenger", analysis_id="synthetic-analysis", scope="owner", **changes):
    assert hasattr(jobs, "V2JobContext"), "V2JobContext is not implemented"
    saved = result(kind, analysis_id)
    return jobs.V2JobContext.model_validate(
        dict(
            schema_version="2",
            snapshot_id=saved.snapshot_id,
            snapshot_hash="synthetic-snapshot-hash",
            versions=saved.versions,
            analysis_id=analysis_id,
            normalized_request=saved.request,
            access_scope=scope,
        )
        | changes
    )


def proposal(saved):
    return ScenarioProposal(
        schema_version="2",
        scenario_id="provisional-scenario",
        baseline_analysis_id=saved.analysis_id,
        template_id="coordinate-transfer-v1",
        operation_type="coordinate_transfer",
        fixed_parameters=dict(
            service_ids=["synthetic-service"],
            shift_seconds=300,
            demand_evidence=[],
            resource_evidence=[],
            turnaround_evidence=[],
            resource_constraints=[],
        ),
        evidence_refs=saved.evidence_refs,
        assumptions=["synthetic_only"],
        status="proposed",
        feasibility="unverified",
        versions=saved.versions,
        baseline_result_hash=saved.result_hash,
        editable_local_graph=True,
        limitations=["TURNAROUND_UNVERIFIED"],
    )


def scenario_result(saved, command):
    old = saved.metrics[0]
    changed = old.model_copy(update={"value": old.value - 100})
    return ScenarioResult(
        schema_version="2",
        run_id="synthetic-scenario-run",
        scenario_id=command.scenario_id,
        baseline_analysis_id=saved.analysis_id,
        status="available",
        before=dict(metrics=saved.metrics, scores=saved.scores),
        after=dict(metrics=[changed, *saved.metrics[1:]], scores=saved.scores),
        deltas=[
            dict(
                effect_id="time-effect",
                kind="metric",
                before_id=old.metric_id,
                after_id=old.metric_id,
                unit="seconds",
                delta=100,
                sign_convention="before_minus_after",
                outcome="improved",
                reason_code=None,
                scope="transport",
            )
        ],
        affected_population=None,
        affected_sites=None,
        worsened_metric_ids=[],
        feasibility="unverified",
        service_changes=[
            dict(
                service_id="synthetic-service",
                shift_seconds=300,
                whole_trip=True,
                subsequent_stops_updated=True,
            )
        ],
        versions=saved.versions,
        overlay_graph_id="synthetic-overlay",
        baseline_result_hash=saved.result_hash,
        result_hash="provisional",
        params_hash="provisional",
        operations_hash="provisional",
        evidence_refs=saved.evidence_refs,
        turnaround_evidence=[],
        resource_evidence=[],
        assumptions=["synthetic_only"],
        limitations=["TURNAROUND_UNVERIFIED"],
        editable_local_graph=True,
    )


def test_v2_context_binds_normalized_snapshot_and_fingerprint_excludes_only_generated_identity():
    first = context()
    assert (
        first.bundle_id == first.snapshot_id and first.code_version == first.versions.code_version
    )
    other = context(analysis_id="another-id")
    assert jobs.cache_fingerprint("analysis", request(), first) == jobs.cache_fingerprint(
        "analysis", request(), other
    )
    assert jobs.cache_fingerprint("analysis", request(), first) != jobs.cache_fingerprint(
        "analysis", request(), context(scope="other")
    )
    with pytest.raises(ValidationError):
        context(snapshot_id="different-snapshot")


@pytest.mark.asyncio
async def test_v2_records_require_scope_on_every_read_and_replay(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    accepted = await repo.enqueue(
        "analysis", request(), context(), "scope-key", "trace", scope="owner"
    )
    for unauthorized in (None, "another-owner"):
        with pytest.raises(DomainError) as error:
            await repo.get_job(accepted.job_id, scope=unauthorized)
        assert error.value.status_code == 404
        with pytest.raises(DomainError):
            await repo.replay("analysis", request(), "scope-key", scope=unauthorized)
    claim = await repo.claim("worker-token")
    assert isinstance(claim.context, jobs.V2JobContext)
    await repo.succeed(claim, result())
    for method in (repo.get_run, repo.get_run_parameters):
        for unauthorized in (None, "another-owner"):
            with pytest.raises(DomainError) as error:
                await method("synthetic-analysis", scope=unauthorized)
            assert error.value.status_code == 404
    saved = await repo.get_run("synthetic-analysis", scope="owner")
    assert isinstance(saved, AnalysisResult)
    assert saved.result_hash != result().result_hash
    assert saved.result_hash == canonical_hash(
        saved.model_dump(mode="json", exclude={"result_hash"})
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["urban_passenger", "intercity_passenger", "urban_cargo", "intercity_cargo"]
)
async def test_all_four_request_kinds_roundtrip_and_cache_reuses_saved_identity(database, kind):
    pool, _, _ = database
    repo = JobRepository(pool)
    first = await repo.enqueue(
        "analysis", request(kind), context(kind), "first", "trace", scope="owner"
    )
    claim = await repo.claim("token")
    await repo.succeed(claim, result(kind))
    cached = await repo.enqueue(
        "analysis",
        request(kind),
        context(kind, analysis_id="new-provisional-id"),
        "cached",
        "trace",
        scope="owner",
    )
    assert first.job_id != cached.job_id and cached.status == "succeeded"
    assert (await repo.get_job(cached.job_id, scope="owner")).run_id == "synthetic-analysis"
    assert (
        await pool.fetchval(
            "SELECT payload->'context'->>'analysis_id' FROM jobs WHERE job_id=$1", cached.job_id
        )
        == "synthetic-analysis"
    )
    foreign = await repo.enqueue(
        "analysis",
        request(kind),
        context(kind, scope="other", analysis_id="foreign"),
        "foreign-key",
        "trace",
        scope="other",
    )
    assert foreign.status == "queued"


@pytest.mark.asyncio
async def test_live_cache_expiry_blocks_reuse_but_not_immutable_replay(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    valid_until = datetime.now(UTC) + timedelta(seconds=30)
    accepted = await repo.enqueue(
        "analysis",
        request(),
        context(cache_valid_until=valid_until),
        "first",
        "trace",
        scope="owner",
    )
    claim = await repo.claim("token")
    await repo.succeed(claim, result())
    await pool.execute(
        "UPDATE jobs SET cache_valid_until=now()-interval '1 second' WHERE job_id=$1",
        accepted.job_id,
    )
    fresh = await repo.enqueue(
        "analysis",
        request(),
        context(analysis_id="fresh", cache_valid_until=valid_until),
        "fresh",
        "trace",
        scope="owner",
    )
    assert fresh.status == "queued"
    assert (await repo.replay("analysis", request(), "first", scope="owner")).status == "succeeded"


@pytest.mark.asyncio
async def test_cache_hit_never_extends_original_provider_expiry(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    original_expiry = datetime.now(UTC) + timedelta(seconds=30)
    await repo.enqueue(
        "analysis",
        request(),
        context(cache_valid_until=original_expiry),
        "base",
        "trace",
        scope="owner",
    )
    claim = await repo.claim("token")
    await repo.succeed(claim, result())
    later = await repo.enqueue(
        "analysis",
        request(),
        context(analysis_id="new", cache_valid_until=original_expiry + timedelta(hours=1)),
        "later",
        "trace",
        scope="owner",
    )
    assert later.status == "succeeded"
    expiry = await pool.fetchval("SELECT cache_valid_until FROM jobs WHERE job_id=$1", later.job_id)
    assert expiry == original_expiry


@pytest.mark.asyncio
async def test_scoped_product_api_cannot_read_or_cancel_a_legacy_job(database):
    from test_storage import context as legacy_context
    from test_storage import request as legacy_request

    pool, _, _ = database
    repo = JobRepository(pool)
    accepted = await repo.enqueue("analysis", legacy_request(), legacy_context(), "legacy", "trace")
    for method in (repo.get_job, repo.cancel):
        with pytest.raises(DomainError) as error:
            await method(accepted.job_id, scope="owner")
        assert error.value.status_code == 404
    assert (await repo.get_job(accepted.job_id)).status == "queued"
    assert (await repo.cancel(accepted.job_id)).status == "cancelled"


@pytest.mark.asyncio
async def test_result_must_match_persisted_normalized_context_not_mutated_claim(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    await repo.enqueue("analysis", request(), context(), "first", "trace", scope="owner")
    claim = await repo.claim("token")
    forged = result().model_copy(deep=True)
    forged.request.city_id = "different-city"
    with pytest.raises(DomainError):
        await repo.succeed(claim, forged)
    forged_claim = claim.model_copy(deep=True)
    forged_claim.context.normalized_request.city_id = "different-city"
    with pytest.raises(DomainError):
        await repo.succeed(forged_claim, forged)
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0


@pytest.mark.asyncio
async def test_proposals_are_scoped_immutable_rebound_and_published_with_analysis(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    await repo.enqueue("analysis", request(), context(), "first", "trace", scope="owner")
    claim = await repo.claim("token")
    candidate = proposal(result())
    await repo.succeed(claim, result(), proposals=[candidate])
    saved = await repo.get_run("synthetic-analysis", scope="owner")
    proposals = await repo.get_proposals(saved.analysis_id, "owner")
    assert len(proposals) == 1
    command = proposals[0]
    assert (
        command.scenario_id != candidate.scenario_id
        and command.baseline_result_hash == saved.result_hash
    )
    assert (await repo.get_proposal(command.scenario_id, "owner")) == command
    assert await repo.save_proposals([command], "owner") == proposals
    for method, arg in [
        (repo.get_proposal, command.scenario_id),
        (repo.get_proposals, saved.analysis_id),
    ]:
        with pytest.raises(DomainError) as error:
            await method(arg, "another-owner")
        assert error.value.status_code == 404
    changed = command.model_copy(deep=True)
    changed.assumptions.append("not-the-published-proposal")
    with pytest.raises(DomainError):
        await repo.save_proposals([changed], "owner")


@pytest.mark.asyncio
async def test_invalid_proposal_rolls_back_analysis_and_job_publication_atomically(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    accepted = await repo.enqueue("analysis", request(), context(), "first", "trace", scope="owner")
    claim = await repo.claim("token")
    invalid = proposal(result()).model_copy(update={"baseline_analysis_id": "not-this-analysis"})
    with pytest.raises(DomainError):
        await repo.succeed(claim, result(), proposals=[invalid])
    assert (await repo.get_job(accepted.job_id, scope="owner")).status == "running"
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0
    assert await pool.fetchval("SELECT count(*) FROM scenario_proposals") == 0


async def prepare_scenario(repo):
    await repo.enqueue("analysis", request(), context(), "base", "trace", scope="owner")
    claim = await repo.claim("baseline")
    await repo.succeed(claim, result(), proposals=[proposal(result())])
    saved = await repo.get_run("synthetic-analysis", scope="owner")
    command = (await repo.get_proposals(saved.analysis_id, "owner"))[0]
    command_request = ScenarioRunRequest(
        schema_version="2", scenario_id=command.scenario_id, baseline_analysis_id=saved.analysis_id
    )
    command_context = context(scenario_proposal=command)
    accepted = await repo.enqueue(
        "scenario", command_request, command_context, "scenario", "trace", scope="owner"
    )
    return saved, command, accepted, await repo.claim("scenario-token")


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["before", "versions", "operation"])
async def test_scenario_publication_binds_saved_baseline_versions_and_frozen_command(
    database, change
):
    pool, _, _ = database
    repo = JobRepository(pool)
    saved, command, _, claim = await prepare_scenario(repo)
    scenario = scenario_result(saved, command)
    raw = scenario.model_dump(mode="json")
    if change == "before":
        raw["before"]["metrics"][0]["value"] = 1900
        raw["deltas"][0]["delta"] = 200
    elif change == "versions":
        raw["versions"]["graph_version"] = "another-graph"
    else:
        raw["service_changes"][0]["shift_seconds"] = 301
    with pytest.raises(DomainError):
        await repo.succeed(claim, ScenarioResult.model_validate(raw))
    assert await pool.fetchval("SELECT count(*) FROM runs") == 1
    await repo.succeed(claim, scenario)
    stored = await repo.get_run(scenario.run_id, scope="owner")
    assert stored.params_hash == claim.cache_key and stored.operations_hash != "provisional"


@pytest.mark.asyncio
async def test_cancellation_and_expired_claim_fence_publication_without_auto_retry(database):
    pool, _, _ = database
    repo = JobRepository(pool)
    accepted = await repo.enqueue("analysis", request(), context(), "first", "trace", scope="owner")
    claim = await repo.claim("token")
    assert await repo.heartbeat(claim) is True
    cancelled = await repo.cancel(accepted.job_id, "owner")
    assert cancelled.status == "cancelled" and cancelled.error is None
    assert await repo.heartbeat(claim) is False
    with pytest.raises(DomainError):
        await repo.succeed(claim, result())
    assert await repo.claim("no-auto-retry") is None
    assert await pool.fetchval("SELECT attempts FROM jobs WHERE job_id=$1", accepted.job_id) == 1
    fresh = await repo.enqueue(
        "analysis", request(), context(analysis_id="fresh"), "new-key", "trace", scope="owner"
    )
    fresh_claim = await repo.claim("new-token")
    await pool.execute(
        "UPDATE jobs SET lease_expires_at=now()-interval '1 second' WHERE job_id=$1", fresh.job_id
    )
    assert await repo.heartbeat(fresh_claim) is False
    with pytest.raises(DomainError):
        await repo.succeed(fresh_claim, result(analysis_id="fresh"))


def synthetic_success(claim):
    from a2_api.core.jobs import CalculationOutput

    saved = result(claim.context.normalized_request.analysis_kind, claim.context.analysis_id)
    return CalculationOutput(analysis=saved, proposals=[proposal(saved)])


@pytest.mark.asyncio
async def test_spawned_worker_can_publish_v2_analysis_and_proposals(database):
    from a2_api.workers.runner import Worker

    pool, _, _ = database
    repo = JobRepository(pool)
    accepted = await repo.enqueue("analysis", request(), context(), "first", "trace", scope="owner")
    await Worker(
        repo,
        handler="test_v2_storage:synthetic_success",
        code_version=context().code_version,
        timeout_seconds=5,
    ).run_once()
    ready = await repo.get_job(accepted.job_id, scope="owner")
    assert ready.status == "succeeded" and ready.run_id == "synthetic-analysis"
    assert len(await repo.get_proposals(ready.run_id, "owner")) == 1


@pytest.mark.asyncio
async def test_worker_cancellation_reaps_child_and_preserves_cancelled_state(
    database, tmp_path, monkeypatch
):
    import os

    from a2_api.workers.runner import Worker

    pool, _, _ = database
    repo = JobRepository(pool)
    accepted = await repo.enqueue("analysis", request(), context(), "first", "trace", scope="owner")
    pid_file = tmp_path / "v2-child.pid"
    monkeypatch.setenv("A2_TEST_CHILD_PID", str(pid_file))
    work = asyncio.create_task(
        Worker(
            repo,
            handler="worker_handlers:slow",
            code_version=context().code_version,
            timeout_seconds=5,
        ).run_once()
    )
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.02)
    assert pid_file.exists()
    await repo.cancel(accepted.job_id, "owner")
    await asyncio.wait_for(work, 3)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
    assert (await repo.get_job(accepted.job_id, scope="owner")).status == "cancelled"
