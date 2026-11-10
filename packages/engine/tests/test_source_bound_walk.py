"""Adversarial controls for native inventory and additive elapsed bounds."""

import hashlib
import json
import random
import struct
from fractions import Fraction
from typing import Any

import pytest
from a2_engine.urban.walk.source_bound import (
    NativeWalkCandidate,
    binary_fraction,
    check_native_walk_bounds,
    load_native_walk_export,
    solve_native_walk_relaxation,
)


def bits(number: float, *, double: bool = False) -> int:
    return int.from_bytes(struct.pack("<d" if double else "<f", number), "little")


def records() -> list[dict[str, Any]]:
    return [
        {
            "kind": "header",
            "schema": 1,
            "model": "valhalla-3.5.1-binary32-edge-seconds-zero-turn-relaxation",
        },
        *({"kind": "node", "id": i} for i in (1, 2, 3)),
        {
            "kind": "edge",
            "id": 11,
            "from": 1,
            "to": 2,
            "retained": True,
            "seconds_bits": bits(2),
            "length": 2,
        },
        {
            "kind": "edge",
            "id": 12,
            "from": 2,
            "to": 3,
            "retained": True,
            "seconds_bits": bits(3),
            "length": 3,
        },
        *(
            {
                "kind": "endpoint",
                "endpoint": e,
                "index": 0,
                "edge": 11 + e,
                "percent_bits": bits(0.5, double=True),
                "lon_bits": bits(20, double=True),
                "lat_bits": bits(54, double=True),
                "partial_seconds_bits": bits(1 + e * 0.5),
            }
            for e in (0, 1)
        ),
        {"kind": "tile", "id": 0, "nodes": 3, "edges": 2},
        {
            "kind": "footer",
            "tiles": 1,
            "nodes": 3,
            "edges": 2,
            "retained_edges": 2,
            "transitions": 0,
        },
    ]


def content(rows: list[dict[str, Any]]) -> tuple[str, str]:
    text = "\n".join(json.dumps(row) for row in rows)
    return text, hashlib.sha256(text.encode()).hexdigest()


def candidate_replay(turn: float = 0) -> tuple[str, str]:
    text, digest = content(records())
    graph = load_native_walk_export(text, expected_sha256=digest)
    candidate = solve_native_walk_relaxation(graph)
    replay = [
        {
            "kind": "replay",
            "id": edge,
            "fraction_bits": fraction,
            "edge_seconds_bits": bits(1 + i * 0.5),
            "turn_seconds_bits": bits(turn if i else 0),
        }
        for i, (edge, fraction) in enumerate(candidate.traversals)
    ]
    replay.append({"kind": "replay_footer", "feasible": True, "edges": 2})
    return text, "\n".join(json.dumps(row) for row in replay)


def test_binary_weights_preserve_actual_float_instead_of_decimal_assumption() -> None:
    assert binary_fraction(bits(0.1)) != Fraction(1, 10)
    assert binary_fraction(bits(0.1)) == Fraction(13421773, 134217728)
    for invalid in (bits(float("nan")), bits(float("inf")), bits(-1)):
        with pytest.raises(ValueError):
            binary_fraction(invalid)


@pytest.mark.parametrize("attack", ["edge", "node", "footer", "endpoint", "direction", "split"])
def test_missing_inventory_and_changed_transport_are_rejected(attack: str) -> None:
    rows = records()
    if attack == "direction":
        rows[4]["to"] = 99
    elif attack == "split":
        rows[6]["partial_seconds_bits"] = bits(0.999)
    else:
        rows.pop(next(i for i, row in enumerate(rows) if row["kind"] == attack))
    text, digest = content(rows)
    with pytest.raises(ValueError):
        load_native_walk_export(text, expected_sha256=digest)
    original, original_hash = content(records())
    assert original != text
    with pytest.raises(ValueError, match="CONTENT_CHANGED"):
        load_native_walk_export(text, expected_sha256=original_hash)


def test_positive_turn_gap_does_not_certify_minimum_or_round_towards_equality() -> None:
    text, replay = candidate_replay(0.25)
    graph = load_native_walk_export(text, expected_sha256=hashlib.sha256(text.encode()).hexdigest())
    candidate = solve_native_walk_relaxation(graph)
    result = check_native_walk_bounds(
        candidate,
        replay,
        origin_connector_seconds=Fraction(9),
        destination_connector_seconds=Fraction(18),
        origin_offset_seconds=Fraction(1, 100),
        destination_offset_seconds=Fraction(2, 100),
        physical_endpoint_inventory_complete=True,
    )
    assert result.network_lower_seconds == Fraction(5, 2)
    assert result.network_upper_seconds == Fraction(11, 4)
    assert result.physical_lower_seconds is None
    assert result.physical_upper_seconds == Fraction(2978, 100)
    assert not result.network_minimum_proven and not result.physical_minimum_proven


