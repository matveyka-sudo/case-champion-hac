"""Central account/day reservations; initialization never assumes unused quota."""

import math
import re
import time
from datetime import date, datetime, timedelta
from datetime import time as day_time
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import asyncpg  # type: ignore[import-untyped]

from a2_api.core.rasp_quota import HARD_DAILY_LIMIT, AttemptPermit, QuotaUnavailable

_HASH = re.compile(r"^[a-f0-9]{64}$")
_ACCOUNT = re.compile(r"^[a-zA-Z0-9_.:-]{1,128}$")


def _valid_account(account_id: str, quota_timezone: str) -> None:
    if not _ACCOUNT.fullmatch(account_id):
        raise QuotaUnavailable()
    try:
        ZoneInfo(quota_timezone)
    except (ValueError, KeyError):
        raise QuotaUnavailable() from None


async def initialize_day(
    pool: asyncpg.Pool,
    *,
    account_id: str,
    credential_fingerprint: str,
    quota_timezone: str,
    quota_day: date,
    allowance: int,
    already_used: int,
    baseline_ref: str,
    exclusive_usage_attested: bool,
) -> None:
    """Explicit operator action; repeated initialization cannot reset any count."""
    _valid_account(account_id, quota_timezone)
    if (
        not _HASH.fullmatch(credential_fingerprint)
        or type(allowance) is not int
        or not 1 <= allowance <= HARD_DAILY_LIMIT
        or type(already_used) is not int
        or not 0 <= already_used <= HARD_DAILY_LIMIT
        or type(quota_day) is not date
        or not isinstance(baseline_ref, str)
        or not baseline_ref.strip()
        or not 1 <= len(baseline_ref) <= 200
        or exclusive_usage_attested is not True
    ):
        raise QuotaUnavailable()
    mismatch = False
    try:
        async with pool.acquire() as connection, connection.transaction():
            now = await connection.fetchval("SELECT clock_timestamp()")
            zone = ZoneInfo(quota_timezone)
            if quota_day != now.astimezone(zone).date():
                raise QuotaUnavailable()
            await connection.execute(
                "INSERT INTO rasp_quota_accounts(account_id,ledger_scope_id,quota_timezone) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
                account_id,
                str(uuid4()),
                quota_timezone,
            )
            account = await connection.fetchrow(
                "SELECT * FROM rasp_quota_accounts WHERE account_id=$1 FOR UPDATE", account_id
            )
            if account["quota_timezone"] != quota_timezone:
                raise QuotaUnavailable()
            await connection.execute(
                "INSERT INTO rasp_quota_credentials(credential_fingerprint,account_id) VALUES($1,$2) ON CONFLICT DO NOTHING",
                credential_fingerprint,
                account_id,
            )
            owner = await connection.fetchval(
                "SELECT account_id FROM rasp_quota_credentials WHERE credential_fingerprint=$1",
                credential_fingerprint,
            )
            if owner != account_id:
                raise QuotaUnavailable()
            row = await connection.fetchrow(
                "SELECT * FROM rasp_quota_days WHERE account_id=$1 AND quota_day=$2 FOR UPDATE",
                account_id,
                quota_day,
            )
            if row is not None:
                mismatch = (row["allowance"], row["already_used"], row["baseline_ref"]) != (
                    allowance,
                    already_used,
                    baseline_ref,
                )
                if mismatch:
                    # A changed usage baseline is uncertainty, not permission to
                    # subtract prior reservations. Commit the block before error.
                    await connection.execute(
                        "UPDATE rasp_quota_days SET blocked=true WHERE account_id=$1 AND quota_day=$2",
                        account_id,
                        quota_day,
                    )
            else:
                start = datetime.combine(quota_day, day_time.min, tzinfo=zone)
                end = datetime.combine(quota_day + timedelta(days=1), day_time.min, tzinfo=zone)
                await connection.execute(
                    "INSERT INTO rasp_quota_days(account_id,quota_day,window_start,window_end,allowance,already_used,baseline_ref,exclusive_usage_attested) VALUES($1,$2,$3,$4,$5,$6,$7,true)",
                    account_id,
                    quota_day,
                    start,
                    end,
                    allowance,
                    already_used,
                    baseline_ref,
                )
        if mismatch:
            raise QuotaUnavailable()
    except QuotaUnavailable:
        raise
    except Exception:
        raise QuotaUnavailable() from None


