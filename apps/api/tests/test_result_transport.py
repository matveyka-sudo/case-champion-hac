"""TEST_ONLY independent wire/decoded budgets and fenced worker publication."""

import asyncio
import os
import random
import struct
import time
import zlib
from pathlib import Path

import pytest
from a2_api.core.config import RuntimeSettings
from a2_api.core.errors import DomainError
from a2_api.db.repositories.jobs import JobRepository
from a2_api.workers import calculation
from a2_api.workers.result_transport import (
    DEFAULT_RESULT_BYTES,
    MAX_DECODED_RESULT_BYTES,
    decode_result,
    encode_result,
)
from a2_api.workers.runner import Worker
from pydantic import ValidationError
from test_v2_storage import context, request

WIRE = DEFAULT_RESULT_BYTES
DECODED = MAX_DECODED_RESULT_BYTES
HEADER = struct.Struct(">8sQ")
PREFIX = b"A2RZ1\x00\r\n"


@pytest.fixture(scope="module")
def oversized_bytes():
    # Deterministic opaque transport bytes. No fabricated route or product fact.
    rng = random.Random(20261111)
    shared = rng.randbytes(18000)
    return b"".join(rng.randbytes(2048) + shared for _ in range(1000))


def frame(raw, declared=None, prefix=PREFIX):
    return HEADER.pack(prefix, len(raw) if declared is None else declared) + zlib.compress(raw)


def test_default_decoded_limit_and_exact_uncompressed_bytes():
    raw = b'{"required_null":null,"unknown":true}'
    assert encode_result(raw, decoded_limit=WIRE, wire_limit=WIRE) is raw
    assert decode_result(raw, decoded_limit=WIRE, wire_limit=WIRE) is raw


def test_explicit_32mib_decoded_exact_roundtrip_keeps_16mib_wire(oversized_bytes):
    raw = oversized_bytes
    assert WIRE < len(raw) < DECODED
    with pytest.raises(ValueError, match="decoded"):
        encode_result(raw, decoded_limit=WIRE, wire_limit=WIRE)
    encoded = encode_result(raw, decoded_limit=DECODED, wire_limit=WIRE)
    assert len(encoded) < WIRE
    assert decode_result(encoded, decoded_limit=DECODED, wire_limit=WIRE) == raw
    with pytest.raises(ValueError, match="decoded"):
        decode_result(encoded, decoded_limit=WIRE, wire_limit=WIRE)


def test_incompressible_oversized_result_keeps_wire_bound():
    raw = random.Random(20261112).randbytes(WIRE + 4096)
    with pytest.raises(ValueError, match="transport limit"):
        encode_result(raw, decoded_limit=DECODED, wire_limit=WIRE)
    with pytest.raises(ValueError, match="transport limit"):
        decode_result(raw, decoded_limit=DECODED, wire_limit=WIRE)


@pytest.mark.parametrize("attack", ["version", "short", "long", "truncated", "trailing", "concat"])
def test_forged_frame_is_rejected(oversized_bytes, attack):
    encoded = frame(oversized_bytes)
    if attack == "version":
        encoded = frame(oversized_bytes, prefix=b"A2RZ2\x00\r\n")
    elif attack == "short":
        encoded = frame(oversized_bytes, declared=len(oversized_bytes) - 1)
    elif attack == "long":
        encoded = frame(oversized_bytes, declared=len(oversized_bytes) + 1)
    elif attack == "truncated":
        encoded = encoded[:-1]
    elif attack == "trailing":
        encoded += b"TEST_ONLY-trailing"
    else:
        encoded += zlib.compress(b"TEST_ONLY-second-stream")
    with pytest.raises(ValueError):
        decode_result(encoded, decoded_limit=DECODED, wire_limit=WIRE)


def test_declared_limit_expansion_bomb_corrupt_stream_and_short_header_are_rejected():
    compressed_bomb = zlib.compress(b"x" * (DECODED + 1))
    for encoded in (
        HEADER.pack(PREFIX, DECODED + 1) + compressed_bomb,
        HEADER.pack(PREFIX, DECODED) + compressed_bomb,
        HEADER.pack(PREFIX, 100) + b"not-a-valid-stream",
        PREFIX,
    ):
        with pytest.raises(ValueError):
            decode_result(encoded, decoded_limit=DECODED, wire_limit=WIRE)


@pytest.mark.parametrize("value", ["0", "15", "33", "64", "32.5", "NaN", "invalid"])
def test_operator_decoded_setting_rejects_unbounded_or_invalid_values(monkeypatch, value):
    monkeypatch.setenv("A2_WORKER_RESULT_DECODED_MIB", value)
    with pytest.raises(ValidationError):
        RuntimeSettings(_env_file=None)


