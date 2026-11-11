"""Per-alternative physical weights inside a declared-access research proof.

This consumes source-verified ML2 receipts. It does not approve a restricted
approach policy for the product, establish category completeness, or emit an index.
"""

import hashlib
import json
import math
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from a2_engine.urban.valhalla.models import WalkCoordinate
from a2_engine.urban.walk.source_bound import (
    NativeEndpoint,
    NativeWalkCandidate,
    NativeWalkExport,
    verify_native_walk_replay,
)
from a2_engine.urban.walk.turn_certificate import (
    NativeTurnIncumbent,
    NativeTurnPlan,
    NativeTurnWeights,
    _solve_native_turn_problem,
    make_native_turn_incumbent,
    plan_native_turn_pairs,
    solve_native_turn_plan,
)


@dataclass(frozen=True)
class PhysicalAccessChoice:
    endpoint: int
    index: int
    edge_id: int
    percent: Fraction
    lon_bits: int
    lat_bits: int
    connector_seconds: Fraction
    offset_seconds: Fraction
    physical_node_id: str
    access_node_id: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.endpoint not in (0, 1) or type(self.index) is not int or self.index < 0:
            raise ValueError("INVALID_PHYSICAL_CHOICE_ID")
        if (
            any(
                not isinstance(value, Fraction) or value < 0
                for value in (self.connector_seconds, self.offset_seconds)
            )
            or not self.evidence_refs
        ):
            raise ValueError("EXACT_SOURCE_BOUND_PHYSICAL_WEIGHTS_REQUIRED")

    @property
    def seconds(self) -> Fraction:
        return self.connector_seconds + self.offset_seconds


@dataclass(frozen=True)
class DesignatedAccessReceipt:
    content_sha256: str
    scope_id: str
    native_scope_id: str
    native_export_sha256: str
    choices: tuple[PhysicalAccessChoice, ...]
    limitations: tuple[str, ...]
    source_json: str = ""

    def __post_init__(self) -> None:
        if not self.source_json:
            return  # Explicit finite synthetic inputs may lack source authority.
        if hashlib.sha256(self.source_json.encode()).hexdigest() != self.content_sha256:
            raise ValueError("PHYSICAL_SOURCE_CONTENT_CHANGED")
        body = json.loads(self.source_json)
        if (self.scope_id, self.native_scope_id, self.native_export_sha256, self.limitations) != (
            body["scope_id"],
            body["source_scope_id"],
            body["native_export_sha256"],
            tuple(body["limitations"]),
        ):
            raise ValueError("PHYSICAL_SOURCE_NORMALIZATION_CHANGED")
        raw = []
        for side in body["endpoints"]:
            for row in side["native_alternatives"]:
                raw.append(
                    (
                        row["endpoint"],
                        row["index"],
                        row["edge_id"],
                        Fraction(row["percent"]),
                        row["lon_bits"],
                        row["lat_bits"],
                        Fraction(row["external_physical_connector"]["seconds_binary64_ratio"]),
                        Fraction(row["access_to_native_offset"]["seconds_binary64_ratio"]),
                        row["physical_node_id"],
                        row["access_node_id"],
                        tuple(row["evidence_refs"]),
                    )
                )
        actual = [
            (
                c.endpoint,
                c.index,
                c.edge_id,
                c.percent,
                c.lon_bits,
                c.lat_bits,
                c.connector_seconds,
                c.offset_seconds,
                c.physical_node_id,
                c.access_node_id,
                c.evidence_refs,
            )
            for c in self.choices
        ]
        if sorted(raw) != sorted(actual):
            raise ValueError("PHYSICAL_SOURCE_NORMALIZATION_CHANGED")

    @property
    def weighted_scope_id(self) -> str:
        semantics = [
            (
                c.endpoint,
                c.index,
                c.edge_id,
                str(c.percent),
                c.lon_bits,
                c.lat_bits,
                str(c.connector_seconds),
                str(c.offset_seconds),
                c.physical_node_id,
                c.access_node_id,
                c.evidence_refs,
            )
            for c in self.choices
        ]
        return hashlib.sha256(
            json.dumps(
                [
                    self.native_scope_id,
                    self.content_sha256,
                    self.scope_id,
                    "research-only-designated-access",
                    semantics,
                ]
            ).encode()
        ).hexdigest()


@dataclass(frozen=True)
class DesignatedAccessPlan:
    access: DesignatedAccessReceipt
    turns: NativeTurnPlan


@dataclass(frozen=True)
class DesignatedAccessCandidate:
    receipt_sha256: str
    plan_sha256: str
    network_path: NativeWalkCandidate
    origin_external_seconds: Fraction
    destination_external_seconds: Fraction
    total_seconds: Fraction


