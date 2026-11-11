"""Private OD progress fenced by the existing worker session and active job row."""

import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from time import monotonic
from typing import TYPE_CHECKING, Any, cast

from a2_api.core.batches import MAX_CHECKPOINT_BYTES, BatchCheckpoint, BatchExecution
from a2_api.core.errors import DomainError
from a2_api.core.jobs import ClaimedJob, V2JobContext
from a2_api.core.walk_observations import observation_directory
from a2_api.core.walk_route_artifacts import resolve_checkpoint
from a2_api.db.rows import json_text

if TYPE_CHECKING:
    from a2_api.db.repositories.jobs import JobRepository


def _conflict(message: str) -> DomainError:
    return DomainError(409, "incompatible_versions", message)


def validate_raw_files(
    checkpoint: BatchCheckpoint, execution: BatchExecution, now: datetime
) -> datetime | None:
    deadline = monotonic() + 15
    observations = resolve_checkpoint(checkpoint, execution)
    if now >= execution.valid_until:
        raise ValueError("OD_EXECUTION_EXPIRED")
    folder = observation_directory(legacy=execution.schema_version == "waydex-walk-execution/1")
    planned = {pair.pair_id: pair for _, pairs in execution.batches for pair in pairs}
    for observation in observations:
        if monotonic() >= deadline:
            raise ValueError("OD_RAW_VALIDATION_DEADLINE_EXCEEDED")
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
            if monotonic() >= deadline:
                raise ValueError("OD_RAW_VALIDATION_DEADLINE_EXCEEDED")
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
    if monotonic() >= deadline:
        raise ValueError("OD_RAW_VALIDATION_DEADLINE_EXCEEDED")
    return min(
        (
            evidence.retrieved_at + timedelta(seconds=execution.observation_retention_seconds)
            for observation in observations
            for evidence in observation.evidence
        ),
        default=None,
    )


@dataclass(frozen=True)
class _CheckpointRow:
    batch_id: str
    payload: str
    content_hash: str
    payload_bytes: int
    created_at: datetime
    source_job_id: str
    source_claim_token: str


