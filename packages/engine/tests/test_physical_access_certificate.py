"""Physical weights must participate in minimization; every fixture is synthetic."""

import hashlib
import json
import struct
from dataclasses import replace
from fractions import Fraction
from typing import Any

import pytest
from a2_engine.urban.walk.physical_certificate import (
    DesignatedAccessReceipt,
    PhysicalAccessChoice,
    load_designated_access_receipt,
    plan_designated_access,
    solve_designated_access,
    verify_common_designated_access_minimum,
    verify_designated_access_minimum,
)
from a2_engine.urban.walk.source_bound import (
    NativeEdge,
    NativeEndpoint,
    NativeWalkCandidate,
    NativeWalkExport,
)
from a2_engine.urban.walk.turn_certificate import (
    NativeTurnPlan,
    NativeTurnWeights,
    make_native_turn_incumbent,
    plan_native_turn_pairs,
)

SCOPE = "a" * 64


def bits(value: int) -> int:
    return int.from_bytes(struct.pack("<f", value), "little")


def graph() -> NativeWalkExport:
    return NativeWalkExport(
        "b" * 64,
        frozenset(range(4)),
        (NativeEdge(10, 0, 1, Fraction(1), True), NativeEdge(20, 2, 3, Fraction(2), True)),
        (),
        (
            NativeEndpoint(0, 0, 10, Fraction(), 0, 0, Fraction(1)),
            NativeEndpoint(0, 1, 20, Fraction(), 0, 0, Fraction(2)),
            NativeEndpoint(1, 0, 10, Fraction(1), 0, 0, Fraction(1)),
            NativeEndpoint(1, 1, 20, Fraction(1), 0, 0, Fraction(2)),
        ),
        frozenset({0}),
    )


def route(edge: int, seconds: int, index: int) -> NativeWalkCandidate:
    return NativeWalkCandidate(
        index, index, ((edge, bits(1)),), Fraction(seconds), (Fraction(seconds),), (Fraction(),)
    )


def replay(candidate: NativeWalkCandidate) -> str:
    return "\n".join(
        json.dumps(row)
        for row in [
            {
                "kind": "replay",
                "id": candidate.traversals[0][0],
                "fraction_bits": bits(1),
                "edge_seconds_bits": bits(int(candidate.lower_seconds)),
                "turn_seconds_bits": bits(0),
            },
            {"kind": "replay_footer", "feasible": True, "edges": 1},
        ]
    )


def access(
    source: NativeWalkExport, origin: tuple[int, int], destination: tuple[int, int]
) -> DesignatedAccessReceipt:
    choices = tuple(
        PhysicalAccessChoice(
            e.endpoint,
            e.index,
            e.edge_id,
            e.percent,
            e.lon_bits,
            e.lat_bits,
            Fraction((origin if e.endpoint == 0 else destination)[e.index]),
            Fraction(),
            "physical:point:p" if e.endpoint == 0 else "physical:destination:d",
            "access",
            ("synthetic-source",),
        )
        for e in source.endpoints
    )
    return DesignatedAccessReceipt(
        "c" * 64,
        "d" * 64,
        SCOPE,
        source.content_sha256,
        choices,
        ("Synthetic declared access model; no product readiness.",),
    )


def weights(plan: NativeTurnPlan) -> NativeTurnWeights:
    assert not plan.pairs
    return NativeTurnWeights(plan.incumbent.source_scope_id, plan.fingerprint, "e" * 64, ())


def test_scalar_addition_counterexample_is_reranked_inside_weighted_search() -> None:
    source = graph()
    first = route(10, 1, 0)
    incumbent = make_native_turn_incumbent(source, SCOPE, first, replay(first))
    receipt = access(source, (100, 0), (0, 0))
    plan = plan_designated_access(source, receipt, incumbent)
    assert plan.turns.ceiling_seconds == 101
    assert 3 in plan.turns.ball_nodes  # B's network2s lies outside unweighted incumbent1s.
    candidate = solve_designated_access(source, plan, weights(plan.turns))
    assert candidate.network_path == route(20, 2, 1)
    assert candidate.total_seconds == 2
    checked = verify_designated_access_minimum(
        source, plan, weights(plan.turns), candidate, replay(candidate.network_path)
    )
    assert checked.minimum_in_declared_scope
    assert checked.lower_seconds == checked.upper_seconds == 2
    assert not checked.full_physical_minimum_proven and not checked.product_index_eligible
    network_plan = plan_native_turn_pairs(source, SCOPE, incumbent)
    with pytest.raises(ValueError, match="NONCONSTANT"):
        verify_common_designated_access_minimum(
            source, receipt, network_plan, weights(network_plan), first, replay(first)
        )


