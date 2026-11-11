"""Scoped orchestration for canonical analyses. No formulas or routing in HTTP."""

import asyncio
from collections.abc import Awaitable, Callable
from uuid import uuid4

from a2_assistant import FlashAssistant
from a2_contracts.api import Job
from a2_contracts.common import canonical_hash
from a2_contracts.v2.api import BatchProgress, Capabilities, ErrorDetail, JobAccepted, JobView
from a2_contracts.v2.assistant import Explanation, ExplanationRequest
from a2_contracts.v2.base import VersionRefs
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.requests import AnalysisRequest
from a2_contracts.v2.results import AnalysisResult
from a2_contracts.v2.scenarios import (
    ScenarioProposal,
    ScenarioResult,
    ScenarioRunOptions,
)

from a2_api.assistant.explanations import explain as _explain
from a2_api.core.analysis_groups import GroupAdmission
from a2_api.core.batches import BatchExecution, BatchPlan
from a2_api.core.errors import DomainError, unavailable
from a2_api.core.job_admission import Admission
from a2_api.core.jobs import V2JobContext, batch_context_fingerprint, request_hash
from a2_api.core.registry import ProductRegistry
from a2_api.core.scope import territory_ids as territory_ids
from a2_api.db.repositories.jobs import JobRepository
from a2_api.services.scenarios import proposals as _proposals
from a2_api.services.scenarios import scenario as _scenario
from a2_api.services.scenarios import scenario_result as _scenario_result

Preflight = Callable[[AnalysisRequest, AcceptedData], Awaitable[BatchPlan | None]]
DEFAULT_TEMPLATES = frozenset({"coordinate_transfer", "additional_departure"})


