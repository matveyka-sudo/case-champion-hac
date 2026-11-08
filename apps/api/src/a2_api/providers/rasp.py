"""Optional Yandex Rasp direct-service reader, not an accepted routing snapshot.

Docs checked 2026-11-08:
https://yandex.ru/dev/rasp/doc/ru/reference/schedule-point-point
https://yandex.ru/dev/rasp/doc/ru/concepts/access

The search API accepts a date, not an exact departure instant. We enumerate
intersecting origin-local dates, then filter [departure_at, departure_at + 7d).
Direct scheduled rows only: no mixed-chain search, interval-service expansion,
codeshare identity resolution, freight or network-completeness assertion.
Multiple requested types use documented all-types search plus local filtering;
the search documentation does not promise comma-separated transport filters.
An empty response never proves no route. Cached responses are expiring provider
observations, not accepted immutable bundles. Credentials and quota are supplied
explicitly by the operator; quotas are per instance, not shared across workers.
Rate/TTL defaults are conservative engineering settings, not tariff claims.
The caller owns AsyncClient lifetime and must not install request-logging hooks
that disclose Authorization headers. No URLs from response data are followed.
"""

import asyncio
import hashlib
import json
import math
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

Host = Literal["api.rasp.yandex-net.ru", "api.rasp.yandex.net"]
Transport = Literal["plane", "train", "suburban", "bus", "water", "helicopter"]
Code = Annotated[str, Field(pattern=r"^[sc][0-9]+$")]
ErrorCode = Literal[
    "disabled",
    "invalid_request",
    "quota_exceeded",
    "timeout",
    "rate_limited",
    "upstream_error",
    "invalid_response",
    "redirect_rejected",
]