def test_verified_common_weights_and_weighted_solver_agree_and_count_once() -> None:
    source = graph()
    first = route(10, 1, 0)
    incumbent = make_native_turn_incumbent(source, SCOPE, first, replay(first))
    receipt = access(source, (3, 3), (5, 5))
    network_plan = plan_native_turn_pairs(source, SCOPE, incumbent)
    common = verify_common_designated_access_minimum(
        source, receipt, network_plan, weights(network_plan), first, replay(first)
    )
    physical_plan = plan_designated_access(source, receipt, incumbent)
    candidate = solve_designated_access(source, physical_plan, weights(physical_plan.turns))
    assert candidate.total_seconds == common.lower_seconds == common.upper_seconds == 9
    assert common.minimum_in_declared_scope and not common.product_index_eligible
    failed = verify_designated_access_minimum(
        source, physical_plan, weights(physical_plan.turns), candidate, None
    )
    assert not failed.minimum_in_declared_scope and failed.upper_seconds == 9


def test_alternative_omissions_identity_changes_and_stale_weights_are_rejected() -> None:
    source = graph()
    first = route(10, 1, 0)
    incumbent = make_native_turn_incumbent(source, SCOPE, first, replay(first))
    receipt = access(source, (3, 3), (5, 5))
    for changed in (
        replace(receipt, choices=receipt.choices[:-1]),
        replace(receipt, native_scope_id="f" * 64),
        replace(receipt, choices=(replace(receipt.choices[0], edge_id=999), *receipt.choices[1:])),
    ):
        with pytest.raises(ValueError):
            plan_designated_access(source, changed, incumbent)
    plan = plan_designated_access(source, receipt, incumbent)
    changed = replace(
        receipt,
        choices=(replace(receipt.choices[0], connector_seconds=Fraction(4)), *receipt.choices[1:]),
    )
    assert changed.weighted_scope_id != receipt.weighted_scope_id
    with pytest.raises(ValueError, match="SCOPE_MISMATCH"):
        solve_designated_access(source, replace(plan, access=changed), weights(plan.turns))


