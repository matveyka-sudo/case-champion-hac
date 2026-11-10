"""Stable identity bytes and bounded memory for shared immutable networks."""

import hashlib
import json
import tracemalloc
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from fractions import Fraction
from types import MappingProxyType
from typing import Any

import pytest
from a2_engine.scenario.identity import canonical, scenario_hash


@dataclass(frozen=True)
class Network:
    payload: str
    nodes: frozenset[str]
    options: Any


@dataclass(frozen=True)
class Search:
    point: str
    network: Network


def legacy_bytes(value: object) -> bytes:
    return json.dumps(
        canonical(value), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        0,
        -10,
        -0.0,
        0.00000001,
        "Москва\n\t😀\ud800",
        date(2026, 11, 10),
        datetime(2026, 11, 10, tzinfo=UTC),
        time(23, 59, 1, 12),
        Fraction(2, 3),
        Decimal("1.2300"),
        MappingProxyType({2: "two", 10: "ten", -1: (Fraction(1, 4), None)}),
        {("key", 2): {"é": {3, 1, -2}}, ("key", 1): frozenset({"a", "ab", "a\n"})},
        [Network("é", frozenset({"b", "a"}), MappingProxyType({"b": 2, "a": 1}))],
    ],
)
def test_streamed_hash_equals_exact_legacy_canonical_json(value: object) -> None:
    assert scenario_hash(value) == hashlib.sha256(legacy_bytes(value)).hexdigest()


def test_shared_frozen_identity_preserves_every_repeated_network_and_no_global_cache() -> None:
    mutable = [1, 2]
    network = Network("x", frozenset({"Москва", "other"}), mutable)
    source = (Search("a", network), Search("b", network), network)
    expected = hashlib.sha256(legacy_bytes(source)).hexdigest()
    assert scenario_hash(source) == expected
    mutable.append(3)
    assert scenario_hash(source) == hashlib.sha256(legacy_bytes(source)).hexdigest()
    assert scenario_hash(source) != expected


def test_shared_network_stream_avoids_full_expanded_json_allocation() -> None:
    network = Network("x" * (256 * 1024), frozenset({"n1", "n2"}), None)
    rows = tuple(Search(str(i), network) for i in range(192))
    # Independent legacy-format reference, one small row at a time: retain every
    # repeated network byte without constructing the ~48MiB whole JSON string.
    expected = hashlib.sha256()
    expected.update(b"[")
    for i, row in enumerate(rows):
        if i:
            expected.update(b",")
        expected.update(legacy_bytes(row))
    expected.update(b"]")
    tracemalloc.start()
    try:
        actual = scenario_hash(rows)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert actual == expected.hexdigest()
    assert peak < 8 * 1024 * 1024


@pytest.mark.parametrize("value", [float("nan"), float("inf"), object(), {"x": float("-inf")}])
def test_unsupported_values_still_fail_closed(value: object) -> None:
    with pytest.raises(ValueError, match="UNSUPPORTED_SCENARIO_CANONICAL_VALUE"):
        scenario_hash(value)


@pytest.mark.parametrize("hidden", [object(), float("nan"), float("inf"), float("-inf")])
def test_colliding_mapping_keys_validate_overwritten_children(hidden: object) -> None:
    value = {float("nan"): hidden, float("nan"): 1}
    assert len(value) == 2
    with pytest.raises(ValueError, match="UNSUPPORTED_SCENARIO_CANONICAL_VALUE"):
        legacy_bytes(value)
    with pytest.raises(ValueError, match="UNSUPPORTED_SCENARIO_CANONICAL_VALUE"):
        scenario_hash(value)


def test_colliding_mapping_keys_preserve_legacy_last_value_hash() -> None:
    value = {float("nan"): {"overwritten": [1, 2]}, float("nan"): 1}
    assert canonical(value) == {"nan": 1}
    assert scenario_hash(value) == hashlib.sha256(legacy_bytes(value)).hexdigest()
