"""Read-only provider observations behind accepted, server-owned place mappings."""

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from a2_contracts.common import Identifier, valid_timezone
from a2_contracts.v2.dataset import AcceptedData, Period
from pydantic import BaseModel, ConfigDict, Field, field_validator

from a2_api.providers.rasp import Code, RaspDeparture, RaspError, RaspProvider, Transport


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class RaspPlaceBinding(_DTO):
    """Trusted ML2 mapping. Never constructed from tool arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    territory_id: Identifier
    snapshot_id: Identifier
    provider_code: Code
    timezone: Identifier
    evidence_id: Identifier
    _timezone = field_validator("timezone")(valid_timezone)


class DepartureQuery(_DTO):
    origin_id: str
    destination_id: str
    departure_at: datetime
    window_end: datetime | None = None
    origin_timezone: str | None = None
    transport_types: tuple[Transport, ...]


class DepartureIssue(_DTO):
    code: Literal["provider_unavailable", "disabled", "quota_exceeded", "timeout"]
    provider_code: str
    retryable: bool


class DepartureObservation(_DTO):
    status: Literal["available", "unknown"]
    source: Literal["yandex_rasp"] = "yandex_rasp"
    accepted_snapshot: Literal[False] = False
    coverage: Literal["partial"] = "partial"
    completeness: Literal["partial"] = "partial"
    query: DepartureQuery
    snapshot_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    departures: list[RaspDeparture] = Field(default_factory=list)
    fetched_at: datetime | None = None
    expires_at: datetime | None = None
    content_hash: str | None = None
    cache_key: str | None = None
    pagination_complete: bool = False
    queried_dates: list[date] = Field(default_factory=list)
    completed_dates: list[date] = Field(default_factory=list)
    errors: list[DepartureIssue] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(
        default_factory=lambda: [
            "DIRECT_SERVICES_ONLY",
            "PROVIDER_COVERAGE_NOT_COMPLETE",
            "PROVIDER_OBSERVATION_NOT_ACCEPTED_SNAPSHOT",
        ]
    )


def _covers(period: Period, day: date) -> bool:
    return period.start is not None and period.end is not None and period.start <= day <= period.end


def _issue(error: RaspError) -> DepartureIssue:
    code: Literal["provider_unavailable", "disabled", "quota_exceeded", "timeout"] = (
        error.code
        if error.code in {"disabled", "quota_exceeded", "timeout"}
        else "provider_unavailable"
    )
    return DepartureIssue(code=code, provider_code=error.code, retryable=error.retryable)


class RaspDepartureGateway:
    def __init__(
        self,
        provider: RaspProvider,
        bindings: Sequence[RaspPlaceBinding],
        *,
        transport_types: tuple[Transport, ...] = (
            "train",
            "suburban",
            "bus",
            "plane",
            "water",
            "helicopter",
        ),
    ) -> None:
        allowed = {"train", "suburban", "bus", "plane", "water", "helicopter"}
        if (
            not transport_types
            or len(set(transport_types)) != len(transport_types)
            or not set(transport_types) <= allowed
        ):
            raise ValueError("Gateway requires unique supported transport types")
        self._provider = provider
        self._bindings = tuple(RaspPlaceBinding.model_validate(b.model_dump()) for b in bindings)
        self._transport_types = transport_types

    def _resolve(
        self,
        territory_id: str,
        departure_at: datetime,
        snapshots: Sequence[AcceptedData],
        source_ids: frozenset[str],
    ) -> tuple[RaspPlaceBinding | None, str | None, str | None]:
        bindings = [b for b in self._bindings if b.territory_id == territory_id]
        if not bindings:
            return None, None, "PLACE_MAPPING_MISSING"
        snapshots_by_id = {s.snapshot_id: s for s in snapshots}
        if len(snapshots_by_id) != len(snapshots):
            return None, None, "SNAPSHOT_MAPPING_AMBIGUOUS"
        selected = [b for b in bindings if b.snapshot_id in snapshots_by_id]
        if not selected:
            return None, None, "MAPPING_SNAPSHOT_NOT_AUTHORIZED"
        if len(selected) != 1:
            return None, None, "PLACE_MAPPING_AMBIGUOUS"
        binding = selected[0]
        snapshot = snapshots_by_id[binding.snapshot_id]
        territories = [t for t in snapshot.territories if t.territory_id == territory_id]
        if territory_id not in snapshot.geographic_scope or len(territories) != 1:
            return None, None, "PLACE_OUTSIDE_ACCEPTED_SCOPE"
        if territories[0].timezone != binding.timezone:
            return None, None, "MAPPING_TIMEZONE_MISMATCH"
        evidence = next(
            (e for e in snapshot.evidence if e.evidence_id == binding.evidence_id), None
        )
        if evidence is None or not evidence.verified:
            return None, None, "MAPPING_EVIDENCE_UNVERIFIED"
        if (
            evidence.source_id not in source_ids
            or snapshot.source_versions.get(evidence.source_id) != evidence.source_version
        ):
            return None, None, "MAPPING_SOURCE_NOT_AUTHORIZED"
        local_day = departure_at.astimezone(ZoneInfo(binding.timezone)).date()
        if not _covers(snapshot.valid_period, local_day) or not _covers(
            evidence.valid_period, local_day
        ):
            return None, None, "DATE_OUTSIDE_ACCEPTED_COVERAGE"
        return binding, evidence.source_id, None

    async def get_departures(
        self,
        origin_id: str,
        destination_id: str,
        departure_at: datetime,
        *,
        snapshots: Sequence[AcceptedData],
        source_ids: frozenset[str],
    ) -> DepartureObservation:
        query = DepartureQuery(
            origin_id=origin_id,
            destination_id=destination_id,
            departure_at=departure_at,
            transport_types=self._transport_types,
        )
        observation = DepartureObservation(status="unknown", query=query)
        if departure_at.tzinfo is None or departure_at.utcoffset() is None:
            observation.reason_codes = ["DEPARTURE_TIME_REQUIRES_TIMEZONE"]
            return observation
        # The provider's seven-day interval is elapsed time, including DST changes.
        try:
            query.window_end = departure_at.astimezone(UTC) + timedelta(days=7)
        except (OverflowError, ValueError):
            observation.reason_codes = ["DEPARTURE_TIME_OUT_OF_RANGE"]
            return observation
        if origin_id == destination_id:
            observation.reason_codes = ["DISTINCT_PLACES_REQUIRED"]
            return observation
        origin, origin_source, error = self._resolve(origin_id, departure_at, snapshots, source_ids)
        if error:
            observation.reason_codes = [error]
            return observation
        destination, destination_source, error = self._resolve(
            destination_id, departure_at, snapshots, source_ids
        )
        if error:
            observation.reason_codes = [error]
            return observation
        assert origin is not None and destination is not None
        assert origin_source is not None and destination_source is not None
        if origin.provider_code == destination.provider_code:
            observation.reason_codes = ["PLACE_MAPPING_COLLISION"]
            return observation
        query.origin_timezone = origin.timezone
        observation.snapshot_ids = sorted({origin.snapshot_id, destination.snapshot_id})
        observation.evidence_ids = sorted({origin.evidence_id, destination.evidence_id})
        observation.source_ids = sorted({origin_source, destination_source})
        try:
            result = await self._provider.intercity_departures(
                origin.provider_code,
                destination.provider_code,
                departure_at,
                origin_timezone=origin.timezone,
                transport_types=self._transport_types,
            )
        except RaspError as exc:
            observation.errors = [_issue(exc)]
            observation.reason_codes = ["PROVIDER_REQUEST_FAILED"]
            return observation
        observation.status = "available"
        observation.departures = result.departures[:100]
        observation.fetched_at = result.fetched_at
        observation.expires_at = result.expires_at
        observation.content_hash = result.content_hash
        observation.cache_key = result.cache_key
        observation.pagination_complete = result.pagination_complete
        observation.queried_dates = result.queried_dates
        observation.completed_dates = result.completed_dates
        observation.errors = [
            _issue(RaspError(error.code, retryable=error.retryable)) for error in result.errors
        ]
        observation.reason_codes = list(result.reason_codes)
        if not result.departures:
            observation.reason_codes.append("EMPTY_DIRECT_RESPONSE_NOT_NO_ROUTE")
        if len(result.departures) > 100:
            observation.reason_codes.append("OBSERVATION_ROWS_TRUNCATED")
        return observation
