"""Cancellation cannot turn one admitted metadata load into parallel heavy work."""

import asyncio
import threading

import pytest
from a2_api.adapters import product
from a2_api.core import runtime_admission as admission
from a2_api.core.errors import DomainError


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.002)


@pytest.fixture
def isolated(monkeypatch):
    assert not admission._ACTIVE
    monkeypatch.setattr(admission, "_CAPACITY", threading.BoundedSemaphore(1))


async def busy():
    with pytest.raises(DomainError) as rejected:
        await admission.run_heavy(lambda: pytest.fail("Unadmitted work executed"))
    assert rejected.value.status_code == 503
    assert rejected.value.message == "RUNTIME_INPUTS_BUSY"
    assert rejected.value.retryable


@pytest.mark.asyncio
async def test_simultaneous_requests_have_one_running_load_and_no_waiting_queue(isolated):
    started, release = threading.Event(), threading.Event()
    calls = []

    def work():
        calls.append(True)
        started.set()
        assert release.wait(3)
        return "finished"

    first = asyncio.create_task(admission.run_heavy(work))
    await until(started.is_set)
    try:
        await asyncio.gather(*(busy() for _ in range(12)))
        assert len(calls) == len(admission._ACTIVE) == 1
    finally:
        release.set()
    assert await first == "finished"
    assert await admission.run_heavy(lambda: "next") == "next"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", ["caller", "timeout"])
async def test_cancelled_or_timed_out_caller_retains_capacity_until_thread_finishes(
    isolated, cancel
):
    started, release = threading.Event(), threading.Event()

    def work():
        started.set()
        assert release.wait(3)

    task = asyncio.create_task(admission.run_heavy(work))
    await until(started.is_set)
    try:
        if cancel == "caller":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(task, 0.005)
        await busy()
        assert len(admission._ACTIVE) == 1
    finally:
        release.set()
    await until(lambda: not admission._ACTIVE)
    assert await admission.run_heavy(lambda: "recovered") == "recovered"


@pytest.mark.asyncio
async def test_caller_cancel_before_thread_start_does_not_free_queued_validation(
    isolated, monkeypatch
):
    queued, allow_start = asyncio.Event(), asyncio.Event()
    original = asyncio.to_thread
    calls = []

    async def delayed(*args, **kwargs):
        queued.set()
        await allow_start.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", delayed)
    caller = asyncio.create_task(admission.run_heavy(lambda: calls.append(True)))
    await queued.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    try:
        await busy()
        assert not calls
    finally:
        allow_start.set()
    await until(lambda: not admission._ACTIVE)
    assert calls == [True]
    assert await admission.run_heavy(lambda: "next") == "next"


@pytest.mark.asyncio
async def test_scheduler_cancellation_before_thread_start_releases_without_executing(
    isolated, monkeypatch
):
    queued, allow_start = asyncio.Event(), asyncio.Event()
    calls = []

    async def delayed(*args, **kwargs):
        queued.set()
        await allow_start.wait()
        pytest.fail("Cancelled scheduler started work")

    original = asyncio.to_thread
    monkeypatch.setattr(asyncio, "to_thread", delayed)
    caller = asyncio.create_task(admission.run_heavy(lambda: calls.append(True)))
    await queued.wait()
    (task,) = admission._ACTIVE
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    await until(lambda: not admission._ACTIVE)
    monkeypatch.setattr(asyncio, "to_thread", original)
    assert not calls
    assert await admission.run_heavy(lambda: "next") == "next"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["construction", "create_task", "executor", "load"])
async def test_failed_scheduling_or_load_eventually_releases_admission(
    isolated, monkeypatch, failure
):
    original_thread, original_task = asyncio.to_thread, asyncio.create_task

    def failed(*args, **kwargs):
        raise RuntimeError("TEST_ONLY-scheduling-or-load-failure")

    async def executor_failure(*args, **kwargs):
        raise RuntimeError("TEST_ONLY-scheduling-or-load-failure")

    if failure == "construction":
        monkeypatch.setattr(asyncio, "to_thread", failed)
    elif failure == "create_task":
        monkeypatch.setattr(asyncio, "create_task", failed)
    elif failure == "executor":
        monkeypatch.setattr(asyncio, "to_thread", executor_failure)
    with pytest.raises(RuntimeError, match="TEST_ONLY"):
        await admission.run_heavy(failed if failure == "load" else lambda: "unused")
    await until(lambda: not admission._ACTIVE)
    monkeypatch.setattr(asyncio, "to_thread", original_thread)
    monkeypatch.setattr(asyncio, "create_task", original_task)
    assert await admission.run_heavy(lambda: "next") == "next"


@pytest.mark.asyncio
async def test_product_preflight_has_one_reservation_across_all_heavy_stages(isolated, monkeypatch):
    second_started, release = threading.Event(), threading.Event()

    async def stages(request, snapshot):
        await asyncio.to_thread(lambda: None)

        def second():
            second_started.set()
            assert release.wait(3)

        await asyncio.to_thread(second)
        return None

    monkeypatch.setattr(product, "_preflight", stages)
    caller = asyncio.create_task(product.preflight(None, None))
    await until(second_started.is_set)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    try:
        await busy()
    finally:
        release.set()
    await until(lambda: not admission._ACTIVE)
    assert await admission.run_heavy(lambda: "next") == "next"