class RaspError(Exception):
    """Safe domain failure. Never carries provider text, URLs, headers or keys."""

    def __init__(self, code: ErrorCode, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(f"Yandex Rasp provider: {code}")


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class RaspQuery(_DTO):
    from_code: Code
    to_code: Code
    departure_at: AwareDatetime
    origin_timezone: str
    transport_types: tuple[Transport, ...] = Field(min_length=1)

    @field_validator("origin_timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if self.from_code == self.to_code or len(set(self.transport_types)) != len(
            self.transport_types
        ):
            raise ValueError("Distinct places and unique transport types are required")
        return self


class RaspStation(_DTO):
    code: Code
    title: str


class RaspDeparture(_DTO):
    service_uid: Annotated[str, Field(min_length=1)]
    transport_type: Transport
    from_station: RaspStation
    to_station: RaspStation
    departure_at: AwareDatetime
    arrival_at: AwareDatetime
    duration_seconds: Annotated[float, Field(ge=0)]

    @model_validator(mode="after")
    def elapsed_duration(self) -> Self:
        elapsed = (
            self.arrival_at.astimezone(UTC) - self.departure_at.astimezone(UTC)
        ).total_seconds()
        if elapsed != self.duration_seconds:
            raise ValueError("Provider times and duration disagree")
        return self


class RaspIssue(_DTO):
    code: ErrorCode
    retryable: bool


class RaspDepartures(_DTO):
    from_code: Code
    to_code: Code
    requested_departure_at: AwareDatetime
    window_end: AwareDatetime
    origin_timezone: str
    transport_types: tuple[Transport, ...]
    departures: list[RaspDeparture]
    coverage: Literal["partial"] = "partial"
    pagination_complete: bool
    reason_codes: list[str]
    errors: list[RaspIssue]
    fetched_at: AwareDatetime
    expires_at: AwareDatetime
    cache_key: str
    content_hash: str
    source_url: str
    queried_dates: list[date]
    completed_dates: list[date]


class _WireModel(BaseModel):
    # Upstream adds unrelated fields (prices, logos, carrier contacts). They are
    # neither persisted nor fetched, and are not part of our public own DTOs.
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class _Pagination(_WireModel):
    total: Annotated[int, Field(strict=True, ge=0)]
    offset: Annotated[int, Field(strict=True, ge=0)]
    limit: Annotated[int, Field(strict=True, ge=1, le=100)]


class _SearchPlace(_WireModel):
    code: Code


class _Search(_WireModel):
    date: date
    origin: _SearchPlace = Field(alias="from")
    destination: _SearchPlace = Field(alias="to")


class _Page(_WireModel):
    search: _Search
    pagination: _Pagination
    segments: list[dict[str, JsonValue]]
    interval_segments: list[dict[str, JsonValue]] = Field(default_factory=list)


class _Station(_WireModel):
    code: Code
    title: str


class _Thread(_WireModel):
    uid: Annotated[str, Field(min_length=1)]
    transport_type: Transport


class _Segment(_WireModel):
    departure: AwareDatetime
    arrival: AwareDatetime
    duration: Annotated[float, Field(ge=0)]
    origin: _Station = Field(alias="from")
    destination: _Station = Field(alias="to")
    thread: _Thread
    has_transfers: bool = Field(strict=True)


def _hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


class RaspProvider:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        api_key: str | SecretStr | None,
        host: Host = "api.rasp.yandex-net.ru",
        timeout_seconds: float = 10,
        operation_timeout_seconds: float = 60,
        requests_per_second: float = 1,
        max_concurrency: int = 1,
        request_quota: int = 100,
        max_pages: int = 32,
        page_size: int = 100,
        cache_ttl_seconds: float = 300,
        max_cache_entries: int = 128,
        max_response_bytes: int = 2 * 1024 * 1024,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if host not in ("api.rasp.yandex-net.ru", "api.rasp.yandex.net"):
            raise ValueError("Unsupported provider host")
        for value, upper in (
            (timeout_seconds, 60),
            (operation_timeout_seconds, 120),
            (requests_per_second, 1000),
            (cache_ttl_seconds, 86400),
        ):
            if not math.isfinite(value) or not 0 < value <= upper:
                raise ValueError("Provider limits must be finite, positive and bounded")
        for value, upper in (
            (max_concurrency, 16),
            (request_quota, 100000),
            (max_pages, 1000),
            (page_size, 100),
            (max_cache_entries, 10000),
            (max_response_bytes, 16 * 1024 * 1024),
        ):
            if type(value) is not int or not 1 <= value <= upper:
                raise ValueError("Provider count limits must be bounded positive integers")
        key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        if key and any(character.isspace() for character in key):
            raise ValueError("Invalid provider credential")
        self._key = SecretStr(key) if key else None
        self._client, self._host = client, host
        self._url = f"https://{host}/v3.0/search/"
        self._timeout, self._operation_timeout = timeout_seconds, operation_timeout_seconds
        self._interval, self._quota = 1 / requests_per_second, request_quota
        self._page_limit, self._page_size = max_pages, page_size
        self._max_response_bytes = max_response_bytes
        self._ttl, self._cache_size = cache_ttl_seconds, max_cache_entries
        self._clock, self._sleep = clock, sleep
        self._next_request = 0.0
        self._requests_used = 0
        self._rate_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._cache: OrderedDict[str, tuple[float, RaspDepartures]] = OrderedDict()

    async def intercity_departures(
        self,
        from_code: str,
        to_code: str,
        departure_at: datetime,
        *,
        origin_timezone: str,
        transport_types: tuple[Transport, ...],
    ) -> RaspDepartures:
        if self._key is None:
            raise RaspError("disabled")
        try:
            query = RaspQuery(
                from_code=from_code,
                to_code=to_code,
                departure_at=departure_at,
                origin_timezone=origin_timezone,
                transport_types=transport_types,
            )
        except (ValidationError, ValueError, ZoneInfoNotFoundError):
            raise RaspError("invalid_request") from None
        start = query.departure_at.astimezone(UTC)
        end = start + timedelta(days=7)
        scope = query.model_dump(mode="json") | {
            "departure_at": start.isoformat(),
            "transport_types": sorted(query.transport_types),
            "endpoint": self._url,
            "schema": "rasp-direct-v1",
            "window_end": end.isoformat(),
        }
        cache_key = _hash(scope)
        cached = self._cache.get(cache_key)
        if cached is not None and cached[0] > self._clock():
            self._cache.move_to_end(cache_key)
            return cached[1].model_copy(deep=True)
        reasons = {
            "PROVIDER_COVERAGE_NOT_COMPLETE",
            "DIRECT_SERVICES_ONLY",
            "CODESHARE_IDENTITY_NOT_VERIFIED",
        }
        errors: list[RaspIssue] = []
        rows: dict[tuple[str, str, str, datetime], RaspDeparture] = {}
        queried: list[date] = []
        completed: list[date] = []
        zone = ZoneInfo(query.origin_timezone)
        current = start.astimezone(zone).date()
        last = (end - timedelta(microseconds=1)).astimezone(zone).date()
        pages = 0
        try:
            async with asyncio.timeout(self._operation_timeout):
                while current <= last:
                    offset = 0
                    while True:
                        if pages >= self._page_limit:
                            reasons.add("PAGE_LIMIT")
                            break
                        params = {
                            "from": query.from_code,
                            "to": query.to_code,
                            "format": "json",
                            "lang": "ru_RU",
                            "system": "yandex",
                            "date": current.isoformat(),
                            "result_timezone": "UTC",
                            "transfers": "false",
                            "offset": str(offset),
                            "limit": str(self._page_size),
                        }
                        if len(query.transport_types) == 1:
                            params["transport_types"] = query.transport_types[0]
                        page = await self._request(params)
                        if (
                            page.search.origin.code != query.from_code
                            or page.search.destination.code != query.to_code
                            or page.search.date != current
                        ):
                            raise RaspError("invalid_response")
                        pages += 1
                        if current not in queried:
                            queried.append(current)
                        pagination = page.pagination
                        if (
                            pagination.offset != offset
                            or len(page.segments) > pagination.limit
                            or (offset + len(page.segments) > pagination.total)
                            or (not page.segments and offset < pagination.total)
                        ):
                            raise RaspError("invalid_response")
                        if page.interval_segments:
                            reasons.add("INTERVAL_SERVICES_UNRESOLVED")
                        for raw in page.segments:
                            if raw.get("has_transfers") is True:
                                reasons.add("TRANSFER_SERVICES_UNRESOLVED")
                                continue
                            try:
                                row = _Segment.model_validate(raw)
                                departure = RaspDeparture(
                                    service_uid=row.thread.uid,
                                    transport_type=row.thread.transport_type,
                                    from_station=RaspStation(
                                        code=row.origin.code, title=row.origin.title
                                    ),
                                    to_station=RaspStation(
                                        code=row.destination.code, title=row.destination.title
                                    ),
                                    departure_at=row.departure,
                                    arrival_at=row.arrival,
                                    duration_seconds=row.duration,
                                )
                            except ValidationError:
                                raise RaspError("invalid_response") from None
                            if (
                                query.from_code.startswith("s")
                                and row.origin.code != query.from_code
                            ) or (
                                query.to_code.startswith("s")
                                and row.destination.code != query.to_code
                            ):
                                raise RaspError("invalid_response")
                            if departure.transport_type not in query.transport_types:
                                continue
                            if start <= departure.departure_at < end:
                                identity = (
                                    departure.service_uid,
                                    departure.from_station.code,
                                    departure.to_station.code,
                                    departure.departure_at,
                                )
                                if identity in rows and rows[identity] != departure:
                                    raise RaspError("invalid_response")
                                rows[identity] = departure
                        offset += len(page.segments)
                        if offset >= pagination.total:
                            completed.append(current)
                            break
                    if pages >= self._page_limit and current not in completed:
                        break
                    current += timedelta(days=1)
        except TimeoutError:
            if not pages:
                raise RaspError("timeout", retryable=True) from None
            errors.append(RaspIssue(code="timeout", retryable=True))
        except RaspError as exc:
            # A malformed first page cannot become a successful empty result.
            if exc.code == "invalid_response" or (not rows and not completed):
                raise
            errors.append(RaspIssue(code=exc.code, retryable=exc.retryable))
        if errors:
            reasons.add("PROVIDER_REQUEST_INTERRUPTED")
        fetched_at = datetime.now(UTC)
        departures = sorted(rows.values(), key=lambda row: (row.departure_at, row.service_uid))
        pagination_complete = current > last and not errors
        content_hash = _hash(
            {
                "scope": scope,
                "departures": [row.model_dump(mode="json") for row in departures],
                "pagination_complete": pagination_complete,
                "reason_codes": sorted(reasons),
                "errors": [error.model_dump(mode="json") for error in errors],
                "queried_dates": [day.isoformat() for day in queried],
                "completed_dates": [day.isoformat() for day in completed],
            }
        )
        result = RaspDepartures(
            from_code=query.from_code,
            to_code=query.to_code,
            requested_departure_at=query.departure_at,
            window_end=end,
            origin_timezone=query.origin_timezone,
            transport_types=tuple(sorted(query.transport_types)),
            departures=departures,
            pagination_complete=pagination_complete,
            reason_codes=sorted(reasons),
            errors=errors,
            fetched_at=fetched_at,
            expires_at=fetched_at + timedelta(seconds=self._ttl),
            cache_key=cache_key,
            content_hash=content_hash,
            source_url=self._url,
            queried_dates=queried,
            completed_dates=completed,
        )
        if not errors:
            self._cache[cache_key] = (self._clock() + self._ttl, result.model_copy(deep=True))
            self._cache.move_to_end(cache_key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return result

    async def _admit(self) -> None:
        async with self._rate_lock:
            if self._requests_used >= self._quota:
                raise RaspError("quota_exceeded")
            wait = self._next_request - self._clock()
            if wait > 0:
                await self._sleep(wait)
            self._requests_used += 1
            self._next_request = self._clock() + self._interval

    async def _request(self, params: dict[str, str]) -> _Page:
        assert self._key is not None
        for attempt in range(3):
            async with self._semaphore:
                await self._admit()
                request = self._client.build_request(
                    "GET",
                    self._url,
                    params=params,
                    headers={"Authorization": self._key.get_secret_value()},
                    timeout=self._timeout,
                )
                if (
                    request.url.scheme != "https"
                    or request.url.host != self._host
                    or (request.url.path != "/v3.0/search/" or dict(request.url.params) != params)
                ):
                    raise RaspError("invalid_request")
                try:
                    async with asyncio.timeout(self._timeout):
                        response = await self._client.send(
                            request, follow_redirects=False, auth=None, stream=True
                        )
                        try:
                            body = bytearray()
                            async for chunk in response.aiter_bytes(chunk_size=65536):
                                if len(body) + len(chunk) > self._max_response_bytes:
                                    raise RaspError("invalid_response")
                                body.extend(chunk)
                        finally:
                            await response.aclose()
                except (TimeoutError, httpx.TimeoutException):
                    raise RaspError("timeout", retryable=True) from None
                except httpx.HTTPError:
                    raise RaspError("upstream_error", retryable=True) from None
            status = response.status_code
            if 300 <= status < 400:
                raise RaspError("redirect_rejected")
            if status == 429 or 500 <= status <= 599:
                delay = self._retry_after(response.headers.get("Retry-After"), attempt)
                async with self._rate_lock:
                    self._next_request = max(self._next_request, self._clock() + delay)
                code: ErrorCode = "rate_limited" if status == 429 else "upstream_error"
                if attempt == 2 or delay > 10:
                    raise RaspError(code, retryable=True)
                await self._sleep(delay)
                continue
            if status != 200:
                raise RaspError("upstream_error")
            try:
                return _Page.model_validate(json.loads(body))
            except (ValueError, ValidationError):
                raise RaspError("invalid_response") from None
        raise AssertionError("Unreachable retry state")

    @staticmethod
    def _retry_after(value: str | None, attempt: int) -> float:
        if value is not None:
            try:
                seconds = float(value)
            except ValueError:
                try:
                    timestamp = parsedate_to_datetime(value)
                    seconds = (timestamp.astimezone(UTC) - datetime.now(UTC)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    seconds = -1
            if math.isfinite(seconds) and seconds >= 0:
                return seconds
        return float(0.5 * (2**attempt))
