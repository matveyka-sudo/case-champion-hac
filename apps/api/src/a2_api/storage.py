"""Transactional PostgreSQL jobs and immutable runs; no ORM at runtime."""

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import cast
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
from a2_contracts.api import AnalysisRequest, ErrorDetail, Job, JobAccepted
from a2_contracts.common import canonical_hash
from a2_contracts.results import ProfileResult
from a2_contracts.scenarios import Comparison, ScenarioRequest
from pydantic import JsonValue

from .jobs import ClaimedJob, JobContext, JobKind, JobRequest, cache_fingerprint, request_hash
from .services import DomainError

RunResult = ProfileResult | Comparison
JOB_FIELDS = (
    "job_id",
    "status",
    "trace_id",
    "bundle_id",
    "input_hash",
    "code_version",
    "created_at",
    "started_at",
    "finished_at",
    "run_id",
    "error",
)


def json_text(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def job_from_row(row: Mapping[str, object]) -> Job:
    data = {name: row[name] for name in JOB_FIELDS}
    if data["error"] is not None:
        data["error"] = json.loads(str(data["error"]))
    return Job.model_validate(data)


class JobRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def replay(self, kind: JobKind, request: JobRequest, key: str) -> JobAccepted | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM jobs WHERE kind=$1 AND idempotency_key=$2", kind, key
        )
        if row is None:
            return None
        if row["input_hash"] != request_hash(request):
            raise DomainError(
                409, "idempotency_conflict", "Idempotency key used for another request"
            )
        job = job_from_row(row)
        return JobAccepted(job_id=job.job_id, status=job.status)

    async def enqueue(
        self, kind: JobKind, request: JobRequest, context: JobContext, key: str, trace_id: str
    ) -> JobAccepted:
        if not key.strip():
            raise DomainError(422, "invalid_request", "Idempotency key must not be blank")
        digest = request_hash(request)
        cache_key = cache_fingerprint(kind, request, context)
        payload = json_text(
            {"request": request.model_dump(mode="json"), "context": context.model_dump(mode="json")}
        )
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                cached = await connection.fetchval(
                    "SELECT run_id FROM jobs WHERE cache_key=$1 AND status='succeeded' "
                    "ORDER BY finished_at DESC LIMIT 1",
                    cache_key,
                )
                row = await connection.fetchrow(
                    "INSERT INTO jobs(job_id,kind,status,trace_id,bundle_id,payload,input_hash,"
                    "code_version,idempotency_key,cache_key,started_at,finished_at,run_id,claim_token) "
                    "VALUES($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,$10,"
                    "CASE WHEN $11::text IS NULL THEN NULL ELSE now() END,"
                    "CASE WHEN $11::text IS NULL THEN NULL ELSE now() END,$11,"
                    "CASE WHEN $11::text IS NULL THEN NULL ELSE 'cache' END) "
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
                )
                if row is None:
                    row = await connection.fetchrow(
                        "SELECT * FROM jobs WHERE kind=$1 AND idempotency_key=$2", kind, key
                    )
                assert row is not None
                if row["input_hash"] != digest:
                    raise DomainError(
                        409, "idempotency_conflict", "Idempotency key used for another request"
                    )
                job = job_from_row(row)
                return JobAccepted(job_id=job.job_id, status=job.status)

    async def get_job(self, job_id: str) -> Job:
        row = await self.pool.fetchrow("SELECT * FROM jobs WHERE job_id=$1", job_id)
        if row is None:
            raise DomainError(404, "unknown_id", "Job not found")
        return job_from_row(row)

    async def get_run(self, run_id: str) -> RunResult:
        row = await self.pool.fetchrow(
            "SELECT result_kind,payload FROM runs WHERE run_id=$1", run_id
        )
        if row is None:
            raise DomainError(404, "unknown_id", "Run not found")
        model = ProfileResult if row["result_kind"] == "profile" else Comparison
        return model.model_validate_json(row["payload"])

    async def get_run_parameters(self, run_id: str) -> dict[str, JsonValue]:
        payload = await self.pool.fetchval(
            "SELECT payload FROM jobs WHERE run_id=$1 AND status='succeeded' ORDER BY finished_at LIMIT 1",
            run_id,
        )
        if payload is None:
            raise DomainError(404, "unknown_id", "Run input parameters not found")
        return cast(dict[str, JsonValue], json.loads(payload))

    async def claim(self, token: str) -> ClaimedJob | None:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    "UPDATE jobs SET status='running',started_at=now(),claim_token=$1 "
                    "WHERE job_id=(SELECT job_id FROM jobs WHERE status='queued' "
                    "ORDER BY created_at,job_id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *",
                    token,
                )
        if row is None:
            return None
        payload = json.loads(row["payload"])
        request_model = AnalysisRequest if row["kind"] == "analysis" else ScenarioRequest
        return ClaimedJob(
            job=job_from_row(row),
            kind=cast(JobKind, row["kind"]),
            request=request_model.model_validate(payload["request"]),
            context=JobContext.model_validate(payload["context"]),
            cache_key=row["cache_key"],
            claim_token=token,
        )

    async def succeed(self, claim: ClaimedJob, result: RunResult) -> None:
        self._check_result_context(claim, result)
        if isinstance(result, Comparison):
            await self._check_saved_baselines(result)
        payload = result.model_dump(mode="json")
        payload["params_hash"] = claim.cache_key
        payload.pop("result_hash")
        payload["result_hash"] = canonical_hash(payload)
        # Revalidate metadata before publishing it; backend owns the saved content hash.
        saved = type(result).model_validate(payload)
        try:
            async with self.pool.acquire() as connection:
                async with connection.transaction():
                    current = await connection.fetchrow(
                        "SELECT status,claim_token FROM jobs WHERE job_id=$1 FOR UPDATE",
                        claim.job.job_id,
                    )
                    if (
                        current is None
                        or current["status"] != "running"
                        or current["claim_token"] != claim.claim_token
                    ):
                        raise DomainError(
                            409, "incompatible_versions", "Job claim is no longer active"
                        )
                    await connection.execute(
                        "INSERT INTO runs(run_id,result_kind,payload,result_hash) VALUES($1,$2,$3::jsonb,$4)",
                        saved.run_id,
                        "profile" if isinstance(saved, ProfileResult) else "comparison",
                        saved.model_dump_json(),
                        saved.result_hash,
                    )
                    await connection.execute(
                        "UPDATE jobs SET status='succeeded',run_id=$2,finished_at=now() WHERE job_id=$1",
                        claim.job.job_id,
                        saved.run_id,
                    )
        except asyncpg.UniqueViolationError as exc:
            raise DomainError(
                409, "incompatible_versions", "Run identity already published"
            ) from exc

    async def _check_saved_baselines(self, result: Comparison) -> None:
        baselines = {
            profile: await self.get_run(run_id) for profile, run_id in result.base_run_ids.items()
        }
        for entry in result.comparisons:
            baseline = baselines[entry.profile_id]
            if not isinstance(baseline, ProfileResult) or (
                baseline.profile_id != entry.profile_id
                or baseline.bundle_id != result.bundle_id
                or baseline.profile_version != entry.profile_version
                or baseline.task_set_hash != entry.task_set_hash
                or baseline.code_version != result.code_version
            ):
                raise DomainError(
                    409, "incompatible_versions", "Saved baseline versions do not match comparison"
                )
            if baseline.status == "insufficient_data":
                if (
                    entry.status != "insufficient_data"
                    or entry.reason != "baseline_insufficient_data"
                ):
                    raise DomainError(
                        409, "incompatible_versions", "Insufficient baseline cannot produce effects"
                    )
                continue
            city = next(
                (city for city in baseline.cities if city.origin_id == entry.origin_id), None
            )
            if city is None or entry.before != (city.score_lower, city.score_upper):
                raise DomainError(
                    409,
                    "incompatible_versions",
                    "Comparison before interval differs from saved baseline",
                )

    @staticmethod
    def _check_result_context(claim: ClaimedJob, result: RunResult) -> None:
        if (
            result.bundle_id != claim.context.bundle_id
            or result.code_version != claim.context.code_version
        ):
            raise DomainError(
                409, "incompatible_versions", "Result bundle/code differs from accepted input"
            )
        if isinstance(claim.request, AnalysisRequest):
            request = claim.request
            if not isinstance(result, ProfileResult) or (
                result.profile_id != request.profile_id
                or result.profile_version != request.profile_version
                or result.task_set_hash != request.task_set_hash
            ):
                raise DomainError(
                    409, "incompatible_versions", "Result profile/task differs from request"
                )
            if result.status != "insufficient_data" and {c.origin_id for c in result.cities} != set(
                request.origin_ids
            ):
                raise DomainError(
                    409, "incompatible_versions", "Result origins differ from request"
                )
        else:
            if (
                not isinstance(result, Comparison)
                or result.base_run_ids != claim.request.base_run_ids
            ):
                raise DomainError(
                    409, "incompatible_versions", "Scenario result baselines differ from request"
                )
            operations = [op.model_dump(mode="json") for op in claim.request.operations]
            if [
                op.model_dump(mode="json") for op in result.operations
            ] != operations or result.operations_hash != canonical_hash(operations):
                raise DomainError(
                    409, "incompatible_versions", "Scenario result operations differ from request"
                )
            for entry in result.comparisons:
                if entry.profile_version != claim.context.profile_versions.get(
                    entry.profile_id
                ) or entry.task_set_hash != claim.context.task_hashes.get(entry.profile_id):
                    raise DomainError(
                        409,
                        "incompatible_versions",
                        "Scenario result versions differ from accepted context",
                    )
            for profile in claim.request.evaluate_profile_ids:
                origins = {
                    entry.origin_id
                    for entry in result.comparisons
                    if entry.profile_id == profile and entry.origin_id is not None
                }
                if origins != set(claim.context.origin_ids[profile]):
                    raise DomainError(
                        409, "incompatible_versions", "Scenario result origins differ from baseline"
                    )

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
            try:
                yield acquired
            finally:
                if acquired and not disconnected.is_set():
                    try:
                        connection.remove_termination_listener(terminated)
                        await connection.execute(
                            "SELECT pg_advisory_unlock(21702,hashtext(current_schema()))"
                        )
                    except (asyncpg.InterfaceError, asyncpg.PostgresConnectionError, OSError):
                        terminated(connection)
