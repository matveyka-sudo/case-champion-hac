"""TEST_ONLY concurrent admission, without routing or loading observed inputs."""

import asyncio
import json
import threading
from unittest.mock import AsyncMock

import httpx
import pytest
from a2_api.api.application import create_app
from a2_api.core import job_admission as module
from a2_api.core.errors import DomainError
from a2_api.db.repositories.jobs import JobRepository
from a2_api.services.analyses import AnalysisService
from a2_contracts.v2.requests import parse_analysis_request
from test_v2_http import body
from v2_handlers import registry


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.002)


@pytest.mark.asyncio
async def test_identical_retry_shares_whole_operation_after_original_caller_aborts():
    admission = module.Admission[str]()
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def work():
        calls.append(True)
        started.set()
        await release.wait()
        return "TEST_ONLY-same-job"

    first = asyncio.create_task(admission.run(("owner", "key"), "digest", work))
    await started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    retry = asyncio.create_task(admission.run(("owner", "key"), "digest", work))
    await until(lambda: admission._active.waiters == 1)
    release.set()
    assert await retry == "TEST_ONLY-same-job"
    assert calls == [True]
    await until(lambda: admission._active is None)


@pytest.mark.asyncio
async def test_conflicting_body_and_other_scope_or_key_cannot_share_pending_job():
    admission = module.Admission[str]()
    release = asyncio.Event()

    async def work():
        await release.wait()
        return "private"

    first = asyncio.create_task(admission.run(("owner", "key"), "digest", work))
    await until(lambda: admission._active is not None)
    for key, digest, status in [
        (("owner", "key"), "different", 409),
        (("stranger", "key"), "digest", 503),
        (("owner", "other"), "digest", 503),
    ]:
        with pytest.raises(DomainError) as rejected:
            await admission.run(key, digest, work)
        assert rejected.value.status_code == status
    release.set()
    assert await first == "private"


@pytest.mark.asyncio
async def test_waiter_limit_bounds_identical_callers_without_queueing_other_work():
    admission = module.Admission[str]()
    release = asyncio.Event()

    async def work():
        await release.wait()
        return "same"

    callers = [
        asyncio.create_task(admission.run(("owner", "key"), "digest", work))
        for _ in range(module._MAX_WAITERS)
    ]
    await until(
        lambda: admission._active is not None and admission._active.waiters == module._MAX_WAITERS
    )
    with pytest.raises(DomainError, match="RUNTIME_INPUTS_BUSY"):
        await admission.run(("owner", "key"), "digest", work)
    release.set()
    assert await asyncio.gather(*callers) == ["same"] * module._MAX_WAITERS


@pytest.mark.asyncio
async def test_bounded_wait_does_not_restart_or_cancel_producer(monkeypatch):
    admission = module.Admission[str]()
    release = asyncio.Event()
    calls = []

    async def work():
        calls.append(True)
        await release.wait()
        return "done"

    monkeypatch.setattr(module, "_WAIT_SECONDS", 0.01)
    with pytest.raises(TimeoutError):
        await admission.run(("owner", "key"), "digest", work)
    assert admission._active is not None
    assert not admission._active.task.cancelled()
    release.set()
    assert await admission.run(("owner", "key"), "digest", work) == "done"
    assert calls == [True]


@pytest.mark.asyncio
async def test_failure_is_cleaned_and_new_attempt_can_proceed_without_retained_inputs():
    admission = module.Admission[str]()

    async def failure():
        raise ValueError("TEST_ONLY-sensitive-error-value")

    with pytest.raises(ValueError, match="TEST_ONLY"):
        await admission.run(("owner", "key"), "digest", failure)
    await until(lambda: admission._active is None)
    assert await admission.run(("owner", "key"), "digest", AsyncMock(return_value="next")) == "next"


@pytest.mark.asyncio
async def test_shutdown_cancels_producer_and_prevents_later_side_effects():
    admission = module.Admission[str]()
    started, release = asyncio.Event(), asyncio.Event()
    writes = []

    async def work():
        started.set()
        await release.wait()
        writes.append(True)
        return "unreachable"

    caller = asyncio.create_task(admission.run(("owner", "key"), "digest", work))
    await started.wait()
    await admission.close()
    with pytest.raises(asyncio.CancelledError):
        await caller
    release.set()
    assert not writes
    assert admission._active is None
    with pytest.raises(DomainError, match="RUNTIME_INPUTS_BUSY"):
        await admission.run(("owner", "key"), "digest", work)


@pytest.mark.asyncio
async def test_failed_scheduler_closes_coroutine_without_reserving_slot(monkeypatch):
    admission = module.Admission[str]()

    def failed(coroutine):
        raise RuntimeError("TEST_ONLY-scheduler")

    with monkeypatch.context() as patch:
        patch.setattr(asyncio, "create_task", failed)
        with pytest.raises(RuntimeError, match="TEST_ONLY"):
            await admission.run(("owner", "key"), "digest", AsyncMock(return_value="unused"))
    assert admission._active is None
    assert await admission.run(("owner", "key"), "digest", AsyncMock(return_value="next")) == "next"


