"""Public selection constraints derived from already verified runtime profiles."""

from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from a2_contracts.v2.api import UrbanPassengerScope

if TYPE_CHECKING:
    from a2_api.adapters.product_runtime import Runtime


def urban_passenger_scopes(
    runtime: "Runtime", *, certified_service_date: date | None = None
) -> list[UrbanPassengerScope]:
    """No routing, files or provider calls; omit unregistered or unbound inputs.

    The caller supplies the date from the verified certified proof, never from
    a user request. Request preflight remains authoritative (including budgets
    and overlapping-profile rejection). These scopes make no coverage claim.
    """
    scopes = []
    for profile, _ in sorted(runtime.profiles.values(), key=lambda row: row[0].profile_id):
        if profile.analysis_kind != "urban_passenger" or profile.city_id is None:
            continue
        snapshot = runtime.snapshots.get(profile.snapshot_id)
        if snapshot is None:
            continue
        city = next((t for t in snapshot.territories if t.territory_id == profile.city_id), None)
        if city is None:
            continue
        zone = ZoneInfo(city.timezone)
        start, end = profile.valid_from, profile.valid_to
        if snapshot.valid_period.start is not None:
            start = max(start, datetime.combine(snapshot.valid_period.start, time.min, zone))
        if snapshot.valid_period.end is not None:
            end = min(
                end,
                datetime.combine(snapshot.valid_period.end + timedelta(days=1), time.min, zone),
            )
        if profile.executor == "certified_walk_model":
            if certified_service_date is None:
                continue
            start = max(start, datetime.combine(certified_service_date, time.min, zone))
            end = min(
                end,
                datetime.combine(certified_service_date + timedelta(days=1), time.min, zone),
            )
        if start >= end:
            continue
        scopes.append(
            UrbanPassengerScope.model_validate(
                {
                    "scope_id": profile.profile_id,
                    "snapshot_id": profile.snapshot_id,
                    "city_id": profile.city_id,
                    "passenger_modes": profile.passenger_modes,
                    "categories": profile.categories,
                    "traffic_modes": profile.traffic_modes,
                    "valid_from": start.astimezone(UTC),
                    "valid_to": end.astimezone(UTC),
                    # Runtime.select currently accepts only area_id=None.
                    "whole_city": True,
                    "area_ids": [],
                }
            )
        )
    return scopes
