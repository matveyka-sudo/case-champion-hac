"""Transactional PostgreSQL jobs and immutable runs; no ORM at runtime."""

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import cast
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
from a2_contracts.api import AnalysisRequest, ErrorDetail, Job, JobAccepted
from a2_contracts.common import canonical_hash
from a2_contracts.results import ProfileResult
from a2_contracts.scenarios import Comparison, ScenarioRequest
from a2_contracts.v2.requests import parse_analysis_request
from a2_contracts.v2.results import (
    AnalysisResult,
)
from a2_contracts.v2.scenarios import (
    ScenarioProposal,
    ScenarioResult,
    ScenarioRunRequest,
)

from a2_api.core.errors import DomainError
from a2_api.core.jobs import (
    ClaimedJob,
    JobContext,
    JobContextUnion,
    JobKind,
    JobRequest,
    V2JobContext,
    cache_fingerprint,
    request_hash,
)
from a2_api.db.repositories.private_sources import PrivateSourceRepository
from a2_api.db.repositories.scenarios import ScenariosRepository
from a2_api.db.rows import (
    RunResult,
    _check_scope,
    _context,
    _context_scope,
    job_from_row,
    json_text,
)
from a2_api.db.validation import (
    _check_result_context,
    _check_submission,
    _operation_hash,
)


