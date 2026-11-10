"""tau / calendar — canonical urban implementation."""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from a2_engine.urban.models import identifier


def _local_instant(day: date, clock: time, zone: ZoneInfo) -> datetime:
    naive = datetime.combine(day, clock)
    alternatives = [naive.replace(tzinfo=zone, fold=fold).astimezone(UTC) for fold in (0, 1)]
    valid = {
        instant
        for instant in alternatives
        if instant.astimezone(zone).replace(tzinfo=None) == naive
    }
    if not valid:
        raise ValueError("NONEXISTENT_LOCAL_SLICE_TIME")
    if len(valid) != 1:
        raise ValueError("AMBIGUOUS_LOCAL_SLICE_TIME")
    return next(iter(valid))


def city_zone(name: str) -> ZoneInfo:
    identifier(name, "timezone")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("INVALID_IANA_TIMEZONE") from exc
