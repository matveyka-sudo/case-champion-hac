"""Explicit snapshot-compatible point projection for the reviewed access model.

Expected receipt hashes are server-reviewed registry inputs, never request fields.
This keeps the original routing proof's snapshot while deriving a new data context.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from fractions import Fraction
from typing import Any, Literal
from zoneinfo import ZoneInfo

from a2_engine.shared.config import UrbanConfig
from a2_engine.shared.results import MetricResult
from a2_engine.shared.scoring import category_index
from a2_engine.urban.indices import point_index, select_h_seconds
from a2_engine.urban.models import (
    UrbanArea,
    UrbanContext,
    UrbanDestination,
    UrbanPoint,
    UrbanVersions,
    references,
    utc_timestamp,
)
from a2_engine.urban.valhalla.codec import digest
from a2_engine.urban.valhalla.models import WalkCoordinate
from a2_engine.urban.walk.inputs import prepare_walk_inputs
from a2_engine.urban.walk.physical_certificate import (
    DesignatedAccessCandidate,
    DesignatedAccessReceipt,
    DesignatedAccessResult,
    load_designated_access_receipt_for_verified_roster,
)
from a2_engine.urban.walk.source_bound import NativeEndpoint, NativeWalkCandidate


@dataclass(frozen=True)
class CanonicalWalkPointResult:
    context: UrbanContext
    source_proof_snapshot_id: str
    point: UrbanPoint
    destination: UrbanDestination
    area: UrbanArea
    point_location: WalkCoordinate
    destination_location: WalkCoordinate
    destination_name: str
    h_seconds: MetricResult
    minimum_seconds: MetricResult
    best_known_model_seconds: float
    category_index: MetricResult
    point_index: MetricResult
    city_index: MetricResult
    completeness: Literal["partial"]
    minimum_proven_in_model: bool
    model_profile_id: str
    evidence_refs: tuple[str, ...]
    limitations: tuple[str, ...]
    uncomputed_point_ids: tuple[str, ...]

    @property
    def geometry(self) -> None:
        return None

    @property
    def ranking_eligible(self) -> bool:
        return False


def _checked_json(content: str, expected_sha256: str, name: str) -> Mapping[str, Any]:
    if hashlib.sha256(content.encode()).hexdigest() != expected_sha256:
        raise ValueError(name + "_CONTENT_CHANGED")
    body = json.loads(content)
    if not isinstance(body, dict):
        raise ValueError(name + "_OBJECT_REQUIRED")
    return body


def _active(period: Mapping[str, Any], day: date) -> bool:
    return (period["start"] is None or date.fromisoformat(period["start"]) <= day) and (
        period["end"] is None or day <= date.fromisoformat(period["end"])
    )


def load_reviewed_common_access_model(
    native_receipt_json: str,
    model_receipt_json: str,
    physical_receipt_json: str,
    source_artifact_json: str,
    *,
    native_receipt_sha256: str,
    model_receipt_sha256: str,
    physical_receipt_sha256: str,
) -> tuple[DesignatedAccessReceipt, DesignatedAccessResult]:
    """Fast replay of a registered, independently reviewed receipt attestation.

    Hashes must come from the trusted deployment registry. Supplying self-selected
    hashes does not establish source authority. Deployment also verifies the
    native receipt's recorded files; full proof replay remains available offline.
    """
    native = _checked_json(native_receipt_json, native_receipt_sha256, "REVIEWED_NATIVE_RECEIPT")
    model = _checked_json(model_receipt_json, model_receipt_sha256, "REVIEWED_PHYSICAL_MODEL")
    raw_access = _checked_json(
        physical_receipt_json, physical_receipt_sha256, "REVIEWED_PHYSICAL_ACCESS"
    )
    artifact = json.loads(source_artifact_json)
    binding = native["closed_turn_certificate"]["source_binding"]
    scope = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
    if (
        scope != native["closed_turn_certificate"]["source_scope_id"]
        or scope != model["source_scope_id"]
        or model["native_receipt_sha256"] != native_receipt_sha256
        or model["physical_receipt_sha256"] != physical_receipt_sha256
        or hashlib.sha256(source_artifact_json.encode()).hexdigest() != binding["artifact.json"]
    ):
        raise ValueError("REVIEWED_MODEL_RECEIPTS_NOT_CROSS_BOUND")
    if (
        native["result"]["network_minimum_proven"] is not True
        or native["result"] != native["closed_turn_certificate"]["result"]
        or Fraction(native["result"]["network_lower_seconds"])
        != Fraction(native["result"]["network_upper_seconds"])
    ):
        raise ValueError("REVIEWED_EXACT_NETWORK_CERTIFICATE_REQUIRED")
    endpoints = tuple(
        NativeEndpoint(
            row["endpoint"],
            row["index"],
            row["edge_id"],
            Fraction(row["percent"]),
            row["lon_bits"],
            row["lat_bits"],
            Fraction(row["partial_seconds"]),
        )
        for row in native["native_endpoint_inventory"]
    )
    if "osm-geofabrik-pbf-" + raw_access["pbf_sha256"] not in artifact["build"]["evidence_refs"]:
        raise ValueError("REVIEWED_ACCESS_PBF_BUILD_MISMATCH")
    access = load_designated_access_receipt_for_verified_roster(
        physical_receipt_json,
        expected_sha256=physical_receipt_sha256,
        native_export_sha256=binding["export.jsonl"],
        native_endpoints=endpoints,
        expected_bindings={
            "source_scope_id": scope,
            "artifact_sha256": binding["artifact.json"],
            "request_sha256": binding["request.json"],
            "profile_sha256": binding["walking-profile.json"],
            "pbf_sha256": raw_access["pbf_sha256"],
        },
        artifact=artifact,
    )
    result = model["result"]
    candidate_row = result["candidate"]
    path_row = candidate_row["network_path"]
    if path_row != native["result"]["candidate"]:
        raise ValueError("REVIEWED_COMMON_WEIGHT_PATH_MISMATCH")
    path = NativeWalkCandidate(
        path_row["origin_index"],
        path_row["destination_index"],
        tuple((edge, bits) for edge, bits in path_row["traversals"]),
        Fraction(path_row["lower_seconds"]),
        tuple(Fraction(s) for s in path_row["edge_seconds"]),
        tuple(Fraction(s) for s in path_row["lower_turn_seconds"]),
    )
    origins = {c.seconds for c in access.choices if c.endpoint == 0}
    destinations = {c.seconds for c in access.choices if c.endpoint == 1}
    if len(origins) != 1 or len(destinations) != 1:
        raise ValueError("REVIEWED_COMMON_WEIGHT_CERTIFICATE_REQUIRED")
    origin, destination = next(iter(origins)), next(iter(destinations))
    total = path.lower_seconds + origin + destination
    if (
        sum(path.edge_seconds + path.lower_turn_seconds, Fraction()) != path.lower_seconds
        or path.lower_seconds < 0
        or result["minimum_in_declared_scope"] is not True
        or result["declared_scope_id"] != access.scope_id
        or Fraction(result["lower_seconds"]) != total
        or Fraction(result["upper_seconds"]) != total
        or candidate_row["receipt_sha256"] != access.content_sha256
        or Fraction(candidate_row["origin_external_seconds"]) != origin
        or Fraction(candidate_row["destination_external_seconds"]) != destination
        or Fraction(candidate_row["total_seconds"]) != total
    ):
        raise ValueError("REVIEWED_MODEL_EXACT_TOTAL_MISMATCH")
    candidate = DesignatedAccessCandidate(
        access.content_sha256, candidate_row["plan_sha256"], path, origin, destination, total
    )
    return access, DesignatedAccessResult(
        access.scope_id, total, total, True, candidate, access.limitations
    )


def project_canonical_airport_point(
    accepted: Mapping[str, Any],
    source_artifact_json: str,
    access: DesignatedAccessReceipt,
    minimum: DesignatedAccessResult,
    *,
    compatibility_json: str,
    compatibility_sha256: str,
    policy_json: str,
    policy_sha256: str,
    official_sources_json: str,
    official_sources_sha256: str,
    requested_at: datetime,
    selected_categories: Mapping[str, str],
    config: UrbanConfig,
    config_hash: str,
    passenger_mode: str = "walk",
    traffic_mode: str = "static",
) -> CanonicalWalkPointResult:
    """Project a verified model minimum only after target catalog/source checks.

    ``access`` and ``minimum`` must be obtained from full engine verification or a
    registered exact-hash reviewed receipt. This method verifies their bindings;
    it does not elevate arbitrary deserialized booleans to source authority.
    """
    if passenger_mode != "walk" or traffic_mode != "static":
        raise ValueError("CERTIFIED_MODEL_REQUIRES_STATIC_WALK")
    if (
        config.methodology_version != "a2-methodology-v3"
        or "walk" not in config.modes
        or "airport" not in config.categories
    ):
        raise ValueError("CERTIFIED_MODEL_CONFIGURATION_MISMATCH")
    if accepted["schema_version"] != "2" or accepted["qa"]["acceptance_state"] != "accepted":
        raise ValueError("ACCEPTED_DATA_V2_REQUIRED")
    if "airport" not in selected_categories:
        raise ValueError("CERTIFIED_AIRPORT_CATEGORY_NOT_SELECTED")
    compatibility = _checked_json(
        compatibility_json, compatibility_sha256, "SNAPSHOT_COMPATIBILITY"
    )
    policy = _checked_json(policy_json, policy_sha256, "CANONICAL_ACCESS_POLICY")
    official = _checked_json(
        official_sources_json, official_sources_sha256, "OFFICIAL_AIRPORT_SOURCES"
    )
    artifact = json.loads(source_artifact_json)
    build = artifact["build"]
    original = build["snapshot_id"]
    target = accepted["snapshot_id"]
    if compatibility["schema"] != "a2-native-snapshot-compatibility-v1" or (
        compatibility["source_snapshot_id"],
        compatibility["original_snapshot_id"],
        compatibility["target_snapshot_id"],
        compatibility["derivative_snapshot_id"],
    ) != (original, original, target, target):
        raise ValueError("EXPLICIT_SOURCE_TARGET_SNAPSHOT_COMPATIBILITY_REQUIRED")
    if digest(accepted) != compatibility["target_canonical_content_sha256"]:
        raise ValueError("TARGET_CANONICAL_PAYLOAD_CHANGED")
    if (
        hashlib.sha256(source_artifact_json.encode()).hexdigest()
        != compatibility["native_artifact_sha256"]
        or compatibility["native_build_identity_preserved"] != build
        or compatibility["native_artifact_relabelled"] is not False
        or compatibility["all_real_entrances_claimed"] is not False
    ):
        raise ValueError("ORIGINAL_NATIVE_PROOF_IDENTITY_CHANGED")
    if (
        not access.source_json
        or access.content_sha256 != compatibility["source_physical_receipt_sha256"]
    ):
        raise ValueError("SOURCE_BOUND_PHYSICAL_RECEIPT_REQUIRED")
    raw_access = json.loads(access.source_json)
    if (
        raw_access["snapshot_id"] != original
        or raw_access["artifact_sha256"] != compatibility["native_artifact_sha256"]
        or minimum.declared_scope_id != access.scope_id
        or minimum.candidate.receipt_sha256 != access.content_sha256
        or minimum.minimum_in_declared_scope is not True
        or minimum.lower_seconds != minimum.upper_seconds
    ):
        raise ValueError("VERIFIED_PHYSICAL_MODEL_MINIMUM_REQUIRED")
    choices = {(c.endpoint, c.index): c for c in access.choices}
    path = minimum.candidate.network_path
    if (
        minimum.candidate.origin_external_seconds != choices[0, path.origin_index].seconds
        or minimum.candidate.destination_external_seconds
        != choices[1, path.destination_index].seconds
        or minimum.lower_seconds
        != path.lower_seconds
        + minimum.candidate.origin_external_seconds
        + minimum.candidate.destination_external_seconds
    ):
        raise ValueError("PHYSICAL_MODEL_TIME_BINDING_MISMATCH")
    if (
        compatibility["canonical_access_policy_replay_sha256"] != policy_sha256
        or compatibility["official_closure_source_manifest_sha256"] != official_sources_sha256
        or policy["schema"] != "a2-canonical-access-policy-replay-v1"
        or policy["snapshot_id"] != original
        or policy["walk_rules"] != "osm-walk-network-v1"
        or policy["metric_crs"] != "EPSG:32634"
        or policy["tie_tolerance_m"] != 0.001
        or not policy["implementation_sha256"]
    ):
        raise ValueError("CANONICAL_ACCESS_MODEL_POLICY_NOT_BOUND")
    if (
        accepted["provenance"]["graph_version"] != build["source_graph_version"]
        or compatibility["graph_version"] != build["source_graph_version"]
        or policy["source_graph_version"] != build["source_graph_version"]
        or compatibility["osm_source_version"]["checksum"]["value"] != raw_access["pbf_sha256"]
    ):
        raise ValueError("DERIVATIVE_ROUTING_SOURCE_CHANGED")
    facts = compatibility["selected_pair_invariants"]
    point_id = facts["point"]["point_id"]
    destination_id = facts["destination"]["destination_id"]
    city_id = facts["destination"]["serving_city_id"]
    if artifact["point_ids"] != [point_id] or artifact["destination_ids"] != [destination_id]:
        raise ValueError("DERIVATIVE_SUBJECT_SCOPE_CHANGED")
    point_rows = [p for p in accepted["sample_points"] if p["point_id"] == point_id]
    destination_rows = [
        d for d in accepted["destinations"] if d["destination_id"] == destination_id
    ]
    cities = [c for c in accepted["territories"] if c["territory_id"] == city_id]
    if len(point_rows) != 1 or len(destination_rows) != 1 or len(cities) != 1:
        raise ValueError("DERIVATIVE_SUBJECT_NOT_UNIQUE")
    point_row, destination_row, city = point_rows[0], destination_rows[0], cities[0]
    for row, fact in ((point_row, facts["point"]), (destination_row, facts["destination"])):
        if (
            row["territory_id"] != city_id
            or row["location"] != {"lon": fact["lon"], "lat": fact["lat"]}
            or row["access_node_id"] != fact["access_node_id"]
        ):
            raise ValueError("DERIVATIVE_PHYSICAL_OR_ACCESS_LOCATION_CHANGED")
    connectors = {c["physical_node_id"]: c for c in artifact["connectors"]}
    for side, row, kind, identifier in (
        (0, point_row, "point", point_id),
        (1, destination_row, "destination", destination_id),
    ):
        source = connectors[f"physical:{kind}:{identifier}"]
        if (
            source["physical_location"] != row["location"]
            or source["access_node_id"] != row["access_node_id"]
        ):
            raise ValueError("SOURCE_DERIVATIVE_CONNECTOR_BINDING_CHANGED")
        nodes = [n for n in accepted["access_nodes"] if n["node_id"] == row["access_node_id"]]
        replayed = [
            r
            for r in policy["endpoint_replays"]
            if r["physical_node_id"] == source["physical_node_id"]
        ]
        if (
            len(nodes) != 1
            or len(replayed) != 1
            or nodes[0]["location"] != source["access_location"]
            or replayed[0]["selected_access_node_id"] != row["access_node_id"]
            or replayed[0]["source_coordinates_replayed"] != nodes[0]["location"]
        ):
            raise ValueError("CANONICAL_ACCESS_POLICY_REPLAY_CHANGED")
        if any(
            c.physical_node_id != source["physical_node_id"]
            or c.access_node_id != row["access_node_id"]
            for c in access.choices
            if c.endpoint == side
        ):
            raise ValueError("PHYSICAL_CHOICE_SUBJECT_BINDING_CHANGED")
    cells = [c for c in accepted["population_cells"] if c["cell_id"] == point_row["cell_id"]]
    original_cell = facts["original_population_cell"]
    if len(cells) != 1 or point_row["cell_id"] != facts["point"]["cell_id"]:
        raise ValueError("ORIGINAL_POPULATION_CELL_CHANGED")
    cell = cells[0]
    if any(
        cell[key] != original_cell[key]
        for key in ("cell_id", "population", "population_year", "population_method")
    ):
        raise ValueError("ORIGINAL_CELL_POPULATION_CHANGED")
    admin = facts["administrative_city"]
    if (
        city["area_km2"] != admin["area_km2"]
        or city["timezone"] != admin["timezone"]
        or city["geometry"]["version"] != admin["boundary_version"]
    ):
        raise ValueError("ADMINISTRATIVE_AREA_OR_GEOMETRY_CHANGED")
    at = utc_timestamp(requested_at)
    day = at.astimezone(ZoneInfo(city["timezone"])).date()
    if not _active(accepted["valid_period"], day):
        raise ValueError("DERIVATIVE_SNAPSHOT_OUTSIDE_VALID_PERIOD")
    observed_day = date.fromisoformat(compatibility["official_roster_full_only_date"])
    if official["review_date"] != observed_day.isoformat():
        raise ValueError("OFFICIAL_OBSERVATION_DATE_CHANGED")
    official_refs = frozenset(compatibility["official_closure_source_refs"])
    source_captures = {(s["url"], s["sha256"]) for s in official["sources"]}
    if not official_refs or not source_captures:
        raise ValueError("OFFICIAL_CATALOG_SOURCE_REFERENCES_REQUIRED")
    evidence = {e["evidence_id"]: e for e in accepted["evidence"]}
    for ref in official_refs:
        row = evidence.get(ref)
        if (
            row is None
            or row["verified"] is not True
            or (row["url"], row["content_hash"]) not in source_captures
        ):
            raise ValueError("OFFICIAL_CATALOG_SOURCE_IDENTITY_UNVERIFIED")
    catalog_ids = {
        d["destination_id"]
        for d in accepted["destinations"]
        if d["territory_id"] == city_id and "airport" in d["categories"]
    }
    complete = (
        day == observed_day
        and catalog_ids == {destination_id}
        and official_refs.issubset(destination_row["source_refs"])
        and any(
            scope["completeness"] == "full"
            and city_id in scope["territory_ids"]
            and "walk" in scope["modes"]
            and "airport" in scope["categories"]
            and _active(scope["valid_period"], day)
            and official_refs.issubset(scope["source_refs"])
            for scope in accepted["coverage_by_mode_date_category"]
        )
    )
    complete = complete and all(
        _active(evidence[ref]["valid_period"], day) for ref in official_refs
    )
    parameters = {
        "methodology_version": config.methodology_version,
        "area_bands": [(b.max_area_km2, b.h_seconds) for b in config.area_h_bands],
        "weights": dict(config.priority_weights),
        "categories": sorted(config.categories),
        "modes": sorted(config.modes),
        "low_below": config.low_accessibility_below,
    }
    model_id = digest(
        [
            compatibility_sha256,
            policy_sha256,
            official_sources_sha256,
            access.content_sha256,
            access.native_scope_id,
            target,
            digest(accepted),
            config_hash,
            parameters,
            city["area_km2"],
            point_id,
            destination_id,
            day.isoformat(),
            sorted(selected_categories.items()),
        ]
    )
    prepared = prepare_walk_inputs(
        accepted,
        city_id=city_id,
        departure_at=at,
        methodology_version=config.methodology_version,
        config_hash=config_hash,
        search_parameters_id="canonical-access-model:" + model_id,
    )
    point = next(p for p in prepared.points if p.point_id == point_id)
    destination = next(d for d in prepared.destinations if d.destination_id == destination_id)
    versions = UrbanVersions(
        target,
        build["graph_version"],
        accepted["provenance"]["calendar_version"],
        accepted["provenance"]["population_version"],
        accepted["provenance"]["geometry_version"],
        config.methodology_version,
        config_hash,
        "static:" + build["build_id"],
    )
    context = UrbanContext(
        city_id, city_id, at, "static", versions, "canonical-access-model:" + model_id
    )
    h = select_h_seconds(city["area_km2"], bands=config.area_h_bands)
    exact = float(minimum.lower_seconds)
    seconds = (
        MetricResult(exact, "available")
        if complete
        else MetricResult(None, "unknown", "CATALOG_DATE_OR_ROSTER_UNPROVEN")
    )
    score = category_index(seconds.value, h.value)
    category_values = {
        category: score
        if category == "airport"
        else MetricResult(None, "unknown", "UNCOMPUTED_CATEGORY")
        for category in selected_categories
    }
    overall = point_index(category_values, selected_categories, config=config)
    refs = references(
        (
            *point.source_refs,
            *destination.source_refs,
            *prepared.area.source_refs,
            *official_refs,
            *(ref for c in access.choices for ref in c.evidence_refs),
            "snapshot-compatibility:" + compatibility_sha256,
            "canonical-access-policy:" + policy_sha256,
            "physical-access-proof:" + access.content_sha256,
            "native-scope:" + access.native_scope_id,
        ),
        required=True,
    )
    limitations = {
        "CANONICAL_ACCESS_MODEL",
        "PARTIAL_APPROACH_MODEL_COVERAGE",
        "POINT_ONLY_SCOPE",
        "TERRITORY_INDEX_UNAVAILABLE",
    }
    if not complete:
        limitations.add("CATALOG_DATE_OR_ROSTER_UNPROVEN")
    return CanonicalWalkPointResult(
        context,
        original,
        point,
        destination,
        prepared.area,
        WalkCoordinate(**point_row["location"]),
        WalkCoordinate(**destination_row["location"]),
        facts["destination"]["name"],
        h,
        seconds,
        exact,
        score,
        overall,
        MetricResult(None, "unknown", "UNCOMPUTED_TERRITORY_POINTS"),
        "partial",
        complete,
        model_id,
        refs,
        tuple(sorted(limitations)),
        tuple(p.point_id for p in prepared.points if p.point_id != point_id),
    )
