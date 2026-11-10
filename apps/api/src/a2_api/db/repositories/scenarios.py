"""Transactional PostgreSQL jobs and immutable runs; no ORM at runtime."""

from collections.abc import Sequence
from uuid import NAMESPACE_URL, uuid5

import asyncpg  # type: ignore[import-untyped]
from a2_contracts.common import canonical_hash
from a2_contracts.v2.requests import RequestBase
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
    V2JobContext,
)
from a2_api.db.repositories.runs import RunsRepository
from a2_api.db.rows import RunResult, _proposal_from_row


class ScenariosRepository(RunsRepository):
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
        self._validate_scenario_semantics(
            result,
            baseline,
            context.scenario_proposal,
            endpoint_scope=context.scenario_endpoint_scope,
            expected_snapshot_hash=context.snapshot_hash,
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
