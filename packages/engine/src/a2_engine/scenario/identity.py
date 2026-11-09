"""Canonical immutable identities, including frozen configuration mappings."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from fractions import Fraction
from math import isfinite


def canonical(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: canonical(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(k): canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [canonical(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((canonical(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, (Fraction, Decimal)):
        return str(value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float) and isfinite(value):
        return value
    raise ValueError("UNSUPPORTED_SCENARIO_CANONICAL_VALUE")


def scenario_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            canonical(value), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def collect_evidence(value: object) -> tuple[str, ...]:
    found: set[str] = set()
    visited: set[int] = set()

    def walk(item: object) -> None:
        if item is None or isinstance(item, (str, int, float, bool)) or id(item) in visited:
            return
        visited.add(id(item))
        if is_dataclass(item) and not isinstance(item, type):
            for entry in fields(item):
                child = getattr(item, entry.name)
                if entry.name.endswith("_refs") and isinstance(
                    child, (tuple, list, set, frozenset)
                ):
                    found.update(v for v in child if isinstance(v, str))
                walk(child)
        elif isinstance(item, Mapping):
            for child in item.values():
                walk(child)
        elif isinstance(item, (tuple, list, set, frozenset)):
            for child in item:
                walk(child)

    walk(value)
    return tuple(sorted(found))
