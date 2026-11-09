"""Immutable accepted traffic inputs. No transport, ingest or map matching.

QA metadata is checked, never authenticated here. Entry-speed-v1 holds the
selected speed over the supplied traversed length; it does not integrate bins.
"""

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import cached_property
from typing import Literal
from zoneinfo import ZoneInfo

from a2_engine.routing.road import road_hash
from a2_engine.shared.results import finite_number

TrafficMode = Literal["typical", "live", "historical"]


def traffic_refs(values: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(values, str) or any(not isinstance(x, str) or not x.strip() for x in values):
        raise ValueError("INVALID_TRAFFIC_REFERENCES")
    return tuple(sorted(set(values)))


def traffic_id(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("INVALID_TRAFFIC_IDENTIFIER")


def traffic_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("AWARE_TRAFFIC_TIMESTAMP_REQUIRED")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class TrafficScope:
    snapshot_id: str  # accepted base data, independent of traffic snapshot
    source_graph_version: str
    graph_version: str
    build_id: str
    traffic_version: str
    territory_id: str
    timezone: str
    mode: TrafficMode
    costing_profile_hash: str  # executor's complete effective auto costing options
    temporal_policy: Literal["entry_speed_v1"]
    evidence_refs: tuple[str, ...]
    engine_version: str
    costing_options_json: str
    calendar_version: str

    def __post_init__(self) -> None:
        for value in (
            self.snapshot_id,
            self.source_graph_version,
            self.graph_version,
            self.build_id,
            self.traffic_version,
            self.territory_id,
            self.costing_profile_hash,
        ):
            traffic_id(value)
        traffic_id(self.engine_version)
        traffic_id(self.calendar_version)
        options = json.loads(self.costing_options_json)
        if not isinstance(options, dict):
            raise ValueError("TRAFFIC_COSTING_OBJECT_REQUIRED")
        if options.get("costing") != "auto" or options.get("speed_source") != "accepted_snapshot":
            raise ValueError("TRAFFIC_AUTO_SNAPSHOT_COSTING_REQUIRED")
        object.__setattr__(
            self,
            "costing_options_json",
            json.dumps(options, sort_keys=True, separators=(",", ":"), allow_nan=False),
        )
        if self.costing_profile_hash != road_hash(
            [self.engine_version, options, self.temporal_policy]
        ):
            raise ValueError("TRAFFIC_COSTING_CONTENT_HASH_MISMATCH")
        ZoneInfo(self.timezone)
        if (
            self.mode not in ("typical", "live", "historical")
            or self.temporal_policy != "entry_speed_v1"
        ):
            raise ValueError("UNSUPPORTED_TRAFFIC_POLICY_OR_MODE")
        object.__setattr__(self, "evidence_refs", traffic_refs(self.evidence_refs))
        if not self.evidence_refs:
            raise ValueError("TRAFFIC_SCOPE_EVIDENCE_REQUIRED")


@dataclass(frozen=True)
class TrafficEdge:
    edge_id: str
    from_node_id: str
    to_node_id: str
    length_m: float

    def __post_init__(self) -> None:
        for value in (self.edge_id, self.from_node_id, self.to_node_id):
            traffic_id(value)
        object.__setattr__(
            self, "length_m", finite_number(self.length_m, "edge metres", positive=True)
        )


@dataclass(frozen=True)
class TrafficGroup:
    scope: TrafficScope
    edge: TrafficEdge
    day_type: str
    season: str
    local_15min_bin: int

    def __post_init__(self) -> None:
        traffic_id(self.day_type)
        traffic_id(self.season)
        if type(self.local_15min_bin) is not int or not 0 <= self.local_15min_bin < 96:
            raise ValueError("INVALID_TRAFFIC_TIME_BIN")


@dataclass(frozen=True)
class TrafficCalendarDay:
    local_date: date
    day_type: str
    season: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.local_date) is not date:
            raise ValueError("TRAFFIC_LOCAL_DATE_REQUIRED")
        traffic_id(self.day_type)
        traffic_id(self.season)
        object.__setattr__(self, "evidence_refs", traffic_refs(self.evidence_refs))
        if not self.evidence_refs:
            raise ValueError("TRAFFIC_CALENDAR_EVIDENCE_REQUIRED")


@dataclass(frozen=True)
class TrafficProvenance:
    source_id: str
    method: str
    license_id: str
    quality: Literal["accepted", "unverified", "rejected"]
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for value in (self.source_id, self.method, self.license_id):
            traffic_id(value)
        if self.quality not in ("accepted", "unverified", "rejected"):
            raise ValueError("INVALID_TRAFFIC_QUALITY")
        object.__setattr__(self, "evidence_refs", traffic_refs(self.evidence_refs))
        if not self.evidence_refs:
            raise ValueError("TRAFFIC_SOURCE_EVIDENCE_REQUIRED")


@dataclass(frozen=True)
class TrafficObservation:
    observation_id: str
    group: TrafficGroup
    observed_at: datetime
    fetched_at: datetime
    speed_kph: float | None
    closed: bool | None
    provenance: TrafficProvenance
    unit: Literal["km/h"] = "km/h"

    def __post_init__(self) -> None:
        traffic_id(self.observation_id)
        for name in ("observed_at", "fetched_at"):
            object.__setattr__(self, name, traffic_utc(getattr(self, name)))
        if self.observed_at > self.fetched_at or self.unit != "km/h":
            raise ValueError("INVALID_OBSERVATION_TIME_OR_UNIT")
        _speed_state(self.speed_kph, self.closed)


def _speed_state(speed: float | None, closed: bool | None) -> None:
    if closed is not None and type(closed) is not bool:
        raise ValueError("EXPLICIT_CLOSURE_STATE_REQUIRED")
    if speed is not None:
        finite_number(speed, "speed km/h", positive=True)
    if closed is True and speed is not None:
        raise ValueError("CLOSURE_IS_NOT_A_SPEED")


@dataclass(frozen=True)
class TrafficProfile:
    profile_id: str
    group: TrafficGroup
    valid_from: datetime
    valid_until: datetime
    observed_from: date
    observed_to: date
    speed_kph: float | None
    closed: bool | None
    provenance: TrafficProvenance
    aggregation: Literal["median_recomputed_v1", "accepted_aggregate", "observation"]
    observation_days: tuple[date, ...]
    evidence_refs: tuple[str, ...]
    samples_hash: str | None = None
    observed_at: datetime | None = None
    fetched_at: datetime | None = None
    unit: Literal["km/h"] = "km/h"
    observation_start_at: datetime | None = None
    observation_end_at: datetime | None = None

    def __post_init__(self) -> None:
        traffic_id(self.profile_id)
        for name in ("valid_from", "valid_until"):
            object.__setattr__(self, name, traffic_utc(getattr(self, name)))
        if (
            self.valid_from >= self.valid_until
            or type(self.observed_from) is not date
            or type(self.observed_to) is not date
            or self.observed_from > self.observed_to
        ):
            raise ValueError("INVALID_TRAFFIC_PERIOD")
        _speed_state(self.speed_kph, self.closed)
        if self.unit != "km/h" or self.aggregation not in (
            "median_recomputed_v1",
            "accepted_aggregate",
            "observation",
        ):
            raise ValueError("INVALID_PROFILE_UNIT_OR_METHOD")
        days = tuple(self.observation_days)
        if any(
            type(d) is not date or not self.observed_from <= d <= self.observed_to for d in days
        ) or len(set(days)) != len(days):
            raise ValueError("INVALID_OR_DUPLICATE_OBSERVATION_DAYS")
        object.__setattr__(self, "observation_days", tuple(sorted(days)))
        object.__setattr__(self, "evidence_refs", traffic_refs(self.evidence_refs))
        for name in ("observed_at", "fetched_at", "observation_start_at", "observation_end_at"):
            if getattr(self, name) is not None:
                object.__setattr__(self, name, traffic_utc(getattr(self, name)))
        if (
            self.observed_at is not None
            and self.fetched_at is not None
            and self.observed_at > self.fetched_at
        ):
            raise ValueError("OBSERVED_AFTER_FETCHED")
        if (self.observation_start_at is None) != (self.observation_end_at is None):
            raise ValueError("SOURCE_COVERAGE_REQUIRES_BOTH_BOUNDARIES")
        if self.observation_start_at is not None and self.observation_end_at is not None:
            if self.observation_start_at >= self.observation_end_at:
                raise ValueError("INVALID_SOURCE_OBSERVATION_COVERAGE")
        if self.group.scope.mode == "typical":
            if self.aggregation == "observation":
                raise ValueError("TYPICAL_AGGREGATION_REQUIRED")
            if self.aggregation == "median_recomputed_v1" and not self.samples_hash:
                raise ValueError("RECOMPUTED_SAMPLES_HASH_REQUIRED")
        elif self.aggregation != "observation":
            raise ValueError("LIVE_HISTORICAL_REQUIRE_OBSERVATIONS")

    @property
    def qa_reason(self) -> str | None:
        if self.provenance.quality != "accepted" or not self.evidence_refs:
            return "TRAFFIC_QA_UNCONFIRMED"
        if self.group.scope.mode == "typical" and len(self.observation_days) < 5:
            return "INSUFFICIENT_DISTINCT_OBSERVATION_DAYS"
        if self.closed is None:
            return "CLOSURE_STATE_UNKNOWN"
        if not self.closed and self.speed_kph is None:
            return "TRAFFIC_SPEED_UNKNOWN"
        return None


@dataclass(frozen=True)
class TrafficSnapshot:
    scope: TrafficScope
    profiles: tuple[TrafficProfile, ...]
    calendar: tuple[TrafficCalendarDay, ...]
    acceptance_evidence_refs: tuple[str, ...]
    declared_content_hash: str | None = None

    def __post_init__(self) -> None:
        table: dict[str, TrafficProfile] = {}
        for profile in self.profiles:
            if profile.group.scope != self.scope:
                raise ValueError("TRAFFIC_PROFILE_SCOPE_MISMATCH")
            previous = table.get(profile.profile_id)
            if previous is not None and previous != profile:
                raise ValueError("CONFLICTING_TRAFFIC_PROFILE_ID")
            table[profile.profile_id] = profile
        profiles = tuple(table[k] for k in sorted(table))
        edges: dict[str, TrafficEdge] = {}
        for profile in profiles:
            edge = profile.group.edge
            if edge.edge_id in edges and edges[edge.edge_id] != edge:
                raise ValueError("CONFLICTING_DIRECTED_EDGE")
            edges[edge.edge_id] = edge
        calendar = tuple(sorted(self.calendar, key=lambda d: d.local_date))
        if len({d.local_date for d in calendar}) != len(calendar):
            raise ValueError("DUPLICATE_TRAFFIC_CALENDAR_DAY")
        object.__setattr__(self, "profiles", profiles)
        object.__setattr__(self, "calendar", calendar)
        object.__setattr__(
            self, "acceptance_evidence_refs", traffic_refs(self.acceptance_evidence_refs)
        )
        if not self.acceptance_evidence_refs:
            raise ValueError("ACCEPTED_TRAFFIC_SNAPSHOT_REQUIRED")
        if (
            self.declared_content_hash is not None
            and self.declared_content_hash != self.content_hash
        ):
            raise ValueError("TRAFFIC_CONTENT_HASH_MISMATCH")

    @cached_property
    def content_hash(self) -> str:
        return road_hash([self.scope, self.profiles, self.calendar, self.acceptance_evidence_refs])

    @property
    def version_key(self) -> str:
        return self.scope.traffic_version + ":" + self.content_hash

    def profile_reason(self, profile: TrafficProfile) -> str | None:
        if profile.qa_reason:
            return profile.qa_reason
        if self.scope.mode == "typical":
            calendar = {day.local_date: day for day in self.calendar}
            for date in profile.observation_days:
                day = calendar.get(date)
                if day is None:
                    return "OBSERVATION_CALENDAR_UNAVAILABLE"
                if (day.day_type, day.season) != (profile.group.day_type, profile.group.season):
                    return "OBSERVATION_CALENDAR_GROUP_MISMATCH"
        return None

    @property
    def typical_accepted(self) -> bool:
        return (
            self.scope.mode == "typical"
            and bool(self.profiles)
            and all(self.profile_reason(p) is None for p in self.profiles)
        )
