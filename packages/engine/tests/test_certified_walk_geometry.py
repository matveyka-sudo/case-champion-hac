"""Independent directed-shape/clip controls; every fixture is synthetic."""

import hashlib
import json
import math
import struct
from dataclasses import asdict, replace
from fractions import Fraction
from typing import Any

import pytest
from a2_engine.urban.walk.geometry import (
    _node_shape_representations,
    load_certified_walk_geometry,
    make_certified_geometry_plan,
)
from a2_engine.urban.walk.physical_certificate import DesignatedAccessReceipt, PhysicalAccessChoice
from a2_engine.urban.walk.source_bound import NativeEdge, NativeWalkCandidate, NativeWalkExport


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def bits(x: float) -> int:
    return int.from_bytes(struct.pack("<d", x), "little")


def xy(x: float, y: float = 0.0) -> list[int]:
    return [bits(x), bits(y)]


def fixture() -> dict[str, Any]:
    candidate = NativeWalkCandidate(
        0,
        0,
        ((10, 1061158912), (20, 1061158912)),
        Fraction(3),
        (Fraction(1), Fraction(2)),
        (Fraction(), Fraction()),
    )
    graph = NativeWalkExport(
        "e" * 64,
        frozenset({0, 1, 2}),
        (NativeEdge(10, 0, 1, Fraction(2), True), NativeEdge(20, 1, 2, Fraction(2), True)),
        (),
        (),
        frozenset({0}),
    )
    request = json.dumps({"locations": [{"lon": 0.5, "lat": 0.1}, {"lon": 3.5, "lat": 0.1}]})
    source = {
        "export.jsonl": graph.content_sha256,
        "request.json": sha(request),
        "walking-profile.json": "p" * 64,
        "valhalla.json": "c" * 64,
        "build-manifest.json": "b" * 64,
    }
    result = {
        "network_minimum_proven": True,
        "network_lower_seconds": "3",
        "network_upper_seconds": "3",
        "candidate": json.loads(json.dumps(asdict(candidate), default=str)),
    }
    endpoints = [
        {
            "endpoint": side,
            "index": 0,
            "edge_id": edge,
            "percent": "1/4" if side == 0 else "3/4",
            "lon_bits": bits(x),
            "lat_bits": bits(0),
            "partial_seconds": "1",
        }
        for side, edge, x in ((0, 10, 0.5), (1, 20, 3.5))
    ]
    native = json.dumps(
        {
            "files_sha256": {"turn-candidate.json": sha(candidate.replay_json())},
            "result": result,
            "native_endpoint_inventory": endpoints,
            "closed_turn_certificate": {
                "result": result,
                "source_binding": source,
                "source_scope_id": sha(json.dumps(source, sort_keys=True)),
            },
        }
    )
    plan_json = make_certified_geometry_plan(
        native, candidate.replay_json(), request, native_receipt_sha256=sha(native)
    )
    plan = json.loads(plan_json)
    rows = [
        {
            "kind": "geometry_header",
            "schema": 1,
            "shape_decode_precision_bits": bits(1e-6),
            "shape_encode_precision": 1000000,
            "plan_sha256": plan["plan_sha256"],
            "source_scope_id": plan["bindings"]["source_scope_id"],
        }
    ]
    rows.extend(
        {
            "kind": "geometry_projection",
            "endpoint": side,
            "index": 0,
            "edge_id": edge,
            "storage_segment": 0,
            "lon_scale_bits": bits(1),
            "approx_m_per_lng_degree_bits": bits(110567),
            "projected_bits": xy(x),
        }
        for side, edge, x in ((0, 10, 0.5), (1, 20, 3.5))
    )
    rows.extend(
        [
            {
                "kind": "geometry_edge",
                "ordinal": 0,
                "id": 10,
                "fraction_bits": 1061158912,
                "from": 0,
                "to": 1,
                "forward": True,
                "way_id": 100,
                "from_node_bits": xy(0),
                "to_node_bits": xy(2),
                "shape_bits": [xy(0), xy(1), xy(2)],
            },
            {
                "kind": "geometry_edge",
                "ordinal": 1,
                "id": 20,
                "fraction_bits": 1061158912,
                "from": 1,
                "to": 2,
                "forward": False,
                "way_id": 200,
                "from_node_bits": xy(2),
                "to_node_bits": xy(4),
                "shape_bits": [xy(4), xy(3), xy(2)],
            },
            {"kind": "geometry_footer", "edges": 2},
        ]
    )
    return {
        "graph": graph,
        "candidate": candidate,
        "plan_json": plan_json,
        "rows": rows,
        "native": native,
        "request": request,
    }


def load(f: dict[str, Any], access: DesignatedAccessReceipt | None = None) -> Any:
    content = "\n".join(json.dumps(r) for r in f["rows"])
    return load_certified_walk_geometry(
        content,
        expected_sha256=sha(content),
        plan_json=f["plan_json"],
        graph=f["graph"],
        candidate=f["candidate"],
        access=access,
    )


