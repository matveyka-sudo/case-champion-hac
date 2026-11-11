"""Comparable-day median QA (IMPLEMENTATION.md engineering rule v1: >=5 days)."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from statistics import median
from zoneinfo import ZoneInfo

from a2_engine.routing.road import road_hash
from a2_engine.routing.traffic.models import (
    TrafficCalendarDay,
    TrafficGroup,
    TrafficObservation,
    TrafficProfile,
    TrafficProvenance,
    TrafficSnapshot,
    traffic_utc,
)


def build_typical_profile(
    profile_id: str,
    group: TrafficGroup,
    observations: Sequence[TrafficObservation],
    *,
    valid_from: datetime,
    valid_until: datetime,
    calendar: Sequence[TrafficCalendarDay],
    provenance: TrafficProvenance,
) -> TrafficProfile:
    if group.scope.mode != "typical" or not observations:
        raise ValueError("TYPICAL_OBSERVATIONS_REQUIRED")
    days = {c.local_date: c for c in calendar}
    if len(days) != len(calendar):
        raise ValueError("DUPLICATE_TRAFFIC_CALENDAR_DAY")
    unique: dict[str, TrafficObservation] = {}
    for observation in observations:
        if observation.group != group:
            raise ValueError("INCOMPARABLE_TRAFFIC_OBSERVATION")
        if (
            observation.observation_id in unique
            and unique[observation.observation_id] != observation
        ):
            raise ValueError("CONFLICTING_TRAFFIC_OBSERVATION")
        local = observation.observed_at.astimezone(ZoneInfo(group.scope.timezone))
        day = days.get(local.date())
        if day is None or (day.day_type, day.season, local.hour * 4 + local.minute // 15) != (
            group.day_type,
            group.season,
            group.local_15min_bin,
        ):
            raise ValueError("OBSERVATION_CALENDAR_GROUP_MISMATCH")
        if (
            observation.provenance.quality != "accepted"
            or observation.closed is None
            or (not observation.closed and observation.speed_kph is None)
        ):
            raise ValueError("INVALID_TYPICAL_SAMPLE_QA")
        unique[observation.observation_id] = observation
    measurements: dict[tuple[datetime, str], TrafficObservation] = {}
    for key in sorted(unique):
        observation = unique[key]
        identity = (observation.observed_at, observation.provenance.source_id)
        previous = measurements.get(identity)
        if previous is not None:
            if replace(previous, observation_id=observation.observation_id) != observation:
                raise ValueError("CONFLICTING_TRAFFIC_MEASUREMENT")
            continue
        measurements[identity] = observation
    samples = tuple(measurements.values())
    dates = tuple(
        sorted({o.observed_at.astimezone(ZoneInfo(group.scope.timezone)).date() for o in samples})
    )
    if len({o.closed for o in samples}) != 1:
        raise ValueError("INCOMPARABLE_CLOSURE_STATES")
    closed = samples[0].closed
    speeds = [o.speed_kph for o in samples if o.speed_kph is not None]
    speed = float(median(speeds)) if speeds and len(dates) >= 5 else None
    evidence = tuple(e for o in samples for e in o.provenance.evidence_refs)
    return TrafficProfile(
        profile_id,
        group,
        valid_from,
        valid_until,
        min(dates),
        max(dates),
        speed,
        closed,
        provenance,
        "median_recomputed_v1",
        dates,
        evidence,
        road_hash(samples),
    )


def select_traffic_profile(
    snapshot: TrafficSnapshot, group_edge_id: str, entry_at: datetime, *, checked_at: datetime
) -> tuple[TrafficProfile | None, str | None]:
    entry, checked = traffic_utc(entry_at), traffic_utc(checked_at)
    local = entry.astimezone(ZoneInfo(snapshot.scope.timezone))
    day = next((d for d in snapshot.calendar if d.local_date == local.date()), None)
    if day is None:
        return None, "TRAFFIC_CALENDAR_UNAVAILABLE"
    matches = [
        p
        for p in snapshot.profiles
        if p.group.edge.edge_id == group_edge_id
        and (p.group.day_type, p.group.season, p.group.local_15min_bin)
        == (day.day_type, day.season, local.hour * 4 + local.minute // 15)
        and p.valid_from <= entry < p.valid_until
    ]
    if len(matches) != 1:
        return None, (
            "TRAFFIC_OUTSIDE_APPLICABILITY"
            if any(p.group.edge.edge_id == group_edge_id for p in snapshot.profiles)
            else "TRAFFIC_PROFILE_MISSING"
        ) if not matches else "AMBIGUOUS_TRAFFIC_PROFILE"
    profile = matches[0]
    reason = snapshot.profile_reason(profile)
    if reason:
        return profile, reason
    if snapshot.scope.mode != "typical":
        if profile.observed_at is None:
            return profile, "OBSERVED_AT_UNKNOWN"
        observed_day = profile.observed_at.astimezone(ZoneInfo(snapshot.scope.timezone)).date()
        if not profile.observed_from <= observed_day <= profile.observed_to:
            return profile, "OBSERVATION_OUTSIDE_SOURCE_PERIOD"
        if profile.fetched_at is None or profile.fetched_at > checked:
            return profile, "OBSERVATION_NOT_FETCHED_AT_CHECK"
        if snapshot.scope.mode == "live" and not (
            profile.observed_at <= entry and profile.observed_at <= checked < profile.valid_until
        ):
            return profile, "LIVE_OBSERVATION_STALE_OR_INAPPLICABLE"
        if snapshot.scope.mode == "historical":
            if profile.observation_start_at is None or profile.observation_end_at is None:
                return profile, "HISTORICAL_SOURCE_COVERAGE_UNKNOWN"
            if profile.fetched_at < profile.observation_end_at:
                return profile, "HISTORICAL_SOURCE_NOT_COMPLETE_AT_FETCH"
            if not (
                profile.observation_start_at <= entry < profile.observation_end_at
                and profile.observation_start_at <= profile.observed_at < profile.observation_end_at
                and profile.observed_from <= local.date() <= profile.observed_to
            ):
                return profile, "HISTORICAL_OBSERVATION_NOT_IN_COVERAGE"
    return profile, None