@dataclass(frozen=True)
class DesignatedAccessResult:
    declared_scope_id: str
    lower_seconds: Fraction
    upper_seconds: Fraction
    minimum_in_declared_scope: bool
    candidate: DesignatedAccessCandidate
    limitations: tuple[str, ...]

    @property
    def full_physical_minimum_proven(self) -> bool:
        return False

    @property
    def product_index_eligible(self) -> bool:
        return False


def _coordinate(values: Any) -> WalkCoordinate:
    if not isinstance(values, list) or len(values) != 2:
        raise ValueError("PHYSICAL_GEOMETRY_COORDINATE_REQUIRED")
    return WalkCoordinate(values[0], values[1])


def _segment(
    row: Mapping[str, Any], start: WalkCoordinate, end: WalkCoordinate, speed: float
) -> Fraction:
    if (
        row["traversable"] is not True
        or row["reasons"]
        or row["no_intermediate_decimal_rounding"] is not True
    ):
        raise ValueError("SOURCE_VERIFIED_UNROUNDED_PHYSICAL_WEIGHT_REQUIRED")
    geometry = row["geometry_lonlat"]
    if len(geometry) != 2 or _coordinate(geometry[0]) != start or _coordinate(geometry[1]) != end:
        raise ValueError("PHYSICAL_GEOMETRY_ENDPOINT_MISMATCH")
    if row["geometry_crs"] != "EPSG:4326" or row["length_crs"] != "EPSG:32634":
        raise ValueError("UNSUPPORTED_PHYSICAL_GEOMETRY_MODEL")
    for field in ("length_m", "seconds"):
        value = row[field]
        if (
            isinstance(value, bool)
            or not isinstance(value, (float, int))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError("INVALID_PHYSICAL_BINARY64_WEIGHT")
        if Fraction(float(value)) != Fraction(row[field + "_binary64_ratio"]):
            raise ValueError("PHYSICAL_BINARY64_RATIO_MISMATCH")
    length, seconds = (
        Fraction(row["length_m_binary64_ratio"]),
        Fraction(row["seconds_binary64_ratio"]),
    )
    if seconds != Fraction(float(row["length_m"]) / (speed / 3.6)):
        raise ValueError("PHYSICAL_PROFILE_TIME_MISMATCH")
    if start != end and (length <= 0 or seconds <= 0):
        raise ValueError("NONZERO_APPROACH_CANNOT_HAVE_ZERO_WEIGHT")
    if row["policy"] == "EXACT_BINARY64_COORDINATE_IDENTITY":
        if start != end or length or seconds:
            raise ValueError("FALSE_EXACT_COORDINATE_IDENTITY")
    elif row["policy"] != "walk-connector-geometry-v2":
        raise ValueError("UNSUPPORTED_PHYSICAL_APPROACH_POLICY")
    return seconds


def load_designated_access_receipt_for_verified_roster(
    content: str,
    *,
    expected_sha256: str,
    native_export_sha256: str,
    native_endpoints: tuple[NativeEndpoint, ...],
    expected_bindings: Mapping[str, str],
    artifact: Mapping[str, Any],
) -> DesignatedAccessReceipt:
    digest = hashlib.sha256(content.encode()).hexdigest()
    if digest != expected_sha256:
        raise ValueError("PHYSICAL_ACCESS_RECEIPT_CHANGED")
    body = json.loads(content)
    if (
        body["schema"] != "a2-designated-physical-access-receipt-v1"
        or body["acceptance_state"] != "research_only"
        or body["scope_kind"] != "canonical_designated_access"
        or body["complete_for_declared_scope"] is not True
        or body["full_physical_entrance_roster_complete"] is not False
    ):
        raise ValueError("RESEARCH_DESIGNATED_ACCESS_SCOPE_REQUIRED")
    for key in (
        "source_scope_id",
        "profile_sha256",
        "artifact_sha256",
        "request_sha256",
        "pbf_sha256",
    ):
        if body[key] != expected_bindings[key]:
            raise ValueError("PHYSICAL_ACCESS_SOURCE_PROFILE_BINDING_MISMATCH:" + key)
    if body["native_export_sha256"] != native_export_sha256:
        raise ValueError("PHYSICAL_ACCESS_NATIVE_EXPORT_MISMATCH")
    for key in (
        "snapshot_id",
        "build_id",
        "graph_version",
        "source_graph_version",
        "profile_version",
    ):
        if body[key] != artifact["build"][key]:
            raise ValueError("PHYSICAL_ACCESS_BUILD_CONTEXT_MISMATCH")
    speed = float(json.loads(artifact["build"]["pedestrian_options_json"])["walking_speed"])
    if body["walking_speed_kmh"] != speed:
        raise ValueError("PHYSICAL_ACCESS_WALKING_PROFILE_MISMATCH")
    native = {(e.endpoint, e.index): e for e in native_endpoints}
    if len(native) != len(native_endpoints) or {e.endpoint for e in native_endpoints} != {0, 1}:
        raise ValueError("VERIFIED_NATIVE_ENDPOINT_ROSTER_REQUIRED")
    connectors = {c["physical_node_id"]: c for c in artifact["connectors"]}
    endpoint_ids = (
        "physical:point:" + artifact["point_ids"][0],
        "physical:destination:" + artifact["destination_ids"][0],
    )
    choices: dict[tuple[int, int], PhysicalAccessChoice] = {}
    seen_sides: set[int] = set()
    for side in body["endpoints"]:
        endpoint = side["endpoint"]
        if endpoint not in (0, 1) or endpoint in seen_sides:
            raise ValueError("PHYSICAL_ENDPOINT_ROSTER_MISMATCH")
        seen_sides.add(endpoint)
        physical_id = endpoint_ids[endpoint]
        accepted = connectors[physical_id]
        access_id = accepted["access_node_id"]
        if (
            side["physical_node_id"] != physical_id
            or side["access_node_id"] != access_id
            or side["declared_access_node_ids"] != [access_id]
            or side["complete_for_declared_scope"] is not True
            or side["full_physical_entrance_roster_complete"] is not False
        ):
            raise ValueError("DECLARED_CANONICAL_ENDPOINT_SCOPE_MISMATCH")
        physical = WalkCoordinate(**accepted["physical_location"])
        access = WalkCoordinate(**accepted["access_location"])
        for row in side["native_alternatives"]:
            choice_key = (row["endpoint"], row["index"])
            if choice_key not in native or choice_key in choices or choice_key[0] != endpoint:
                raise ValueError("PHYSICAL_ALTERNATIVE_ROSTER_MISMATCH")
            match = native[choice_key]
            if (
                row["edge_id"],
                Fraction(row["percent"]),
                row["lon_bits"],
                row["lat_bits"],
                Fraction(row["partial_seconds"]),
            ) != (
                match.edge_id,
                match.percent,
                match.lon_bits,
                match.lat_bits,
                match.partial_seconds,
            ):
                raise ValueError("PHYSICAL_ALTERNATIVE_NATIVE_IDENTITY_MISMATCH")
            projected = _coordinate(row["projected_coordinates"])
            if (
                int.from_bytes(struct.pack("<d", projected.lon), "little"),
                int.from_bytes(struct.pack("<d", projected.lat), "little"),
            ) != (match.lon_bits, match.lat_bits):
                raise ValueError("PHYSICAL_ALTERNATIVE_PROJECTED_COORDINATE_MISMATCH")
            if (
                row["traversable"] is not True
                or not row["evidence_refs"]
                or row["physical_node_id"] != physical_id
                or row["access_node_id"] != access_id
            ):
                raise ValueError("PHYSICAL_ALTERNATIVE_UNCONFIRMED")
            connector = _segment(row["external_physical_connector"], physical, access, speed)
            offset = _segment(row["access_to_native_offset"], access, projected, speed)
            if connector + offset != Fraction(
                row["combined_external_seconds_binary64_operands_sum"]
            ):
                raise ValueError("PHYSICAL_ALTERNATIVE_TOTAL_WEIGHT_MISMATCH")
            choices[choice_key] = PhysicalAccessChoice(
                endpoint,
                row["index"],
                match.edge_id,
                match.percent,
                match.lon_bits,
                match.lat_bits,
                connector,
                offset,
                physical_id,
                access_id,
                tuple(row["evidence_refs"]),
            )
    if seen_sides != {0, 1} or choices.keys() != native.keys() or not body["limitations"]:
        raise ValueError("PHYSICAL_ACCESS_ROSTER_INCOMPLETE")
    return DesignatedAccessReceipt(
        digest,
        body["scope_id"],
        body["source_scope_id"],
        native_export_sha256,
        tuple(choices[key] for key in sorted(choices)),
        tuple(body["limitations"]),
        content,
    )


def load_designated_access_receipt(
    content: str,
    *,
    expected_sha256: str,
    graph: NativeWalkExport,
    expected_bindings: Mapping[str, str],
    artifact: Mapping[str, Any],
) -> DesignatedAccessReceipt:
    return load_designated_access_receipt_for_verified_roster(
        content,
        expected_sha256=expected_sha256,
        native_export_sha256=graph.content_sha256,
        native_endpoints=graph.endpoints,
        expected_bindings=expected_bindings,
        artifact=artifact,
    )


def _costs(access: DesignatedAccessReceipt, side: int) -> dict[int, Fraction]:
    return {c.index: c.seconds for c in access.choices if c.endpoint == side}


def _validate_access_graph(graph: NativeWalkExport, access: DesignatedAccessReceipt) -> None:
    if (
        access.native_export_sha256 != graph.content_sha256
        or len(access.choices) != len(graph.endpoints)
        or {
            (c.endpoint, c.index, c.edge_id, c.percent, c.lon_bits, c.lat_bits)
            for c in access.choices
        }
        != {
            (e.endpoint, e.index, e.edge_id, e.percent, e.lon_bits, e.lat_bits)
            for e in graph.endpoints
        }
    ):
        raise ValueError("PHYSICAL_ALTERNATIVE_ROSTER_MISMATCH")


def plan_designated_access(
    graph: NativeWalkExport, access: DesignatedAccessReceipt, incumbent: NativeTurnIncumbent
) -> DesignatedAccessPlan:
    if (
        access.native_scope_id != incumbent.source_scope_id
        or access.native_export_sha256 != graph.content_sha256
    ):
        raise ValueError("PHYSICAL_INCUMBENT_SOURCE_PROFILE_MISMATCH")
    _validate_access_graph(graph, access)
    origins, finals = _costs(access, 0), _costs(access, 1)
    upper = (
        incumbent.upper_seconds
        + origins[incumbent.candidate.origin_index]
        + finals[incumbent.candidate.destination_index]
    )
    rebound = make_native_turn_incumbent(
        graph, access.weighted_scope_id, incumbent.candidate, incumbent.replay
    )
    turns = plan_native_turn_pairs(graph, access.weighted_scope_id, rebound, ceiling_seconds=upper)
    return DesignatedAccessPlan(access, turns)


def solve_designated_access(
    graph: NativeWalkExport, plan: DesignatedAccessPlan, weights: NativeTurnWeights
) -> DesignatedAccessCandidate:
    if plan.turns.incumbent.source_scope_id != plan.access.weighted_scope_id:
        raise ValueError("PHYSICAL_WEIGHT_PLAN_SCOPE_MISMATCH")
    _validate_access_graph(graph, plan.access)
    origins, finals = _costs(plan.access, 0), _costs(plan.access, 1)
    candidate, total = _solve_native_turn_problem(graph, plan.turns, weights, origins, finals)
    return DesignatedAccessCandidate(
        plan.access.content_sha256,
        plan.turns.fingerprint,
        candidate,
        origins[candidate.origin_index],
        finals[candidate.destination_index],
        total,
    )


def verify_designated_access_minimum(
    graph: NativeWalkExport,
    plan: DesignatedAccessPlan,
    weights: NativeTurnWeights,
    candidate: DesignatedAccessCandidate,
    replay: str | None,
) -> DesignatedAccessResult:
    if solve_designated_access(graph, plan, weights) != candidate:
        raise ValueError("PHYSICAL_MINIMUM_CANDIDATE_SUBSTITUTED")
    upper = (
        plan.turns.ceiling_seconds
        if replay is None
        else (
            verify_native_walk_replay(candidate.network_path, replay)
            + candidate.origin_external_seconds
            + candidate.destination_external_seconds
        )
    )
    if upper < candidate.total_seconds:
        raise ValueError("PHYSICAL_RELAXATION_EXCEEDS_FEASIBLE_CANDIDATE")
    return DesignatedAccessResult(
        plan.access.scope_id,
        candidate.total_seconds,
        upper,
        replay is not None and upper == candidate.total_seconds,
        candidate,
        plan.access.limitations,
    )


def verify_common_designated_access_minimum(
    graph: NativeWalkExport,
    access: DesignatedAccessReceipt,
    plan: NativeTurnPlan,
    weights: NativeTurnWeights,
    network_candidate: NativeWalkCandidate,
    replay: str,
) -> DesignatedAccessResult:
    """Verified constant-weight lemma; unequal alternatives require weighted search."""
    _validate_access_graph(graph, access)
    if access.native_scope_id != plan.incumbent.source_scope_id:
        raise ValueError("PHYSICAL_NETWORK_PROOF_SCOPE_MISMATCH")
    origins, finals = set(_costs(access, 0).values()), set(_costs(access, 1).values())
    if len(origins) != 1 or len(finals) != 1:
        raise ValueError("NONCONSTANT_PHYSICAL_ALTERNATIVES_REQUIRE_WEIGHTED_SEARCH")
    if solve_native_turn_plan(graph, plan, weights) != network_candidate:
        raise ValueError("PHYSICAL_NETWORK_MINIMUM_SUBSTITUTED")
    network_upper = verify_native_walk_replay(network_candidate, replay)
    if network_upper != network_candidate.lower_seconds:
        raise ValueError("PHYSICAL_NETWORK_MINIMUM_UNPROVEN")
    origin, destination = next(iter(origins)), next(iter(finals))
    total = origin + network_upper + destination
    candidate = DesignatedAccessCandidate(
        access.content_sha256, plan.fingerprint, network_candidate, origin, destination, total
    )
    return DesignatedAccessResult(
        access.scope_id, total, total, True, candidate, access.limitations
    )