def test_network_equality_cannot_bypass_physical_offsets_or_entrance_roster() -> None:
    text, replay = candidate_replay()
    graph = load_native_walk_export(text, expected_sha256=hashlib.sha256(text.encode()).hexdigest())
    candidate = solve_native_walk_relaxation(graph)
    result = check_native_walk_bounds(
        candidate,
        replay,
        origin_connector_seconds=Fraction(9),
        destination_connector_seconds=Fraction(18),
    )
    assert result.network_minimum_proven
    assert result.physical_lower_seconds is None and result.physical_upper_seconds is None
    assert not result.physical_minimum_proven
    assert result.reasons == (
        "PHYSICAL_CORRELATION_OFFSET_UNCONFIRMED",
        "PHYSICAL_ENDPOINT_ALTERNATIVES_UNCONFIRMED",
        "PHYSICAL_ENDPOINT_WEIGHTS_NOT_BOUND",
    )
    complete = check_native_walk_bounds(
        candidate,
        replay,
        origin_connector_seconds=Fraction(9),
        destination_connector_seconds=Fraction(18),
        origin_offset_seconds=Fraction(1),
        destination_offset_seconds=Fraction(2),
        physical_endpoint_inventory_complete=True,
    )
    assert not complete.physical_minimum_proven
    assert complete.physical_lower_seconds is None
    assert complete.physical_upper_seconds == Fraction(65, 2)
    assert complete.reasons == ("PHYSICAL_ENDPOINT_WEIGHTS_NOT_BOUND",)


def test_incomplete_replay_wrong_fraction_and_optimistic_upper_are_rejected() -> None:
    text, replay = candidate_replay()
    graph = load_native_walk_export(text, expected_sha256=hashlib.sha256(text.encode()).hexdigest())
    candidate = solve_native_walk_relaxation(graph)
    rows = [json.loads(line) for line in replay.splitlines()]
    for change in ("omit", "fraction", "time"):
        attacked = json.loads(json.dumps(rows))
        if change == "omit":
            attacked.pop()
        elif change == "fraction":
            attacked[0]["fraction_bits"] = bits(0.25)
        else:
            attacked[0]["edge_seconds_bits"] = bits(0)
        with pytest.raises(ValueError):
            check_native_walk_bounds(
                candidate,
                "\n".join(json.dumps(r) for r in attacked),
                origin_connector_seconds=Fraction(),
                destination_connector_seconds=Fraction(),
            )


def test_shared_edge_direct_alternative_is_not_lost() -> None:
    rows = records()
    for row in rows:
        if row.get("kind") == "endpoint" and row.get("endpoint") == 1:
            row["edge"] = 11
            row["percent_bits"] = bits(0.75, double=True)
            row["partial_seconds_bits"] = bits(1.5)
    text, digest = content(rows)
    candidate = solve_native_walk_relaxation(load_native_walk_export(text, expected_sha256=digest))
    assert candidate.lower_seconds == Fraction(1, 2)
    assert candidate.traversals == ((11, bits(0.25)),)


def test_cheaper_endpoint_alternative_is_considered() -> None:
    rows = records()
    alternate = dict(rows[7])
    alternate.update(
        index=1, edge=11, percent_bits=bits(0.75, double=True), partial_seconds_bits=bits(1.5)
    )
    rows.insert(-1, alternate)
    text, digest = content(rows)
    candidate = solve_native_walk_relaxation(load_native_walk_export(text, expected_sha256=digest))
    assert candidate.lower_seconds == Fraction(1, 2)
    assert candidate.destination_index == 1


def test_exact_native_search_matches_independent_simple_path_enumeration() -> None:
    for seed in range(25):
        rng = random.Random(seed)
        rows = records()
        rows = [row for row in rows if row["kind"] in ("header",)]
        rows += [{"kind": "node", "id": node} for node in range(8)]
        weights = {(node, node + 1): rng.randrange(5) for node in range(1, 6)}
        for start in range(1, 7):
            for end in range(1, 7):
                if start != end and rng.random() < 0.2:
                    weights[start, end] = rng.randrange(5)
        edge_specs = [(0, 1, 0), (6, 7, 0)] + [(a, b, w) for (a, b), w in weights.items()]
        for index, (start, end, weight) in enumerate(edge_specs):
            rows.append(
                {
                    "kind": "edge",
                    "id": index,
                    "from": start,
                    "to": end,
                    "retained": True,
                    "seconds_bits": bits(weight),
                    "length": weight,
                }
            )
        for endpoint in (0, 1):
            rows.append(
                {
                    "kind": "endpoint",
                    "endpoint": endpoint,
                    "index": 0,
                    "edge": endpoint,
                    "percent_bits": bits(1 - endpoint, double=True),
                    "lon_bits": bits(20, double=True),
                    "lat_bits": bits(54, double=True),
                    "partial_seconds_bits": bits(0),
                }
            )
        rows += [
            {"kind": "tile", "id": 0, "nodes": 8, "edges": len(edge_specs)},
            {
                "kind": "footer",
                "tiles": 1,
                "nodes": 8,
                "edges": len(edge_specs),
                "retained_edges": len(edge_specs),
                "transitions": 0,
            },
        ]
        totals: list[int] = []

        def enumerate_paths(node: int, visited: frozenset[int], elapsed: int) -> None:
            if node == 6:
                totals.append(elapsed)
                return
            for (start, end), weight in weights.items():
                if start == node and end not in visited:
                    enumerate_paths(end, visited | {end}, elapsed + weight)

        enumerate_paths(1, frozenset({1}), 0)
        text, digest = content(rows)
        candidate = solve_native_walk_relaxation(
            load_native_walk_export(text, expected_sha256=digest)
        )
        assert candidate.lower_seconds == min(totals), seed


