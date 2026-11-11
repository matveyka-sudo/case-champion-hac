"""Bounded sharing of one pending enqueue, never a cache of validated inputs."""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from a2_api.core.errors import DomainError

T = TypeVar("T")
_MAX_WAITERS = 16
_WAIT_SECONDS = 30


def _busy() -> DomainError:
    return DomainError(503, "engine_unavailable", "RUNTIME_INPUTS_BUSY", retryable=True)


@dataclass
class _Flight(Generic[T]):
    key: tuple[str, str]
    digest: str
    task: asyncio.Task[T]
    waiters: int = 0


class Admission(Generic[T]):
    def __init__(self) -> None:
        self._active: _Flight[T] | None = None
        self._closed = False

    def _finished(self, flight: _Flight[T]) -> None:
        if self._active is flight:
            self._active = None
        if not flight.task.cancelled():
            # Retrieve abandoned failures without logging messages or inputs.
            flight.task.exception()

    async def run(
        self,
        key: tuple[str, str],
        digest: str,
        work: Callable[[], Coroutine[Any, Any, T]],
    ) -> T:
        if self._closed:
            raise _busy()
        flight = self._active
        if flight is not None and flight.task.done():
            self._finished(flight)
            flight = None
        if flight is not None:
            if flight.key != key:
                raise _busy()
            if flight.digest != digest:
                raise DomainError(
                    409, "idempotency_conflict", "Idempotency key used for another request"
                )
            if flight.waiters >= _MAX_WAITERS:
                raise _busy()
        else:
            coroutine = work()
            try:
                task = asyncio.create_task(coroutine)
            except BaseException:
                coroutine.close()
                raise
            flight = _Flight(key, digest, task)
            self._active = flight
            task.add_done_callback(lambda _: self._finished(flight))
        flight.waiters += 1
        try:
            # A caller's timeout/disconnect cannot cancel the producer or free a
            # slot while preflight/context construction/enqueue still runs.
            return await asyncio.wait_for(asyncio.shield(flight.task), _WAIT_SECONDS)
        finally:
            flight.waiters -= 1

    async def close(self) -> None:
        self._closed = True
        flight = self._active
        if flight is not None:
            flight.task.cancel()
            await asyncio.gather(flight.task, return_exceptions=True)
            self._finished(flight)
