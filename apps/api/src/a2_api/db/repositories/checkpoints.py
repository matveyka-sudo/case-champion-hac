"""Private OD progress fenced by the existing worker session and active job row."""

import asyncio
import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from a2_api.core.batches import MAX_CHECKPOINT_BYTES, BatchCheckpoint, BatchExecution
from a2_api.core.errors import DomainError
from a2_api.core.jobs import ClaimedJob, V2JobContext
from a2_api.db.rows import json_text

if TYPE_CHECKING:
    from a2_api.db.repositories.jobs import JobRepository


def _conflict(message: str) -> DomainError:
    return DomainError(409, "incompatible_versions", message)


def validate_raw_files(
    checkpoint: BatchCheckpoint, execution: BatchExecution, now: datetime
) -> None:
    checkpoint.validate_inventory(execution)
    if now >= execution.valid_until:
        raise ValueError("OD_EXECUTION_EXPIRED")
    folder = Path(os.environ["A2_WALK_PILOT_EVIDENCE_DIR"]).resolve()
    planned = {pair.pair_id: pair for _, pairs in execution.batches for pair in pairs}
    for observation in checkpoint.observations:
        envelope = observation.envelope
        # Version 1 reuses the deliberately unproven HTTP executor. A later
        # finite-state certificate requires its own explicit manifest version.
        if (
            envelope.search_complete
            or envelope.minimum_time_evidence_refs
            or envelope.unreachable_evidence_refs
        ):
            raise ValueError("OD_EXECUTOR_CANNOT_ATTEST_MINIMUM_OR_ABSENCE")
        pair = planned[observation.pair_id]
        raw_responses = {}
        raw_ids = {}
        for raw in observation.raw_observations:
            path = (folder / (raw.sha256 + ".json")).resolve()
            if path.parent != folder:
                raise ValueError("OD_RAW_OBSERVATION_OUTSIDE_STORE")
            with path.open("rb") as source:
                content = source.read(raw.size_bytes + 1)
            if len(content) != raw.size_bytes or hashlib.sha256(content).hexdigest() != raw.sha256:
                raise ValueError("OD_RAW_OBSERVATION_HASH_MISMATCH")
            document = json.loads(content)
            if document["build"] != json.loads(json.dumps(asdict(pair.request.build))):
                raise ValueError("OD_RAW_BUILD_MISMATCH")
            path_kind = document["path"]
            payload = pair.request.payload()
            expected = (
                payload
                if path_kind == "/route"
                else {
                    "locations": payload["locations"],
                    "costing": "pedestrian",
                    "costing_options": payload["costing_options"],
                    "verbose": True,
                }
            )
            if path_kind not in ("/route", "/locate") or document["request"] != expected:
                raise ValueError("OD_RAW_REQUEST_MISMATCH")
            if path_kind in raw_responses:
                raise ValueError("OD_DUPLICATE_RAW_OBSERVATION_KIND")
            raw_responses[path_kind] = document["response"]
            raw_ids[path_kind] = "valhalla-observation-" + raw.sha256
        for evidence in observation.evidence:
            if (
                evidence.source_id != "valhalla-" + pair.request.build.build_id
                or evidence.url.path != "/" + evidence.section
            ):
                raise ValueError("OD_OBSERVATION_PROVENANCE_MISMATCH")
            if evidence.retrieved_at > now + timedelta(seconds=5) or (
                evidence.retrieved_at + timedelta(seconds=execution.observation_retention_seconds)
                <= now
            ):
                raise ValueError("OD_OBSERVATION_EXPIRED_OR_FUTURE")
        if envelope.response_json is not None and (
            "/route" not in raw_responses
            or json.loads(envelope.response_json) != raw_responses["/route"]
        ):
            raise ValueError("OD_ENVELOPE_DIFFERS_FROM_RAW_RESPONSE")
        if envelope.outcome == "response":
            locate = raw_responses.get("/locate")
            matched = isinstance(locate, list) and len(locate) == 2
            if matched:
                assert isinstance(locate, list)
                for row, correlation in zip(
                    locate, (pair.request.origin, pair.request.destination), strict=True
                ):
                    edges = row.get("edges", []) if isinstance(row, dict) else []
                    if (
                        not edges
                        or row.get("warnings")
                        or any(
                            not isinstance(edge, dict)
                            or "correlated_lon" not in edge
                            or "correlated_lat" not in edge
                            for edge in edges
                        )
                    ):
                        matched = False
                        break
                    if {(edge["correlated_lon"], edge["correlated_lat"]) for edge in edges} != {
                        (correlation.correlated_location.lon, correlation.correlated_location.lat)
                    }:
                        matched = False
                        break
            expected_origin = pair.request.origin.correlated_location if matched else None
            expected_destination = pair.request.destination.correlated_location if matched else None
            expected_refs = {raw_ids["/locate"]} if matched else set()
            if (
                envelope.matched_origin != expected_origin
                or envelope.matched_destination != expected_destination
                or set(envelope.endpoint_evidence_refs) != expected_refs
            ):
                raise ValueError("OD_ENDPOINT_ATTESTATION_DIFFERS_FROM_RAW_LOCATE")
            from a2_api.adapters.walk_pilot import _pure_walk_response

            pure = _pure_walk_response(raw_responses.get("/route"))
            expected_pure = {raw_ids["/route"]} if pure else set()
            if set(envelope.pure_walk_evidence_refs) != expected_pure:
                raise ValueError("OD_PEDESTRIAN_ATTESTATION_DIFFERS_FROM_RAW_ROUTE")
        elif (
            envelope.endpoint_evidence_refs
            or envelope.pure_walk_evidence_refs
            or envelope.matched_origin is not None
            or envelope.matched_destination is not None
        ):
            raise ValueError("OD_FAILED_OBSERVATION_CANNOT_ATTEST_ENDPOINTS_OR_MODE")