def receipt_body(source: NativeWalkExport) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    build = {
        "snapshot_id": "snapshot",
        "build_id": "build",
        "graph_version": "graph",
        "source_graph_version": "source",
        "profile_version": "profile",
        "pedestrian_options_json": '{"type":"foot","walking_speed":5}',
    }
    artifact = {
        "build": build,
        "point_ids": ["p"],
        "destination_ids": ["d"],
        "connectors": [
            {
                "physical_node_id": f"physical:{kind}:{identifier}",
                "access_node_id": "access",
                "physical_location": {"lon": 0.0, "lat": 0.0},
                "access_location": {"lon": 0.0, "lat": 0.0},
            }
            for kind, identifier in (("point", "p"), ("destination", "d"))
        ],
    }
    bindings = {
        key: SCOPE
        for key in (
            "source_scope_id",
            "profile_sha256",
            "artifact_sha256",
            "request_sha256",
            "pbf_sha256",
        )
    }
    segment = {
        "traversable": True,
        "reasons": [],
        "no_intermediate_decimal_rounding": True,
        "geometry_lonlat": [[0.0, 0.0], [0.0, 0.0]],
        "geometry_crs": "EPSG:4326",
        "length_crs": "EPSG:32634",
        "length_m": 0.0,
        "length_m_binary64_ratio": "0/1",
        "seconds": 0.0,
        "seconds_binary64_ratio": "0/1",
        "policy": "EXACT_BINARY64_COORDINATE_IDENTITY",
    }
    body: dict[str, Any] = {
        "schema": "a2-designated-physical-access-receipt-v1",
        "acceptance_state": "research_only",
        "scope_kind": "canonical_designated_access",
        "complete_for_declared_scope": True,
        "full_physical_entrance_roster_complete": False,
        **bindings,
        "native_export_sha256": source.content_sha256,
        **{k: v for k, v in build.items() if k != "pedestrian_options_json"},
        "walking_speed_kmh": 5,
        "scope_id": "d" * 64,
        "limitations": ["Synthetic source."],
        "endpoints": [],
    }
    for side in (0, 1):
        physical = "physical:point:p" if side == 0 else "physical:destination:d"
        alternatives = []
        for e in source.endpoints:
            if e.endpoint == side:
                alternatives.append(
                    {
                        "endpoint": side,
                        "index": e.index,
                        "edge_id": e.edge_id,
                        "percent": str(e.percent),
                        "lon_bits": e.lon_bits,
                        "lat_bits": e.lat_bits,
                        "partial_seconds": str(e.partial_seconds),
                        "projected_coordinates": [0.0, 0.0],
                        "physical_node_id": physical,
                        "access_node_id": "access",
                        "traversable": True,
                        "evidence_refs": ["synthetic-source"],
                        "external_physical_connector": segment,
                        "access_to_native_offset": segment,
                        "combined_external_seconds_binary64_operands_sum": "0/1",
                    }
                )
        body["endpoints"].append(
            {
                "endpoint": side,
                "physical_node_id": physical,
                "access_node_id": "access",
                "declared_access_node_ids": ["access"],
                "complete_for_declared_scope": True,
                "full_physical_entrance_roster_complete": False,
                "native_alternatives": alternatives,
            }
        )
    return body, artifact, bindings


@pytest.mark.parametrize(
    "attack", ["scope", "ratio", "geometry", "omission", "unknown", "rounding", "source"]
)
def test_physical_receipt_rejects_unbound_or_unknown_source_input(attack: str) -> None:
    source = graph()
    body, artifact, bindings = receipt_body(source)
    good = json.dumps(body)
    load_designated_access_receipt(
        good,
        expected_sha256=hashlib.sha256(good.encode()).hexdigest(),
        graph=source,
        expected_bindings=bindings,
        artifact=artifact,
    )
    if attack == "scope":
        body["full_physical_entrance_roster_complete"] = True
    elif attack == "ratio":
        body["endpoints"][0]["native_alternatives"][0]["access_to_native_offset"][
            "seconds_binary64_ratio"
        ] = "1/10"
    elif attack == "geometry":
        body["endpoints"][0]["native_alternatives"][0]["access_to_native_offset"][
            "geometry_lonlat"
        ][1] = [1e-9, 0.0]
    elif attack == "omission":
        body["endpoints"][0]["native_alternatives"].pop()
    elif attack == "unknown":
        body["endpoints"][0]["native_alternatives"][0]["traversable"] = None
    elif attack == "rounding":
        body["endpoints"][0]["native_alternatives"][0]["access_to_native_offset"][
            "no_intermediate_decimal_rounding"
        ] = False
    else:
        body["source_scope_id"] = "f" * 64
    attacked = json.dumps(body)
    with pytest.raises(ValueError):
        load_designated_access_receipt(
            attacked,
            expected_sha256=hashlib.sha256(attacked.encode()).hexdigest(),
            graph=source,
            expected_bindings=bindings,
            artifact=artifact,
        )


def test_loaded_physical_source_cannot_be_relabelled_with_different_weights() -> None:
    source = graph()
    body, artifact, bindings = receipt_body(source)
    content = json.dumps(body)
    loaded = load_designated_access_receipt(
        content,
        expected_sha256=hashlib.sha256(content.encode()).hexdigest(),
        graph=source,
        expected_bindings=bindings,
        artifact=artifact,
    )
    with pytest.raises(ValueError, match="PHYSICAL_SOURCE_NORMALIZATION_CHANGED"):
        replace(
            loaded,
            choices=(
                replace(loaded.choices[0], connector_seconds=Fraction(1)),
                *loaded.choices[1:],
            ),
        )