def minimum_turn_records() -> list[dict[str, Any]]:
    rows = records()
    rows[0] = {
        "kind": "header",
        "schema": 2,
        "model": "valhalla-3.5.1-binary32-edge-seconds-minimum-turn-relaxation",
    }
    rows[-1:-1] = [
        {
            "kind": "edge",
            "id": 13,
            "from": 3,
            "to": 1,
            "retained": True,
            "seconds_bits": bits(10),
            "length": 10,
        },
        *[
            {"kind": "turn_lower", "edge": edge, "seconds_bits": bits(weight), "predecessors": 1}
            for edge, weight in ((11, 5), (12, 0.25), (13, 0))
        ],
    ]
    for row in rows:
        if row["kind"] in ("footer", "tile"):
            row["edges"] = 3
        if row["kind"] == "footer":
            row["retained_edges"] = 3
    return rows


def test_minimum_turn_tightening_and_initial_partial_exemption() -> None:
    text, digest = content(minimum_turn_records())
    candidate = solve_native_walk_relaxation(load_native_walk_export(text, expected_sha256=digest))
    assert candidate.lower_seconds == Fraction(11, 4)
    assert candidate.lower_turn_seconds == (Fraction(), Fraction(1, 4))
    _, replay = candidate_replay(0.25)
    result = check_native_walk_bounds(
        candidate,
        replay,
        origin_connector_seconds=Fraction(),
        destination_connector_seconds=Fraction(),
    )
    assert result.network_minimum_proven and not result.physical_minimum_proven


@pytest.mark.parametrize("endpoint", [0, 1])
def test_node_snapped_endpoints_do_not_invent_turns(endpoint: int) -> None:
    rows = minimum_turn_records()
    rows[6 + endpoint]["percent_bits"] = bits(1 - endpoint, double=True)
    rows[6 + endpoint]["partial_seconds_bits"] = bits(0)
    text, digest = content(rows)
    candidate = solve_native_walk_relaxation(load_native_walk_export(text, expected_sha256=digest))
    assert candidate.lower_turn_seconds == (Fraction(), Fraction())
    assert candidate.lower_seconds == (Fraction(3, 2) if endpoint == 0 else Fraction(1))


def test_omitted_minimum_turn_roster_or_excessive_lower_weight_is_rejected() -> None:
    rows = minimum_turn_records()
    rows.pop(-2)
    text, digest = content(rows)
    with pytest.raises(ValueError, match="MINIMUM_TURN_INVENTORY_INCOMPLETE"):
        load_native_walk_export(text, expected_sha256=digest)
    rows = minimum_turn_records()
    next(row for row in rows if row["kind"] == "turn_lower" and row["edge"] == 12)[
        "seconds_bits"
    ] = bits(0.5)
    text, digest = content(rows)
    candidate = solve_native_walk_relaxation(load_native_walk_export(text, expected_sha256=digest))
    _, replay = candidate_replay(0.25)
    with pytest.raises(ValueError, match="MINIMUM_TURN_EXCEEDS_FEASIBLE_TURN"):
        check_native_walk_bounds(
            candidate,
            replay,
            origin_connector_seconds=Fraction(),
            destination_connector_seconds=Fraction(),
        )


def test_candidate_specific_access_cannot_shift_a_global_network_minimum() -> None:
    # Alternative A is network-minimal (1s) but has 100s access; B would take
    # network 2s + access 0s. A complete roster does not prove common offsets.
    candidate = NativeWalkCandidate(
        0, 0, ((11, bits(1)),), Fraction(1), (Fraction(1),), (Fraction(),)
    )
    replay = "\n".join(
        json.dumps(row)
        for row in [
            {
                "kind": "replay",
                "id": 11,
                "fraction_bits": bits(1),
                "edge_seconds_bits": bits(1),
                "turn_seconds_bits": bits(0),
            },
            {"kind": "replay_footer", "feasible": True, "edges": 1},
        ]
    )
    result = check_native_walk_bounds(
        candidate,
        replay,
        origin_connector_seconds=Fraction(),
        destination_connector_seconds=Fraction(),
        origin_offset_seconds=Fraction(100),
        destination_offset_seconds=Fraction(),
        physical_endpoint_inventory_complete=True,
    )
    assert result.network_minimum_proven
    assert result.physical_upper_seconds == 101
    assert result.physical_lower_seconds is None and not result.physical_minimum_proven
    assert result.reasons == ("PHYSICAL_ENDPOINT_WEIGHTS_NOT_BOUND",)
