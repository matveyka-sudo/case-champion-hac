"""TEST_ONLY: real engine scenarios, private trusted scopes and PostgreSQL fences."""

import json

import pytest
from a2_api.adapters import product_scenarios as bridge
from a2_api.core.errors import DomainError
from a2_api.core.jobs import V2JobContext
from a2_api.db.repositories.jobs import JobRepository
from a2_contracts.common import canonical_hash
from a2_contracts.v2.scenarios import ScenarioResult, ScenarioRunRequest
from test_product_scenarios import (
    closest_destination_case,
    intercity_case,
    make_case,
    trusted_urban_scope,
)


async def queued_case(pool, case):
    """Act as a trusted TEST_ONLY source loader, never as a calculation output."""
    context, facts, document, baseline, _ = case
    endpoint_scope = (
        trusted_urban_scope(case) if baseline.analysis_kind == "urban_passenger" else None
    )
    if endpoint_scope is not None:
        context = context.model_copy(update={"snapshot_hash": endpoint_scope.snapshot_hash})
        document = document.model_copy(update={"snapshot_hash": endpoint_scope.snapshot_hash})
    repository = JobRepository(pool)
    commands = bridge.propose(context, facts, document, baseline, profile_id=document.profile_id)
    await repository.enqueue(
        "analysis", context.normalized_request, context, "base", "trace", scope=context.access_scope
    )
    await repository.succeed(
        await repository.claim("baseline-worker"), baseline, proposals=commands
    )
    saved = await repository.get_run(baseline.analysis_id, context.access_scope)
    command = (await repository.get_proposals(saved.analysis_id, context.access_scope))[0]
    payload = context.model_dump(mode="json")
    payload.update(
        scenario_proposal=command.model_dump(mode="json"),
        scenario_endpoint_scope=endpoint_scope.model_dump(mode="json")
        if endpoint_scope is not None
        else None,
    )
    if "batch_execution" in V2JobContext.model_fields:
        payload["batch_execution"] = None
    context = V2JobContext.model_validate(payload)
    request = ScenarioRunRequest(
        schema_version="2", scenario_id=command.scenario_id, baseline_analysis_id=saved.analysis_id
    )
    await repository.enqueue(
        "scenario", request, context, "scenario", "trace", scope=context.access_scope
    )
    claim = await repository.claim("scenario-worker")
    candidate = bridge.calculate(
        claim, baseline=saved, inputs=facts, document=document, profile_id=document.profile_id
    )
    return repository, claim, candidate, saved


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["new_intercity", "new_urban", "changed_closest_urban"])
async def test_actual_engine_scenario_roundtrip_replay_cache_and_private_scope(database, kind):
    pool, _, _ = database
    case = (
        intercity_case()
        if kind == "new_intercity"
        else make_case()
        if kind == "new_urban"
        else closest_destination_case()
    )
    repository, claim, candidate, baseline = await queued_case(pool, case)
    baseline_bytes = baseline.model_dump_json()
    await repository.succeed(claim, candidate)
    stored = await repository.get_run(candidate.run_id, claim.context.access_scope)
    assert stored.before.metrics == baseline.metrics and stored.before.scores == baseline.scores
    assert stored.after_journeys
    assert (
        await repository.get_run(baseline.analysis_id, claim.context.access_scope)
    ).model_dump_json() == baseline_bytes
    if kind == "changed_closest_urban":
        assert any(
            row.point_id == "p2" and row.destination_object_id == "d2"
            for row in stored.after_journeys
        )
    else:
        assert any(row.outcome == "new_connection" for row in stored.deltas)
    parameters = await repository.get_run_parameters(stored.run_id, claim.context.access_scope)
    assert parameters["context"]["scenario_proposal"]["scenario_id"] == stored.scenario_id
    replay = await repository.replay(
        "scenario", claim.request, "scenario", claim.context.access_scope
    )
    assert replay.status == "succeeded"
    cached = await repository.enqueue(
        "scenario",
        claim.request,
        claim.context,
        "cached",
        "trace",
        scope=claim.context.access_scope,
    )
    assert cached.status == "succeeded"
    for reader in (repository.get_run, repository.get_run_parameters):
        with pytest.raises(DomainError) as denied:
            await reader(stored.run_id, "TEST_ONLY-foreign-owner")
        assert denied.value.status_code == 404


