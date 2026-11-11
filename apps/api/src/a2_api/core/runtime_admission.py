"""One expensive runtime validation per API process, including abandoned callers."""

import asyncio
import threading
from collections.abc import Callable
from typing import Any, TypeVar

from a2_api.core.errors import DomainError

T = TypeVar("T")
_CAPACITY = threading.BoundedSemaphore(1)
# Strong references keep shielded tasks alive after their HTTP caller leaves.
# This is bounded by the same admission token; it never stores runtime inputs.
_ACTIVE: set[asyncio.Task[Any]] = set()


class _Reservation:
    def __init__(self, capacity: threading.BoundedSemaphore) -> None:
        self.capacity = capacity
        self.lock = threading.Lock()
        self.started = False
        self.released = False

    def _release(self) -> None:
        if not self.released:
            self.released = True
            self.capacity.release()

    def run(self, work: Callable[[], T]) -> T:
        with self.lock:
            if self.released:
                # A scheduler cancellation won before this thread started.
                raise asyncio.CancelledError
            self.started = True
        try:
            return work()
        finally:
            with self.lock:
                self._release()

    def unscheduled(self) -> None:
        with self.lock:
            if not self.started:
                self._release()

    def completed(self, task: asyncio.Task[Any]) -> None:
        _ACTIVE.discard(task)
        self.unscheduled()
        if not task.cancelled():
            # Retrieve an abandoned caller's exception without logging its data.
            task.exception()


async def run_heavy(work: Callable[[], T]) -> T:
    """Reject excess work instead of adding it to an unbounded executor queue."""
    capacity = _CAPACITY
    reservation = _Reservation(capacity)
    if not capacity.acquire(blocking=False):
        raise DomainError(503, "engine_unavailable", "RUNTIME_INPUTS_BUSY", retryable=True)
    coroutine = None
    try:
        coroutine = asyncio.to_thread(reservation.run, work)
        task = asyncio.create_task(coroutine)
    except BaseException:
        if coroutine is not None:
            coroutine.close()
        reservation.unscheduled()
        raise
    _ACTIVE.add(task)
    task.add_done_callback(reservation.completed)
    # Cancelling this await cannot cancel the queued/running validation task or
    # release its token while the thread still holds the large input objects.
    return await asyncio.shield(task)
