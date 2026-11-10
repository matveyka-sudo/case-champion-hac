"""Bounded immutable route envelopes; only SHA-derived paths under operator storage."""

import hashlib
import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from time import monotonic
from typing import Any, Literal, NoReturn
from uuid import uuid4

from a2_contracts.v2.base import ContractModel
from pydantic import Field

from a2_api.core.batches import (
    MAX_ROUTE_ARTIFACT_BYTES,
    BatchCheckpoint,
    BatchExecution,
    PairObservation,
    PairObservationRef,
)
from a2_api.core.walk_observations import observation_directory


class RouteArtifact(ContractModel):
    schema_version: Literal["waydex-walk-route-artifact/1"]
    execution_identity: str = Field(pattern="^[a-f0-9]{64}$")
    pair_id: str = Field(pattern="^[a-f0-9]{64}$")
    observation: PairObservation


@dataclass(frozen=True, init=False)
class RouteArtifactContext:
    """Per-operation frozen authority, derived only from the saved execution."""

    identity: str
    pair_ids: frozenset[str]
    max_retained_bytes: int

    def __init__(self, execution: BatchExecution) -> None:
        if (
            execution.checkpoint_format != "artifact_refs_v2"
            or execution.max_route_artifact_bytes is None
        ):
            raise ValueError("OD_ARTIFACT_EXECUTION_PROTOCOL_REQUIRED")
        object.__setattr__(self, "identity", execution.identity)
        object.__setattr__(self, "pair_ids", frozenset(pair.pair_id for pair in execution.pairs))
        object.__setattr__(self, "max_retained_bytes", execution.max_route_artifact_bytes)


def _context(execution: BatchExecution | RouteArtifactContext) -> RouteArtifactContext:
    return (
        execution
        if isinstance(execution, RouteArtifactContext)
        else RouteArtifactContext(execution)
    )


@contextmanager
def _directory() -> Iterator[int]:
    root = observation_directory()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    folder_fd = None
    try:
        try:
            os.mkdir("route-results", mode=0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
        folder_fd = os.open(
            "route-results", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd
        )
        yield folder_fd
    finally:
        if folder_fd is not None:
            os.close(folder_fd)
        os.close(root_fd)


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("OD_ARTIFACT_DUPLICATE_JSON_KEY")
        value[key] = item
    return value


def _constant(_value: str) -> NoReturn:
    raise ValueError("OD_ARTIFACT_NONFINITE_JSON")


def _read(reference: PairObservationRef) -> bytes:
    with _directory() as directory:
        fd = os.open(
            reference.artifact_sha256 + ".json",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory,
        )
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != reference.size_bytes:
            raise ValueError("OD_ARTIFACT_SIZE_OR_TYPE_MISMATCH")
        raw = source.read(reference.size_bytes + 1)
    if (
        len(raw) != reference.size_bytes
        or hashlib.sha256(raw).hexdigest() != reference.artifact_sha256
    ):
        raise ValueError("OD_ARTIFACT_CONTENT_HASH_MISMATCH")
    return raw


def read_route_artifact(
    reference: PairObservationRef, execution: BatchExecution | RouteArtifactContext
) -> PairObservation:
    context = _context(execution)
    if reference.retained_bytes > context.max_retained_bytes:
        raise ValueError("OD_ARTIFACT_RETAINED_BYTE_BUDGET_EXCEEDED")
    # Verify bytes before JSON/model parsing; no source-supplied filename is followed.
    raw = _read(reference)
    artifact = RouteArtifact.model_validate(
        json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    )
    observation = artifact.observation
    if (
        artifact.execution_identity != context.identity
        or artifact.pair_id != reference.pair_id
        or observation.pair_id != reference.pair_id
        or reference.retained_bytes
        != len(raw) + sum(row.size_bytes for row in observation.raw_observations)
        or reference.pair_id not in context.pair_ids
    ):
        raise ValueError("OD_ARTIFACT_PAIR_EXECUTION_OR_CLOSURE_MISMATCH")
    return observation


def write_route_artifact(
    observation: PairObservation,
    execution: BatchExecution | RouteArtifactContext,
    *,
    remaining_bytes: int | None = None,
) -> PairObservationRef:
    context = _context(execution)
    if observation.pair_id not in context.pair_ids:
        raise ValueError("OD_ARTIFACT_UNPLANNED_PAIR")
    raw = (
        RouteArtifact(
            schema_version="waydex-walk-route-artifact/1",
            execution_identity=context.identity,
            pair_id=observation.pair_id,
            observation=observation,
        )
        .model_dump_json()
        .encode()
    )
    if len(raw) > MAX_ROUTE_ARTIFACT_BYTES:
        raise ValueError("OD_ARTIFACT_FILE_BYTE_BUDGET_EXCEEDED")
    reference = PairObservationRef(
        pair_id=observation.pair_id,
        artifact_sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
        retained_bytes=len(raw) + sum(row.size_bytes for row in observation.raw_observations),
    )
    if reference.retained_bytes > context.max_retained_bytes:
        raise ValueError("OD_ARTIFACT_RETAINED_BYTE_BUDGET_EXCEEDED")
    if remaining_bytes is not None and reference.retained_bytes > remaining_bytes:
        raise ValueError("OD_ARTIFACT_RETAINED_BYTE_BUDGET_EXCEEDED")
    name = reference.artifact_sha256 + ".json"
    with _directory() as directory:
        temporary = ".write-" + uuid4().hex
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
        )
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
                os.fchmod(output.fileno(), 0o400)
            try:
                os.link(
                    temporary,
                    name,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                    follow_symlinks=False,
                )
                os.fsync(directory)
            except FileExistsError:
                if _read(reference) != raw:
                    raise ValueError("OD_ARTIFACT_IMMUTABLE_CONTENT_CONFLICT") from None
        finally:
            os.unlink(temporary, dir_fd=directory)
    # Matching content alone cannot authorize another pair or execution.
    read_route_artifact(reference, context)
    return reference


def resolve_checkpoint(
    checkpoint: BatchCheckpoint,
    execution: BatchExecution,
    *,
    context: RouteArtifactContext | None = None,
) -> list[PairObservation]:
    checkpoint.validate_pair_inventory(execution)
    if execution.checkpoint_format == "artifact_refs_v2":
        context = context or RouteArtifactContext(execution)
        if context.identity != checkpoint.execution_identity:
            raise ValueError("OD_ARTIFACT_CONTEXT_IDENTITY_MISMATCH")
    deadline = monotonic() + 15
    observations = []
    for item in checkpoint.observations:
        if monotonic() >= deadline:
            raise ValueError("OD_ARTIFACT_VALIDATION_DEADLINE_EXCEEDED")
        observations.append(
            read_route_artifact(item, context or execution)
            if isinstance(item, PairObservationRef)
            else item
        )
    if monotonic() >= deadline:
        raise ValueError("OD_ARTIFACT_VALIDATION_DEADLINE_EXCEEDED")
    checkpoint.validate_inventory(execution, resolved=observations)
    return observations
