"""Transactional PostgreSQL jobs and immutable runs; no ORM at runtime."""

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import cast
from uuid import NAMESPACE_URL, uuid4, uuid5

import asyncpg  # type: ignore[import-untyped]
from a2_contracts.api import AnalysisRequest, ErrorDetail, Job, JobAccepted
from a2_contracts.common import canonical_hash
from a2_contracts.results import ProfileResult
from a2_contracts.scenarios import Comparison, ScenarioRequest
from a2_contracts.v2.requests import RequestBase, parse_analysis_request
from a2_contracts.v2.results import (
    AnalysisResult,
    validate_reference_applicability,
    validate_score_context,
)
from a2_contracts.v2.scenarios import (
    ScenarioProposal,
    ScenarioResult,
    ScenarioRunRequest,
    _measure_key,
)
from pydantic import JsonValue, ValidationError

from a2_api.jobs import (
    ClaimedJob,
    JobContext,
    JobContextUnion,
    JobKind,
    JobRequest,
    V2JobContext,
    cache_fingerprint,
    request_hash,
)
from a2_api.services import DomainError

RunResult = ProfileResult | Comparison | AnalysisResult | ScenarioResult
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


def _check_scope(row: Mapping[str, object], scope: str | None) -> None:
    if (row["schema_version"] == "2" and (scope is None or row["access_scope"] != scope)) or (
        row["schema_version"] != "2" and scope is not None
    ):
        raise DomainError(404, "unknown_id", "Resource not found")


def _context_scope(context: JobContextUnion) -> str | None:
    return context.access_scope if isinstance(context, V2JobContext) else None


def _context(data: dict[str, object]) -> JobContextUnion:
    model = V2JobContext if data.get("schema_version") == "2" else JobContext
    return model.model_validate(data)


def _run_from_row(row: Mapping[str, object]) -> RunResult:
    models: dict[
        str, type[ProfileResult] | type[Comparison] | type[AnalysisResult] | type[ScenarioResult]
    ] = {
        "profile": ProfileResult,
        "comparison": Comparison,
        "analysis_v2": AnalysisResult,
        "scenario_v2": ScenarioResult,
    }
    model = models[str(row["result_kind"])]
    try:
        payload = json.loads(str(row["payload"]))
        if not isinstance(payload, dict) or payload.get("result_hash") != row["result_hash"]:
            raise ValueError("Stored hash mismatch")
        unhashed = {key: value for key, value in payload.items() if key != "result_hash"}
        if canonical_hash(unhashed) != row["result_hash"]:
            raise ValueError("Stored payload hash mismatch")
        return model.model_validate_json(str(row["payload"]))
    except (ValueError, ValidationError):
        raise DomainError(
            409, "stored_result_incompatible", "Stored result requires recalculation"
        ) from None


def _proposal_from_row(row: Mapping[str, object]) -> ScenarioProposal:
    """Parse an already scoped immutable command without trusting stale hash metadata."""
    try:
        payload = json.loads(str(row["payload"]))
        if canonical_hash(payload) != row["content_hash"]:
            raise ValueError("Stored command hash mismatch")
        return ScenarioProposal.model_validate_json(str(row["payload"]))
    except (ValueError, ValidationError):
        raise DomainError(
            409, "stored_result_incompatible", "Stored result requires recalculation"
        ) from None


