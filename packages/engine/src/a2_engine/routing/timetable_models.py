"""Immutable event timetable. No urban imports, calendars inferred, or service-clock guessing."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from a2_engine.shared.results import finite_number


def checked_id(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("NONEMPTY_TIMETABLE_ID_REQUIRED")


def refs(values: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(values, str):
        raise ValueError("REFERENCE_COLLECTION_REQUIRED")
    for value in values:
        checked_id(value)
    return tuple(sorted(set(values)))


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("AWARE_EVENT_TIME_REQUIRED")
    return value.astimezone(UTC)


def microseconds(delta: timedelta) -> int:
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def duration_delta(seconds: float) -> timedelta:
    number = finite_number(seconds, "footpath seconds")
    try:
        delta = timedelta(seconds=number)
    except OverflowError as exc:
        raise ValueError("TIMETABLE_DURATION_RANGE_EXCEEDED") from exc
    if number > 0 and delta == timedelta():
        raise ValueError("TIMETABLE_DURATION_BELOW_TIMESTAMP_RESOLUTION")
    return delta


@dataclass(frozen=True)
class TimetableEvent:
    sequence: int
    node_id: str
    arrival_at: datetime
    departure_at: datetime
    pickup: bool
    dropoff: bool
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 0
        ):
            raise ValueError("NONNEGATIVE_EVENT_SEQUENCE_REQUIRED")
        checked_id(self.node_id)
        for name in ("arrival_at", "departure_at"):
            object.__setattr__(self, name, utc(getattr(self, name)))
        if self.arrival_at > self.departure_at:
            raise ValueError("REVERSED_STOP_DWELL")
        if not isinstance(self.pickup, bool) or not isinstance(self.dropoff, bool):
            raise ValueError("EXPLICIT_PICKUP_DROPOFF_REQUIRED")
        object.__setattr__(self, "evidence_refs", refs(self.evidence_refs))


@dataclass(frozen=True)
class TimetableRun:
    snapshot_id: str
    source_id: str
    trip_id: str
    service_date: date
    timezone: str
    transport_type: str
    events: tuple[TimetableEvent, ...]
    active: bool | None
    calendar_evidence_refs: tuple[str, ...]

    @property
    def run_id(self) -> str:
        identity = [self.snapshot_id, self.source_id, self.trip_id, self.service_date.isoformat()]
        return hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()

    def __post_init__(self) -> None:
        for name in ("snapshot_id", "source_id", "trip_id", "transport_type"):
            checked_id(getattr(self, name))
        if type(self.service_date) is not date:
            raise ValueError("EXPLICIT_SERVICE_DATE_REQUIRED")
        ZoneInfo(self.timezone)
        if self.active is not None and not isinstance(self.active, bool):
            raise ValueError("EXPLICIT_CALENDAR_STATE_REQUIRED")
        events = tuple(sorted(self.events, key=lambda event: event.sequence))
        if len(events) < 2 or len({e.sequence for e in events}) != len(events):
            raise ValueError("DISTINCT_ORDERED_EVENTS_REQUIRED")
        if any(b.arrival_at < a.departure_at for a, b in zip(events, events[1:])):
            raise ValueError("REVERSED_RUN_TIME")
        object.__setattr__(self, "events", events)
        object.__setattr__(self, "calendar_evidence_refs", refs(self.calendar_evidence_refs))


@dataclass(frozen=True)
class TimetableFootpath:
    path_id: str
    from_node_id: str
    to_node_id: str
    seconds: float | None
    confirmed: bool
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("path_id", "from_node_id", "to_node_id"):
            checked_id(getattr(self, name))
        if not isinstance(self.confirmed, bool):
            raise ValueError("EXPLICIT_FOOTPATH_CONFIRMATION_REQUIRED")
        if self.seconds is not None:
            object.__setattr__(self, "seconds", finite_number(self.seconds, "footpath seconds"))
            duration_delta(self.seconds)
        object.__setattr__(self, "evidence_refs", refs(self.evidence_refs))

    @property
    def usable(self) -> bool:
        return self.confirmed and self.seconds is not None and bool(self.evidence_refs)


@dataclass(frozen=True)
class TimetableTransfer:
    rule_id: str
    from_node_id: str
    to_node_id: str
    minimum_seconds: float | None
    allowed: bool | None
    evidence_refs: tuple[str, ...]
    from_run_id: str | None = None
    to_run_id: str | None = None
    semantics: Literal["alight_to_departure"] = "alight_to_departure"

    def __post_init__(self) -> None:
        for value in (self.rule_id, self.from_node_id, self.to_node_id):
            checked_id(value)
        for optional_id in (self.from_run_id, self.to_run_id):
            if optional_id is not None:
                checked_id(optional_id)
        if self.allowed is not None and not isinstance(self.allowed, bool):
            raise ValueError("EXPLICIT_TRANSFER_PERMISSION_REQUIRED")
        if self.minimum_seconds is not None:
            object.__setattr__(
                self, "minimum_seconds", finite_number(self.minimum_seconds, "minimum transfer")
            )
        if self.semantics != "alight_to_departure":
            raise ValueError("UNSUPPORTED_TRANSFER_SEMANTICS")
        object.__setattr__(self, "evidence_refs", refs(self.evidence_refs))


@dataclass(frozen=True)
class TimetableScope:
    snapshot_id: str
    graph_version: str
    calendar_version: str
    timetable_version: str
    footpath_version: str
    transfer_version: str
    timezone: str
    start_at: datetime
    end_at: datetime
    node_ids: frozenset[str]
    schedule_complete: bool
    calendar_complete: bool
    footpaths_complete: bool
    transfers_complete: bool
    continuations_complete: bool
    evidence_refs: tuple[str, ...]
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "snapshot_id",
            "graph_version",
            "calendar_version",
            "timetable_version",
            "footpath_version",
            "transfer_version",
        ):
            checked_id(getattr(self, name))
        ZoneInfo(self.timezone)
        for name in ("start_at", "end_at"):
            object.__setattr__(self, name, utc(getattr(self, name)))
        if self.start_at >= self.end_at:
            raise ValueError("FINITE_ORDERED_SEARCH_HORIZON_REQUIRED")
        for name in (
            "schedule_complete",
            "calendar_complete",
            "footpaths_complete",
            "transfers_complete",
            "continuations_complete",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError("EXPLICIT_TIMETABLE_COVERAGE_REQUIRED")
        object.__setattr__(self, "node_ids", frozenset(refs(tuple(self.node_ids))))
        object.__setattr__(self, "evidence_refs", refs(self.evidence_refs))
        object.__setattr__(self, "limitations", refs(self.limitations))


@dataclass(frozen=True)
class Timetable:
    scope: TimetableScope
    runs: tuple[TimetableRun, ...]
    footpaths: tuple[TimetableFootpath, ...]
    transfers: tuple[TimetableTransfer, ...]

    def __post_init__(self) -> None:
        for name, key in (("runs", "run_id"), ("footpaths", "path_id"), ("transfers", "rule_id")):
            rows = tuple(sorted(getattr(self, name), key=lambda row: getattr(row, key)))
            if len({getattr(row, key) for row in rows}) != len(rows):
                raise ValueError("DUPLICATE_TIMETABLE_" + key.upper())
            object.__setattr__(self, name, rows)
        nodes: set[str] = set()
        for run in self.runs:
            if run.snapshot_id != self.scope.snapshot_id:
                raise ValueError("RUN_SNAPSHOT_MISMATCH")
            nodes.update(e.node_id for e in run.events)
        for path in self.footpaths:
            nodes.update((path.from_node_id, path.to_node_id))
        for transfer in self.transfers:
            nodes.update((transfer.from_node_id, transfer.to_node_id))
        if nodes - self.scope.node_ids:
            raise ValueError("UNKNOWN_TIMETABLE_NODE")
        run_ids = {r.run_id for r in self.runs}
        patterns = set()
        for rule in self.transfers:
            if any(v is not None and v not in run_ids for v in (rule.from_run_id, rule.to_run_id)):
                raise ValueError("UNKNOWN_TRANSFER_RUN")
            pattern = (rule.from_node_id, rule.to_node_id, rule.from_run_id, rule.to_run_id)
            if pattern in patterns:
                raise ValueError("CONFLICTING_TRANSFER_RULE")
            patterns.add(pattern)


@dataclass(frozen=True)
class TimetableStep:
    kind: Literal["walk", "ride", "wait_initial", "wait_transfer"]
    from_node_id: str
    to_node_id: str
    start_at: datetime
    end_at: datetime
    transport_type: str
    run_id: str | None
    evidence_refs: tuple[str, ...]
    original_seconds: float | None = None
    footpath_id: str | None = None

    @property
    def duration_seconds(self) -> float:
        return (self.end_at - self.start_at).total_seconds()


@dataclass(frozen=True)
class TimetablePath:
    origin_node_id: str
    destination_node_id: str
    departure_at: datetime
    arrival_at: datetime
    steps: tuple[TimetableStep, ...]
    boardings: tuple[tuple[str, int, datetime], ...]

    @property
    def total_seconds(self) -> float:
        return (self.arrival_at - self.departure_at).total_seconds()

    @property
    def waiting_seconds(self) -> float:
        return (
            sum(
                microseconds(s.end_at - s.start_at) for s in self.steps if s.kind.startswith("wait")
            )
            / 1e6
        )

    @property
    def transfers(self) -> int:
        return sum(a[0] != b[0] for a, b in zip(self.boardings, self.boardings[1:]))


@dataclass(frozen=True)
class TimetableSearchResult:
    timetable: Timetable
    origin_node_id: str
    destination_node_id: str
    departure_at: datetime
    state: Literal["reachable", "confirmed_unreachable", "unknown"]
    best_known: TimetablePath | None
    proven: bool
    reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    expanded_states: int
    interrupted: bool
    max_wait_seconds: float
    allow_transit: bool
    require_transit: bool
    first_boarding: tuple[str, int] | None
    resource_limit: int | None
