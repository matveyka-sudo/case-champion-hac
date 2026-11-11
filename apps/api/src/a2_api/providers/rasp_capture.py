"""Private source capture before provider DTO trimming; contains no request headers."""

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PageCapture:
    endpoint: str
    params: tuple[tuple[str, str], ...]
    raw_json: bytes
    fetched_at: datetime
    reservation_id: str
    request_fingerprint: str

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.raw_json).hexdigest()


CaptureCallback = Callable[[PageCapture], Awaitable[None]]