class JobRepository:
    def __init__(self, pool: asyncpg.Pool, *, lease_seconds: float = 30) -> None:
        if not 0 < lease_seconds <= 600:
            raise ValueError("Lease must be positive and at most 600 seconds")
        self.pool = pool
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

    async def get_run(self, run_id: str, scope: str | None = None) -> RunResult:
        async with self.pool.acquire() as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                return await self._read_run(connection, run_id, scope)

    async def get_run_parameters(
        self, run_id: str, scope: str | None = None
    ) -> dict[str, JsonValue]:
        await self.get_run(run_id, scope)
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
                    await connection.execute(
                        "UPDATE jobs SET status='succeeded',run_id=$2,finished_at=now() WHERE job_id=$1",
                        claim.job.job_id,
                        run_id,
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
        if not isinstance(claim.context, JobContext) or not isinstance(
            result, (ProfileResult, Comparison)
        ):
            raise DomainError(409, "incompatible_versions", "Legacy result type differs from job")
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
        elif isinstance(claim.request, ScenarioRequest):
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
        else:
            raise DomainError(409, "incompatible_versions", "Legacy request type differs from job")

    @staticmethod
    def _check_submission(
        kind: JobKind, request: JobRequest, context: JobContextUnion, scope: str | None
    ) -> None:
        if isinstance(context, JobContext):
            if scope is not None:
                raise DomainError(404, "unknown_id", "Legacy operation not found")
            if not isinstance(request, (AnalysisRequest, ScenarioRequest)):
                raise DomainError(
                    422, "invalid_request", "Legacy context requires a legacy request"
                )
            return
        if scope is not None and scope != context.access_scope:
            raise DomainError(404, "unknown_id", "Resource not found")
        if kind == "scenario":
            if not isinstance(request, ScenarioRunRequest) or context.scenario_proposal is None:
                raise DomainError(422, "invalid_request", "Scenario requires its saved proposal")
            if (
                request.baseline_analysis_id != context.analysis_id
                or request.scenario_id != context.scenario_proposal.scenario_id
            ):
                raise DomainError(
                    409, "incompatible_versions", "Scenario command differs from context"
                )
        elif not isinstance(request, RequestBase) or context.scenario_proposal is not None:
            raise DomainError(422, "invalid_request", "Analysis requires an analysis request")
        else:
            normalized = context.normalized_request
            if (
                request.model_dump(mode="json", exclude={"snapshot_id", "methodology"})
                != normalized.model_dump(mode="json", exclude={"snapshot_id", "methodology"})
                or (
                    request.snapshot_id is not None
                    and request.snapshot_id != normalized.snapshot_id
                )
                or (
                    request.methodology is not None
                    and request.methodology != normalized.methodology
                )
            ):
                raise DomainError(
                    409, "incompatible_versions", "Normalized request changes user parameters"
                )

    @classmethod
    async def _read_run(
        cls, connection: asyncpg.Connection, run_id: str, scope: str | None, *, lock: bool = False
    ) -> RunResult:
        suffix = " FOR SHARE" if lock else ""
        row = await connection.fetchrow("SELECT * FROM runs WHERE run_id=$1" + suffix, run_id)
        if row is None:
            raise DomainError(404, "unknown_id", "Run not found")
        _check_scope(row, scope)
        result = _run_from_row(row)
        if not isinstance(result, ScenarioResult):
            return result
        try:
            baseline_row = await connection.fetchrow(
                "SELECT * FROM runs WHERE run_id=$1" + suffix, result.baseline_analysis_id
            )
            if baseline_row is None:
                raise ValueError("Missing baseline")
            _check_scope(baseline_row, scope)
            if baseline_row["result_kind"] != "analysis_v2":
                raise ValueError("Invalid baseline kind")
            baseline = _run_from_row(baseline_row)
            if not isinstance(baseline, AnalysisResult):
                raise ValueError("Invalid baseline")
            proposal_row = await connection.fetchrow(
                "SELECT payload,content_hash FROM scenario_proposals WHERE scenario_id=$1 AND access_scope=$2"
                + suffix,
                result.scenario_id,
                scope,
            )
            if proposal_row is None:
                raise ValueError("Missing proposal")
            command = _proposal_from_row(proposal_row)
            cls._validate_scenario_semantics(result, baseline, command)
        except (DomainError, ValueError):
            raise DomainError(
                409, "stored_result_incompatible", "Stored result requires recalculation"
            ) from None
        return result

    async def _check_scenario_inputs(
        self, connection: asyncpg.Connection, request: ScenarioRunRequest, context: V2JobContext
    ) -> AnalysisResult:
        row = await connection.fetchrow(
            "SELECT payload,content_hash FROM scenario_proposals WHERE scenario_id=$1 AND access_scope=$2 FOR SHARE",
            request.scenario_id,
            context.access_scope,
        )
        if row is None:
            raise DomainError(404, "unknown_id", "Scenario proposal not found")
        command = _proposal_from_row(row)
        if command != context.scenario_proposal:
            raise DomainError(
                409, "incompatible_versions", "Proposal differs from saved immutable command"
            )
        baseline = await self._read_run(
            connection, request.baseline_analysis_id, context.access_scope, lock=True
        )
        if not isinstance(baseline, AnalysisResult):
            raise DomainError(404, "unknown_id", "Analysis not found")
        if (
            baseline.request != context.normalized_request
            or baseline.versions != context.versions
            or baseline.result_hash != command.baseline_result_hash
        ):
            raise DomainError(409, "incompatible_versions", "Scenario baseline inputs changed")
        return baseline

    async def _check_v2_result(
        self, connection: asyncpg.Connection, claim: ClaimedJob, result: RunResult
    ) -> None:
        context = claim.context
        assert isinstance(context, V2JobContext)
        if (
            not isinstance(result, (AnalysisResult, ScenarioResult))
            or result.versions != context.versions
        ):
            raise DomainError(
                409, "incompatible_versions", "Result type/versions differ from saved job"
            )
        if isinstance(result, AnalysisResult):
            if (
                claim.kind != "analysis"
                or not isinstance(claim.request, RequestBase)
                or result.analysis_id != context.analysis_id
                or result.request != context.normalized_request
            ):
                raise DomainError(
                    409, "incompatible_versions", "Result identity/request differs from saved job"
                )
            return
        if claim.kind != "scenario" or not isinstance(claim.request, ScenarioRunRequest):
            raise DomainError(409, "incompatible_versions", "Scenario result differs from job kind")
        baseline = await self._check_scenario_inputs(connection, claim.request, context)
        assert context.scenario_proposal is not None
        self._validate_scenario_semantics(result, baseline, context.scenario_proposal)

    @staticmethod
    def _validate_scenario_semantics(
        result: ScenarioResult, baseline: AnalysisResult, command: ScenarioProposal
    ) -> None:
        if (
            result.versions != baseline.versions
            or command.versions != baseline.versions
            or command.baseline_analysis_id != baseline.analysis_id
            or command.baseline_result_hash != baseline.result_hash
        ):
            raise DomainError(
                409, "incompatible_versions", "Scenario baseline versions/hash differ"
            )
        try:
            validate_score_context(
                result.after.metrics, result.after.scores, baseline.analysis_kind
            )
        except ValueError:
            raise DomainError(
                409, "incompatible_versions", "Scenario index weights are incomplete"
            ) from None
        references = {item.reference_id: item for item in baseline.references}
        baseline_metrics = {item.metric_id: item for item in baseline.metrics}
        after_metrics = {item.metric_id: item for item in result.after.metrics}
        baseline_scores = {item.score_id: item for item in baseline.scores}
        journeys = {item.journey_id: item for item in baseline.journeys}
        renamed = {
            effect.after_id: effect.before_id
            for effect in result.deltas
            if effect.kind == "metric" and effect.before_id and effect.after_id
        }
        renamed_scores = {
            effect.after_id: effect.before_id
            for effect in result.deltas
            if effect.kind == "score" and effect.before_id and effect.after_id
        }
        after_scores = {item.score_id: item for item in result.after.scores}
        evaluated_metrics: dict[str, set[str]] = {}

        def operand_metric_ids(score_id: str) -> set[str]:
            # ScenarioValues already checked that the component graph is resolved
            # and acyclic. Resolve the same transitive operands for every score.
            if score_id not in evaluated_metrics:
                linked: set[str] = set()
                for component in after_scores[score_id].components:
                    if component.kind == "metric":
                        linked.add(component.id)
                    else:
                        linked.update(operand_metric_ids(component.id))
                evaluated_metrics[score_id] = linked
            return evaluated_metrics[score_id]

        for score in result.after.scores:
            full = score.status == "available" and score.completeness == "full"
            required_references = set(score.reference_ids)
            previous_score = baseline_scores.get(score.score_id) or baseline_scores.get(
                renamed_scores.get(score.score_id, "")
            )
            if previous_score is None:
                matches_scores = [
                    item
                    for item in baseline.scores
                    if _measure_key(
                        item.model_copy(
                            update={
                                "completeness": score.completeness,
                                "calculation_kind": score.calculation_kind,
                            }
                        )
                    )
                    == _measure_key(score)
                ]
                previous_score = matches_scores[0] if len(matches_scores) == 1 else None
            if previous_score is not None and _measure_key(
                previous_score.model_copy(
                    update={
                        "completeness": score.completeness,
                        "calculation_kind": score.calculation_kind,
                    }
                )
            ) == _measure_key(score):
                # A new declaration must not erase the frozen operands of this task.
                required_references.update(previous_score.reference_ids)
            try:
                score.validate_calculation(baseline.analysis_kind)
            except ValueError:
                raise DomainError(
                    409, "incompatible_versions", "Scenario index semantics are unavailable"
                ) from None
            applicability_journeys = []
            for metric_id in sorted(operand_metric_ids(score.score_id)):
                metric = after_metrics[metric_id]
                previous = baseline_metrics.get(metric.metric_id) or baseline_metrics.get(
                    renamed.get(metric.metric_id, "")
                )
                if previous is None:
                    matches = [
                        item
                        for item in baseline.metrics
                        if _measure_key(item) == _measure_key(metric)
                    ]
                    previous = matches[0] if len(matches) == 1 else None
                supplied_journey = journeys.get(metric.journey_id or "")
                frozen_journey = (
                    journeys.get(previous.journey_id or "") if previous is not None else None
                )
                if supplied_journey is not None and frozen_journey is not None:
                    frozen_reference = references.get(frozen_journey.reference_id or "")
                    origins = (
                        frozen_reference.boundary_node_sets.origin_node_ids
                        if frozen_reference
                        else [frozen_journey.origin_node_id]
                    )
                    destinations = (
                        frozen_reference.boundary_node_sets.destination_node_ids
                        if frozen_reference
                        else [frozen_journey.destination_node_id]
                    )
                    if (
                        supplied_journey.origin_node_id not in origins
                        or supplied_journey.destination_node_id not in destinations
                    ):
                        raise DomainError(
                            409, "incompatible_versions", "Scenario journey belongs to another task"
                        )
                # Check both: a changed journey ID cannot replace the original
                # task's frozen reference, even with another complete baseline journey.
                dependency_journeys = [
                    item for item in (frozen_journey, supplied_journey) if item is not None
                ]
                if full and score.calculation_kind == "initial_wait" and not dependency_journeys:
                    raise DomainError(
                        409, "incompatible_versions", "Scenario wait operand is unavailable"
                    )
                applicability_journeys.extend(dependency_journeys)
                for journey in dependency_journeys:
                    if full and (journey.status != "available" or journey.completeness != "full"):
                        raise DomainError(
                            409,
                            "incompatible_versions",
                            "Scenario journey dependency is unavailable",
                        )
                    if (
                        score.calculation_kind == "reference_ratio"
                        and journey.reference_id is not None
                    ):
                        required_references.add(journey.reference_id)
            if full and score.calculation_kind == "reference_ratio" and not required_references:
                raise DomainError(
                    409, "incompatible_versions", "Scenario reference operand is unavailable"
                )
            if (
                full
                and score.calculation_kind == "delivery_deadline"
                and not any(
                    component.kind == "metric"
                    and after_metrics[component.id].time_kind == "complete_chain"
                    for component in score.components
                )
            ):
                raise DomainError(
                    409, "incompatible_versions", "Scenario delivery operand is incomplete"
                )
            for identifier in required_references:
                reference = references.get(identifier)
                if reference is None or (
                    full and (reference.status != "available" or reference.completeness != "full")
                ):
                    raise DomainError(
                        409, "incompatible_versions", "Scenario index reference is unavailable"
                    )
                if full:
                    try:
                        validate_reference_applicability(
                            reference, baseline.request, applicability_journeys
                        )
                    except ValueError:
                        raise DomainError(
                            409,
                            "incompatible_versions",
                            "Scenario reference does not apply to the task",
                        ) from None
        if (
            result.baseline_analysis_id != baseline.analysis_id
            or result.scenario_id != command.scenario_id
            or result.baseline_result_hash != baseline.result_hash
        ):
            raise DomainError(
                409, "incompatible_versions", "Scenario result baseline identity differs"
            )
        before_metrics = {item.metric_id: item for item in result.before.metrics}
        before_scores = {item.score_id: item for item in result.before.scores}
        if before_metrics != {
            item.metric_id: item for item in baseline.metrics
        } or before_scores != {item.score_id: item for item in baseline.scores}:
            raise DomainError(
                409, "incompatible_versions", "Scenario before differs from saved baseline"
            )
        expected = {
            service_id: command.fixed_parameters.shift_seconds
            for service_id in command.fixed_parameters.service_ids
        }
        actual = {change.service_id: change.shift_seconds for change in result.service_changes}
        if (
            actual != expected
            or result.feasibility != command.feasibility
            or result.editable_local_graph != command.editable_local_graph
            or result.turnaround_evidence != command.fixed_parameters.turnaround_evidence
            or result.resource_evidence != command.fixed_parameters.resource_evidence
            or not set(command.assumptions) <= set(result.assumptions)
            or not set(command.limitations) <= set(result.limitations)
        ):
            raise DomainError(
                409, "incompatible_versions", "Scenario result changes frozen operation/constraints"
            )

    @staticmethod
    def _operation_hash(proposal: ScenarioProposal) -> str:
        return canonical_hash(
            {
                "operation_type": proposal.operation_type,
                "fixed_parameters": proposal.fixed_parameters.model_dump(mode="json"),
            }
        )

    async def get_proposal(self, scenario_id: str, scope: str) -> ScenarioProposal:
        row = await self.pool.fetchrow(
            "SELECT payload,content_hash FROM scenario_proposals WHERE scenario_id=$1 AND access_scope=$2",
            scenario_id,
            scope,
        )
        if row is None:
            raise DomainError(404, "unknown_id", "Scenario proposal not found")
        return _proposal_from_row(row)

    async def get_proposals(self, analysis_id: str, scope: str) -> list[ScenarioProposal]:
        baseline = await self.get_run(analysis_id, scope)
        if not isinstance(baseline, AnalysisResult):
            raise DomainError(404, "unknown_id", "Analysis not found")
        rows = await self.pool.fetch(
            "SELECT payload,content_hash FROM scenario_proposals WHERE baseline_analysis_id=$1 AND access_scope=$2 "
            "ORDER BY created_at,scenario_id",
            analysis_id,
            scope,
        )
        return [_proposal_from_row(row) for row in rows]

    async def save_proposals(
        self, proposals: Sequence[ScenarioProposal], scope: str
    ) -> list[ScenarioProposal]:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                return await self._save_proposals(connection, proposals, scope)

    async def _save_proposals(
        self, connection: asyncpg.Connection, proposals: Sequence[ScenarioProposal], scope: str
    ) -> list[ScenarioProposal]:
        if len(proposals) > 10:
            raise DomainError(422, "invalid_request", "Too many scenario proposals")
        saved: dict[str, ScenarioProposal] = {}
        for proposal in proposals:
            baseline = await self._read_run(connection, proposal.baseline_analysis_id, scope)
            if not isinstance(baseline, AnalysisResult):
                raise DomainError(404, "unknown_id", "Analysis not found")
            if (
                proposal.versions != baseline.versions
                or proposal.baseline_result_hash != baseline.result_hash
            ):
                raise DomainError(
                    409, "incompatible_versions", "Proposal baseline versions/hash differ"
                )
            identity = canonical_hash(
                {
                    "scope": scope,
                    "baseline_hash": baseline.result_hash,
                    "template_id": proposal.template_id,
                    "operation_type": proposal.operation_type,
                    "fixed_parameters": proposal.fixed_parameters.model_dump(mode="json"),
                }
            )
            payload = proposal.model_dump(mode="json") | {
                "scenario_id": str(uuid5(NAMESPACE_URL, identity))
            }
            command = ScenarioProposal.model_validate(payload)
            content_hash = canonical_hash(command.model_dump(mode="json"))
            await connection.execute(
                "INSERT INTO scenario_proposals(scenario_id,baseline_analysis_id,access_scope,payload,content_hash) "
                "VALUES($1,$2,$3,$4::jsonb,$5) ON CONFLICT(scenario_id) DO NOTHING",
                command.scenario_id,
                command.baseline_analysis_id,
                scope,
                command.model_dump_json(),
                content_hash,
            )
            existing = await connection.fetchrow(
                "SELECT access_scope,content_hash,payload FROM scenario_proposals WHERE scenario_id=$1",
                command.scenario_id,
            )
            if (
                existing is None
                or existing["access_scope"] != scope
                or existing["content_hash"] != content_hash
            ):
                raise DomainError(
                    409, "incompatible_versions", "Scenario proposal identity already published"
                )
            _proposal_from_row(existing)
            saved[command.scenario_id] = command
        return list(saved.values())

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