class PostgresRaspQuota:
    durable = True

    def __init__(self, pool: asyncpg.Pool, *, account_id: str, quota_timezone: str) -> None:
        _valid_account(account_id, quota_timezone)
        self.pool, self.account_id, self.quota_timezone = pool, account_id, quota_timezone

    async def reserve_attempt(
        self,
        *,
        attempt_id: str,
        credential_fingerprint: str,
        request_fingerprint: str,
        max_wire_seconds: float,
    ) -> AttemptPermit | None:
        started = time.monotonic()
        try:
            if str(UUID(attempt_id)) != attempt_id:
                raise ValueError
            if (
                not _HASH.fullmatch(credential_fingerprint)
                or not _HASH.fullmatch(request_fingerprint)
                or isinstance(max_wire_seconds, bool)
                or not math.isfinite(max_wire_seconds)
                or not 0 < max_wire_seconds <= 60
            ):
                raise ValueError
        except (TypeError, ValueError, AttributeError):
            raise QuotaUnavailable() from None
        try:
            async with self.pool.acquire() as connection, connection.transaction():
                account = await connection.fetchrow(
                    "SELECT * FROM rasp_quota_accounts WHERE account_id=$1 FOR UPDATE",
                    self.account_id,
                )
                if account is None or account["quota_timezone"] != self.quota_timezone:
                    raise QuotaUnavailable()
                owner = await connection.fetchval(
                    "SELECT account_id FROM rasp_quota_credentials WHERE credential_fingerprint=$1",
                    credential_fingerprint,
                )
                if owner != self.account_id:
                    raise QuotaUnavailable()
                now = await connection.fetchval("SELECT clock_timestamp()")
                day = now.astimezone(ZoneInfo(self.quota_timezone)).date()
                row = await connection.fetchrow(
                    "SELECT * FROM rasp_quota_days WHERE account_id=$1 AND quota_day=$2 FOR UPDATE",
                    self.account_id,
                    day,
                )
                if row is None or row["blocked"] or not row["exclusive_usage_attested"]:
                    raise QuotaUnavailable()
                if await connection.fetchval(
                    "SELECT 1 FROM rasp_quota_attempts WHERE reservation_id=$1", attempt_id
                ):
                    return None
                counter = await connection.fetchrow(
                    "UPDATE rasp_quota_days SET reserved_count=reserved_count+1 "
                    "WHERE account_id=$1 AND quota_day=$2 AND NOT blocked "
                    "AND already_used+reserved_count<allowance AND already_used+reserved_count<500 "
                    "AND window_start<=clock_timestamp() "
                    "AND clock_timestamp()+$3::double precision*interval '1 second'<window_end "
                    "RETURNING already_used+reserved_count AS day_reserved_total,clock_timestamp() AS reserved_at,window_end",
                    self.account_id,
                    day,
                    max_wire_seconds + 2,
                )
                if counter is None:
                    return None
                await connection.execute(
                    "INSERT INTO rasp_quota_attempts(reservation_id,account_id,quota_day,credential_fingerprint,request_fingerprint,day_reserved_total,reserved_at) VALUES($1,$2,$3,$4,$5,$6,$7)",
                    attempt_id,
                    self.account_id,
                    day,
                    credential_fingerprint,
                    request_fingerprint,
                    counter["day_reserved_total"],
                    counter["reserved_at"],
                )
                permit = AttemptPermit(
                    attempt_id,
                    account["ledger_scope_id"],
                    day,
                    counter["reserved_at"],
                    counter["window_end"],
                    counter["day_reserved_total"],
                    started + 1,
                )
            return permit
        except QuotaUnavailable:
            raise
        except Exception:
            # Commit acknowledgment can be lost. Never retry this reservation ID
            # as an authorization, and never refund an uncertain transaction.
            raise QuotaUnavailable() from None


