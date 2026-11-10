"""Transactional PostgreSQL jobs and immutable runs; no ORM at runtime."""

import json
from typing import cast

import asyncpg  # type: ignore[import-untyped]
from a2_contracts.results import ProfileResult
from a2_contracts.scenarios import Comparison
from a2_contracts.v2.requests import parse_analysis_request
from a2_contracts.v2.results import (
    AnalysisResult,
)
from a2_contracts.v2.scenarios import (
    ScenarioResult,
    ScenarioRunRequest,
)
from pydantic import JsonValue

from a2_api.core.errors import DomainError
from a2_api.core.jobs import JobKind, JobRequest, V2JobContext, cache_fingerprint, request_hash
from a2_api.core.private_sources import PrivateSourceBinding
from a2_api.db.repositories.private_sources import PrivateSourceRepository
from a2_api.db.rows import RunResult, _check_scope, _proposal_from_row, _run_from_row
from a2_api.db.validation import _check_submission, _operation_hash, _validate_scenario_semantics


class RunsRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    _validate_scenario_semantics = staticmethod(_validate_scenario_semantics)

    async def private_source_bindings(
        self, run_id: str, scope: str
    ) -> tuple[PrivateSourceBinding, ...]:
        return await PrivateSourceRepository(self.pool).read_access(run_id, scope)

    async def get_run(self, run_id: str, scope: str | None = None) -> RunResult:
        async with self.pool.acquire() as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                result = await self._read_run(connection, run_id, scope)
        await PrivateSourceRepository(self.pool).read_access(
            run_id, scope, expected_hash=result.result_hash
        )
        return result

    async def get_run_parameters(
        self, run_id: str, scope: str | None = None
    ) -> dict[str, JsonValue]:
        result = await self.get_run(run_id, scope)
        payload = await self.pool.fetchval(
            "SELECT payload FROM jobs WHERE run_id=$1 AND status='succeeded' "
            "AND access_scope IS NOT DISTINCT FROM $2 ORDER BY finished_at LIMIT 1",
            run_id,
            scope,
        )
        if payload is None:
            raise DomainError(404, "unknown_id", "Run input parameters not found")
        await PrivateSourceRepository(self.pool).read_access(
            run_id, scope, expected_hash=result.result_hash
        )
        return cast(dict[str, JsonValue], json.loads(payload))

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
    async def _frozen_job_context(
        connection: asyncpg.Connection,
        run_id: str,
        scope: str | None,
        kind: JobKind,
    ) -> tuple[V2JobContext, JobRequest, str]:
        # Read the persisted trusted command directly inside the same snapshot.
        # Calling get_run_parameters here would recursively validate this run.
        row = await connection.fetchrow(
            "SELECT * FROM jobs WHERE run_id=$1 AND kind=$2 AND status='succeeded' "
            "AND schema_version='2' AND access_scope=$3 ORDER BY finished_at,job_id LIMIT 1",
            run_id,
            kind,
            scope,
        )
        if row is None:
            raise ValueError("Missing frozen scenario calculation context")
        _check_scope(row, scope)
        payload = json.loads(row["payload"])
        context = V2JobContext.model_validate(payload["context"])
        request = (
            ScenarioRunRequest.model_validate(payload["request"])
            if kind == "scenario"
            else parse_analysis_request(payload["request"])
        )
        _check_submission(kind, request, context, scope)
        if (
            row["input_hash"] != request_hash(request)
            or row["cache_key"] != cache_fingerprint(kind, request, context)
            or row["code_version"] != context.code_version
            or row["bundle_id"] != context.snapshot_id
        ):
            raise ValueError("Frozen scenario calculation context hash differs")
        return context, request, str(row["cache_key"])

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
        await PrivateSourceRepository.run_bindings(connection, row, scope)
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
            baseline_bindings = await PrivateSourceRepository.run_bindings(
                connection, baseline_row, scope
            )
            scenario_bindings = await PrivateSourceRepository.run_bindings(connection, row, scope)
            if baseline_bindings != scenario_bindings:
                raise ValueError("Scenario private source binding differs from baseline")
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
            endpoint_scope = None
            expected_snapshot_hash = None
            if result.after_journeys and baseline.analysis_kind == "urban_passenger":
                context, request, cache_key = await cls._frozen_job_context(
                    connection, run_id, scope, "scenario"
                )
                baseline_context, _, _ = await cls._frozen_job_context(
                    connection, baseline.analysis_id, scope, "analysis"
                )
                if (
                    not isinstance(request, ScenarioRunRequest)
                    or request.scenario_id != command.scenario_id
                    or request.baseline_analysis_id != baseline.analysis_id
                    or context.scenario_proposal != command
                    or context.normalized_request != baseline.request
                    or context.analysis_id != baseline.analysis_id
                    or context.versions != baseline.versions
                    or baseline_context.normalized_request != baseline.request
                    or baseline_context.analysis_id != baseline.analysis_id
                    or baseline_context.versions != baseline.versions
                    or baseline_context.snapshot_hash != context.snapshot_hash
                    or result.params_hash != cache_key
                    or result.operations_hash != _operation_hash(command)
                ):
                    raise ValueError("Frozen scenario calculation context differs from baseline")
                endpoint_scope = context.scenario_endpoint_scope
                expected_snapshot_hash = context.snapshot_hash
            cls._validate_scenario_semantics(
                result,
                baseline,
                command,
                endpoint_scope=endpoint_scope,
                expected_snapshot_hash=expected_snapshot_hash,
            )
        except (DomainError, ValueError, KeyError, TypeError):
            raise DomainError(
                409, "stored_result_incompatible", "Stored result requires recalculation"
            ) from None
        return result