class AnalysisService:
    def __init__(
        self,
        registry: ProductRegistry | None = None,
        repository: JobRepository | None = None,
        *,
        code_version: str | None = None,
        engine_enabled: bool = False,
        preflight: Preflight | None = None,
        assistant: FlashAssistant | None = None,
        registered_template_ids: frozenset[str] = DEFAULT_TEMPLATES,
    ) -> None:
        self.registry = registry or ProductRegistry()
        self.repository = repository
        self.code_version = code_version
        self.engine_enabled = engine_enabled
        self.preflight = preflight
        self.assistant = assistant
        self.registered_template_ids = registered_template_ids
        self._admission: Admission[JobAccepted] = Admission()

    def capabilities(self) -> Capabilities:
        result = self.registry.capabilities()
        if not self.engine_enabled or self.repository is None or not self.code_version:
            for item in result.analyses:
                item.calculation_available = False
                item.reason_code = "ENGINE_OR_STORAGE_UNAVAILABLE"
        return result

    def _repository(self) -> JobRepository:
        if self.repository is None:
            raise unavailable()
        return self.repository

    def _engine(self, kind: str) -> None:
        if (
            not self.engine_enabled
            or not self.code_version
            or not any(
                c.analysis_kind == kind and c.calculation_available
                for c in self.registry.capabilities().analyses
            )
        ):
            raise unavailable()

    @staticmethod
    def _key(key: str, scope: str) -> str:
        if not key.strip():
            raise DomainError(422, "invalid_request", "Idempotency key must not be blank")
        return canonical_hash({"scope": scope, "key": key})

    async def analyse(
        self,
        request: AnalysisRequest,
        key: str,
        request_id: str,
        scope: str,
        *,
        group: GroupAdmission | None = None,
    ) -> JobAccepted:
        repository = self._repository()
        scoped_key = (
            self._key(key, scope)
            if group is None
            else canonical_hash({"scope": scope, "group_admission": vars(group)})
        )
        replay = await repository.replay("analysis", request, scoped_key, scope=scope)
        if replay is not None:
            return JobAccepted(job_id=replay.job_id, status=replay.status, request_id=request_id)
        accepted = await self._admission.run(
            (scope, scoped_key),
            request_hash(request),
            lambda: self._admit(repository, request, scoped_key, request_id, scope, group=group),
        )
        return accepted.model_copy(update={"request_id": request_id})

    async def close_admission(self) -> None:
        await self._admission.close()

    async def _admit(
        self,
        repository: JobRepository,
        request: AnalysisRequest,
        scoped_key: str,
        request_id: str,
        scope: str,
        *,
        group: GroupAdmission | None = None,
    ) -> JobAccepted:
        # An outer replay can return a captured miss after the previous producer
        # has committed and released its slot. Recheck after reserving this one.
        replay = await repository.replay("analysis", request, scoped_key, scope=scope)
        if replay is not None:
            return JobAccepted(job_id=replay.job_id, status=replay.status, request_id=request_id)
        self._engine(request.analysis_kind)
        normalized = self.registry.normalize(request)
        snapshot = self.registry.resolve_snapshot(normalized)
        normalized = normalized.model_copy(update={"snapshot_id": snapshot.snapshot_id})
        batch_plan = None
        if self.preflight is not None:
            # I/O readiness only. CPU calculations stay in the separate worker process.
            batch_plan = await asyncio.wait_for(self.preflight(normalized, snapshot), timeout=30)
        assert self.code_version is not None
        context = V2JobContext(
            schema_version="2",
            snapshot_id=snapshot.snapshot_id,
            snapshot_hash=canonical_hash(snapshot.model_dump(mode="json")),
            versions=VersionRefs(
                snapshot_id=snapshot.snapshot_id,
                graph_version=snapshot.provenance.graph_version,
                calendar_version=snapshot.provenance.calendar_version,
                population_version=snapshot.provenance.population_version,
                methodology_version=self.registry.methodology.version,
                config_hash=self.registry.config_hash,
                code_version=self.code_version,
            ),
            analysis_id=str(uuid4()),
            normalized_request=normalized,
            access_scope=scope,
            cache_valid_until=self.registry.required_cache_valid_until(normalized, snapshot),
        )
        if batch_plan is not None:
            plan = BatchPlan.model_validate(batch_plan.model_dump(mode="json"))
            context = context.model_copy(
                update={
                    "cache_valid_until": min(
                        plan.valid_until,
                        context.cache_valid_until or plan.valid_until,
                    )
                }
            )
            execution = BatchExecution.model_validate(
                {
                    **plan.model_dump(mode="json"),
                    "access_scope": scope,
                    "context_fingerprint": batch_context_fingerprint(context),
                }
            )
            context = V2JobContext.model_validate(
                {
                    **context.model_dump(mode="json"),
                    "batch_execution": execution.model_dump(mode="json"),
                }
            )
        if group is None:
            accepted = await repository.enqueue(
                "analysis", request, context, scoped_key, request_id, scope=scope
            )
        else:
            accepted = await repository.enqueue(
                "analysis", request, context, scoped_key, request_id, scope=scope, group=group
            )
        return JobAccepted(job_id=accepted.job_id, status=accepted.status, request_id=request_id)

    async def job(self, job_id: str, scope: str, *, include_progress: bool = False) -> JobView:
        progress = None
        if include_progress:
            from a2_api.db.repositories.job_progress import job_with_progress

            job, progress = await job_with_progress(self._repository(), job_id, scope)
        else:
            job = await self._repository().get_job(job_id, scope=scope)
        return await self._view(job, scope, progress=progress)

    async def _view(
        self, job: Job, scope: str, *, progress: BatchProgress | None = None
    ) -> JobView:
        analysis_id = scenario_run_id = None
        if job.status == "succeeded":
            assert job.run_id is not None
            result = await self._repository().get_run(job.run_id, scope=scope)
            if isinstance(result, AnalysisResult):
                analysis_id = result.analysis_id
            elif isinstance(result, ScenarioResult):
                scenario_run_id = result.run_id
            else:
                raise DomainError(404, "unknown_id", "Job not found")
        error = (
            None
            if job.error is None
            else ErrorDetail(
                code=job.error.code,
                message=job.error.message,
                field_errors=job.error.field_errors,
                request_id=job.trace_id,
                retryable=job.error.retryable,
            )
        )
        return JobView(
            job_id=job.job_id,
            status=job.status,
            request_id=job.trace_id,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            analysis_id=analysis_id,
            scenario_run_id=scenario_run_id,
            error=error,
            progress=progress,
        )

    async def cancel(self, job_id: str, scope: str) -> JobView:
        return await self._view(await self._repository().cancel(job_id, scope=scope), scope)

    async def analysis(self, analysis_id: str, scope: str) -> AnalysisResult:
        result = await self._repository().get_run(analysis_id, scope=scope)
        if not isinstance(result, AnalysisResult):
            raise DomainError(404, "unknown_id", "Analysis not found")
        return result

    async def proposals(self, analysis_id: str, scope: str) -> list[ScenarioProposal]:
        return await _proposals(self, analysis_id, scope)

    async def scenario(
        self, scenario_id: str, options: ScenarioRunOptions, key: str, request_id: str, scope: str
    ) -> JobAccepted:
        return await _scenario(self, scenario_id, options, key, request_id, scope)

    async def scenario_result(self, run_id: str, scope: str) -> ScenarioResult:
        return await _scenario_result(self, run_id, scope)

    async def accepted_snapshot(self, result: AnalysisResult, scope: str) -> AcceptedData | None:
        """Bind tool metadata to the snapshot hash actually accepted by this saved run."""
        snapshot = self.registry.snapshots.get(result.snapshot_id)
        if snapshot is None:
            return None
        parameters = await self._repository().get_run_parameters(result.analysis_id, scope=scope)
        context = V2JobContext.model_validate(parameters["context"])
        if context.versions != result.versions or context.snapshot_hash != canonical_hash(
            snapshot.model_dump(mode="json")
        ):
            return None
        return snapshot

    async def explain(
        self, analysis_id: str, scope: str, request_id: str, body: ExplanationRequest | None = None
    ) -> Explanation:
        return await _explain(self, analysis_id, scope, request_id, body)
