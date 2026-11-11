"""TEST_ONLY permission metadata and disposable PostgreSQL; no provider data."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from a2_api.core.errors import DomainError
from a2_api.core.jobs import V2JobContext, cache_fingerprint
from a2_api.core.private_sources import (
    PrivateSourceBinding,
    PrivateSourceDescriptor,
    PrivateSourceGrant,
)
from a2_api.db.repositories.jobs import JobRepository
from a2_api.db.repositories.private_sources import PrivateSourceRepository
from a2_contracts.common import canonical_hash
from a2_data_pipeline.rasp_inputs import PrivateCachePolicy
from pydantic import ValidationError
from test_product_scenario_runtime import bound_case


def permission(context, now, *, expires_at=None, external_model_use=False):
    """A trusted synthetic operator receipt, unrelated to provider observations."""
    policy = PrivateCachePolicy(
        schema_version="waydex-private-source-validity/1",
        observed_at=now - timedelta(minutes=1),
        expires_at=expires_at or now + timedelta(hours=1),
        usage_scope="temporary_provider_service_cache",
        authorization_ref="TEST_ONLY-operator-authorization",
        purge_policy_ref="TEST_ONLY-purge",
    )
    source = PrivateSourceDescriptor(
        source_kind="yandex_rasp",
        episode_id="rasp:" + "a" * 64,
        manifest_sha256="a" * 64,
        snapshot_id=context.snapshot_id,
        snapshot_hash=context.snapshot_hash,
        policy=policy,
        policy_hash=canonical_hash(policy.model_dump(mode="json")),
        copyright_hash="b" * 64,
    )
    grant = PrivateSourceGrant(
        grant_id="TEST_ONLY-grant",
        episode_id=source.episode_id,
        access_scope=context.access_scope,
        authorization_ref=policy.authorization_ref,
        approved_by="TEST_ONLY-operator",
        valid_from=policy.observed_at,
        valid_until=policy.expires_at,
        external_model_use=external_model_use,
    )
    return PrivateSourceBinding(source=source, grant=grant, grant_hash=grant.content_hash)


def frozen(context, binding):
    return V2JobContext.model_validate(
        context.model_dump(mode="json")
        | {"private_source_bindings": [binding.model_dump(mode="json")]}
    )


def test_nonprivate_serialization_and_cache_identity_are_byte_compatible():
    _, case = bound_case()
    original = case[0]
    payload = original.model_dump(mode="json")
    assert "private_source_bindings" not in payload
    empty = V2JobContext.model_validate(payload | {"private_source_bindings": []})
    assert empty.model_dump_json() == original.model_dump_json()
    assert cache_fingerprint("analysis", original.normalized_request, original) == canonical_hash(
        {
            "kind": "analysis",
            "context": {
                k: v for k, v in payload.items() if k not in {"analysis_id", "cache_valid_until"}
            },
        }
    )
    binding = permission(original, datetime.now(UTC))
    protected = frozen(original, binding)
    assert cache_fingerprint(
        "analysis", original.normalized_request, protected
    ) != cache_fingerprint("analysis", original.normalized_request, original)


@pytest.mark.parametrize(
    "attack", ["scope", "snapshot", "policy_hash", "grant_hash", "extended_grant", "duplicate"]
)
def test_binding_cannot_be_broadened_or_detached_from_source(attack):
    _, case = bound_case()
    context = case[0]
    binding = permission(context, datetime.now(UTC)).model_dump(mode="json")
    if attack == "scope":
        binding["grant"]["access_scope"] = "FOREIGN"
    elif attack == "snapshot":
        binding["source"]["snapshot_hash"] = "f" * 64
    elif attack == "policy_hash":
        binding["source"]["policy_hash"] = "f" * 64
    elif attack == "grant_hash":
        binding["grant_hash"] = "f" * 64
    elif attack == "extended_grant":
        binding["grant"]["valid_until"] = "2099-01-01T00:00:00Z"
    with pytest.raises(ValidationError):
        V2JobContext.model_validate(
            context.model_dump(mode="json")
            | {
                "private_source_bindings": [binding, binding]
                if attack == "duplicate"
                else [binding]
            }
        )


async def admitted(
    pool,
    *,
    lifetime=None,
    publish=False,
    proposals=False,
    external_model_use=False,
    claim_job=True,
    binding_count=1,
):
    from a2_api.adapters.product_scenarios import propose

    _, case = bound_case()
    context, facts, document, baseline, _ = case
    now = await pool.fetchval("SELECT clock_timestamp()")
    binding = permission(
        context,
        now,
        expires_at=now + lifetime if lifetime else None,
        external_model_use=external_model_use,
    )
    await PrivateSourceRepository(pool).register(binding.source, binding.grant)
    bindings = [binding]
    if binding_count == 2:
        other = permission(context, now)
        source = other.source.model_copy(
            update={"episode_id": "rasp:" + "c" * 64, "manifest_sha256": "c" * 64}
        )
        grant = other.grant.model_copy(
            update={"grant_id": "TEST_ONLY-grant-2", "episode_id": source.episode_id}
        )
        other = PrivateSourceBinding(source=source, grant=grant, grant_hash=grant.content_hash)
        await PrivateSourceRepository(pool).register(other.source, other.grant)
        bindings.append(other)
    context = V2JobContext.model_validate(
        context.model_dump(mode="json")
        | {"private_source_bindings": [row.model_dump(mode="json") for row in bindings]}
    )
    repository = JobRepository(pool)
    await repository.enqueue(
        "analysis",
        context.normalized_request,
        context,
        "TEST_ONLY-key",
        "trace",
        scope=context.access_scope,
    )
    claim = await repository.claim("TEST_ONLY-worker") if claim_job else None
    commands = (
        propose(context, facts, document, baseline, profile_id=document.profile_id)
        if proposals
        else []
    )
    if publish:
        await repository.succeed(claim, baseline, proposals=commands)
    return repository, claim, baseline, binding, commands


@pytest.mark.asyncio
async def test_scoped_private_run_roundtrip_and_general_cache_expiry_are_separate(database):
    pool, _, _ = database
    repository, claim, baseline, binding, _ = await admitted(pool, publish=True)
    before = await repository.get_run(baseline.analysis_id, claim.context.access_scope)
    assert before.metrics == baseline.metrics
    metadata = await pool.fetchval(
        "SELECT private_source_bindings FROM runs WHERE run_id=$1", baseline.analysis_id
    )
    assert metadata and "private_source_bindings" not in before.model_dump(mode="json")
    await pool.execute(
        "UPDATE jobs SET cache_valid_until=clock_timestamp()-interval '1 second' WHERE job_id=$1",
        claim.job.job_id,
    )
    assert (
        await repository.get_run(baseline.analysis_id, claim.context.access_scope)
    ).result_hash == before.result_hash
    with pytest.raises(DomainError) as foreign:
        await repository.get_run(baseline.analysis_id, "FOREIGN")
    assert foreign.value.status_code == 404
    await pool.execute(
        "UPDATE private_source_grants SET revoked_at=clock_timestamp() WHERE grant_id=$1",
        binding.grant.grant_id,
    )
    for reader in (repository.get_run, repository.get_run_parameters):
        with pytest.raises(DomainError) as denied:
            await reader(baseline.analysis_id, claim.context.access_scope)
        assert denied.value.status_code == 410
    assert (
        await pool.fetchval("SELECT result_hash FROM runs WHERE run_id=$1", baseline.analysis_id)
        == before.result_hash
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attack", ["missing", "empty", "altered", "missing_job", "dropped_context", "purging"]
)
async def test_private_side_metadata_cannot_downgrade_to_permanent_public(database, attack):
    pool, _, _ = database
    repository, claim, baseline, binding, _ = await admitted(pool, publish=True)
    if attack in {"missing", "empty", "altered"}:
        value = None if attack == "missing" else "[]" if attack == "empty" else "{}"
        await pool.execute(
            "UPDATE runs SET private_source_bindings=$2::jsonb WHERE run_id=$1",
            baseline.analysis_id,
            value,
        )
    elif attack == "missing_job":
        await pool.execute("DELETE FROM jobs WHERE job_id=$1", claim.job.job_id)
    elif attack == "dropped_context":
        await pool.execute(
            "UPDATE jobs SET payload=payload#-'{context,private_source_bindings}' WHERE job_id=$1",
            claim.job.job_id,
        )
    else:
        await pool.execute(
            "UPDATE private_source_episodes SET state='purging' WHERE episode_id=$1",
            binding.source.episode_id,
        )
    with pytest.raises(DomainError) as denied:
        await repository.get_run(baseline.analysis_id, claim.context.access_scope)
    assert denied.value.status_code == 410


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["revoke", "expire"])
async def test_publication_fence_after_expensive_validation_rolls_back_run_and_proposals(
    database, monkeypatch, mutation
):
    pool, _, _ = database
    repository, claim, baseline, binding, commands = await admitted(
        pool, lifetime=timedelta(seconds=1) if mutation == "expire" else None, proposals=True
    )
    ready, release = asyncio.Event(), asyncio.Event()
    original = repository._save_proposals

    async def paused(*args, **kwargs):
        result = await original(*args, **kwargs)
        ready.set()
        await release.wait()
        return result

    monkeypatch.setattr(repository, "_save_proposals", paused)
    task = asyncio.create_task(repository.succeed(claim, baseline, proposals=commands))
    await asyncio.wait_for(ready.wait(), 3)
    if mutation == "revoke":
        await pool.execute(
            "UPDATE private_source_grants SET revoked_at=clock_timestamp() WHERE grant_id=$1",
            binding.grant.grant_id,
        )
    else:
        await asyncio.sleep(
            max(
                0,
                (
                    binding.valid_until - await pool.fetchval("SELECT clock_timestamp()")
                ).total_seconds(),
            )
            + 0.02
        )
    release.set()
    with pytest.raises(DomainError):
        await task
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0
    assert await pool.fetchval("SELECT count(*) FROM scenario_proposals") == 0
    assert (
        await repository.get_job(claim.job.job_id, scope=claim.context.access_scope)
    ).status == "running"


@pytest.mark.asyncio
async def test_repeatable_read_cannot_keep_a_revoked_permission_alive(database, monkeypatch):
    pool, _, _ = database
    repository, claim, baseline, binding, _ = await admitted(pool, publish=True)
    ready, release = asyncio.Event(), asyncio.Event()
    original = repository._read_run

    async def paused(*args, **kwargs):
        result = await original(*args, **kwargs)
        ready.set()
        await release.wait()
        return result

    monkeypatch.setattr(repository, "_read_run", paused)
    task = asyncio.create_task(repository.get_run(baseline.analysis_id, claim.context.access_scope))
    await asyncio.wait_for(ready.wait(), 3)
    await pool.execute(
        "UPDATE private_source_grants SET revoked_at=clock_timestamp() WHERE grant_id=$1",
        binding.grant.grant_id,
    )
    release.set()
    with pytest.raises(DomainError) as denied:
        await task
    assert denied.value.status_code == 410


@pytest.mark.asyncio
async def test_permanent_nonprivate_result_survives_cache_freshness_deadline(database):
    pool, _, _ = database
    _, case = bound_case()
    context, _, _, baseline, _ = case
    context = context.model_copy(
        update={"cache_valid_until": datetime.now(UTC) - timedelta(days=1)}
    )
    repository = JobRepository(pool)
    await repository.enqueue(
        "analysis",
        context.normalized_request,
        context,
        "permanent",
        "trace",
        scope=context.access_scope,
    )
    claim = await repository.claim("TEST_ONLY-worker")
    await repository.succeed(claim, baseline)
    saved = await repository.get_run(baseline.analysis_id, context.access_scope)
    assert saved.metrics == baseline.metrics
    assert (
        await pool.fetchval(
            "SELECT private_source_bindings FROM runs WHERE run_id=$1", baseline.analysis_id
        )
        is None
    )
    assert (
        await repository.replay(
            "analysis", context.normalized_request, "permanent", context.access_scope
        )
    ).status == "succeeded"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state", ["missing", "revoked", "purging", "altered_hash", "future", "expired"]
)
async def test_admission_requires_current_exact_operator_grant(database, state):
    pool, _, _ = database
    _, case = bound_case()
    context = case[0]
    now = await pool.fetchval("SELECT clock_timestamp()")
    binding = permission(
        context, now, expires_at=now - timedelta(seconds=1) if state == "expired" else None
    )
    if state == "future":
        grant = binding.grant.model_copy(update={"valid_from": now + timedelta(minutes=1)})
        binding = PrivateSourceBinding(
            source=binding.source, grant=grant, grant_hash=grant.content_hash
        )
    repository = PrivateSourceRepository(pool)
    if state in {"future", "expired"}:
        with pytest.raises(DomainError):
            await repository.register(binding.source, binding.grant)
    elif state != "missing":
        await repository.register(binding.source, binding.grant)
        if state == "revoked":
            await pool.execute("UPDATE private_source_grants SET revoked_at=clock_timestamp()")
        elif state == "purging":
            await pool.execute("UPDATE private_source_episodes SET state='purging'")
        else:
            await pool.execute("UPDATE private_source_grants SET content_hash='FOREIGN'")
    with pytest.raises(DomainError):
        await JobRepository(pool).enqueue(
            "analysis",
            context.normalized_request,
            frozen(context, binding),
            "denied",
            "trace",
            scope=context.access_scope,
        )
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 0


@pytest.mark.asyncio
async def test_external_model_is_not_called_for_private_result_without_operator_permission(
    database,
):
    from a2_api.services.analyses import AnalysisService

    pool, _, _ = database
    repository, claim, baseline, _, _ = await admitted(pool, publish=True)

    class ForbiddenAssistant:
        async def explain(self, *args, **kwargs):
            pytest.fail("Private source escaped to external model")

    service = AnalysisService(repository=repository, assistant=ForbiddenAssistant())
    result = await service.explain(baseline.analysis_id, claim.context.access_scope, "trace")
    assert result.status == "template"


@pytest.mark.asyncio
async def test_initialized_private_history_blocks_migration_downgrade(database):
    from apps.api.tests.migration_support import migrate

    pool, schema, url = database
    await admitted(pool)
    with pytest.raises(RuntimeError, match="Private source evidence and retention history"):
        await asyncio.to_thread(migrate, url, schema, "0006_rasp_quota", True)
    assert await pool.fetchval("SELECT count(*) FROM private_source_episodes") == 1
    assert await pool.fetchval("SELECT version_num FROM alembic_version") == "0007_private_sources"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["cancel", "stale", "lease"])
async def test_private_permission_does_not_bypass_existing_claim_fences(database, state):
    pool, _, _ = database
    repository, claim, baseline, _, _ = await admitted(pool)
    if state == "cancel":
        await repository.cancel(claim.job.job_id, claim.context.access_scope)
    elif state == "stale":
        claim = claim.model_copy(update={"claim_token": "TEST_ONLY-stale"})
    else:
        await pool.execute(
            "UPDATE jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=$1",
            claim.job.job_id,
        )
    with pytest.raises(DomainError):
        await repository.succeed(claim, baseline)
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0


@pytest.mark.asyncio
async def test_opted_in_private_result_never_reaches_real_model_attempt_path(database):
    from a2_api.assistant.tools import SavedTools
    from a2_api.services.analyses import AnalysisService
    from a2_assistant.core import FlashAssistant, FlashConfig
    from test_alice_runtime import FakeSDKTransport, SyntheticDurableBudget

    pool, _, _ = database
    repository, claim, baseline, _, _ = await admitted(pool, publish=True, external_model_use=True)

    class RecordingTransport(FakeSDKTransport):
        instances = []

    config = FlashConfig(api_key="TEST_ONLY-no-network", folder_id="TEST_ONLY", tools_enabled=False)
    transport = RecordingTransport(config)
    budget = SyntheticDurableBudget()
    service = AnalysisService(repository=repository)
    service.assistant = FlashAssistant(
        config, transport=transport, tool_backend=SavedTools(service), deployment_budget=budget
    )
    assert service.assistant._enabled()
    explanation = await service.explain(baseline.analysis_id, claim.context.access_scope, "trace")
    assert transport.calls == 0 and budget.reservations == []
    assert explanation.status == "template"
    assert explanation.reason_code == "PRIVATE_SOURCE_EXTERNAL_MODEL_DISABLED"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["revoke", "expire"])
async def test_private_template_is_not_returned_after_permission_changes(
    database, monkeypatch, mutation
):
    from a2_api.services.analyses import AnalysisService

    pool, _, _ = database
    repository, claim, baseline, binding, _ = await admitted(
        pool,
        publish=True,
        external_model_use=True,
        lifetime=timedelta(seconds=1) if mutation == "expire" else None,
    )
    ready, release = asyncio.Event(), asyncio.Event()

    class ForbiddenAssistant:
        async def explain(self, saved, **kwargs):
            pytest.fail("Private source reached external model orchestration")

    service = AnalysisService(repository=repository, assistant=ForbiddenAssistant())
    original = service.analysis
    reads = 0

    async def delayed_delivery(*args, **kwargs):
        nonlocal reads
        reads += 1
        if reads == 2:
            ready.set()
            await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(service, "analysis", delayed_delivery)
    task = asyncio.create_task(
        service.explain(baseline.analysis_id, claim.context.access_scope, "trace")
    )
    await asyncio.wait_for(ready.wait(), 3)
    if mutation == "revoke":
        await pool.execute(
            "UPDATE private_source_grants SET revoked_at=clock_timestamp() WHERE grant_id=$1",
            binding.grant.grant_id,
        )
    else:
        await asyncio.sleep(
            max(
                0,
                (
                    binding.valid_until - await pool.fetchval("SELECT clock_timestamp()")
                ).total_seconds(),
            )
            + 0.02
        )
    release.set()
    with pytest.raises(DomainError) as denied:
        await task
    assert denied.value.status_code == 410


@pytest.mark.asyncio
async def test_source_expiry_terminates_spawned_worker_before_publication(
    database, tmp_path, monkeypatch
):
    import os

    from a2_api.workers.runner import Worker

    pool, _, _ = database
    repository, _, baseline, binding, _ = await admitted(
        pool, lifetime=timedelta(seconds=2), claim_job=False
    )
    pid_file = tmp_path / "TEST_ONLY-expiring-source-child.pid"
    monkeypatch.setenv("A2_TEST_CHILD_PID", str(pid_file))
    assert await Worker(
        repository,
        handler="worker_handlers:slow",
        code_version=baseline.versions.code_version,
        timeout_seconds=15,
    ).run_once()
    row = await pool.fetchrow("SELECT job_id FROM jobs")
    job = await repository.get_job(row["job_id"], scope=binding.grant.access_scope)
    assert job.status == "failed" and job.error.code == "data_unavailable"
    assert pid_file.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["keep", "revoke", "expire"])
async def test_two_bindings_cannot_keep_first_permission_valid_from_an_earlier_query(
    database, mutation
):
    pool, _, _ = database
    repository, claim, baseline, binding, _ = await admitted(
        pool,
        publish=True,
        binding_count=2,
        lifetime=timedelta(seconds=1) if mutation == "expire" else None,
    )
    ready, release = asyncio.Event(), asyncio.Event()

    class Connection:
        def __init__(self, actual):
            self.actual = actual

        def __getattr__(self, name):
            return getattr(self.actual, name)

        async def after(self, sql, result):
            if " AS episode_payload" in sql and not ready.is_set():
                ready.set()
                await release.wait()
            return result

        async def fetchrow(self, sql, *args):
            return await self.after(sql, await self.actual.fetchrow(sql, *args))

        async def fetch(self, sql, *args):
            return await self.after(sql, await self.actual.fetch(sql, *args))

    class Acquisition:
        async def __aenter__(self):
            self.acquired = pool.acquire()
            return Connection(await self.acquired.__aenter__())

        async def __aexit__(self, *args):
            return await self.acquired.__aexit__(*args)

    class PausingPool:
        def acquire(self):
            return Acquisition()

    repository.pool = PausingPool()
    reading = asyncio.create_task(
        repository.get_run(baseline.analysis_id, claim.context.access_scope)
    )
    await asyncio.wait_for(ready.wait(), 3)
    if mutation == "revoke":
        await pool.execute(
            "UPDATE private_source_grants SET revoked_at=clock_timestamp() WHERE grant_id=$1",
            binding.grant.grant_id,
        )
    elif mutation == "expire":
        await asyncio.sleep(
            max(
                0,
                (
                    binding.valid_until - await pool.fetchval("SELECT clock_timestamp()")
                ).total_seconds(),
            )
            + 0.02
        )
    release.set()
    if mutation == "keep":
        assert (await reading).metrics == baseline.metrics
        return
    with pytest.raises(DomainError) as denied:
        await reading
    assert denied.value.status_code == 410