async def read_status(pool: asyncpg.Pool, *, account_id: str) -> dict[str, Any]:
    try:
        async with pool.acquire() as connection, connection.transaction(readonly=True):
            account = await connection.fetchrow(
                "SELECT * FROM rasp_quota_accounts WHERE account_id=$1", account_id
            )
            if account is None:
                return {"status": "unconfigured", "hard_daily_limit": HARD_DAILY_LIMIT}
            now = await connection.fetchval("SELECT clock_timestamp()")
            day = now.astimezone(ZoneInfo(account["quota_timezone"])).date()
            row = await connection.fetchrow(
                "SELECT * FROM rasp_quota_days WHERE account_id=$1 AND quota_day=$2",
                account_id,
                day,
            )
            result = dict(
                status="unknown_usage"
                if row is None
                else "blocked"
                if row["blocked"]
                else "configured",
                hard_daily_limit=HARD_DAILY_LIMIT,
                ledger_scope_id=account["ledger_scope_id"],
                quota_timezone=account["quota_timezone"],
                quota_day=day.isoformat(),
            )
            if row is not None:
                result.update(
                    allowance=row["allowance"],
                    already_used=row["already_used"],
                    reserved_count=row["reserved_count"],
                    available=max(
                        0, row["allowance"] - row["already_used"] - row["reserved_count"]
                    ),
                )
            return result
    except Exception:
        raise QuotaUnavailable() from None


async def export_reservations(
    pool: asyncpg.Pool,
    *,
    account_id: str,
    quota_day: date,
    reservation_ids: list[str],
) -> dict[str, Any]:
    if (
        not reservation_ids
        or len(reservation_ids) > 500
        or len(set(reservation_ids)) != len(reservation_ids)
    ):
        raise QuotaUnavailable()
    try:
        async with (
            pool.acquire() as connection,
            connection.transaction(isolation="repeatable_read", readonly=True),
        ):
            account = await connection.fetchrow(
                "SELECT * FROM rasp_quota_accounts WHERE account_id=$1", account_id
            )
            row = await connection.fetchrow(
                "SELECT * FROM rasp_quota_days WHERE account_id=$1 AND quota_day=$2",
                account_id,
                quota_day,
            )
            reservations = await connection.fetch(
                "SELECT reservation_id,request_fingerprint,quota_day,reserved_at,day_reserved_total FROM rasp_quota_attempts WHERE account_id=$1 AND quota_day=$2 AND reservation_id=ANY($3::text[]) ORDER BY day_reserved_total",
                account_id,
                quota_day,
                reservation_ids,
            )
            if account is None or row is None or len(reservations) != len(reservation_ids):
                raise QuotaUnavailable()
            return dict(
                schema_version="waydex-rasp-quota-export/1",
                hard_daily_limit=500,
                ledger_scope_id=account["ledger_scope_id"],
                quota_timezone=account["quota_timezone"],
                quota_day=quota_day.isoformat(),
                allowance=row["allowance"],
                already_used=row["already_used"],
                baseline_ref=row["baseline_ref"],
                exclusive_usage_attested=row["exclusive_usage_attested"],
                blocked=row["blocked"],
                reservations=[
                    dict(
                        reservation_id=r["reservation_id"],
                        request_fingerprint=r["request_fingerprint"],
                        quota_day=r["quota_day"].isoformat(),
                        reserved_at=r["reserved_at"].isoformat(),
                        day_reserved_total=r["day_reserved_total"],
                        reserved_before_send=True,
                    )
                    for r in reservations
                ],
            )
    except QuotaUnavailable:
        raise
    except Exception:
        raise QuotaUnavailable() from None