def forged_candidate(candidate, attack):
    payload = candidate.model_dump(mode="json")
    journey = next(row for row in payload["after_journeys"] if row["point_id"] == "p1")
    if attack == "origin":
        journey["origin_node_id"] = journey["legs"][0]["from_node_id"] = "TEST_ONLY-foreign-origin"
    elif attack == "object":
        journey["destination_object_id"] = "TEST_ONLY-foreign-object"
    elif attack == "mode":
        for leg in journey["legs"]:
            if leg["kind"] != "walk":
                leg["mode"] = "private_car"
    else:
        omitted = {
            row["journey_id"] for row in payload["after_journeys"] if row["point_id"] == "p1"
        }
        payload["after_journeys"] = [
            row for row in payload["after_journeys"] if row["point_id"] != "p1"
        ]
        for metric in payload["after"]["metrics"]:
            if metric["journey_id"] in omitted:
                metric["journey_id"] = None
    return ScenarioResult.model_validate(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["origin", "object", "mode", "missing_proof"])
async def test_forged_urban_worker_output_never_publishes_and_claim_can_retry(database, attack):
    pool, _, _ = database
    repository, claim, candidate, baseline = await queued_case(pool, make_case())
    with pytest.raises(DomainError):
        await repository.succeed(claim, forged_candidate(candidate, attack))
    assert await pool.fetchval("SELECT count(*) FROM runs WHERE result_kind='scenario_v2'") == 0
    assert (
        await repository.get_run(baseline.analysis_id, claim.context.access_scope)
    ).result_hash == baseline.result_hash
    await repository.succeed(claim, candidate)
    assert (await repository.get_run(candidate.run_id, claim.context.access_scope)).after_journeys


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attack", ["forged_result", "private_scope", "private_snapshot", "missing_job"]
)
async def test_stored_new_urban_scenarios_revalidate_immutable_job_context_on_read(
    database, attack
):
    pool, _, _ = database
    repository, claim, candidate, baseline = await queued_case(pool, make_case())
    await repository.succeed(claim, candidate)
    if attack == "forged_result":
        forged = forged_candidate(candidate, "origin").model_dump(mode="json")
        forged["params_hash"] = claim.cache_key
        forged["result_hash"] = canonical_hash(
            {key: value for key, value in forged.items() if key != "result_hash"}
        )
        await pool.execute(
            "UPDATE runs SET payload=$2::jsonb,result_hash=$3 WHERE run_id=$1",
            candidate.run_id,
            json.dumps(forged),
            forged["result_hash"],
        )
    elif attack == "missing_job":
        await pool.execute("DELETE FROM jobs WHERE job_id=$1", claim.job.job_id)
    else:
        payload = json.loads(
            await pool.fetchval("SELECT payload FROM jobs WHERE job_id=$1", claim.job.job_id)
        )
        if attack == "private_scope":
            payload["context"]["scenario_endpoint_scope"]["point_origins"]["p1"] = (
                "TEST_ONLY-foreign-origin"
            )
        else:
            payload["context"]["snapshot_hash"] = "f" * 64
            payload["context"]["scenario_endpoint_scope"]["snapshot_hash"] = "f" * 64
        await pool.execute(
            "UPDATE jobs SET payload=$2::jsonb WHERE job_id=$1",
            claim.job.job_id,
            json.dumps(payload),
        )
    unchanged = await pool.fetchrow(
        "SELECT payload,result_hash FROM runs WHERE run_id=$1", candidate.run_id
    )
    for reader in (repository.get_run, repository.get_run_parameters):
        with pytest.raises(DomainError) as invalid:
            await reader(candidate.run_id, claim.context.access_scope)
        assert invalid.value.code == "stored_result_incompatible"
    assert (
        await pool.fetchrow(
            "SELECT payload,result_hash FROM runs WHERE run_id=$1", candidate.run_id
        )
        == unchanged
    )
    assert (
        await repository.get_run(baseline.analysis_id, claim.context.access_scope)
    ).result_hash == baseline.result_hash


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["accepted", "missing", "changed_hash"])
async def test_service_freezes_canonical_endpoint_scope_only_from_saved_snapshot(
    monkeypatch, source
):
    from a2_contracts.v2.scenarios import ScenarioRunOptions
    from test_v2_scenario_service import ProposalRepository
    from test_v2_storage import proposal
    from test_v2_tools import ScopedSavedService

    service = ScopedSavedService()
    command = proposal(service.saved)
    repository = ProposalRepository(service.saved, command)
    snapshot = service.registry.snapshots[service.saved.snapshot_id]
    repository.context.snapshot_hash = canonical_hash(snapshot.model_dump(mode="json"))
    if source == "changed_hash":
        repository.context.snapshot_hash = "f" * 64
    if source == "missing":

        async def unavailable(*args):
            return None

        monkeypatch.setattr(service, "accepted_snapshot", unavailable)
    service.repository = repository
    service.code_version = service.saved.versions.code_version
    service.engine_enabled = True
    service.registered_template_ids = frozenset({command.template_id})
    original = repository.enqueue
    captured = []

    async def capture(kind, request, context, key, request_id, scope):
        captured.append(context)
        return await original(kind, request, context, key, request_id, scope)

    monkeypatch.setattr(repository, "enqueue", capture)
    if source != "accepted":
        with pytest.raises(DomainError, match="accepted snapshot"):
            await service.scenario(
                command.scenario_id, ScenarioRunOptions(schema_version="2"), "key", "trace", "owner"
            )
        assert not captured
        return
    await service.scenario(
        command.scenario_id, ScenarioRunOptions(schema_version="2"), "key", "trace", "owner"
    )
    context = captured[0]
    frozen = context.scenario_endpoint_scope
    assert frozen.snapshot_hash == canonical_hash(snapshot.model_dump(mode="json"))
    assert frozen.point_origins == {
        point.point_id: "physical:point:" + point.point_id
        for point in snapshot.sample_points
        if point.territory_id == service.saved.request.city_id
    }
    assert repository.context.scenario_proposal is None
    assert repository.context.scenario_endpoint_scope is None
    if "batch_execution" in type(context).model_fields:
        assert context.batch_execution is None