@pytest.mark.asyncio
async def test_public_HTTP_retry_recovers_one_job_through_preflight_and_enqueue(database):
    pool, _, _ = database
    preflight_started, preflight_allowed = asyncio.Event(), asyncio.Event()
    before_insert, insert_allowed = asyncio.Event(), asyncio.Event()
    preflight_calls = []

    class GatedRepository(JobRepository):
        async def enqueue(self, *args, **kwargs):
            before_insert.set()
            await insert_allowed.wait()
            return await super().enqueue(*args, **kwargs)

    async def preflight(request, snapshot):
        preflight_calls.append(True)
        preflight_started.set()
        await preflight_allowed.wait()

    service = AnalysisService(
        registry(),
        GatedRepository(pool),
        code_version="demo-code",
        engine_enabled=True,
        preflight=preflight,
    )
    app = create_app(service)
    payload, headers = body() | {"city_id": "city-1"}, {"Idempotency-Key": "TEST_ONLY-admission"}
    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as owner,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as stranger,
    ):
        await owner.get("/api/v1/capabilities")  # Establish scope before client cancellation.
        await stranger.get("/api/v1/capabilities")
        first = asyncio.create_task(owner.post("/api/v1/analyses", json=payload, headers=headers))
        await preflight_started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(owner.post("/api/v1/analyses", json=payload, headers=headers))
        preflight_allowed.set()
        await before_insert.wait()
        third = asyncio.create_task(owner.post("/api/v1/analyses", json=payload, headers=headers))
        conflict = await owner.post(
            "/api/v1/analyses", json=payload | {"passenger_mode": "private_car"}, headers=headers
        )
        assert conflict.status_code == 409
        foreign = await stranger.post("/api/v1/analyses", json=payload, headers=headers)
        assert foreign.status_code == 503 and "job_id" not in foreign.json()
        insert_allowed.set()
        responses = await asyncio.gather(second, third)
        assert [r.status_code for r in responses] == [202, 202]
        assert responses[0].json()["job_id"] == responses[1].json()["job_id"]
        assert responses[0].json()["request_id"] != responses[1].json()["request_id"]
        assert all(r.json()["request_id"] == r.headers["x-request-id"] for r in responses)
        assert preflight_calls == [True]
        assert await pool.fetchval("SELECT count(*) FROM jobs") == 1
        saved = json.loads(await pool.fetchval("SELECT payload FROM jobs"))
        assert saved["request"] == parse_analysis_request(payload).model_dump(mode="json")
        assert saved["context"]["normalized_request"]["city_id"] == "city-1"
        # Durable replay remains available while a different new admission is pending.
        preflight_started.clear()
        preflight_allowed.clear()
        another = asyncio.create_task(
            owner.post(
                "/api/v1/analyses", json=payload, headers={"Idempotency-Key": "TEST_ONLY-another"}
            )
        )
        await preflight_started.wait()
        replay = await owner.post("/api/v1/analyses", json=payload, headers=headers)
        assert (
            replay.status_code == 202 and replay.json()["job_id"] == responses[0].json()["job_id"]
        )
        preflight_allowed.set()
        assert (await another).status_code == 202
    await service.close_admission()


def test_configured_lifespan_closes_admission_before_database_pool(monkeypatch):
    from a2_api.api import bootstrap
    from a2_api.core.config import RuntimeSettings
    from fastapi.testclient import TestClient

    order = []

    class Pool:
        async def close(self):
            order.append("pool")

    monkeypatch.setattr(bootstrap, "open_pool", AsyncMock(return_value=Pool()))
    app = bootstrap.build_app(RuntimeSettings(database_url="postgresql://TEST_ONLY"))
    with TestClient(app):

        async def close():
            order.append("admission")

        monkeypatch.setattr(app.state.services, "close_admission", close)
    assert order == ["admission", "pool"]


@pytest.mark.asyncio
async def test_shutdown_during_shielded_heavy_load_cannot_enqueue_after_pool_close(database):
    from a2_api.core import runtime_admission

    pool, _, _ = database
    started, release = threading.Event(), threading.Event()
    enqueues = []

    class ObservedRepository(JobRepository):
        async def enqueue(self, *args, **kwargs):
            enqueues.append(True)
            return await super().enqueue(*args, **kwargs)

    async def preflight(request, snapshot):
        def work():
            started.set()
            assert release.wait(5)

        await runtime_admission.run_heavy(work)

    service = AnalysisService(
        registry(),
        ObservedRepository(pool),
        code_version="demo-code",
        engine_enabled=True,
        preflight=preflight,
    )
    caller = asyncio.create_task(
        service.analyse(
            parse_analysis_request(body() | {"city_id": "city-1"}),
            "TEST_ONLY-shutdown",
            "TEST_ONLY-trace",
            "TEST_ONLY-owner",
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 3)
        await service.close_admission()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert await pool.fetchval("SELECT count(*) FROM jobs") == 0
        await pool.close()
    finally:
        release.set()
        await service.close_admission()
        await asyncio.gather(caller, return_exceptions=True)
    await until(lambda: not runtime_admission._ACTIVE)
    assert not enqueues


@pytest.mark.asyncio
async def test_separate_service_instances_still_rely_on_durable_database_idempotency(database):
    pool, _, _ = database
    services = [
        AnalysisService(
            registry(), JobRepository(pool), code_version="demo-code", engine_enabled=True
        )
        for _ in range(2)
    ]
    request = parse_analysis_request(body() | {"city_id": "city-1"})
    try:
        accepted = await asyncio.gather(
            *(
                service.analyse(request, "TEST_ONLY-shared", "TEST_ONLY-trace", "TEST_ONLY-owner")
                for service in services
            )
        )
        assert accepted[0].job_id == accepted[1].job_id
        assert await pool.fetchval("SELECT count(*) FROM jobs") == 1
    finally:
        await asyncio.gather(*(service.close_admission() for service in services))