@dataclass(frozen=True)
class _ValidatedInventory:
    job_id: str
    claim_token: str
    access_scope: str
    execution_identity: str
    execution_payload: str
    claim_payload: str
    rows: tuple[_CheckpointRow, ...]
    expires_at: datetime
    new_checkpoint: str | None = None

    def checkpoints(self) -> list[BatchCheckpoint]:
        return [BatchCheckpoint.model_validate_json(row.payload) for row in self.rows]


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

    @staticmethod
    def _claim_payload(claim: ClaimedJob) -> str:
        return json_text(
            {
                "context": claim.context.model_dump(mode="json"),
                "request": claim.request.model_dump(mode="json"),
            }
        )

    async def _active(self, connection: Any, claim: ClaimedJob, *, lock: bool = True) -> datetime:
        row = await connection.fetchrow(
            "SELECT *,lease_expires_at>clock_timestamp() AS lease_active,clock_timestamp() AS checked_at FROM jobs WHERE job_id=$1"
            + (" FOR UPDATE" if lock else ""),
            claim.job.job_id,
        )
        if (
            row is None
            or row["status"] != "running"
            or row["claim_token"] != claim.claim_token
            or not row["lease_active"]
        ):
            raise _conflict("Checkpoint claim is no longer active")
        if json_text(json.loads(row["payload"])) != self._claim_payload(claim):
            raise _conflict("Checkpoint inputs differ from saved job")
        execution = self.execution(claim)
        if row["access_scope"] != execution.access_scope:
            raise _conflict("Checkpoint scope differs from saved job")
        now = cast(datetime, row["checked_at"])
        if now >= execution.valid_until:
            raise _conflict("Approved execution validity expired")
        return now

    async def _rows(
        self, connection: Any, execution: BatchExecution, *, lock: bool = False
    ) -> tuple[_CheckpointRow, ...]:
        rows = await connection.fetch(
            "SELECT batch_id,payload,content_hash,payload_bytes,created_at,source_job_id,source_claim_token FROM od_checkpoints WHERE access_scope=$1 AND execution_identity=$2 ORDER BY batch_id"
            + (" FOR UPDATE" if lock else ""),
            execution.access_scope,
            execution.identity,
        )
        return tuple(
            _CheckpointRow(**{name: row[name] for name in _CheckpointRow.__dataclass_fields__})
            for row in rows
        )

    async def _prepare(
        self,
        claim: ClaimedJob,
        *,
        complete: bool = False,
        new_checkpoint: BatchCheckpoint | None = None,
        allow_missing_execution: bool = False,
    ) -> _ValidatedInventory:
        """All raw I/O happens here, without job/execution write locks or a transaction."""
        execution = self.execution(claim)
        expected_payload = json_text(execution.model_dump(mode="json"))
        frozen_claim = self._claim_payload(claim)
        frozen_new = (
            json_text(new_checkpoint.model_dump(mode="json"))
            if new_checkpoint is not None
            else None
        )
        async with self.jobs._publication_connection() as connection:
            now = await self._active(connection, claim, lock=False)
            stored = await connection.fetchval(
                "SELECT payload FROM od_executions WHERE access_scope=$1 AND execution_identity=$2",
                execution.access_scope,
                execution.identity,
            )
            if stored is None:
                if not allow_missing_execution:
                    raise _conflict("Saved execution manifest is unavailable")
            elif json_text(json.loads(stored)) != expected_payload:
                raise _conflict("Immutable execution manifest changed")
            rows = await self._rows(connection, execution)
            if (
                len(rows)
                > (len(execution.pairs) + execution.batch_size - 1) // execution.batch_size
            ):
                raise _conflict("Stored checkpoint inventory exceeds the execution plan")
            if sum(row.payload_bytes for row in rows) > execution.max_checkpoint_bytes:
                raise _conflict("Stored checkpoint inventory exceeds its approved byte budget")
            expiry = execution.valid_until
            if (
                isinstance(claim.context, V2JobContext)
                and claim.context.cache_valid_until is not None
            ):
                expiry = min(expiry, claim.context.cache_valid_until)
            retained = 0
            for row in rows:
                checkpoint = BatchCheckpoint.model_validate_json(row.payload)
                encoded_bytes = len(json_text(checkpoint.model_dump(mode="json")).encode())
                if (
                    checkpoint.content_hash != row.content_hash
                    or checkpoint.batch_id != row.batch_id
                    or row.payload_bytes != encoded_bytes
                    or encoded_bytes > MAX_CHECKPOINT_BYTES
                ):
                    raise _conflict("Immutable checkpoint content changed")
                retained += checkpoint.retained_route_bytes
                expiry = min(
                    expiry,
                    row.created_at + timedelta(seconds=execution.observation_retention_seconds),
                )
                if now >= expiry:
                    raise _conflict("Stored checkpoint observations expired")
                # put validates its new observation, as before; existing raw
                # files are all revalidated at resume/finalize/every publication.
                if new_checkpoint is None:
                    evidence_expiry = await asyncio.to_thread(
                        validate_raw_files, checkpoint, execution, now
                    )
                    if evidence_expiry is not None:
                        expiry = min(expiry, evidence_expiry)
            if (
                execution.max_route_artifact_bytes is not None
                and retained > execution.max_route_artifact_bytes
            ):
                raise _conflict("Stored route artifacts exceed their approved byte budget")
            if complete and {row.batch_id for row in rows} != {
                batch_id for batch_id, _ in execution.batches
            }:
                raise _conflict("Final publication requires every approved executable OD batch")
            if frozen_new is not None:
                # Reparse the frozen value: concurrent caller mutation must not
                # make the validated object differ from the later inserted one.
                checkpoint = BatchCheckpoint.model_validate_json(frozen_new)
                if len(frozen_new.encode()) > MAX_CHECKPOINT_BYTES:
                    raise _conflict("Checkpoint exceeds its transport byte budget")
                evidence_expiry = await asyncio.to_thread(
                    validate_raw_files, checkpoint, execution, now
                )
                if evidence_expiry is not None:
                    expiry = min(expiry, evidence_expiry)
                if checkpoint.batch_id not in {row.batch_id for row in rows}:
                    if (
                        sum(row.payload_bytes for row in rows) + len(frozen_new.encode())
                        > execution.max_checkpoint_bytes
                    ):
                        raise _conflict("Execution checkpoint byte budget exhausted")
                    if (
                        execution.max_route_artifact_bytes is not None
                        and retained + checkpoint.retained_route_bytes
                        > execution.max_route_artifact_bytes
                    ):
                        raise _conflict("Execution route artifact byte budget exhausted")
            fresh = await self._active(connection, claim, lock=False)
            if fresh >= expiry:
                raise _conflict("Checkpoint validation validity expired")
        return _ValidatedInventory(
            claim.job.job_id,
            claim.claim_token,
            execution.access_scope,
            execution.identity,
            expected_payload,
            frozen_claim,
            rows,
            expiry,
            frozen_new,
        )

    async def _fence(
        self,
        connection: Any,
        claim: ClaimedJob,
        receipt: _ValidatedInventory,
        *,
        create: bool = False,
    ) -> tuple[BatchExecution, datetime]:
        """Short lock order: job, execution, checkpoint rows. No raw I/O here."""
        now = await self._active(connection, claim)
        execution = self.execution(claim)
        if (
            receipt.job_id,
            receipt.claim_token,
            receipt.access_scope,
            receipt.execution_identity,
            receipt.execution_payload,
            receipt.claim_payload,
        ) != (
            claim.job.job_id,
            claim.claim_token,
            execution.access_scope,
            execution.identity,
            json_text(execution.model_dump(mode="json")),
            self._claim_payload(claim),
        ):
            raise _conflict("Validated checkpoint claim changed")
        if create:
            await connection.execute(
                "INSERT INTO od_executions(access_scope,execution_identity,payload) VALUES($1,$2,$3::jsonb) ON CONFLICT DO NOTHING",
                execution.access_scope,
                execution.identity,
                receipt.execution_payload,
            )
        stored = await connection.fetchval(
            "SELECT payload FROM od_executions WHERE access_scope=$1 AND execution_identity=$2 FOR UPDATE",
            execution.access_scope,
            execution.identity,
        )
        if stored is None or json_text(json.loads(stored)) != receipt.execution_payload:
            raise _conflict("Immutable execution manifest changed")
        if await self._rows(connection, execution, lock=True) != receipt.rows:
            raise _conflict("Validated checkpoint inventory changed")
        # This is the clock AFTER lock acquisition and the final inventory read.
        now = await self._active(connection, claim)
        if now >= receipt.expires_at:
            raise _conflict("Checkpoint publication validity expired")
        return execution, now

    async def begin(self, claim: ClaimedJob) -> list[BatchCheckpoint]:
        receipt = await self._prepare(claim, allow_missing_execution=True)
        execution = self.execution(claim)
        async with self.jobs._publication_connection() as connection:
            async with connection.transaction():
                await self._fence(connection, claim, receipt, create=True)
                row = await connection.fetchrow(
                    "SELECT attempts,reserved_calls FROM od_executions WHERE access_scope=$1 AND execution_identity=$2",
                    execution.access_scope,
                    execution.identity,
                )
                done = {row.batch_id for row in receipt.rows}
                missing = [pairs for batch_id, pairs in execution.batches if batch_id not in done]
                calls = 2 * sum(len(pairs) for pairs in missing) + len(missing) + 2
                if (
                    row["attempts"] >= execution.max_attempts
                    or row["reserved_calls"] + calls > execution.max_provider_calls
                ):
                    raise DomainError(
                        503, "budget_exceeded", "Approved OD attempt or call budget exhausted"
                    )
                if (
                    await connection.fetchval(
                        "SELECT job_id FROM od_attempts WHERE job_id=$1", claim.job.job_id
                    )
                    is not None
                ):
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
        return receipt.checkpoints()

    async def put(self, claim: ClaimedJob, checkpoint: BatchCheckpoint) -> None:
        receipt = await self._prepare(claim, new_checkpoint=checkpoint)
        assert receipt.new_checkpoint is not None
        frozen = BatchCheckpoint.model_validate_json(receipt.new_checkpoint)
        execution = self.execution(claim)
        async with self.jobs._publication_connection() as connection:
            async with connection.transaction():
                await self._fence(connection, claim, receipt)
                attempt = await connection.fetchrow(
                    "SELECT * FROM od_attempts WHERE job_id=$1", claim.job.job_id
                )
                if attempt is None or (
                    attempt["claim_token"],
                    attempt["access_scope"],
                    attempt["execution_identity"],
                ) != (claim.claim_token, execution.access_scope, execution.identity):
                    raise _conflict("Checkpoint has no active approved attempt")
                existing = next(
                    (row for row in receipt.rows if row.batch_id == frozen.batch_id), None
                )
                if existing is not None:
                    if existing.content_hash != frozen.content_hash or json.loads(
                        existing.payload
                    ) != json.loads(receipt.new_checkpoint):
                        raise _conflict("Immutable checkpoint content conflicts")
                    return
                await connection.execute(
                    "INSERT INTO od_checkpoints(access_scope,execution_identity,batch_id,payload,content_hash,payload_bytes,source_job_id,source_claim_token) VALUES($1,$2,$3,$4::jsonb,$5,$6,$7,$8)",
                    execution.access_scope,
                    execution.identity,
                    frozen.batch_id,
                    receipt.new_checkpoint,
                    frozen.content_hash,
                    len(receipt.new_checkpoint.encode()),
                    claim.job.job_id,
                    claim.claim_token,
                )

    async def finalize(self, claim: ClaimedJob) -> None:
        receipt = await self.prepare_publication(claim)
        async with self.jobs._publication_connection() as connection:
            async with connection.transaction():
                await self._fence(connection, claim, receipt)

    async def prepare_publication(self, claim: ClaimedJob) -> _ValidatedInventory:
        # Deliberately fresh on every call; no receipt cache across publication.
        return await self._prepare(claim, complete=True)

    async def publish(
        self, connection: Any, claim: ClaimedJob, run_id: str, receipt: _ValidatedInventory
    ) -> None:
        execution, now = await self._fence(connection, claim, receipt)
        published = await connection.fetchval(
            "UPDATE jobs SET status='succeeded',run_id=$2,finished_at=clock_timestamp(),cache_valid_until=$3,payload=jsonb_set(payload,'{context,cache_valid_until}',to_jsonb($3::timestamptz)) WHERE job_id=$1 AND status='running' AND claim_token=$4 AND lease_expires_at>clock_timestamp() AND $3::timestamptz>clock_timestamp() RETURNING job_id",
            claim.job.job_id,
            run_id,
            receipt.expires_at,
            claim.claim_token,
        )
        if published is None:
            raise _conflict("Checkpoint final publication claim or validity expired")
