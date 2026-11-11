"""Small durable group records; child jobs keep the existing publication fence."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
from a2_contracts.v2.api import ErrorDetail

from a2_api.core.analysis_groups import GroupAdmission, GroupPlan
from a2_api.core.errors import DomainError
from a2_api.core.jobs import V2JobContext
from a2_api.db.rows import json_text

ACTIVE = ("queued", "running")
TERMINAL = ("succeeded", "failed", "cancelled")


def item_status(item: Any) -> str:
    return "failed" if item["error"] else (item["job_status"] or "pending")


@dataclass
class GroupRecord:
    group_id: str
    scope: str
    plan: GroupPlan
    status: str
    error: ErrorDetail | None
    items: list[Any]


def decoded(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


async def read_group(
    connection: Any, group_id: str, scope: str, *, lock: bool = False
) -> GroupRecord:
    row = await connection.fetchrow(
        "SELECT * FROM analysis_groups WHERE group_id=$1 AND access_scope=$2"
        + (" FOR UPDATE" if lock else ""),
        group_id,
        scope,
    )
    if row is None:
        raise DomainError(404, "unknown_id", "Analysis group not found")
    plan = GroupPlan.model_validate(decoded(row["plan"]))
    if plan.identity != row["plan_hash"]:
        raise DomainError(409, "incompatible_versions", "Analysis group plan hash mismatch")
    items = await connection.fetch(
        "SELECT i.*,j.status AS job_status,j.run_id FROM analysis_group_items i "
        "LEFT JOIN jobs j ON j.job_id=i.job_id AND j.access_scope=$2 "
        "WHERE i.group_id=$1 ORDER BY i.ordinal",
        group_id,
        scope,
    )
    if [i["ordinal"] for i in items] != list(range(len(plan.request.categories))) or any(
        i["job_id"] is not None and i["job_status"] is None for i in items
    ):
        raise DomainError(409, "incompatible_versions", "Analysis group item inventory mismatch")
    status = row["status"]
    if status in ACTIVE:
        states = [item_status(i) for i in items]
        if all(s == "succeeded" for s in states):
            status = "succeeded"
        elif all(s in TERMINAL for s in states):
            status = "failed"
    return GroupRecord(
        group_id,
        scope,
        plan,
        status,
        ErrorDetail.model_validate(decoded(row["error"])) if row["error"] else None,
        list(items),
    )


async def lock_admission(connection: Any, binding: GroupAdmission, context: V2JobContext) -> None:
    group = await read_group(connection, binding.group_id, context.access_scope, lock=True)
    if group.plan.identity != binding.plan_hash or group.status not in ACTIVE:
        raise DomainError(409, "incompatible_versions", "Analysis group is no longer admissible")
    item = group.items[binding.ordinal]
    if (
        item["job_id"] is not None
        or item["error"] is not None
        or item["attempt"] != binding.attempt
        or any(item_status(i) not in TERMINAL for i in group.items[: binding.ordinal])
        or context.versions != group.plan.versions
        or context.snapshot_hash != group.plan.snapshot_hash
        or context.normalized_request != group.plan.child(binding.ordinal)
    ):
        raise DomainError(409, "incompatible_versions", "Analysis group child binding changed")
    if group.plan.valid_until is not None and group.plan.valid_until <= datetime.now(UTC):
        raise DomainError(409, "incompatible_versions", "Analysis group input validity expired")


class AnalysisGroupRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def get(self, group_id: str, scope: str) -> GroupRecord:
        async with (
            self.pool.acquire() as connection,
            connection.transaction(isolation="repeatable_read", readonly=True),
        ):
            return await read_group(connection, group_id, scope)

    async def quarantine(self, group_id: str, scope: str) -> None:
        # Corrupt metadata must not prevent the worker from serving other groups
        # or ordinary jobs. Preserve the corrupt bytes for operator inspection.
        await self.pool.execute(
            "UPDATE analysis_groups SET status='failed' WHERE group_id=$1 AND access_scope=$2 "
            "AND status IN ('queued','running')",
            group_id,
            scope,
        )

    async def replay(self, key: str, digest: str, scope: str) -> str | None:
        row = await self.pool.fetchrow(
            "SELECT group_id,input_hash FROM analysis_groups WHERE idempotency_key=$1 AND access_scope=$2",
            key,
            scope,
        )
        if row is None:
            return None
        if row["input_hash"] != digest:
            raise DomainError(409, "idempotency_conflict", "Group key used for another request")
        return str(row["group_id"])

    async def create(self, plan: GroupPlan, key: str, digest: str, scope: str) -> str:
        async with self.pool.acquire() as connection, connection.transaction():
            # Serialize only inexpensive group creation/capacity accounting.
            await connection.execute("SELECT pg_advisory_xact_lock(41822008)")
            row = await connection.fetchrow(
                "SELECT group_id,input_hash FROM analysis_groups WHERE idempotency_key=$1 AND access_scope=$2",
                key,
                scope,
            )
            if row is not None:
                if row["input_hash"] != digest:
                    raise DomainError(
                        409, "idempotency_conflict", "Group key used for another request"
                    )
                return str(row["group_id"])
            counts = await connection.fetchrow(
                "SELECT count(*) AS total,count(*) FILTER(WHERE access_scope=$1) AS owned "
                "FROM analysis_groups WHERE status IN ('queued','running')",
                scope,
            )
            if counts["total"] >= 32 or counts["owned"] >= 2:
                raise DomainError(
                    429, "budget_exceeded", "Analysis group queue is full", retryable=True
                )
            group_id = str(uuid4())
            await connection.execute(
                "INSERT INTO analysis_groups(group_id,access_scope,idempotency_key,input_hash,plan,plan_hash) "
                "VALUES($1,$2,$3,$4,$5::jsonb,$6)",
                group_id,
                scope,
                key,
                digest,
                json_text(plan.model_dump(mode="json")),
                plan.identity,
            )
            await connection.executemany(
                "INSERT INTO analysis_group_items(group_id,ordinal) VALUES($1,$2)",
                [(group_id, i) for i in range(len(plan.request.categories))],
            )
            return group_id

    async def pending(self) -> list[tuple[str, str]]:
        rows = await self.pool.fetch(
            "SELECT group_id,access_scope FROM analysis_groups WHERE status IN ('queued','running') "
            "ORDER BY created_at,group_id LIMIT 32"
        )
        return [(r["group_id"], r["access_scope"]) for r in rows]

    async def settle(self, group_id: str, scope: str) -> GroupRecord:
        async with self.pool.acquire() as connection, connection.transaction():
            group = await read_group(connection, group_id, scope, lock=True)
            await connection.execute(
                "UPDATE analysis_groups SET status=$2 WHERE group_id=$1", group_id, group.status
            )
            return group

    async def fail(self, binding: GroupAdmission, scope: str, error: ErrorDetail) -> None:
        async with self.pool.acquire() as connection, connection.transaction():
            group = await read_group(connection, binding.group_id, scope, lock=True)
            item = group.items[binding.ordinal]
            if (
                group.status not in ACTIVE
                or item["job_id"] is not None
                or item["attempt"] != binding.attempt
            ):
                return
            if error.code == "incompatible_versions":
                await connection.execute(
                    "UPDATE analysis_groups SET status='failed',error=$2::jsonb WHERE group_id=$1",
                    group.group_id,
                    json_text(error.model_dump(mode="json")),
                )
            else:
                await connection.execute(
                    "UPDATE analysis_group_items SET error=$3::jsonb WHERE group_id=$1 AND ordinal=$2",
                    group.group_id,
                    binding.ordinal,
                    json_text(error.model_dump(mode="json")),
                )
                await connection.execute(
                    "UPDATE analysis_groups SET status='running' WHERE group_id=$1", group.group_id
                )

    async def cancel(self, group_id: str, scope: str) -> None:
        async with self.pool.acquire() as connection, connection.transaction():
            group = await read_group(connection, group_id, scope, lock=True)
            if group.status not in ACTIVE:
                return
            await connection.execute(
                "UPDATE jobs SET status='cancelled',finished_at=now(),error=NULL,lease_expires_at=NULL "
                "WHERE access_scope=$2 AND status IN ('queued','running') AND job_id IN "
                "(SELECT job_id FROM analysis_group_items WHERE group_id=$1)",
                group_id,
                scope,
            )
            await connection.execute(
                "UPDATE analysis_groups SET status='cancelled' WHERE group_id=$1", group_id
            )

    async def retry(self, group_id: str, scope: str, key: str) -> None:
        async with self.pool.acquire() as connection, connection.transaction():
            # Shares capacity accounting with create(), before taking a group row lock.
            await connection.execute("SELECT pg_advisory_xact_lock(41822008)")
            group = await read_group(connection, group_id, scope, lock=True)
            keys = await connection.fetchval(
                "SELECT retry_keys FROM analysis_groups WHERE group_id=$1", group_id
            )
            if key in keys:
                return
            if len(keys) >= 27:
                raise DomainError(409, "invalid_request", "Analysis group retry budget exhausted")
            if group.status not in ("failed", "cancelled"):
                raise DomainError(409, "invalid_request", "Only stopped groups can be retried")
            counts = await connection.fetchrow(
                "SELECT count(*) AS total,count(*) FILTER(WHERE access_scope=$1) AS owned "
                "FROM analysis_groups WHERE status IN ('queued','running') AND group_id<>$2",
                scope,
                group_id,
            )
            if counts["total"] >= 32 or counts["owned"] >= 2:
                raise DomainError(
                    429, "budget_exceeded", "Analysis group queue is full", retryable=True
                )
            retry_items = [
                i
                for i in group.items
                if item_status(i) in ("failed", "cancelled") and i["attempt"] < 3
            ]
            pending = any(item_status(i) == "pending" for i in group.items)
            if not retry_items and not pending:
                raise DomainError(409, "invalid_request", "Analysis group retry budget exhausted")
            for item in retry_items:
                await connection.execute(
                    "UPDATE analysis_group_items SET attempt=attempt+1,error=NULL,previous_job_ids=CASE WHEN job_id IS NULL "
                    "THEN previous_job_ids ELSE array_append(previous_job_ids,job_id) END,job_id=NULL "
                    "WHERE group_id=$1 AND ordinal=$2",
                    group_id,
                    item["ordinal"],
                )
            await connection.execute(
                "UPDATE analysis_groups SET status='queued',error=NULL,retry_keys=array_append(retry_keys,$2) WHERE group_id=$1",
                group_id,
                key,
            )
