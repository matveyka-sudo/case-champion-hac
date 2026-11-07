"""HTTP job orchestration; all accepted-data semantics come from preflight."""

from collections.abc import Awaitable, Callable
from typing import Literal

from a2_contracts.api import AnalysisRequest, Job, JobAccepted
from a2_contracts.profiles import ProfileConfig
from a2_contracts.regions import StudyArea
from a2_contracts.scenarios import ScenarioRequest

from .configuration import CatalogConfig
from .jobs import JobContext, JobKind, JobRequest
from .services import DomainError, Services, unavailable
from .storage import JobRepository, RunResult

Prepare = Callable[[JobKind, JobRequest], Awaitable[JobContext]]


class QueuedServices(Services):
    def __init__(
        self,
        repository: JobRepository,
        *,
        catalog: CatalogConfig | None = None,
        prepare: Prepare | None = None,
        code_version: str | None = None,
    ) -> None:
        self.repository = repository
        self.catalog = catalog
        self.prepare = prepare
        self.code_version = code_version

    async def profiles(self) -> list[ProfileConfig]:
        if self.catalog is None:
            raise unavailable()
        return self.catalog.profiles

    async def study_areas(self) -> list[StudyArea]:
        if self.catalog is None:
            raise unavailable()
        return self.catalog.study_areas

    async def _submit(
        self, kind: JobKind, request: JobRequest, key: str, trace: str
    ) -> JobAccepted:
        replay = await self.repository.replay(kind, request, key)
        if replay is not None:
            return replay
        if self.prepare is None or self.code_version is None:
            raise unavailable()
        context = JobContext.model_validate(await self.prepare(kind, request))
        if context.task_count > 3500:
            raise DomainError(
                422, "invalid_request", "Calculation exceeds the 3500-task demo limit"
            )
        if context.code_version != self.code_version:
            raise DomainError(
                409, "incompatible_versions", "Preflight code differs from deployed code"
            )
        if isinstance(request, AnalysisRequest):
            profiles = {request.profile_id}
            if (
                context.bundle_id != request.bundle_id
                or context.task_hashes.get(request.profile_id) != request.task_set_hash
            ):
                raise DomainError(
                    409, "incompatible_versions", "Preflight bundle/tasks differ from request"
                )
            if context.baseline_run_ids:
                raise DomainError(
                    409, "incompatible_versions", "Analysis cannot use scenario baselines"
                )
            if context.profile_versions.get(request.profile_id) != request.profile_version:
                raise DomainError(
                    409, "incompatible_versions", "Preflight profile revision differs from request"
                )
            if set(context.origin_ids.get(request.profile_id, [])) != set(request.origin_ids):
                raise DomainError(
                    409, "incompatible_versions", "Preflight origins differ from request"
                )
        else:
            profiles = set(request.evaluate_profile_ids)
            if context.baseline_run_ids != request.base_run_ids:
                raise DomainError(
                    409, "incompatible_versions", "Preflight baselines differ from request"
                )
            for profile_id, base_id in request.base_run_ids.items():
                baseline = await self.repository.get_run(base_id)
                from a2_contracts.results import ProfileResult

                if not isinstance(baseline, ProfileResult) or (
                    baseline.profile_id != profile_id
                    or baseline.bundle_id != context.bundle_id
                    or baseline.code_version != context.code_version
                    or baseline.profile_version != context.profile_versions.get(profile_id)
                    or baseline.task_set_hash != context.task_hashes.get(profile_id)
                    or {city.origin_id for city in baseline.cities}
                    != set(context.origin_ids.get(profile_id, []))
                ):
                    raise DomainError(
                        409,
                        "incompatible_versions",
                        "Scenario baseline differs from accepted context",
                    )
                original = await self.repository.get_run_parameters(base_id)
                original_context = JobContext.model_validate(original["context"])
                if (
                    original_context.bundle_hash != context.bundle_hash
                    or original_context.profile_hashes.get(profile_id)
                    != context.profile_hashes.get(profile_id)
                ):
                    raise DomainError(
                        409, "incompatible_versions", "Scenario baseline content versions differ"
                    )
        if (
            set(context.profile_hashes) != profiles
            or set(context.task_hashes) != profiles
            or set(context.profile_versions) != profiles
            or set(context.origin_ids) != profiles
        ):
            raise DomainError(
                409, "incompatible_versions", "Preflight must resolve every requested profile"
            )
        return await self.repository.enqueue(kind, request, context, key, trace)

    async def analyse(
        self, request: AnalysisRequest, idempotency_key: str, trace_id: str
    ) -> JobAccepted:
        return await self._submit("analysis", request, idempotency_key, trace_id)

    async def scenario(
        self, request: ScenarioRequest, idempotency_key: str, trace_id: str
    ) -> JobAccepted:
        return await self._submit("scenario", request, idempotency_key, trace_id)

    async def job(self, job_id: str) -> Job:
        return await self.repository.get_job(job_id)

    async def run(self, run_id: str) -> RunResult:
        return await self.repository.get_run(run_id)

    async def export(self, run_id: str, format: Literal["json", "csv"]) -> tuple[str, bytes]:
        from .run_exports import export_run

        result = await self.repository.get_run(run_id)
        parameters = await self.repository.get_run_parameters(run_id)
        return export_run(result, parameters, format)
