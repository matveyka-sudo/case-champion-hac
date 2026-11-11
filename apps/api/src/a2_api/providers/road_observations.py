"""Bounded local Valhalla observations with atomic, identity-scoped positive cache.

This is network movement evidence only. It does not certify approaches, cargo
compatibility, optimality, or absence of a physical route.
"""

import fcntl
import hashlib
import json
import math
import os
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import httpx
from a2_contracts.common import canonical_hash
from a2_contracts.v2.base import ContractModel, Timestamp
from pydantic import Field

from a2_api.core.truck_routing import TruckRoutingBinding
from a2_api.core.walk_observations import observation_directory

MAX_RESPONSE_BYTES = 256 * 1024
MAX_RECORD_BYTES = 2 * MAX_RESPONSE_BYTES
MAX_STORE_BYTES = 256 * 1024 * 1024
MAX_STORE_FILES = 10000


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ROAD_DUPLICATE_RESPONSE_FIELD")
        result[key] = value
    return result


def _duration(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    # Bound before converting huge JSON integers to float.
    if not 0 <= value <= 1_000_000_000 or not math.isfinite(value):
        return None
    return float(value)


class RoadObservation(ContractModel):
    schema_version: Literal["waydex-road-observation/1"] = "waydex-road-observation/1"
    identity: str = Field(pattern="^[a-f0-9]{64}$")
    execution: dict[str, Any]
    retrieved_at: Timestamp
    http_status: int = Field(strict=True, ge=100, le=599)
    response_raw: str = Field(max_length=MAX_RESPONSE_BYTES)
    response_sha256: str = Field(pattern="^[a-f0-9]{64}$")


def network_seconds(observation: RoadObservation) -> float | None:
    if canonical_hash(observation.execution) != observation.identity:
        raise ValueError("ROAD_OBSERVATION_EXECUTION_CHANGED")
    raw = observation.response_raw.encode()
    if (
        len(raw) > MAX_RESPONSE_BYTES
        or hashlib.sha256(raw).hexdigest() != observation.response_sha256
    ):
        raise ValueError("ROAD_OBSERVATION_BYTES_CHANGED")
    if observation.http_status != 200:
        return None
    try:
        body = json.loads(raw, object_pairs_hook=_unique)
    except (ValueError, RecursionError):
        return None
    if not isinstance(body, dict) or not isinstance(body.get("trip"), dict):
        return None
    trip = body["trip"]
    summary, legs = trip.get("summary"), trip.get("legs")
    if (
        type(trip.get("status")) is not int
        or trip["status"] != 0
        or trip.get("units") != "kilometers"
        or not isinstance(summary, dict)
        or not isinstance(legs, list)
        or len(legs) != 1
    ):
        return None
    seconds = _duration(summary.get("time"))
    if seconds is None:
        return None
    leg = legs[0]
    if not isinstance(leg, dict) or not isinstance(leg.get("summary"), dict):
        return None
    duration = _duration(leg["summary"].get("time"))
    maneuvers = leg.get("maneuvers")
    if (
        duration is None
        or abs(duration - seconds) > 0.002
        or not isinstance(leg.get("shape"), str)
        or not leg["shape"]
        or not isinstance(maneuvers, list)
        or not maneuvers
        or any(not isinstance(m, dict) or m.get("travel_mode") != "drive" for m in maneuvers)
    ):
        return None
    return float(seconds)


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("ROAD_EXECUTION_DEADLINE")
    return remaining


@contextmanager
def observation_store(deadline: float) -> Iterator[Path]:
    root = observation_directory() / "road-observations-v1"
    root.mkdir(mode=0o700, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("ROAD_STORAGE_DIRECTORY_INVALID")
    # One local executor slot across worker processes; cancellation releases the
    # OS lock. This also serializes storage quota checks and duplicate requests.
    fd = os.open(root / "executor.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(0.1, _remaining(deadline)))
        yield root
    finally:
        os.close(fd)


def _read(path: Path, identity: str) -> RoadObservation | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RECORD_BYTES:
            raise ValueError("ROAD_CACHE_FILE_INVALID")
        raw = source.read(MAX_RECORD_BYTES + 1)
    if len(raw) > MAX_RECORD_BYTES:
        raise ValueError("ROAD_CACHE_BYTE_LIMIT")
    observation = RoadObservation.model_validate_json(raw)
    if observation.identity != identity or network_seconds(observation) is None:
        raise ValueError("ROAD_CACHE_IDENTITY_OR_RESULT_INVALID")
    return observation


def _save(root: Path, name: str, observation: RoadObservation) -> None:
    raw = observation.model_dump_json().encode()
    if len(raw) > MAX_RECORD_BYTES:
        raise ValueError("ROAD_OBSERVATION_RECORD_LIMIT")
    total, count = 0, 0
    for path in root.iterdir():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("ROAD_STORAGE_FILE_INVALID")
        total += info.st_size
        count += 1
        if count >= MAX_STORE_FILES or total + len(raw) > MAX_STORE_BYTES:
            raise ValueError("ROAD_STORAGE_BUDGET_EXCEEDED")
    temporary = root / ("pending-" + uuid4().hex)
    try:
        with temporary.open("xb") as target:
            os.chmod(temporary, 0o600)
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, root / name)
    finally:
        temporary.unlink(missing_ok=True)


def _call(
    client: httpx.Client,
    binding: TruckRoutingBinding,
    path: str,
    payload: dict[str, Any],
    deadline: float,
) -> tuple[int, bytes]:
    timeout = min(binding.request_timeout_seconds, _remaining(deadline))
    with client.stream(
        "POST", str(binding.endpoint_url).rstrip("/") + path, json=payload, timeout=timeout
    ) as response:
        if response.headers.get("content-encoding", "identity") != "identity":
            raise ValueError("ROAD_COMPRESSED_RESPONSE_FORBIDDEN")
        raw = bytearray()
        for chunk in response.iter_bytes(chunk_size=8192):
            _remaining(deadline)
            raw.extend(chunk)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("ROAD_RESPONSE_BYTE_LIMIT")
        return response.status_code, bytes(raw)


def check_status(client: httpx.Client, binding: TruckRoutingBinding, deadline: float) -> None:
    status, raw = _call(client, binding, "/status", {}, deadline)
    body = json.loads(raw, object_pairs_hook=_unique)
    if (
        status != 200
        or not isinstance(body, dict)
        or body.get("version") != binding.engine_version
        or type(body.get("tileset_last_modified")) is not int
        or body["tileset_last_modified"] != binding.tileset_last_modified
        or body.get("has_tiles") is False
    ):
        raise ValueError("ROAD_ROUTER_BUILD_MISMATCH")


def observe(
    client: httpx.Client,
    binding: TruckRoutingBinding,
    root: Path,
    identity_context: dict[str, Any],
    payload: dict[str, Any],
    deadline: float,
) -> RoadObservation:
    _remaining(deadline)
    execution = {
        "executor": "truck-http/1",
        "binding": binding.model_dump(mode="json"),
        "context": identity_context,
        "request": payload,
    }
    identity = canonical_hash(execution)
    cached = _read(root / (identity + ".json"), identity)
    if cached is not None:
        return cached
    # Unknown transport failures are evidence, never a permanent negative cache.
    try:
        check_status(client, binding, deadline)
        try:
            status, raw = _call(client, binding, "/route", payload, deadline)
            decoded = raw.decode("utf-8")
        except (UnicodeError, ValueError) as exc:
            status = 502
            decoded = json.dumps({"failure": type(exc).__name__})
            raw = decoded.encode()
        check_status(client, binding, deadline)
    except httpx.HTTPError as exc:
        status = 502
        decoded = json.dumps({"failure": type(exc).__name__})
        raw = decoded.encode()
    observation = RoadObservation(
        identity=identity,
        execution=execution,
        retrieved_at=datetime.now(UTC),
        http_status=status,
        response_raw=decoded,
        response_sha256=hashlib.sha256(raw).hexdigest(),
    )
    positive = network_seconds(observation) is not None
    name = (
        identity + ".json"
        if positive
        else "unknown-" + canonical_hash(observation.model_dump(mode="json")) + ".json"
    )
    _save(root, name, observation)
    time.sleep(min(1 / binding.requests_per_second, _remaining(deadline)))
    return observation