class CheckpointRepository:
    def __init__(self, jobs: "JobRepository") -> None:
        self.jobs = jobs

    @staticmethod
    def execution(claim: ClaimedJob) -> BatchExecution:
        if claim.kind != "analysis" or not isinstance(claim.context, V2JobContext):
            raise _conflict("Checkpoint progress requires a canonical analysis job")
        context = V2JobContext.model_validate(claim.context.model_dump(mode="json"))
        if context.batch_execution is None:
            raise _conflict("Saved job has no approved batch manifest")
        return context.batch_execution

    async def _active(self, connection: Any, claim: ClaimedJob) -> datetime:
        row = await connection.fetchrow(
            "SELECT *,lease_expires_at>clock_timestamp() AS lease_active,clock_timestamp() AS checked_at FROM jobs WHERE job_id=$1 FOR UPDATE",
            claim.job.job_id,
        )
        if (
            row is None
            or row["status"] != "running"
            or row["claim_token"] != claim.claim_token
            or not row["lease_active"]
        ):
            raise _conflict("Checkpoint claim is no longer active")
        saved = json.loads(row["payload"])
        if saved["context"] != claim.context.model_dump(mode="json") or saved[
            "request"
        ] != claim.request.model_dump(mode="json"):
            raise _conflict("Checkpoint inputs differ from saved job")
        execution = self.execution(claim)
        if row["access_scope"] != execution.access_scope:
            raise _conflict("Checkpoint scope differs from saved job")
        now = cast(datetime, row["checked_at"])
        if now >= execution.valid_until:
            raise _conflict("Approved execution validity expired")
        return now

    async def _load(
        self, connection: Any, execution: BatchExecution, now: datetime
    ) -> list[BatchCheckpoint]:
        size = await connection.fetchval(
            "SELECT COALESCE(sum(payload_bytes),0) FROM od_checkpoints WHERE access_scope=$1 AND execution_identity=$2",
            execution.access_scope,
            execution.identity,
        )
        if size > execution.max_checkpoint_bytes:
            raise _conflict("Stored checkpoint inventory exceeds its approved byte budget")
        rows = await connection.fetch(
            "SELECT payload,content_hash,created_at FROM od_checkpoints WHERE access_scope=$1 AND execution_identity=$2 ORDER BY batch_id",
            execution.access_scope,
            execution.identity,
        )
        if len(rows) > len(execution.batches):
            raise _conflict("Stored checkpoint inventory exceeds the execution plan")
        result = []
        for row in rows:
            if (
                row["created_at"] + timedelta(seconds=execution.observation_retention_seconds)
                <= now
            ):
                raise _conflict("Stored checkpoint observations expired")
            checkpoint = BatchCheckpoint.model_validate_json(row["payload"])
            if checkpoint.content_hash != row["content_hash"]:
                raise _conflict("Immutable checkpoint hash changed")
            await asyncio.to_thread(validate_raw_files, checkpoint, execution, now)
            result.append(checkpoint)
        return result

    async def begin(self, claim: ClaimedJob) -> list[BatchCheckpoint]:
        execution = self.execution(claim)
        async with self.jobs._publication_connection() as connection:
            async with connection.transaction():
                now = await self._active(connection, claim)
                await connection.execute(
                    "INSERT INTO od_executions(access_scope,execution_identity,payload) VALUES($1,$2,$3::jsonb) ON CONFLICT DO NOTHING",
                    execution.access_scope,
                    execution.identity,
                    json_text(execution.model_dump(mode="json")),
                )
                row = await connection.fetchrow(
                    "SELECT * FROM od_executions WHERE access_scope=$1 AND execution_identity=$2 FOR UPDATE",
                    execution.access_scope,
                    execution.identity,
                )
                if json.loads(row["payload"]) != execution.model_dump(mode="json"):
                    raise _conflict("Immutable execution manifest changed")
                checkpoints = await self._load(connection, execution, now)
                await self._active(connection, claim)
                done = {c.batch_id for c in checkpoints}
                missing = [pairs for batch_id, pairs in execution.batches if batch_id not in done]
                calls = 2 * sum(len(pairs) for pairs in missing) + len(missing) + 2
                if (
                    row["attempts"] >= execution.max_attempts
                    or row["reserved_calls"] + calls > execution.max_provider_calls
                ):
                    raise DomainError(
                        503, "budget_exceeded", "Approved OD attempt or call budget exhausted"
                    )
                previous = await connection.fetchval(
                    "SELECT job_id FROM od_attempts WHERE job_id=$1", claim.job.job_id
                )
                if previous is not None:
                    raise _conflict("An OD attempt cannot restart under the same saved job")
                await connection.execute(
                    "UPDATE od_executions SET attempts=attempts+1,reserved_calls=reserved_calls+$3 WHERE access_scope=$1 AND execution_identity=$2",
                    execution.access_scope,
                    execution.identity,
                    calls,
                )
                await connection.execute(
                    "INSERT INTO od_attempts(job_id,claim_token,access_scope,execution_identity) VALUES($1,$2,$3,$4)",
                    claim.job.job_id,
                    claim.claim_token,
                    execution.access_scope,
                    execution.identity,
                )
                return checkpoints

    async def put(self, claim: ClaimedJob, checkpoint: BatchCheckpoint) -> None:
        execution = self.execution(claim)
        encoded = json_text(checkpoint.model_dump(mode="json"))
        size = len(encoded.encode())
        if size > MAX_CHECKPOINT_BYTES:
            raise _conflict("Checkpoint exceeds its transport byte budget")
        async with self.jobs._publication_connection() as connection:
            async with connection.transaction():
                now = await self._active(connection, claim)
                attempt = await connection.fetchrow(
                    "SELECT * FROM od_attempts WHERE job_id=$1",
                    claim.job.job_id,
                )
                if attempt is None or (
                    attempt["claim_token"],
                    attempt["access_scope"],
                    attempt["execution_identity"],
                ) != (claim.claim_token, execution.access_scope, execution.identity):
                    raise _conflict("Checkpoint has no active approved attempt")
                await connection.fetchrow(
                    "SELECT execution_identity FROM od_executions WHERE access_scope=$1 AND execution_identity=$2 FOR UPDATE",
                    execution.access_scope,
                    execution.identity,
                )
                await asyncio.to_thread(validate_raw_files, checkpoint, execution, now)
                await self._active(connection, claim)
                existing = await connection.fetchval(
                    "SELECT content_hash FROM od_checkpoints WHERE access_scope=$1 AND execution_identity=$2 AND batch_id=$3",
                    execution.access_scope,
                    execution.identity,
                    checkpoint.batch_id,
                )
                if existing is not None:
                    if existing != checkpoint.content_hash:
                        raise _conflict("Immutable checkpoint content conflicts")
                    return
                stored = await connection.fetchval(
                    "SELECT COALESCE(sum(payload_bytes),0) FROM od_checkpoints WHERE access_scope=$1 AND execution_identity=$2",
                    execution.access_scope,
                    execution.identity,
                )
                if stored + size > execution.max_checkpoint_bytes:
                    raise _conflict("Execution checkpoint byte budget exhausted")
                await connection.execute(
                    "INSERT INTO od_checkpoints(access_scope,execution_identity,batch_id,payload,content_hash,payload_bytes,source_job_id,source_claim_token) VALUES($1,$2,$3,$4::jsonb,$5,$6,$7,$8)",
                    execution.access_scope,
                    execution.identity,
                    checkpoint.batch_id,
                    encoded,
                    checkpoint.content_hash,
                    size,
                    claim.job.job_id,
                    claim.claim_token,
                )

    async def finalize(self, claim: ClaimedJob) -> None:
        execution = self.execution(claim)
        async with self.jobs._publication_connection() as connection:
            async with connection.transaction():
                now = await self._active(connection, claim)
                checkpoints = await self._load(connection, execution, now)
                await self._active(connection, claim)
                if {checkpoint.batch_id for checkpoint in checkpoints} != {
                    batch_id for batch_id, _ in execution.batches
                }:
                    raise _conflict("Final publication requires every approved executable OD batch")
