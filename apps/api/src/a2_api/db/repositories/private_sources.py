"""Immutable operator grants, wall-clock permission fences and retention metadata."""

import json
from datetime import datetime
from typing import Any

from a2_contracts.v2.requests import parse_analysis_request
from a2_contracts.v2.scenarios import ScenarioRunRequest

from a2_api.core.errors import DomainError
from a2_api.core.jobs import ClaimedJob, V2JobContext, cache_fingerprint, request_hash
from a2_api.core.private_sources import (
    PrivateSourceBinding,
    PrivateSourceDescriptor,
    PrivateSourceGrant,
    validate_bindings,
)


def unavailable() -> DomainError:
    # Never include source paths, upstream bodies, authorization or session values.
    return DomainError(410, "data_unavailable", "Private source permission is unavailable")


def decoded(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


class PrivateSourceRepository:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def register(self, source: PrivateSourceDescriptor, grant: PrivateSourceGrant) -> None:
        """Trusted operator API only; no HTTP route exposes registration."""
        binding = PrivateSourceBinding(source=source, grant=grant, grant_hash=grant.content_hash)
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                now = await connection.fetchval("SELECT clock_timestamp()")
                try:
                    binding.assert_access(grant.access_scope, now)
                except ValueError:
                    raise unavailable() from None
                await connection.execute(
                    "INSERT INTO private_source_episodes(episode_id,snapshot_id,snapshot_hash,payload,content_hash,observed_at,expires_at) "
                    "VALUES($1,$2,$3,$4::jsonb,$5,$6,$7) ON CONFLICT(episode_id) DO NOTHING",
                    source.episode_id,
                    source.snapshot_id,
                    source.snapshot_hash,
                    source.model_dump_json(),
                    source.content_hash,
                    source.policy.observed_at,
                    source.policy.expires_at,
                )
                await connection.execute(
                    "INSERT INTO private_source_grants(grant_id,episode_id,access_scope,payload,content_hash,valid_from,valid_until) "
                    "VALUES($1,$2,$3,$4::jsonb,$5,$6,$7) ON CONFLICT(grant_id) DO NOTHING",
                    grant.grant_id,
                    grant.episode_id,
                    grant.access_scope,
                    grant.model_dump_json(),
                    grant.content_hash,
                    grant.valid_from,
                    grant.valid_until,
                )
                await self.check(connection, (binding,), grant.access_scope, lock=True)

    @staticmethod
    async def check(
        connection: Any,
        bindings: tuple[PrivateSourceBinding, ...],
        scope: str,
        *,
        lock: bool = False,
    ) -> datetime | None:
        """Validate the bounded roster, then check every permission at one fresh clock."""
        if not bindings:
            return None
        bindings = validate_bindings(bindings)
        receipts = json.dumps(
            [
                dict(
                    episode_id=b.source.episode_id,
                    grant_id=b.grant.grant_id,
                    episode_hash=b.source.content_hash,
                    grant_hash=b.grant_hash,
                )
                for b in bindings
            ]
        )
        rows = await connection.fetch(
            "SELECT b.episode_id AS binding_episode_id,e.payload AS episode_payload,e.content_hash AS episode_hash,e.snapshot_id,e.snapshot_hash,"
            "e.observed_at,e.expires_at,e.state,g.payload AS grant_payload,g.content_hash AS grant_hash,"
            "g.access_scope,g.valid_from,g.valid_until,g.revoked_at "
            "FROM jsonb_to_recordset($1::jsonb) AS b(episode_id text,grant_id text,episode_hash text,grant_hash text) "
            "JOIN private_source_episodes e ON e.episode_id=b.episode_id "
            "JOIN private_source_grants g ON g.episode_id=e.episode_id AND g.grant_id=b.grant_id "
            "WHERE g.access_scope=$2 ORDER BY e.episode_id" + (" FOR SHARE OF e,g" if lock else ""),
            receipts,
            scope,
        )
        if len(rows) != len(bindings):
            raise unavailable()
        inventory = {row["binding_episode_id"]: row for row in rows}
        for binding in sorted(bindings, key=lambda item: item.source.episode_id):
            row = inventory.get(binding.source.episode_id)
            if row is None:
                raise unavailable()
            try:
                source = PrivateSourceDescriptor.model_validate(decoded(row["episode_payload"]))
                grant = PrivateSourceGrant.model_validate(decoded(row["grant_payload"]))
                if (
                    source != binding.source
                    or grant != binding.grant
                    or row["episode_hash"] != source.content_hash
                    or row["grant_hash"] != grant.content_hash
                    or row["grant_hash"] != binding.grant_hash
                    or row["snapshot_id"] != source.snapshot_id
                    or row["snapshot_hash"] != source.snapshot_hash
                    or row["observed_at"] != source.policy.observed_at
                    or row["expires_at"] != source.policy.expires_at
                    or row["access_scope"] != grant.access_scope
                    or row["valid_from"] != grant.valid_from
                    or row["valid_until"] != grant.valid_until
                    or row["state"] != "active"
                    or row["revoked_at"] is not None
                ):
                    raise ValueError("PRIVATE_SOURCE_RECORD_DIFFERS")
            except (ValueError, TypeError, KeyError):
                raise unavailable() from None
        current = await connection.fetchrow(
            "WITH permission_clock AS MATERIALIZED (SELECT clock_timestamp() AS checked_at) "
            "SELECT checked_at FROM permission_clock WHERE NOT EXISTS("
            "SELECT 1 FROM jsonb_to_recordset($1::jsonb) AS b(episode_id text,grant_id text,episode_hash text,grant_hash text) "
            "LEFT JOIN private_source_episodes e ON e.episode_id=b.episode_id "
            "LEFT JOIN private_source_grants g ON g.grant_id=b.grant_id AND g.episode_id=e.episode_id "
            "WHERE e.episode_id IS NULL OR g.grant_id IS NULL OR e.state<>'active' OR g.revoked_at IS NOT NULL "
            "OR e.content_hash<>b.episode_hash OR g.content_hash<>b.grant_hash OR g.access_scope<>$2 "
            "OR e.observed_at>permission_clock.checked_at OR e.expires_at<=permission_clock.checked_at "
            "OR g.valid_from>permission_clock.checked_at OR g.valid_until<=permission_clock.checked_at)",
            receipts,
            scope,
        )
        if current is None:
            raise unavailable()
        checked_at: datetime = current["checked_at"]
        try:
            for binding in bindings:
                binding.assert_access(scope, checked_at)
        except ValueError:
            raise unavailable() from None
        return checked_at

    @staticmethod
    async def run_bindings(
        connection: Any, row: Any, scope: str | None
    ) -> tuple[PrivateSourceBinding, ...]:
        """Bind private side metadata to the original server job, never output hints."""
        stored = row["private_source_bindings"]
        payload = decoded(row["payload"])
        snapshot_id = payload.get("snapshot_id") or payload.get("versions", {}).get("snapshot_id")
        if stored is None:
            known = await connection.fetchval(
                "SELECT EXISTS(SELECT 1 FROM private_source_episodes WHERE snapshot_id=$1) OR EXISTS("
                "SELECT 1 FROM jobs WHERE access_scope IS NOT DISTINCT FROM $3 AND "
                "(run_id=$2 OR (kind='analysis' AND status='running' AND payload->'context'->>'analysis_id'=$2)) "
                "AND payload->'context' ? 'private_source_bindings')",
                snapshot_id,
                row["run_id"],
                scope,
            )
            if known:
                raise unavailable()
            return ()
        job = await connection.fetchrow(
            "SELECT payload,input_hash,cache_key,code_version,bundle_id,kind FROM jobs WHERE access_scope=$2 "
            "AND (run_id=$1 OR (kind='analysis' AND status='running' AND payload->'context'->>'analysis_id'=$1)) "
            "ORDER BY CASE WHEN status='succeeded' THEN 0 ELSE 1 END,finished_at,job_id LIMIT 1",
            row["run_id"],
            scope,
        )
        frozen = decoded(job["payload"]) if job is not None else {}
        try:
            bindings = validate_bindings(decoded(stored))
            if job is None or scope is None:
                raise ValueError("PRIVATE_FROZEN_CONTEXT_MISSING")
            context = V2JobContext.model_validate(frozen["context"])
            request = (
                ScenarioRunRequest.model_validate(frozen["request"])
                if job["kind"] == "scenario"
                else parse_analysis_request(frozen["request"])
            )
            if (
                context.private_source_bindings != bindings
                or context.access_scope != scope
                or job["input_hash"] != request_hash(request)
                or job["cache_key"] != cache_fingerprint(job["kind"], request, context)
                or job["code_version"] != context.code_version
                or job["bundle_id"] != context.snapshot_id
                or snapshot_id != context.snapshot_id
                or (job["kind"] == "scenario" and payload.get("params_hash") != job["cache_key"])
                or (
                    job["kind"] == "analysis"
                    and payload.get("request") != context.normalized_request.model_dump(mode="json")
                )
            ):
                raise ValueError("PRIVATE_FROZEN_CONTEXT_DIFFERS")
            return bindings
        except (ValueError, TypeError, KeyError):
            raise unavailable() from None

    async def read_access(
        self, run_id: str, scope: str | None, *, expected_hash: str | None = None
    ) -> tuple[PrivateSourceBinding, ...]:
        """A new transaction AFTER immutable RR validation observes current grants."""
        async with self.pool.acquire() as connection:
            async with connection.transaction(isolation="read_committed", readonly=True):
                row = await connection.fetchrow(
                    "SELECT * FROM runs WHERE run_id=$1 AND access_scope IS NOT DISTINCT FROM $2",
                    run_id,
                    scope,
                )
                if row is None:
                    raise DomainError(404, "unknown_id", "Run not found")
                if expected_hash is not None and row["result_hash"] != expected_hash:
                    raise unavailable()
                bindings = await self.run_bindings(connection, row, scope)
                if bindings:
                    assert scope is not None
                    await self.check(connection, bindings, scope)
                return bindings

    async def remaining(self, context: Any) -> float | None:
        if not isinstance(context, V2JobContext) or not context.private_source_bindings:
            return None
        async with self.pool.acquire() as connection:
            now = await self.check(
                connection, context.private_source_bindings, context.access_scope
            )
        assert now is not None
        return min(
            (binding.valid_until - now).total_seconds()
            for binding in context.private_source_bindings
        )

    @staticmethod
    async def publish_fence(connection: Any, claim: ClaimedJob, run_id: str) -> None:
        context = claim.context
        assert isinstance(context, V2JobContext) and context.private_source_bindings
        await PrivateSourceRepository.check(
            connection, context.private_source_bindings, context.access_scope, lock=True
        )
        receipts = json.dumps(
            [
                dict(
                    episode_id=b.source.episode_id,
                    grant_id=b.grant.grant_id,
                    episode_hash=b.source.content_hash,
                    grant_hash=b.grant_hash,
                )
                for b in context.private_source_bindings
            ]
        )
        written = await connection.fetchval(
            "UPDATE jobs SET status='succeeded',run_id=$2,finished_at=clock_timestamp() WHERE job_id=$1 "
            "AND status='running' AND claim_token=$3 AND access_scope=$4 AND lease_expires_at>clock_timestamp() "
            "AND NOT EXISTS(SELECT 1 FROM jsonb_to_recordset($5::jsonb) AS b(episode_id text,grant_id text,episode_hash text,grant_hash text) "
            "LEFT JOIN private_source_episodes e ON e.episode_id=b.episode_id "
            "LEFT JOIN private_source_grants g ON g.grant_id=b.grant_id AND g.episode_id=e.episode_id "
            "WHERE e.episode_id IS NULL OR g.grant_id IS NULL OR e.state<>'active' OR g.revoked_at IS NOT NULL "
            "OR e.content_hash<>b.episode_hash OR g.content_hash<>b.grant_hash OR g.access_scope<>$4 "
            "OR e.observed_at>clock_timestamp() OR e.expires_at<=clock_timestamp() "
            "OR g.valid_from>clock_timestamp() OR g.valid_until<=clock_timestamp()) RETURNING job_id",
            claim.job.job_id,
            run_id,
            claim.claim_token,
            context.access_scope,
            receipts,
        )
        if written is None:
            raise unavailable()
