"""Synthetic orchestration tests with real PostgreSQL; not real-route evidence."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from a2_api.api.application import create_app
from a2_api.core.analysis_groups import GroupAdmission
from a2_api.core.errors import DomainError
from a2_api.db.repositories.jobs import JobRepository
from a2_api.services.analyses import AnalysisService
from a2_api.services.analysis_groups import AnalysisGroupService
from a2_api.workers.runner import Worker
from a2_contracts.v2.requests import UrbanPassengerRequest
from a2_contracts.v2.results import AnalysisResult
from test_v2_integration import request_body
from v2_handlers import registry, success


def group_success(claim):
    """Keep fixture metrics tied to the selected category, never production data."""
    saved = success(claim)
    payload = saved.model_dump_json().replace(
        '"school"', '"' + claim.context.normalized_request.categories[0] + '"'
    )
    return AnalysisResult.model_validate_json(payload)


def request():
    return UrbanPassengerRequest.model_validate(
        request_body()
        | {
            "categories": ["school", "adult_polyclinic"],
            "priorities": {"school": "high", "adult_polyclinic": "medium"},
        }
    )


def services(pool, *, preflight=None):
    analyses = AnalysisService(
        registry(),
        JobRepository(pool),
        code_version="demo-code",
        engine_enabled=True,
        preflight=preflight,
    )
    groups = AnalysisGroupService(analyses)
    worker = Worker(
        analyses.repository,
        handler="test_analysis_groups:group_success",
        code_version="demo-code",
        timeout_seconds=5,
        dispatch_groups=groups.dispatch_once,
    )
    return analyses, groups, worker


@pytest.mark.asyncio
async def test_serial_jobs_persist_results_and_resume_with_new_service(database):
    pool, _, _ = database
    _, groups, worker = services(pool)
    group = await groups.create(request(), "key", "owner", "trace")
    assert group.status == "queued" and all(i.job_id is None for i in group.items)
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 0
    assert await worker.run_once()
    first = await groups.get(group.group_id, "owner", "poll")
    assert [i.status for i in first.items] == ["succeeded", "pending"]
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 1
    # A different process/service needs no local checkpoint or browser connection.
    analyses, restarted, worker = services(pool)
    assert await worker.run_once()
    final = await restarted.get(group.group_id, "owner", "poll")
    assert final.status == "succeeded"
    assert len({i.analysis_id for i in final.items}) == 2
    for item in final.items:
        saved = await analyses.analysis(item.analysis_id, "owner")
        assert saved.request.categories == [item.category]
        assert "synthetic_only" in saved.limitations
    assert not await worker.run_once()
    assert await pool.fetchval("SELECT status FROM analysis_groups") == "succeeded"
    assert (await groups.create(request(), "key", "owner", "repeat")).group_id == group.group_id


@pytest.mark.asyncio
async def test_group_http_scope_idempotency_and_csrf(database):
    pool, _, _ = database
    analyses, _, _ = services(pool)
    transport = httpx.ASGITransport(app=create_app(analyses))
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as owner,
        httpx.AsyncClient(transport=transport, base_url="http://test") as other,
    ):
        headers = {"Idempotency-Key": "key"}
        response = await owner.post(
            "/api/v1/analysis-groups", json=request().model_dump(mode="json"), headers=headers
        )
        assert response.status_code == 202, response.text
        group_id = response.json()["group_id"]
        for path, method in (("", "GET"), ("/cancel", "POST"), ("/retry", "POST")):
            assert (
                await other.request(
                    method, "/api/v1/analysis-groups/" + group_id + path, headers=headers
                )
            ).status_code == 404
        duplicate = await owner.post(
            "/api/v1/analysis-groups", json=request().model_dump(mode="json"), headers=headers
        )
        assert duplicate.json()["group_id"] == group_id
        changed = request().model_dump(mode="json") | {"passenger_mode": "private_car"}
        assert (
            await owner.post("/api/v1/analysis-groups", json=changed, headers=headers)
        ).status_code == 409
        assert (
            await owner.post(
                f"/api/v1/analysis-groups/{group_id}/cancel",
                headers={"Origin": "https://foreign.invalid"},
            )
        ).status_code == 403


@pytest.mark.asyncio
async def test_cancel_during_preflight_cannot_enqueue_orphan_job(database):
    pool, _, _ = database
    entered, release = asyncio.Event(), asyncio.Event()

    async def preflight(request, snapshot):
        entered.set()
        await release.wait()

    analyses, groups, _ = services(pool, preflight=preflight)
    group = await groups.create(request(), "key", "owner", "trace")
    task = asyncio.create_task(groups.dispatch_once())
    await asyncio.wait_for(entered.wait(), 5)
    assert (await groups.cancel(group.group_id, "owner", "cancel")).status == "cancelled"
    release.set()
    await task
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 0
    assert (await groups.get(group.group_id, "owner", "poll")).status == "cancelled"
    await analyses.close_admission()


@pytest.mark.asyncio
async def test_link_failure_rolls_back_job_and_retry_recovers(database):
    pool, _, _ = database
    _, groups, _ = services(pool)
    group = await groups.create(request(), "key", "owner", "trace")
    await pool.execute(
        "CREATE FUNCTION reject_group_link() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'test rollback'; END $$"
    )
    await pool.execute(
        "CREATE TRIGGER reject_link BEFORE UPDATE ON analysis_group_items FOR EACH ROW WHEN (NEW.job_id IS NOT NULL) EXECUTE FUNCTION reject_group_link()"
    )
    assert await groups.dispatch_once()
    assert await groups.dispatch_once()
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 0
    assert (await groups.get(group.group_id, "owner", "poll")).status == "failed"
    await pool.execute("DROP TRIGGER reject_link ON analysis_group_items")
    retried = await groups.retry(group.group_id, "retry", "owner", "trace")
    assert retried.items[0].attempt == 2
    assert await groups.dispatch_once()
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 1


@pytest.mark.asyncio
async def test_unavailable_category_does_not_block_remaining_categories(database):
    pool, _, _ = database

    async def preflight(request, snapshot):
        if request.categories == ["school"]:
            raise DomainError(503, "data_unavailable", "Synthetic missing school input")

    _, groups, worker = services(pool, preflight=preflight)
    group = await groups.create(request(), "key", "owner", "trace")
    assert await worker.run_once()
    first = await groups.get(group.group_id, "owner", "poll")
    assert first.status == "running"
    assert first.items[0].status == "failed" and first.items[0].error.code == "data_unavailable"
    assert first.items[0].job_id is None
    assert await worker.run_once()
    final = await groups.get(group.group_id, "owner", "poll")
    assert final.status == "failed"  # Some categories succeeded; no false group success.
    assert final.items[1].analysis_id is not None


@pytest.mark.asyncio
async def test_failed_child_retry_preserves_success_and_is_bounded(database):
    pool, _, _ = database
    _, groups, worker = services(pool)
    group = await groups.create(request(), "key", "owner", "trace")
    assert await worker.run_once()
    saved = (await groups.get(group.group_id, "owner", "poll")).items[0].analysis_id
    for attempt in (1, 2, 3):
        assert await groups.dispatch_once()
        child = (await groups.get(group.group_id, "owner", "poll")).items[1]
        assert child.attempt == attempt
        await worker.repository.cancel(child.job_id, "owner")
        assert (await groups.get(group.group_id, "owner", "poll")).status == "failed"
        if attempt < 3:
            key = f"retry-{attempt}"
            retried = await groups.retry(group.group_id, key, "owner", "trace")
            assert retried.items[0].analysis_id == saved
            assert (await groups.retry(group.group_id, key, "owner", "trace")).items[
                1
            ].attempt == attempt + 1
    with pytest.raises(DomainError, match="budget exhausted"):
        await groups.retry(group.group_id, "retry-3", "owner", "trace")
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 4


@pytest.mark.asyncio
async def test_version_change_stops_next_category(database):
    pool, _, _ = database
    analyses, groups, worker = services(pool)
    group = await groups.create(request(), "key", "owner", "trace")
    assert await worker.run_once()
    analyses.code_version = "new-release"
    assert await groups.dispatch_once()
    stopped = await groups.get(group.group_id, "owner", "poll")
    assert stopped.status == "failed" and stopped.error.code == "incompatible_versions"
    assert stopped.items[0].analysis_id and stopped.items[1].job_id is None
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 1


@pytest.mark.asyncio
async def test_concurrent_admission_links_exactly_one_job(database):
    pool, _, _ = database
    analyses, groups, _ = services(pool)
    _, second, _ = services(pool)
    group = await groups.create(request(), "key", "owner", "trace")
    await asyncio.gather(groups.dispatch_once(), second.dispatch_once())
    state = await groups.get(group.group_id, "owner", "poll")
    assert state.status == "running"
    assert state.items[0].job_id is not None and state.items[1].job_id is None
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 1
    await analyses.close_admission()


@pytest.mark.asyncio
async def test_group_binding_cannot_skip_category_or_change_context(database):
    pool, _, _ = database
    analyses, groups, _ = services(pool)
    group = await groups.create(request(), "key", "owner", "trace")
    record = await groups.repository.get(group.group_id, "owner")
    binding = GroupAdmission(group.group_id, 1, 1, record.plan.identity)
    with pytest.raises(DomainError, match="binding changed"):
        await analyses.analyse(record.plan.child(1), binding.key, "trace", "owner", group=binding)
    bad = replace(binding, ordinal=0)
    with pytest.raises(DomainError, match="binding changed"):
        await analyses.analyse(record.plan.child(1), bad.key, "trace", "owner", group=bad)
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 0


@pytest.mark.asyncio
async def test_group_capacity_limits_are_atomic(database):
    pool, _, _ = database
    _, groups, _ = services(pool)
    results = await asyncio.gather(
        *(groups.create(request(), str(i), "owner", "trace") for i in range(4)),
        return_exceptions=True,
    )
    assert sum(not isinstance(r, Exception) for r in results) == 2
    assert all(r.status_code == 429 for r in results if isinstance(r, DomainError))
    assert await pool.fetchval("SELECT count(*) FROM analysis_groups") == 2


@pytest.mark.asyncio
async def test_cancel_queued_child_is_atomic_and_no_further_work(database):
    pool, _, _ = database
    _, groups, worker = services(pool)
    group = await groups.create(request(), "key", "owner", "trace")
    assert await groups.dispatch_once()
    cancelled = await groups.cancel(group.group_id, "owner", "cancel")
    assert cancelled.status == "cancelled" and cancelled.items[0].status == "cancelled"
    assert not await worker.run_once()
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0


@pytest.mark.asyncio
async def test_corrupt_group_does_not_block_other_work(database):
    pool, _, _ = database
    _, groups, _ = services(pool)
    bad = await groups.create(request(), "bad", "owner", "trace")
    good = await groups.create(request(), "good", "owner", "trace")
    await pool.execute(
        "UPDATE analysis_groups SET plan_hash='tampered' WHERE group_id=$1", bad.group_id
    )
    assert await groups.dispatch_once()
    with pytest.raises(DomainError, match="hash mismatch"):
        await groups.get(bad.group_id, "owner", "trace")
    assert (await groups.get(good.group_id, "owner", "trace")).items[0].job_id is not None
    assert (
        await pool.fetchval("SELECT status FROM analysis_groups WHERE group_id=$1", bad.group_id)
        == "failed"
    )


@pytest.mark.asyncio
async def test_input_expiry_is_rechecked_inside_enqueue_transaction(database):
    from a2_api.db.rows import json_text

    pool, _, _ = database
    analyses, groups, _ = services(pool)
    created = await groups.create(request(), "key", "owner", "trace")
    group = await groups.repository.get(created.group_id, "owner")
    expired = group.plan.model_copy(
        update={"valid_until": datetime.now(UTC) - timedelta(seconds=1)}
    )
    await pool.execute(
        "UPDATE analysis_groups SET plan=$2::jsonb,plan_hash=$3 WHERE group_id=$1",
        group.group_id,
        json_text(expired.model_dump(mode="json")),
        expired.identity,
    )
    binding = GroupAdmission(group.group_id, 0, 1, expired.identity)
    with pytest.raises(DomainError, match="validity expired"):
        await analyses.analyse(expired.child(0), binding.key, "trace", "owner", group=binding)
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 0


@pytest.mark.asyncio
async def test_group_cancel_fences_running_child_publication(database):
    pool, _, _ = database
    analyses, groups, _ = services(pool)
    group = await groups.create(request(), "key", "owner", "trace")
    await groups.dispatch_once()
    claim = await analyses.repository.claim("test-claim")
    saved = group_success(claim)
    assert (await groups.cancel(group.group_id, "owner", "cancel")).status == "cancelled"
    with pytest.raises(DomainError):
        await analyses.repository.succeed(claim, saved)
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0


@pytest.mark.asyncio
async def test_group_history_blocks_destructive_downgrade(database):
    from apps.api.tests.migration_support import migrate

    pool, schema, url = database
    _, groups, _ = services(pool)
    await groups.create(request(), "key", "owner", "trace")
    with pytest.raises(RuntimeError, match="history must not be discarded"):
        await asyncio.to_thread(migrate, url, schema, "0007_private_sources", True)
    assert await pool.fetchval("SELECT count(*) FROM analysis_groups") == 1
