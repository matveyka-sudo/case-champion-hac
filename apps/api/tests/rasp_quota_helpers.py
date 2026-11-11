"""Explicit TEST_ONLY wire authorization for existing synthetic HTTP fixtures.

This in-memory fake is not a production quota ledger. Production constructors
must receive the shared PostgreSQL quota implementation.
"""

import time
from datetime import UTC, datetime, timedelta

from a2_api.core.rasp_quota import AttemptPermit


class TestOnlyQuota:
    __test__ = False
    durable = True

    def __init__(self) -> None:
        self.attempt_ids: set[str] = set()

    async def reserve_attempt(
        self,
        *,
        attempt_id: str,
        credential_fingerprint: str,
        request_fingerprint: str,
        max_wire_seconds: float,
    ) -> AttemptPermit | None:
        if attempt_id in self.attempt_ids:
            return None
        self.attempt_ids.add(attempt_id)
        now = datetime.now(UTC)
        return AttemptPermit(
            reservation_id=attempt_id,
            ledger_scope_id="TEST_ONLY-rasp-quota",
            quota_day=now.date(),
            reserved_at=now,
            window_end=now + timedelta(days=1),
            day_reserved_total=len(self.attempt_ids),
            latest_start_monotonic=time.monotonic() + 1,
        )
