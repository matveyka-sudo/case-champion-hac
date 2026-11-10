"""od / planning — canonical urban implementation."""

from collections.abc import Sequence
from datetime import date, datetime, timedelta

from a2_engine.shared.config import UrbanTauRules
from a2_engine.urban.models import UrbanPoint, references, utc_timestamp
from a2_engine.urban.od.models import UrbanODPlan
from a2_engine.urban.tau.calendar import _local_instant, city_zone
from a2_engine.urban.tau.models import UrbanODScope, UrbanTauDay


def _plan_od_scope(
    scope: UrbanODScope,
    points: Sequence[UrbanPoint],
    *,
    week_start: date,
    reference_at: datetime,
    calendar: Sequence[UrbanTauDay],
    rules: UrbanTauRules,
    profile_hash: str,
    evidence_refs: tuple[str, ...],
) -> UrbanODPlan:
    reference = utc_timestamp(reference_at)
    if week_start + timedelta(days=7) > reference.astimezone(city_zone(scope.timezone)).date():
        raise ValueError("OD_WEEK_NOT_COMPLETED")
    roster = tuple(sorted(points, key=lambda p: p.point_id))
    if (
        len({p.point_id for p in roster}) != len(roster)
        or frozenset(p.point_id for p in roster) != scope.point_ids
    ):
        raise ValueError("OD_POINT_ROSTER_MISMATCH")
    if any(p.territory_id != scope.city_id for p in roster):
        raise ValueError("OD_CITY_MISMATCH")
    if (
        week_start.weekday() != 0
        or rules.methodology_version != scope.versions.methodology_version
        or rules.max_total_wait_seconds != scope.max_total_wait_seconds
    ):
        raise ValueError("OD_WEEK_RULES_MISMATCH")
    if not profile_hash or not evidence_refs:
        raise ValueError("OD_PROFILE_AND_SCOPE_EVIDENCE_REQUIRED")
    days = tuple(sorted(calendar, key=lambda c: c.service_date))
    if len({c.service_date for c in days}) != len(days) or any(
        not week_start <= c.service_date < week_start + timedelta(days=7) for c in days
    ):
        raise ValueError("OD_CALENDAR_MISMATCH")
    departures = tuple(
        _local_instant(week_start + timedelta(days=i), clock, city_zone(scope.timezone))
        for i in range(rules.tau_days)
        for clock in rules.tau_times
    )
    return UrbanODPlan(
        scope,
        roster,
        week_start,
        reference,
        days,
        departures,
        profile_hash,
        references(evidence_refs),
    )
