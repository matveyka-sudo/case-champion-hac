"""Small deterministic primitives for accepted, versioned domain inputs."""

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from a2_engine.shared.results import finite_number


def checked_id(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("NONEMPTY_ACCEPTED_ID_REQUIRED")


def ids(values: tuple[str, ...], *, required: bool = True) -> tuple[str, ...]:
    if isinstance(values, str):
        raise ValueError("ID_COLLECTION_REQUIRED")
    result = tuple(values)
    for value in result:
        checked_id(value)
    if (required and not result) or len(result) != len(set(result)):
        raise ValueError("EMPTY_OR_DUPLICATE_IDS")
    return tuple(sorted(result))


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("AWARE_TIME_REQUIRED")
    return value.astimezone(UTC)


def duration(seconds: float) -> timedelta:
    value = finite_number(seconds, "seconds")
    try:
        result = timedelta(seconds=value)
    except OverflowError as exc:
        raise ValueError("DURATION_OVERFLOW") from exc
    if value and result == timedelta():
        raise ValueError("DURATION_BELOW_MICROSECOND_RESOLUTION")
    return result


def _json(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise TypeError("UNSUPPORTED_ACCEPTED_HASH_VALUE")


def content_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, default=_json, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def advance(at: datetime, seconds: float) -> datetime:
    try:
        return utc(at) + duration(seconds)
    except OverflowError as exc:
        raise ValueError("ACCEPTED_TIME_RANGE_EXCEEDED") from exc
