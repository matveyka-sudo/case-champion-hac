"""Private one-shot wire authorization shared by every Rasp API method."""

import hashlib
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from a2_contracts.common import canonical_hash

HARD_DAILY_LIMIT = 500


class QuotaUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Rasp shared quota is unconfigured, unknown or unavailable")


def credential_fingerprint(value: str) -> str:
    if not value or any(character.isspace() for character in value):
        raise QuotaUnavailable()
    return hashlib.sha256(b"waydex-rasp-credential/1\0" + value.encode()).hexdigest()


def request_fingerprint(host: str, path: str, params: Mapping[str, str]) -> str:
    if (
        host not in {"api.rasp.yandex-net.ru", "api.rasp.yandex.net"}
        or path not in {"/v3.0/search/", "/v3.0/copyright/"}
        or any(key.lower() in {"apikey", "api_key", "authorization", "key"} for key in params)
    ):
        raise QuotaUnavailable()
    return canonical_hash(
        {
            "schema_version": "waydex-rasp-wire-request/1",
            "method": "GET",
            "host": host,
            "path": path,
            "params": dict(sorted(params.items())),
        }
    )


@dataclass(frozen=True)
class AttemptPermit:
    reservation_id: str
    ledger_scope_id: str
    quota_day: date
    reserved_at: datetime
    window_end: datetime
    day_reserved_total: int
    latest_start_monotonic: float

    def usable(self) -> bool:
        return (
            math.isfinite(self.latest_start_monotonic)
            and time.monotonic() < self.latest_start_monotonic
        )


class WireAttemptQuota(Protocol):
    durable: bool

    async def reserve_attempt(
        self,
        *,
        attempt_id: str,
        credential_fingerprint: str,
        request_fingerprint: str,
        max_wire_seconds: float,
    ) -> AttemptPermit | None: ...