class JobRepository(ScenariosRepository):
    _check_result_context = staticmethod(_check_result_context)
    _check_submission = staticmethod(_check_submission)
    _operation_hash = staticmethod(_operation_hash)

    def __init__(self, pool: asyncpg.Pool, *, lease_seconds: float = 30) -> None:
        if not 0 < lease_seconds <= 600:
            raise ValueError("Lease must be positive and at most 600 seconds")
        super().__init__(pool)
        self.lease_seconds = lease_seconds
        self._worker_connection: ContextVar[asyncpg.Connection | None] = ContextVar(
            "worker_connection", default=None
        )

    @asynccontextmanager
    async def _publication_connection(self) -> AsyncIterator[asyncpg.Connection]:
        # Publication must share the session holding the sole-worker lock. Losing
        # that session then aborts publication rather than leaving a valid TTL
        # on an independently acquired connection. Direct repository users retain
        # the ordinary claim-token/heartbeat fence.
        connection = self._worker_connection.get()
        if connection is not None:
            yield connection
        else:
            async with self.pool.acquire() as connection:
                yield connection

    async def replay(
        self, kind: JobKind, request: JobRequest, key: str, scope: str | None = None
    ) -> JobAccepted | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM jobs WHERE kind=$1 AND idempotency_key=$2", kind, key
        )
        if row is None:
            return None
        _check_scope(row, scope)
        if row["input_hash"] != request_hash(request):
            raise DomainError(
                409, "idempotency_conflict", "Idempotency key used for another request"
            )
        job = job_from_row(row)
        if job.status == "succeeded" and job.run_id is not None:
            await self.get_run(job.run_id, scope)
        return JobAccepted(job_id=job.job_id, status=job.status)

    async def enqueue(
        self,
        kind: JobKind,
        request: JobRequest,
        context: JobContextUnion,
        key: str,
        trace_id: str,
        scope: str | None = None,
    ) -> JobAccepted:
        if not key.strip():
            raise DomainError(422, "invalid_request", "Idempotency key must not be blank")
        self._check_submission(kind, request, context, scope)
        digest = request_hash(request)
        cache_key = cache_fingerprint(kind, request, context)
        owner = _context_scope(context)
        schema_version = "2" if isinstance(context, V2JobContext) else "1"
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                if isinstance(context, V2JobContext) and context.private_source_bindings:
                    await PrivateSourceRepository.check(
                        connection, context.private_source_bindings, context.access_scope
                    )
                if isinstance(context, V2JobContext) and isinstance(request, ScenarioRunRequest):
                    await self._check_scenario_inputs(connection, request, context)
                cached_row = await connection.fetchrow(
                    "SELECT run_id,cache_valid_until FROM jobs WHERE cache_key=$1 AND status='succeeded' "
                    "AND access_scope IS NOT DISTINCT FROM $2 "
                    "AND (cache_valid_until IS NULL OR cache_valid_until>now()) "
                    "ORDER BY finished_at DESC LIMIT 1",
                    cache_key,
                    owner,
                )
                cached = cached_row["run_id"] if cached_row is not None else None
                if cached is not None:
                    await self._read_run(connection, cached, owner, lock=True)
                if cached_row is not None and isinstance(context, V2JobContext):
                    expiry = context.cache_valid_until
                    original_expiry = cached_row["cache_valid_until"]
                    if original_expiry is not None and (expiry is None or original_expiry < expiry):
                        expiry = original_expiry
                    context = context.model_copy(
                        update={
                            "cache_valid_until": expiry,
                            "analysis_id": cached if kind == "analysis" else context.analysis_id,
                        }
                    )
                payload = json_text(
                    {
                        "request": request.model_dump(mode="json"),
                        "context": context.model_dump(mode="json"),
                    }
                )
                row = await connection.fetchrow(
                    "INSERT INTO jobs(job_id,kind,status,trace_id,bundle_id,payload,input_hash,"
                    "code_version,idempotency_key,cache_key,started_at,finished_at,run_id,claim_token,"
                    "schema_version,access_scope,cache_valid_until) "
                    "VALUES($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,$10,"
                    "CASE WHEN $11::text IS NULL THEN NULL ELSE now() END,"
                    "CASE WHEN $11::text IS NULL THEN NULL ELSE now() END,$11,"
                    "CASE WHEN $11::text IS NULL THEN NULL ELSE 'cache' END,$12,$13,$14) "
                    "ON CONFLICT(kind,idempotency_key) DO NOTHING RETURNING *",
                    str(uuid4()),
                    kind,
                    "succeeded" if cached else "queued",
                    trace_id,
                    context.bundle_id,
                    payload,
                    digest,
                    context.code_version,
                    key,
                    cache_key,
                    cached,
                    schema_version,
                    owner,
                    context.cache_valid_until if isinstance(context, V2JobContext) else None,
                )
                if row is None:
                    row = await connection.fetchrow(
                        "SELECT * FROM jobs WHERE kind=$1 AND idempotency_key=$2", kind, key
                    )
                assert row is not None
                _check_scope(row, owner)
                if row["input_hash"] != digest:
                    raise DomainError(
                        409, "idempotency_conflict", "Idempotency key used for another request"
                    )
                job = job_from_row(row)
                if job.status == "succeeded" and job.run_id is not None:
                    await self._read_run(connection, job.run_id, owner, lock=True)
                return JobAccepted(job_id=job.job_id, status=job.status)

    async def get_job(self, job_id: str, scope: str | None = None) -> Job:
        row = await self.pool.fetchrow("SELECT * FROM jobs WHERE job_id=$1", job_id)
        if row is None:
            raise DomainError(404, "unknown_id", "Job not found")
        _check_scope(row, scope)
        job = job_from_row(row)
        if job.status == "succeeded" and job.run_id is not None:
            await self.get_run(job.run_id, scope)
        return job

    async def claim(self, token: str) -> ClaimedJob | None:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    "UPDATE jobs SET status='running',started_at=now(),claim_token=$1,attempts=attempts+1,"
                    "heartbeat_at=now(),lease_expires_at=now()+$2::double precision*interval '1 second' "
                    "WHERE job_id=(SELECT job_id FROM jobs WHERE status='queued' AND attempts<1 "
                    "ORDER BY created_at,job_id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *",
                    token,
                    self.lease_seconds,
                )
        if row is None:
            return None
        payload = json.loads(row["payload"])
        request: JobRequest
        if row["schema_version"] == "2":
            request = (
                parse_analysis_request(payload["request"])
                if row["kind"] == "analysis"
                else ScenarioRunRequest.model_validate(payload["request"])
            )
        else:
            request_model = AnalysisRequest if row["kind"] == "analysis" else ScenarioRequest
            request = request_model.model_validate(payload["request"])
        return ClaimedJob(
            job=job_from_row(row),
            kind=cast(JobKind, row["kind"]),
            request=request,
            context=_context(payload["context"]),
            cache_key=row["cache_key"],
            claim_token=token,
        )

    async def succeed(
        self, claim: ClaimedJob, result: RunResult, proposals: Sequence[ScenarioProposal] = ()
    ) -> None:
        if isinstance(claim.context, JobContext):
            self._check_result_context(claim, result)
        if isinstance(result, Comparison):
            await self._check_saved_baselines(result)
        payload = result.model_dump(mode="json")
        if not isinstance(result, AnalysisResult):
            payload["params_hash"] = claim.cache_key
        if isinstance(result, ScenarioResult) and isinstance(claim.context, V2JobContext):
            assert claim.context.scenario_proposal is not None
            payload["operations_hash"] = self._operation_hash(claim.context.scenario_proposal)
        payload.pop("result_hash")
        payload["result_hash"] = canonical_hash(payload)
        # Revalidate metadata before publishing it; backend owns the saved content hash.
        saved = type(result).model_validate(payload)
        try:
            async with self._publication_connection() as connection:
                async with connection.transaction():
                    current = await connection.fetchrow(
                        "SELECT *,lease_expires_at>now() AS lease_active FROM jobs WHERE job_id=$1 FOR UPDATE",
                        claim.job.job_id,
                    )
                    if (
                        current is None
                        or current["status"] != "running"
                        or current["claim_token"] != claim.claim_token
                        or not current["lease_active"]
                    ):
                        raise DomainError(
                            409, "incompatible_versions", "Job claim is no longer active"
                        )
                    persisted = json.loads(current["payload"])
                    if persisted["context"] != claim.context.model_dump(mode="json") or persisted[
                        "request"
                    ] != claim.request.model_dump(mode="json"):
                        raise DomainError(
                            409, "incompatible_versions", "Claim inputs differ from saved job"
                        )
                    owner = _context_scope(claim.context)
                    _check_scope(current, owner)
                    if isinstance(claim.context, V2JobContext):
                        await self._check_v2_result(connection, claim, saved)
                    elif proposals:
                        raise DomainError(
                            422, "invalid_request", "Legacy jobs cannot publish v2 proposals"
                        )
                    run_id = (
                        saved.analysis_id if isinstance(saved, AnalysisResult) else saved.run_id
                    )
                    result_kind = (
                        "analysis_v2"
                        if isinstance(saved, AnalysisResult)
                        else "scenario_v2"
                        if isinstance(saved, ScenarioResult)
                        else "profile"
                        if isinstance(saved, ProfileResult)
                        else "comparison"
                    )
                    if (
                        isinstance(claim.context, V2JobContext)
                        and claim.context.private_source_bindings
                    ):
                        await connection.execute(
                            "INSERT INTO runs(run_id,result_kind,payload,result_hash,schema_version,access_scope,private_source_bindings) "
                            "VALUES($1,$2,$3::jsonb,$4,'2',$5,$6::jsonb)",
                            run_id,
                            result_kind,
                            saved.model_dump_json(),
                            saved.result_hash,
                            owner,
                            json.dumps(
                                [
                                    b.model_dump(mode="json")
                                    for b in claim.context.private_source_bindings
                                ]
                            ),
                        )
                    else:
                        await connection.execute(
                            "INSERT INTO runs(run_id,result_kind,payload,result_hash,schema_version,access_scope) "
                            "VALUES($1,$2,$3::jsonb,$4,$5,$6)",
                            run_id,
                            result_kind,
                            saved.model_dump_json(),
                            saved.result_hash,
                            "2" if isinstance(claim.context, V2JobContext) else "1",
                            owner,
                        )
                    if proposals:
                        if not isinstance(saved, AnalysisResult) or owner is None:
                            raise DomainError(
                                422, "invalid_request", "Only an analysis may publish proposals"
                            )
                        for proposal in proposals:
                            if proposal.baseline_analysis_id != saved.analysis_id:
                                raise DomainError(
                                    409,
                                    "incompatible_versions",
                                    "Proposal baseline differs from result",
                                )
                        rebound = [
                            proposal.model_copy(update={"baseline_result_hash": saved.result_hash})
                            for proposal in proposals
                        ]
                        await self._save_proposals(connection, rebound, owner)
                    if (
                        isinstance(claim.context, V2JobContext)
                        and claim.context.private_source_bindings
                    ):
                        await PrivateSourceRepository.publish_fence(connection, claim, run_id)
                    elif (
                        isinstance(claim.context, V2JobContext)
                        and claim.context.batch_execution is not None
                    ):
                        from a2_api.db.repositories.checkpoints import CheckpointRepository

                        await CheckpointRepository(self).publish(connection, claim, run_id)
                    else:
                        await connection.execute(
                            "UPDATE jobs SET status='succeeded',run_id=$2,finished_at=now() WHERE job_id=$1",
                            claim.job.job_id,
                            run_id,
                        )
        except asyncpg.UniqueViolationError as exc:
            raise DomainError(
                409, "incompatible_versions", "Run identity already published"
            ) from exc

    async def cancel(self, job_id: str, scope: str | None = None) -> Job:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    "SELECT * FROM jobs WHERE job_id=$1 FOR UPDATE", job_id
                )
                if row is None:
                    raise DomainError(404, "unknown_id", "Job not found")
                _check_scope(row, scope)
                if row["status"] in ("queued", "running"):
                    row = await connection.fetchrow(
                        "UPDATE jobs SET status='cancelled',finished_at=now(),error=NULL,"
                        "lease_expires_at=NULL WHERE job_id=$1 RETURNING *",
                        job_id,
                    )
                assert row is not None
                job = job_from_row(row)
                if job.status == "succeeded" and job.run_id is not None:
                    await self._read_run(connection, job.run_id, scope, lock=True)
                return job

    async def heartbeat(self, claim: ClaimedJob) -> bool:
        await PrivateSourceRepository(self.pool).remaining(claim.context)
        updated = await self.pool.fetchval(
            "UPDATE jobs SET heartbeat_at=now(),lease_expires_at=now()+"
            "$3::double precision*interval '1 second' WHERE job_id=$1 AND claim_token=$2 AND status='running' "
            "AND lease_expires_at>now() AND access_scope IS NOT DISTINCT FROM $4 RETURNING job_id",
            claim.job.job_id,
            claim.claim_token,
            self.lease_seconds,
            _context_scope(claim.context),
        )
        return updated is not None

    async def fail(self, claim: ClaimedJob, error: ErrorDetail) -> None:
        await self.pool.execute(
            "UPDATE jobs SET status='failed',error=$3::jsonb,finished_at=now() "
            "WHERE job_id=$1 AND claim_token=$2 AND status='running'",
            claim.job.job_id,
            claim.claim_token,
            error.model_dump_json(),
        )

    async def recover_orphans(self) -> None:
        """Call only after acquiring the sole worker lock. No implicit reruns."""
        await self.pool.execute(
            "UPDATE jobs SET status='failed',finished_at=now(),error=jsonb_build_object("
            "'code','engine_unavailable','message','Worker interrupted; explicit retry required',"
            "'field_errors','[]'::jsonb,'retryable',true,'trace_id',trace_id) WHERE status='running'"
        )

    @asynccontextmanager
    async def worker_lock(self, lost: asyncio.Event | None = None) -> AsyncIterator[bool]:
        async with self.pool.acquire() as connection:
            acquired = bool(
                await connection.fetchval(
                    "SELECT pg_try_advisory_lock(21702,hashtext(current_schema()))"
                )
            )
            disconnected = asyncio.Event()

            def terminated(closed: object) -> None:
                disconnected.set()
                if lost is not None:
                    lost.set()

            if acquired:
                connection.add_termination_listener(terminated)
            publication_token = self._worker_connection.set(connection) if acquired else None
            try:
                yield acquired
            finally:
                if publication_token is not None:
                    self._worker_connection.reset(publication_token)
                if acquired and not disconnected.is_set():
                    try:
                        connection.remove_termination_listener(terminated)
                        await connection.execute(
                            "SELECT pg_advisory_unlock(21702,hashtext(current_schema()))"
                        )
                    except (asyncpg.InterfaceError, asyncpg.PostgresConnectionError, OSError):
                        terminated(connection)
