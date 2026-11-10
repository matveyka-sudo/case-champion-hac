"""TEST_ONLY HTTP observations over real accepted endpoints; no live provider calls."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from a2_api.adapters import walk_batches, walk_pilot
from a2_api.core.batches import BatchExecution
from a2_api.core.jobs import V2JobContext, batch_context_fingerprint
from a2_api.db.repositories.checkpoints import CheckpointRepository, validate_raw_files
from a2_api.db.repositories.jobs import JobRepository
from a2_contracts.common import canonical_hash
from a2_data_pipeline.store import load_bundle
from test_walk_pilot import claim_for as pilot_claim
from test_walk_pilot import configured as configured
from test_walk_pilot import request as pilot_request
from test_walk_pilot import transport


def write(path, value):
    raw = json.dumps(value, sort_keys=True).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def approved(tmp_path, monkeypatch, configured):
    raw, path, snapshot = configured
    graph = dict(
        source_sha256="1" * 64,
        files=[dict(path="tiles/0/TEST_ONLY.gph", sha256="2" * 64, bytes=10)],
        image_id="TEST_ONLY-image",
        image_repo_digests=[],
        config_sha256="3" * 64,
    )
    graph_hash = write(tmp_path / "TEST_ONLY-graph.json", graph)
    raw["build"]["graph_version"] = "TEST_ONLY-graph-" + graph_hash
    artifact_hash = write(path, raw)
    monkeypatch.setenv("A2_WALK_PILOT_ARTIFACT_SHA256", artifact_hash)
    runtime = walk_pilot.load_runtime()
    approval = dict(
        schema_version="waydex-walk-batch-approval/1",
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=canonical_hash(snapshot.model_dump(mode="json")),
        bundle_manifest_hash=canonical_hash(
            load_bundle(snapshot.snapshot_id).manifest.model_dump(mode="json")
        ),
        runtime_artifact_hash=artifact_hash,
        routing_binding=dict(
            schema_version="waydex-routing-binding/1",
            graph_manifest=dict(path="TEST_ONLY-graph.json", sha256=graph_hash),
            source_pbf_sha256=graph["source_sha256"],
            tile_manifest_sha256=canonical_hash(graph["files"]),
            build_image_digest=graph["image_id"],
            build_configuration_hash=graph["config_sha256"],
            runtime_build_id=runtime.build.build_id,
            source_graph_version=runtime.build.source_graph_version,
            endpoint_url=runtime.endpoint_url,
            profile_fingerprint=runtime.build.profile_hash,
            immutable_deployment_attested=True,
            attested_by="TEST_ONLY-owner",
        ),
        city_id="kaliningrad",
        categories=["school"],
        pairs=[
            dict(point_id=p, destination_id=d)
            for p in runtime.point_ids
            for d in runtime.destination_ids
        ],
        batch_size=1,
        max_attempts=3,
        max_provider_calls=100,
        max_checkpoint_bytes=1024 * 1024,
        observation_retention_seconds=3600,
        valid_until=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    )
    digest = write(tmp_path / "TEST_ONLY-batch-approval.json", approval)
    monkeypatch.setenv(
        "A2_WALK_BATCH_APPROVAL_PATH", str(tmp_path / "TEST_ONLY-batch-approval.json")
    )
    monkeypatch.setenv("A2_WALK_BATCH_APPROVAL_SHA256", digest)
    monkeypatch.setenv("A2_CODE_VERSION", "TEST_ONLY-" + artifact_hash + "-" + digest)
    actual_client = httpx.Client

    def client(*args, **kwargs):
        return actual_client(*args, transport=transport(), **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    return snapshot, approval, raw, path


class AcknowledgedChannel:
    """TEST_ONLY parent stand-in; the actual parent transaction is tested separately."""

    def __init__(self, execution, completed=()):
        self.execution = execution
        self.completed = list(completed)
        for checkpoint in self.completed:
            validate_raw_files(checkpoint, execution, datetime.now(UTC))

    def commit(self, checkpoint):
        validate_raw_files(checkpoint, self.execution, datetime.now(UTC))
        self.completed.append(checkpoint)


async def prepared_claim(snapshot):
    batch = await walk_batches.preflight(pilot_request(), snapshot)
    claim = pilot_claim(snapshot)
    context = claim.context
    execution = BatchExecution(
        **batch.model_dump(mode="python"),
        access_scope=context.access_scope,
        context_fingerprint=batch_context_fingerprint(context),
    )
    context = V2JobContext.model_validate(
        {**context.model_dump(mode="json"), "batch_execution": execution.model_dump(mode="json")}
    )
    return claim.model_copy(update={"context": context})


@pytest.mark.asyncio
async def test_approved_real_executor_checkpoints_and_resumes_identically(approved, monkeypatch):
    snapshot, _, _, _ = approved
    claim = await prepared_claim(snapshot)
    channel = AcknowledgedChannel(claim.context.batch_execution)
    first = walk_batches.calculate(claim, channel)
    assert channel.completed and first.status == "partial"
    assert first.journeys and all(not s.ranking_eligible and s.value is None for s in first.scores)
    assert all(not o.envelope.search_complete for c in channel.completed for o in c.observations)

    def forbidden(*args, **kwargs):
        raise AssertionError("Committed observations must not be rerouted")

    monkeypatch.setattr(walk_pilot, "execute_route", forbidden)
    second = walk_batches.calculate(
        claim, AcknowledgedChannel(claim.context.batch_execution, reversed(channel.completed))
    )
    assert second.result_hash == first.result_hash
    assert second == first


@pytest.mark.asyncio
async def test_raw_observation_corruption_refuses_resume(approved):
    snapshot, _, _, path = approved
    claim = await prepared_claim(snapshot)
    channel = AcknowledgedChannel(claim.context.batch_execution)
    walk_batches.calculate(claim, channel)
    raw = channel.completed[0].observations[0].raw_observations[0]
    (path.parent / (raw.sha256 + ".json")).write_text("TEST_ONLY-corrupt")
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        AcknowledgedChannel(claim.context.batch_execution, channel.completed)


@pytest.mark.asyncio
@pytest.mark.parametrize("attestation", ["endpoint", "pedestrian", "minimum"])
async def test_checkpoint_attestations_must_match_raw_provider_facts(approved, attestation):
    snapshot, _, _, _ = approved
    claim = await prepared_claim(snapshot)
    channel = AcknowledgedChannel(claim.context.batch_execution)
    walk_batches.calculate(claim, channel)
    checkpoint = channel.completed[0]
    observation = checkpoint.observations[0]
    changes = {
        "endpoint": {"matched_origin": None},
        "pedestrian": {"pure_walk_evidence_refs": ()},
        "minimum": {"search_complete": True},
    }[attestation]
    altered = observation.model_copy(update={"envelope": replace(observation.envelope, **changes)})
    forged = checkpoint.model_copy(update={"observations": [altered]})
    with pytest.raises(ValueError, match="ATTESTATION|CANNOT_ATTEST"):
        validate_raw_files(forged, claim.context.batch_execution, datetime.now(UTC))


@pytest.mark.asyncio
async def test_changed_physical_graph_manifest_fails_before_routing(approved):
    snapshot, _, _, path = approved
    (path.parent / "TEST_ONLY-graph.json").write_text("{}")
    with pytest.raises(Exception, match="APPROVAL_OR_BINDING_UNAVAILABLE"):
        await walk_batches.preflight(pilot_request(), snapshot)


@pytest.mark.asyncio
async def test_repeated_preflight_does_not_spend_unreserved_provider_calls(approved, monkeypatch):
    snapshot, _, _, _ = approved

    def forbidden(*args, **kwargs):
        raise AssertionError("Provider calls require a fenced reserved attempt")

    monkeypatch.setattr(walk_pilot, "check_status", forbidden)
    monkeypatch.setattr(walk_pilot, "execute_route", forbidden)
    for _ in range(6):
        batch = await walk_batches.preflight(pilot_request(), snapshot)
        assert batch.pairs


@pytest.mark.asyncio
async def test_atomic_publication_cache_ttl_uses_earliest_raw_evidence(
    database, approved, monkeypatch
):
    pool, _, _ = database
    snapshot, approval, _, path = approved
    approval["observation_retention_seconds"] = 1800
    digest = write(path.parent / "TEST_ONLY-batch-approval.json", approval)
    monkeypatch.setenv("A2_WALK_BATCH_APPROVAL_SHA256", digest)
    monkeypatch.setenv(
        "A2_CODE_VERSION", "TEST_ONLY-" + approval["runtime_artifact_hash"] + "-" + digest
    )
    source = await prepared_claim(snapshot)
    repo = JobRepository(pool)
    await repo.enqueue(
        "analysis",
        source.request,
        source.context,
        "TEST_ONLY-raw-expiry",
        "TEST_ONLY-trace",
        scope=source.context.access_scope,
    )
    claim = await repo.claim("TEST_ONLY-claim")
    progress = CheckpointRepository(repo)
    await progress.begin(claim)
    execution = claim.context.batch_execution
    channel = AcknowledgedChannel(execution)
    result = walk_batches.calculate(claim, channel)
    for checkpoint in channel.completed:
        await progress.put(claim, checkpoint)
    await repo.succeed(claim, result)
    expiry = await pool.fetchval(
        "SELECT cache_valid_until FROM jobs WHERE job_id=$1", claim.job.job_id
    )
    earliest = min(
        evidence.retrieved_at
        for checkpoint in channel.completed
        for observation in checkpoint.observations
        for evidence in observation.evidence
    )
    assert expiry == earliest + timedelta(seconds=execution.observation_retention_seconds)
    saved = await repo.get_run_parameters(result.analysis_id, scope=claim.context.access_scope)
    assert V2JobContext.model_validate(saved["context"]).cache_valid_until == expiry


def test_multiple_approved_points_do_not_inherit_single_point_pilot_limit(approved, monkeypatch):
    snapshot, approval, raw, path = approved
    other = next(
        point for point in snapshot.sample_points if point.point_id not in raw["point_ids"]
    )
    raw["point_ids"].append(other.point_id)
    artifact_hash = write(path, raw)
    approval["pairs"].append(
        dict(point_id=other.point_id, destination_id=raw["destination_ids"][0])
    )
    approval["runtime_artifact_hash"] = artifact_hash
    approval_path = path.parent / "TEST_ONLY-batch-approval.json"
    digest = write(approval_path, approval)
    monkeypatch.setenv("A2_WALK_PILOT_ARTIFACT_SHA256", artifact_hash)
    monkeypatch.setenv("A2_WALK_BATCH_APPROVAL_SHA256", digest)
    monkeypatch.setenv("A2_CODE_VERSION", "TEST_ONLY-" + artifact_hash + "-" + digest)
    batch, plan, runtime = walk_batches._prepare(pilot_request(), snapshot)
    assert len(runtime.point_ids) == len(plan.point_ids) == 2
    assert len(batch.pairs) == 1  # Other physical connector/correlation stays unknown.
    assert batch.approved_pair_count == 2 and batch.unresolved_pair_count == 1


def test_sparse_planning_retains_full_catalog_without_unapproved_cartesian_work(
    approved, monkeypatch
):
    snapshot, approval, _, _ = approved
    original = walk_batches.plan_walk_routes
    planned = []

    def bounded(inputs, *, point_ids, categories):
        planned.extend(
            (point_id, endpoint.entity_id)
            for point_id in point_ids
            for endpoint in inputs.destination_endpoints
        )
        return original(inputs, point_ids=point_ids, categories=categories)

    monkeypatch.setattr(walk_batches, "plan_walk_routes", bounded)
    batch, plan, _ = walk_batches._prepare(pilot_request(), snapshot)
    expected = {(pair["point_id"], pair["destination_id"]) for pair in approval["pairs"]}
    assert set(planned) == expected and len(planned) == len(expected)
    assert len(plan.inputs.destination_endpoints) > len(planned)
    assert len(plan.inputs.destinations) == len(plan.inputs.destination_endpoints)
    assert len(batch.pairs) <= len(planned)