@pytest.mark.parametrize("expected_hash", [None, "wrong", "source"])
def test_publication_checks_scope_hash_against_trusted_context_not_worker_result(expected_hash):
    from a2_api.db.validation import _validate_scenario_semantics
    from test_product_scenarios import with_claim

    case = closest_destination_case()
    scope = trusted_urban_scope(case)
    claim, facts, document, baseline, _ = with_claim(case)
    result = bridge.calculate(
        claim, baseline=baseline, inputs=facts, document=document, profile_id=document.profile_id
    )
    expected = (
        scope.snapshot_hash
        if expected_hash == "source"
        else "f" * 64
        if expected_hash == "wrong"
        else None
    )
    if expected_hash == "source":
        _validate_scenario_semantics(
            result,
            baseline,
            claim.context.scenario_proposal,
            endpoint_scope=scope,
            expected_snapshot_hash=expected,
        )
    else:
        with pytest.raises(DomainError, match="scope source hash"):
            _validate_scenario_semantics(
                result,
                baseline,
                claim.context.scenario_proposal,
                endpoint_scope=scope,
                expected_snapshot_hash=expected,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["kind", "unit"])
async def test_changed_raw_point_task_cannot_publish_full_indices_without_chain(database, attack):
    from test_product_scenarios import forged_time_task

    pool, _, _ = database
    repository, claim, candidate, baseline = await queued_case(pool, make_case())
    with pytest.raises(DomainError, match="time task identity"):
        await repository.succeed(claim, forged_time_task(candidate, attack))
    assert await pool.fetchval("SELECT count(*) FROM runs WHERE result_kind='scenario_v2'") == 0
    assert (
        await repository.get_run(baseline.analysis_id, claim.context.access_scope)
    ).result_hash == baseline.result_hash