def test_operator_setting_defaults_to_16_and_explicitly_accepts_32(monkeypatch):
    monkeypatch.delenv("A2_WORKER_RESULT_DECODED_MIB", raising=False)
    assert RuntimeSettings(_env_file=None).worker_result_decoded_mib == 16
    monkeypatch.setenv("A2_WORKER_RESULT_DECODED_MIB", "32")
    assert RuntimeSettings(_env_file=None).worker_result_decoded_mib == 32


def test_saved_real_pt_file_roundtrips_exact_bytes_with_explicit_decoded_budget():
    path = Path(
        os.environ.get(
            "A2_TEST_LARGE_RESULT_PATH",
            "/Users/kirito/waydex-full-runtime/backend-evidence-packing-20261111-v1/actual-pt-grouped-scenario-result.json",
        )
    )
    if not path.is_file():
        pytest.skip("Set A2_TEST_LARGE_RESULT_PATH to the saved actual PT result")
    raw = path.read_bytes()
    assert WIRE < len(raw) <= DECODED
    encoded = encode_result(raw, decoded_limit=DECODED, wire_limit=WIRE)
    assert len(encoded) <= WIRE
    assert decode_result(encoded, decoded_limit=DECODED, wire_limit=WIRE) == raw


@pytest.mark.asyncio
async def test_spawned_large_worker_keeps_frozen_bound_and_pg_cache(database, monkeypatch):
    pool, _, _ = database
    repo = JobRepository(pool)
    ctx = context()
    accepted = await repo.enqueue("analysis", request(), ctx, "large", "trace", scope="owner")
    worker = Worker(
        repo,
        handler="result_transport_handlers:large_v2",
        code_version=ctx.code_version,
        timeout_seconds=30,
        decoded_result_bytes=DECODED,
    )
    # The child must use the explicit parent choice even if its environment differs.
    monkeypatch.setenv("A2_WORKER_RESULT_DECODED_MIB", "16")
    await worker.run_once()
    job = await repo.get_job(accepted.job_id, "owner")
    assert job.status == "succeeded"
    saved = await repo.get_run(job.run_id, "owner")
    assert len(saved.limitations) >= 200000
    assert saved.limitations[-1].startswith("TEST_ONLY-transport-199999:")
    cached = await repo.enqueue("analysis", request(), ctx, "large-cache", "trace", scope="owner")
    assert cached.status == "succeeded"
    with pytest.raises(DomainError):
        await repo.get_run(job.run_id, "TEST_ONLY-foreign")


@pytest.mark.asyncio
async def test_default_and_stale_workers_cannot_publish_oversized_output(database, monkeypatch):
    pool, _, _ = database
    repo = JobRepository(pool)
    ctx = context()
    monkeypatch.setenv("A2_WORKER_RESULT_DECODED_MIB", "32")
    for key, version in (("default", ctx.code_version), ("stale", "TEST_ONLY-stale")):
        accepted = await repo.enqueue("analysis", request(), ctx, key, "trace", scope="owner")
        await Worker(
            repo,
            handler="result_transport_handlers:large_v2",
            code_version=version,
            timeout_seconds=30,
        ).run_once()
        job = await repo.get_job(accepted.job_id, "owner")
        assert job.status == "failed"
        assert job.error.code == (
            "engine_unavailable" if key == "default" else "incompatible_versions"
        )
    assert await pool.fetchval("SELECT count(*) FROM runs") == 0


@pytest.mark.asyncio
async def test_decode_is_off_event_loop_and_deadline_cancels_it(database, monkeypatch):
    pool, _, _ = database
    repo = JobRepository(pool)
    ctx = context()
    await repo.enqueue("analysis", request(), ctx, "decode-deadline", "trace", scope="owner")
    claim = await repo.claim("TEST_ONLY-decode-worker")
    original = calculation.decode_result
    entered = asyncio.Event()
    loop = asyncio.get_running_loop()

    def slow_decode(*args, **kwargs):
        loop.call_soon_threadsafe(entered.set)
        time.sleep(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(calculation, "decode_result", slow_decode)
    task = asyncio.create_task(calculation.calculate(claim, "v2_handlers:success", 2))
    await asyncio.wait_for(entered.wait(), 1.8)
    ticks = 0
    while not task.done():
        ticks += 1
        await asyncio.sleep(0.01)
    with pytest.raises(DomainError) as failure:
        await task
    assert failure.value.code == "job_timeout" and ticks > 5