def test_exact_sequence_direction_and_projection_clipping() -> None:
    f = fixture()
    result = load(f)
    assert result.edge_ids == (10, 20)
    assert result.network_coordinates == ((0.5, 0), (1, 0), (2, 0), (3, 0), (3.5, 0))
    assert result.network_components == (((0.5, 0), (1, 0), (2, 0)), ((2, 0), (3, 0), (3.5, 0)))
    assert result.physical_geojson is None
    assert not result.discontinuities


@pytest.mark.parametrize(
    "attack",
    [
        "reverse_flag",
        "reverse_shape",
        "sequence",
        "fraction",
        "node",
        "projection",
        "segment",
        "incomplete",
        "nonfinite",
        "short",
        "plan",
        "profile",
        "source",
    ],
)
def test_mutated_native_geometry_or_source_is_rejected(attack: str) -> None:
    f = fixture()
    if attack == "reverse_flag":
        f["rows"][4]["forward"] = True
    elif attack == "reverse_shape":
        f["rows"][3]["shape_bits"].reverse()
    elif attack == "sequence":
        f["rows"][3]["id"] = 20
    elif attack == "fraction":
        f["rows"][3]["fraction_bits"] = 0
    elif attack == "node":
        f["rows"][4]["from"] = 0
    elif attack == "projection":
        f["rows"][1]["projected_bits"] = xy(0.6)
    elif attack == "segment":
        f["rows"][1]["storage_segment"] = 1
    elif attack == "incomplete":
        f["rows"].pop()
    elif attack == "nonfinite":
        f["rows"][3]["shape_bits"][1] = xy(math.inf)
    elif attack == "short":
        f["rows"][3]["shape_bits"] = [xy(0)]
    elif attack in ("plan", "profile"):
        plan = json.loads(f["plan_json"])
        plan["bindings"]["walking-profile.json"] = "wrong"
        f["plan_json"] = json.dumps(plan)
    else:
        f["graph"] = replace(f["graph"], content_sha256="other")
    with pytest.raises(ValueError):
        load(f)


def test_topological_hierarchy_hop_does_not_invent_a_geometric_bridge() -> None:
    f = fixture()
    f["graph"] = replace(
        f["graph"],
        nodes=frozenset({0, 1, 2, 3}),
        edges=(f["graph"].edges[0], replace(f["graph"].edges[1], start=3)),
        transitions=((1, 3),),
    )
    row = f["rows"][4]
    row["from"], row["from_node_bits"], row["shape_bits"][-1] = 3, xy(2.1), xy(2.1)
    result = load(f)
    assert result.network_coordinates is None and result.network_geojson is None
    assert result.discontinuities == (1,)
    assert len(result.network_components) == 2
    assert "EXACT_NETWORK_SHAPE_DISCONTINUITY" in result.limitations
    f["graph"] = replace(f["graph"], transitions=())
    with pytest.raises(ValueError, match="TOPOLOGY"):
        load(f)


def test_self_crossing_uses_first_equal_projection_in_storage_order() -> None:
    f = fixture()
    raw = [xy(0), xy(2, 2), xy(0, 2), xy(2), xy(4)]
    row = f["rows"][3]
    row["shape_bits"], row["to_node_bits"] = raw, xy(4)
    f["rows"][4]["from_node_bits"] = xy(4)
    f["rows"][4]["shape_bits"][-1] = xy(4)
    # Make just the first clipped component authoritative for this tie control.
    plan = json.loads(f["plan_json"])
    plan["endpoints"][0]["projected_bits"] = xy(1, 1)
    plan["endpoints"][0]["query_bits"] = xy(1, 1)
    core = {k: v for k, v in plan.items() if k != "plan_sha256"}
    plan["plan_sha256"] = sha(json.dumps(core, sort_keys=True, separators=(",", ":")))
    f["plan_json"] = json.dumps(plan)
    f["rows"][0]["plan_sha256"] = plan["plan_sha256"]
    f["rows"][1]["projected_bits"] = xy(1, 1)
    # Second component now loops 4 -> 3 -> 4; no gap is silently bridged.
    result = load(f)
    assert result.network_components[0] == ((1, 1), (2, 2), (0, 2), (2, 0), (4, 0))
    f["rows"][1]["storage_segment"] = 2
    with pytest.raises(ValueError, match="PROJECTION_REPLAY"):
        load(f)


def test_source_defined_six_digit_encoding_preserves_original_shape_bits() -> None:
    f = fixture()
    f["rows"][3]["from_node_bits"] = xy(0.0000001)
    assert load(f).network_components[0][0] == (0.5, 0)
    assert f["rows"][3]["from_node_bits"] == xy(0.0000001)
    f["rows"][3]["from_node_bits"] = xy(0.0000006)
    with pytest.raises(ValueError, match="ORIENTATION"):
        load(f)


