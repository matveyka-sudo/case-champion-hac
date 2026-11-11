"""One user request, serial existing worker jobs, separately persisted results."""

import logging
from datetime import UTC, datetime
from typing import cast

from a2_contracts.api import FieldError
from a2_contracts.common import canonical_hash
from a2_contracts.v2.api import AnalysisGroupItem, AnalysisGroupView, ErrorDetail
from a2_contracts.v2.base import VersionRefs
from a2_contracts.v2.requests import UrbanPassengerRequest
from pydantic import ValidationError

from a2_api.core.analysis_groups import GroupAdmission, GroupPlan
from a2_api.core.errors import DomainError
from a2_api.db.repositories.analysis_groups import (
    ACTIVE,
    TERMINAL,
    AnalysisGroupRepository,
    GroupRecord,
    decoded,
    item_status,
)
from a2_api.services.analyses import AnalysisService


class AnalysisGroupService:
    def __init__(self, analyses: AnalysisService) -> None:
        self.analyses = analyses
        self.repository = AnalysisGroupRepository(analyses._repository().pool)

    def plan(self, request: UrbanPassengerRequest) -> GroupPlan:
        self.analyses._engine(request.analysis_kind)
        registry = self.analyses.registry
        normalized = cast(UrbanPassengerRequest, registry.normalize(request))
        snapshot = registry.resolve_snapshot(normalized)
        normalized = normalized.model_copy(update={"snapshot_id": snapshot.snapshot_id})
        assert self.analyses.code_version is not None
        return GroupPlan(
            request=normalized,
            snapshot_hash=canonical_hash(snapshot.model_dump(mode="json")),
            versions=VersionRefs(
                snapshot_id=snapshot.snapshot_id,
                graph_version=snapshot.provenance.graph_version,
                calendar_version=snapshot.provenance.calendar_version,
                population_version=snapshot.provenance.population_version,
                methodology_version=registry.methodology.version,
                config_hash=registry.config_hash,
                code_version=self.analyses.code_version,
            ),
            valid_until=registry.required_cache_valid_until(normalized, snapshot),
        )

    def validate_plan(self, plan: GroupPlan) -> None:
        current = self.plan(plan.request)
        if current.identity != plan.identity:
            raise DomainError(
                409, "incompatible_versions", "Group inputs or deployment version changed"
            )
        if plan.valid_until is not None and plan.valid_until <= datetime.now(UTC):
            raise DomainError(409, "incompatible_versions", "Group input validity expired")

    def _validate_children(self, plan: GroupPlan) -> None:
        for ordinal, category in enumerate(plan.request.categories):
            try:
                self.analyses.registry.validate_group_child(plan.child(ordinal))
            except DomainError as exc:
                if exc.status_code != 422:
                    raise
                raise DomainError(
                    422,
                    "invalid_request",
                    "ANALYSIS_GROUP_CATEGORY_REQUEST_UNSUPPORTED",
                    field_errors=[
                        FieldError(
                            location=["categories", str(ordinal)],
                            code="UNSUPPORTED_GROUP_CATEGORY",
                            message=f"{category} has no supported individual analysis request. "
                            "To run a published preset together, use /api/v1/analyses.",
                        )
                    ],
                ) from exc

    @staticmethod
    def view(group: GroupRecord, request_id: str) -> AnalysisGroupView:
        # This is bounded queue metadata. Result bytes are read and validated via
        # the ordinary scoped /analyses/{id}, not nine large payloads on every poll.
        return AnalysisGroupView.model_validate(
            dict(
                group_id=group.group_id,
                request=group.plan.request,
                versions=group.plan.versions,
                status=group.status,
                error=group.error,
                request_id=request_id,
                items=[
                    AnalysisGroupItem.model_validate(
                        dict(
                            category=group.plan.request.categories[item["ordinal"]],
                            attempt=item["attempt"],
                            status=item_status(item),
                            error=decoded(item["error"]) if item["error"] else None,
                            job_id=item["job_id"],
                            analysis_id=item["run_id"]
                            if item["job_status"] == "succeeded"
                            else None,
                        )
                    )
                    for item in group.items
                ],
            )
        )

    async def get(self, group_id: str, scope: str, request_id: str) -> AnalysisGroupView:
        return self.view(await self.repository.get(group_id, scope), request_id)

    async def create(
        self, request: UrbanPassengerRequest, key: str, scope: str, request_id: str
    ) -> AnalysisGroupView:
        scoped_key = self.analyses._key(key, scope)
        digest = canonical_hash(request.model_dump(mode="json"))
        existing = await self.repository.replay(scoped_key, digest, scope)
        if existing is None:
            plan = self.plan(request)
            self.validate_plan(plan)
            self._validate_children(plan)
            existing = await self.repository.create(plan, scoped_key, digest, scope)
        return await self.get(existing, scope, request_id)

    async def cancel(self, group_id: str, scope: str, request_id: str) -> AnalysisGroupView:
        await self.repository.cancel(group_id, scope)
        return await self.get(group_id, scope, request_id)

    async def retry(
        self, group_id: str, key: str, scope: str, request_id: str
    ) -> AnalysisGroupView:
        group = await self.repository.get(group_id, scope)
        self.validate_plan(group.plan)
        self._validate_children(group.plan)
        await self.repository.retry(group_id, scope, self.analyses._key(key, scope))
        return await self.get(group_id, scope, request_id)

    async def dispatch_once(self) -> bool:
        """Called under the ordinary worker lock; admit at most one child per tick."""
        for group_id, scope in await self.repository.pending():
            try:
                group = await self.repository.settle(group_id, scope)
            except (DomainError, ValidationError):
                await self.repository.quarantine(group_id, scope)
                logging.getLogger(__name__).error("invalid_analysis_group group_id=%s", group_id)
                continue
            if group.status not in ACTIVE:
                continue
            item = next(i for i in group.items if item_status(i) not in TERMINAL)
            if item["job_id"] is not None:
                continue
            binding = GroupAdmission(
                group_id, item["ordinal"], item["attempt"], group.plan.identity
            )
            try:
                self.validate_plan(group.plan)
                await self.analyses.analyse(
                    group.plan.child(binding.ordinal), binding.key, group_id, scope, group=binding
                )
            except Exception as exc:
                error = (
                    exc
                    if isinstance(exc, DomainError)
                    else DomainError(
                        503, "engine_unavailable", "Category admission failed", retryable=True
                    )
                )
                await self.repository.fail(
                    binding,
                    scope,
                    ErrorDetail(
                        code=error.code,
                        message=error.message,
                        field_errors=error.field_errors,
                        retryable=error.retryable,
                        request_id=group_id,
                    ),
                )
            return True
        return False
