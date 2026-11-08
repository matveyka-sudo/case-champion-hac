"""Scoped orchestration for canonical analyses. No formulas or routing in HTTP."""

import asyncio
from collections.abc import Awaitable, Callable
from uuid import uuid4

from a2_assistant import AccessScope, FlashAssistant
from a2_assistant.core import template_explanation
from a2_contracts.api import Job
from a2_contracts.common import canonical_hash
from a2_contracts.v2.api import Capabilities, ErrorDetail, JobAccepted, JobView
from a2_contracts.v2.assistant import Explanation
from a2_contracts.v2.base import VersionRefs
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.requests import AnalysisRequest, UrbanCargoRequest, UrbanPassengerRequest
from a2_contracts.v2.results import AnalysisResult
from a2_contracts.v2.scenarios import (
    ScenarioProposal,
    ScenarioResult,
    ScenarioRunOptions,
    ScenarioRunRequest,
)

from .jobs import V2JobContext
from .product_registry import ProductRegistry
from .services import DomainError, unavailable
from .storage import JobRepository

Preflight = Callable[[AnalysisRequest, AcceptedData], Awaitable[None]]
DEFAULT_TEMPLATES = frozenset({"coordinate_transfer", "additional_departure"})


def territory_ids(request: AnalysisRequest) -> frozenset[str]:
    if isinstance(request, (UrbanPassengerRequest, UrbanCargoRequest)):
        return frozenset({request.city_id})
    return frozenset({request.origin_id, request.destination_id})


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
        self, request: AnalysisRequest, key: str, request_id: str, scope: str
    ) -> JobAccepted:
        repository = self._repository()
        scoped_key = self._key(key, scope)
        replay = await repository.replay("analysis", request, scoped_key, scope=scope)
        if replay is not None:
            return JobAccepted(job_id=replay.job_id, status=replay.status, request_id=request_id)
        self._engine(request.analysis_kind)
        normalized = self.registry.normalize(request)
        snapshot = self.registry.resolve_snapshot(normalized)
        normalized = normalized.model_copy(update={"snapshot_id": snapshot.snapshot_id})
        if self.preflight is not None:
            # I/O readiness only. CPU calculations stay in the separate worker process.
            await asyncio.wait_for(self.preflight(normalized, snapshot), timeout=30)
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
        accepted = await repository.enqueue(
            "analysis", request, context, scoped_key, request_id, scope=scope
        )
        return JobAccepted(job_id=accepted.job_id, status=accepted.status, request_id=request_id)

    async def job(self, job_id: str, scope: str) -> JobView:
        job = await self._repository().get_job(job_id, scope=scope)
        return await self._view(job, scope)

    async def _view(self, job: Job, scope: str) -> JobView:
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
        )

    async def cancel(self, job_id: str, scope: str) -> JobView:
        return await self._view(await self._repository().cancel(job_id, scope=scope), scope)

    async def analysis(self, analysis_id: str, scope: str) -> AnalysisResult:
        result = await self._repository().get_run(analysis_id, scope=scope)
        if not isinstance(result, AnalysisResult):
            raise DomainError(404, "unknown_id", "Analysis not found")
        return result

    async def proposals(self, analysis_id: str, scope: str) -> list[ScenarioProposal]:
        await self.analysis(analysis_id, scope)
        return [
            p
            for p in await self._repository().get_proposals(analysis_id, scope)
            if p.template_id in self.registered_template_ids
        ]

    async def scenario(
        self, scenario_id: str, options: ScenarioRunOptions, key: str, request_id: str, scope: str
    ) -> JobAccepted:
        repository = self._repository()
        proposal = await repository.get_proposal(scenario_id, scope)
        baseline = await self.analysis(proposal.baseline_analysis_id, scope)
        request = ScenarioRunRequest(
            schema_version=options.schema_version,
            scenario_id=scenario_id,
            baseline_analysis_id=baseline.analysis_id,
        )
        scoped_key = self._key(key, scope)
        replay = await repository.replay("scenario", request, scoped_key, scope=scope)
        if replay is not None:
            return JobAccepted(job_id=replay.job_id, status=replay.status, request_id=request_id)
        if proposal.template_id not in self.registered_template_ids:
            raise DomainError(422, "unsupported_scenario", "Scenario template is not enabled")
        if not proposal.editable_local_graph:
            raise DomainError(
                409, "unsupported_scenario", "Scenario requires an editable local graph"
            )
        if (
            proposal.versions != baseline.versions
            or proposal.baseline_result_hash != baseline.result_hash
        ):
            raise DomainError(409, "incompatible_versions", "Scenario baseline has changed")
        self._engine(baseline.analysis_kind)
        if baseline.versions.code_version != self.code_version:
            raise DomainError(
                409, "incompatible_versions", "Scenario requires its baseline engine revision"
            )
        parameters = await repository.get_run_parameters(baseline.analysis_id, scope=scope)
        context = V2JobContext.model_validate(parameters["context"])
        if context.scenario_proposal is not None:
            raise DomainError(
                409, "incompatible_versions", "Scenario requires an analysis baseline"
            )
        context = context.model_copy(update={"scenario_proposal": proposal})
        accepted = await repository.enqueue(
            "scenario", request, context, scoped_key, request_id, scope=scope
        )
        return JobAccepted(job_id=accepted.job_id, status=accepted.status, request_id=request_id)

    async def scenario_result(self, run_id: str, scope: str) -> ScenarioResult:
        result = await self._repository().get_run(run_id, scope=scope)
        if not isinstance(result, ScenarioResult):
            raise DomainError(404, "unknown_id", "Scenario run not found")
        return result

    async def explain(self, analysis_id: str, scope: str, request_id: str) -> Explanation:
        result = await self.analysis(analysis_id, scope)
        if self.assistant is None:
            return template_explanation(result, request_id, "DISABLED")
        access = AccessScope(
            principal_id=scope,
            analysis_ids=frozenset({analysis_id}),
            territory_ids=territory_ids(result.request),
            source_ids=frozenset(e.source_id for e in result.evidence_refs),
            snapshot_ids=frozenset({result.snapshot_id}),
        )
        proposals = await self.proposals(analysis_id, scope)
        return await self.assistant.explain(
            result,
            access_scope=access,
            registered_template_ids={p.template_id for p in proposals},
            request_id=request_id,
        )