def test_actual_node_reencoding_counterexample_is_only_a_necessary_sanity_check() -> None:
    # Preserved actual airport source values: reconstructed NodeInfo and shape
    # are different precisions. No bit-equal node->shape conversion is asserted.
    source_node = xy(20.5290215, 54.7688917)
    source_shape = xy(20.529021, 54.768892)
    sanity = _node_shape_representations(source_node)
    assert tuple(source_shape) in sanity
    assert (bits(20.529021999999998), bits(54.768892)) in sanity
    assert source_node == xy(20.5290215, 54.7688917)
    assert tuple(xy(20.529023, 54.768892)) not in sanity


def test_changed_candidate_or_request_cannot_create_a_geometry_plan() -> None:
    f = fixture()
    for candidate, request in (("{}", f["request"]), (f["candidate"].replay_json(), "{}")):
        with pytest.raises(ValueError):
            make_certified_geometry_plan(
                f["native"], candidate, request, native_receipt_sha256=sha(f["native"])
            )


def access_fixture(f: dict[str, Any], attack: str = "") -> DesignatedAccessReceipt:
    plan = json.loads(f["plan_json"])
    choices, sides = [], []
    for side, edge, projected, physical, canonical in (
        (0, 10, 0.5, -1, -0.5),
        (1, 20, 3.5, 5, 4.5),
    ):

        def line(a: float, b: float) -> dict[str, Any]:
            return {
                "traversable": True,
                "reasons": [],
                "geometry_crs": "EPSG:4326",
                "policy": "walk-connector-geometry-v2",
                "directionality": "symmetric straight-link model",
                "geometry_lonlat": [[a, 0], [b, 0]],
                "seconds_binary64_ratio": "0",
            }

        connector, offset = line(physical, canonical), line(canonical, projected)
        if side == 0 and attack == "permission":
            connector["traversable"] = False
        if side == 0 and attack == "gap":
            offset["geometry_lonlat"][-1][0] = 0.6
        choice = PhysicalAccessChoice(
            side,
            0,
            edge,
            Fraction(1, 4) if side == 0 else Fraction(3, 4),
            bits(projected),
            bits(0),
            Fraction(),
            Fraction(),
            "physical",
            "access",
            ("synthetic",),
        )
        choices.append(choice)
        sides.append(
            {
                "native_alternatives": [
                    {
                        "endpoint": side,
                        "index": 0,
                        "edge_id": edge,
                        "percent": str(choice.percent),
                        "lon_bits": bits(projected),
                        "lat_bits": bits(0),
                        "physical_node_id": "physical",
                        "access_node_id": "access",
                        "evidence_refs": ["synthetic"],
                        "external_physical_connector": connector,
                        "access_to_native_offset": offset,
                    }
                ]
            }
        )
    raw = json.dumps(
        {
            "scope_id": "synthetic",
            "source_scope_id": plan["bindings"]["source_scope_id"],
            "native_export_sha256": f["graph"].content_sha256,
            "limitations": ["synthetic"],
            "endpoints": sides,
        }
    )
    return DesignatedAccessReceipt(
        sha(raw),
        "synthetic",
        plan["bindings"]["source_scope_id"],
        f["graph"].content_sha256,
        tuple(choices),
        ("synthetic",),
        raw,
    )


def test_physical_lines_are_taken_only_from_the_verified_selected_choices() -> None:
    f = fixture()
    access = access_fixture(f)
    result = load(f, access)
    assert result.physical_coordinates == (
        (-1, 0),
        (-0.5, 0),
        (0.5, 0),
        (1, 0),
        (2, 0),
        (3, 0),
        (3.5, 0),
        (4.5, 0),
        (5, 0),
    )
    assert result.physical_access_sha256 == access.content_sha256
    for attack in ("gap", "permission"):
        with pytest.raises(ValueError):
            load(f, access_fixture(f, attack))
    with pytest.raises(ValueError, match="PHYSICAL_SOURCE"):
        load(f, replace(access, source_json=""))


def test_reverse_terminal_self_crossing_tie_and_clipped_direction() -> None:
    f = fixture()
    raw = [xy(4), xy(2, 2), xy(4, 2), xy(2), xy(2)]
    f["rows"][4]["shape_bits"] = raw
    plan = json.loads(f["plan_json"])
    plan["endpoints"][1]["projected_bits"] = xy(3, 1)
    plan["endpoints"][1]["query_bits"] = xy(3, 1)
    core = {k: v for k, v in plan.items() if k != "plan_sha256"}
    plan["plan_sha256"] = sha(json.dumps(core, sort_keys=True, separators=(",", ":")))
    f["plan_json"] = json.dumps(plan)
    f["rows"][0]["plan_sha256"] = plan["plan_sha256"]
    f["rows"][2]["projected_bits"] = xy(3, 1)
    assert load(f).network_components[-1] == ((2, 0), (4, 2), (2, 2), (3, 1))
    f["rows"][2]["storage_segment"] = 2
    with pytest.raises(ValueError, match="PROJECTION_REPLAY"):
        load(f)
