"""Synthetic snapshot/coverage attacks on the existing-formula point projection."""

import hashlib
import json
import struct
from dataclasses import replace
from datetime import UTC, datetime
from fractions import Fraction
from typing import Any

import pytest
from a2_engine.shared.config import load_urban_config
from a2_engine.shared.scoring import category_index
from a2_engine.urban.valhalla.codec import digest
from a2_engine.urban.walk.canonical_point import (
    load_reviewed_common_access_model,
    project_canonical_airport_point,
)
from a2_engine.urban.walk.physical_certificate import (
    DesignatedAccessCandidate,
    DesignatedAccessReceipt,
    DesignatedAccessResult,
    PhysicalAccessChoice,
)
from a2_engine.urban.walk.source_bound import NativeWalkCandidate


def sha(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def fixture() -> dict[str, Any]:
    old, new, city = "source-snapshot", "derivative-snapshot", "city"
    point = {
        "point_id": "p",
        "cell_id": "cell",
        "territory_id": city,
        "location": {"lon": 0.0, "lat": 0.0},
        "access_node_id": "access-p",
        "access_distance_m": 1.0,
        "source_refs": ["point-source"],
    }
    destination = {
        "destination_id": "d",
        "object_identity": "airport-object",
        "categories": ["airport"],
        "territory_id": city,
        "location": {"lon": 2.0, "lat": 2.0},
        "access_node_id": "access-d",
        "source_refs": ["official"],
    }
    cell = {
        "cell_id": "cell",
        "territory_id": city,
        "population": 10.0,
        "population_year": 2025,
        "population_method": "synthetic-estimate",
        "sample_status": "included",
        "source_refs": ["population-source"],
    }
    nodes = [
        {
            "node_id": "access-p",
            "territory_id": city,
            "location": {"lon": 0.1, "lat": 0.0},
            "source_refs": ["access"],
        },
        {
            "node_id": "access-d",
            "territory_id": city,
            "location": {"lon": 2.1, "lat": 2.0},
            "source_refs": ["access"],
        },
    ]
    territory = {
        "territory_id": city,
        "timezone": "UTC",
        "area_km2": 224.668,
        "geometry": {"version": "boundary"},
        "source_refs": ["area-source"],
    }
    period = {"start": "2026-11-10", "end": "2026-11-10"}
    accepted = {
        "schema_version": "2",
        "snapshot_id": new,
        "qa": {"acceptance_state": "accepted"},
        "valid_period": {"start": "2026-11-01", "end": "2026-11-30"},
        "provenance": {
            "graph_version": "source-graph",
            "geometry_version": "geometry",
            "calendar_version": "none",
            "population_version": "population",
        },
        "territories": [territory],
        "sample_points": [point, {**point, "point_id": "other", "cell_id": "other-cell"}],
        "population_cells": [cell, {**cell, "cell_id": "other-cell", "population": 90.0}],
        "destinations": [destination],
        "access_nodes": nodes,
        "coverage_by_mode_date_category": [
            {
                "territory_ids": [city],
                "modes": ["walk"],
                "categories": ["airport"],
                "valid_period": period,
                "completeness": "full",
                "source_refs": ["official"],
            }
        ],
        "evidence": [
            {
                "evidence_id": "official",
                "verified": True,
                "url": "https://official.invalid/airports",
                "content_hash": "d" * 64,
                "valid_period": period,
            }
        ],
    }
    build = {
        "snapshot_id": old,
        "build_id": "build",
        "engine_version": "3.5.1",
        "graph_version": "native-graph",
        "source_graph_version": "source-graph",
        "profile_version": "static-five",
        "pedestrian_options_json": '{"walking_speed":5,"type":"foot"}',
        "evidence_refs": ["build-source"],
    }
    connectors: list[dict[str, Any]] = [
        {
            "physical_node_id": "physical:point:p",
            "access_node_id": "access-p",
            "physical_location": point["location"],
            "access_location": nodes[0]["location"],
        },
        {
            "physical_node_id": "physical:destination:d",
            "access_node_id": "access-d",
            "physical_location": destination["location"],
            "access_location": nodes[1]["location"],
        },
    ]
    artifact = {
        "build": build,
        "point_ids": ["p"],
        "destination_ids": ["d"],
        "connectors": connectors,
    }
    artifact_json = json.dumps(artifact)
    choices = []
    raw_sides = []
    for side, seconds in ((0, 1), (1, 2)):
        connector = connectors[side]
        loc = connector["access_location"]
        lon_bits, lat_bits = [
            int.from_bytes(struct.pack("<d", loc[axis]), "little") for axis in ("lon", "lat")
        ]
        choice = PhysicalAccessChoice(
            side,
            0,
            10,
            Fraction(side),
            lon_bits,
            lat_bits,
            Fraction(seconds),
            Fraction(),
            connector["physical_node_id"],
            connector["access_node_id"],
            ("synthetic-source",),
        )
        choices.append(choice)
        raw_sides.append(
            {
                "native_alternatives": [
                    {
                        "endpoint": side,
                        "index": 0,
                        "edge_id": 10,
                        "percent": str(side),
                        "lon_bits": lon_bits,
                        "lat_bits": lat_bits,
                        "external_physical_connector": {"seconds_binary64_ratio": str(seconds)},
                        "access_to_native_offset": {"seconds_binary64_ratio": "0"},
                        "physical_node_id": connector["physical_node_id"],
                        "access_node_id": connector["access_node_id"],
                        "evidence_refs": ["synthetic-source"],
                    }
                ]
            }
        )
    raw_access = {
        "scope_id": "model-scope",
        "source_scope_id": "a" * 64,
        "native_export_sha256": "b" * 64,
        "snapshot_id": old,
        "artifact_sha256": sha(artifact_json),
        "pbf_sha256": "c" * 64,
        "endpoints": raw_sides,
        "limitations": ["Synthetic model."],
    }
    access_json = json.dumps(raw_access)
    access = DesignatedAccessReceipt(
        sha(access_json),
        "model-scope",
        "a" * 64,
        "b" * 64,
        tuple(choices),
        ("Synthetic model.",),
        access_json,
    )
    one = int.from_bytes(struct.pack("<f", 1), "little")
    path = NativeWalkCandidate(0, 0, ((10, one),), Fraction(2), (Fraction(2),), (Fraction(),))
    candidate = DesignatedAccessCandidate(
        access.content_sha256, "plan", path, Fraction(1), Fraction(2), Fraction(5)
    )
    minimum = DesignatedAccessResult(
        access.scope_id, Fraction(5), Fraction(5), True, candidate, access.limitations
    )
    policy = {
        "schema": "a2-canonical-access-policy-replay-v1",
        "snapshot_id": old,
        "source_graph_version": "source-graph",
        "walk_rules": "osm-walk-network-v1",
        "metric_crs": "EPSG:32634",
        "tie_tolerance_m": 0.001,
        "implementation_sha256": {"access.py": "e" * 64},
        "endpoint_replays": [
            {
                "physical_node_id": c["physical_node_id"],
                "selected_access_node_id": c["access_node_id"],
                "source_coordinates_replayed": c["access_location"],
            }
            for c in connectors
        ],
    }
    official = {
        "review_date": "2026-11-10",
        "sources": [{"url": "https://official.invalid/airports", "sha256": "d" * 64}],
    }
    compatibility = {
        "schema": "a2-native-snapshot-compatibility-v1",
        "source_snapshot_id": old,
        "original_snapshot_id": old,
        "target_snapshot_id": new,
        "derivative_snapshot_id": new,
        "target_canonical_content_sha256": digest(accepted),
        "native_artifact_sha256": sha(artifact_json),
        "native_build_identity_preserved": build,
        "native_artifact_relabelled": False,
        "all_real_entrances_claimed": False,
        "source_physical_receipt_sha256": access.content_sha256,
        "canonical_access_policy_replay_sha256": sha(json.dumps(policy)),
        "official_closure_source_manifest_sha256": sha(json.dumps(official)),
        "graph_version": "source-graph",
        "osm_source_version": {"checksum": {"value": "c" * 64}},
        "official_roster_full_only_date": "2026-11-10",
        "official_closure_source_refs": ["official"],
        "selected_pair_invariants": {
            "point": {
                "point_id": "p",
                "cell_id": "cell",
                "lon": 0.0,
                "lat": 0.0,
                "access_node_id": "access-p",
            },
            "destination": {
                "destination_id": "d",
                "serving_city_id": city,
                "lon": 2.0,
                "lat": 2.0,
                "access_node_id": "access-d",
                "name": "Synthetic airport",
            },
            "original_population_cell": dict(cell),
            "administrative_city": {
                "area_km2": 224.668,
                "timezone": "UTC",
                "boundary_version": "boundary",
            },
        },
    }
    return {
        "accepted": accepted,
        "source_artifact_json": artifact_json,
        "access": access,
        "minimum": minimum,
        "compatibility": compatibility,
        "policy": policy,
        "official": official,
    }


def project(
    data: dict[str, Any], at: datetime = datetime(2026, 11, 10, 12, tzinfo=UTC), **overrides: Any
) -> Any:
    compatibility = json.dumps(data["compatibility"])
    policy, official = json.dumps(data["policy"]), json.dumps(data["official"])
    params: dict[str, Any] = {
        "compatibility_json": compatibility,
        "compatibility_sha256": sha(compatibility),
        "policy_json": policy,
        "policy_sha256": sha(policy),
        "official_sources_json": official,
        "official_sources_sha256": sha(official),
        "requested_at": at,
        "selected_categories": {"airport": "medium"},
        "config": load_urban_config(),
        "config_hash": "configuration",
    }
    params.update(overrides)
    return project_canonical_airport_point(
        data["accepted"], data["source_artifact_json"], data["access"], data["minimum"], **params
    )


def test_explicit_derivative_uses_existing_formula_and_preserves_source_identity() -> None:
    data = fixture()
    result = project(data)
    assert result.context.versions.snapshot_id == "derivative-snapshot"
    assert result.source_proof_snapshot_id == "source-snapshot"
    assert json.loads(data["source_artifact_json"])["build"]["snapshot_id"] == "source-snapshot"
    assert result.h_seconds.value == 1800 and result.minimum_seconds.value == 5
    assert result.category_index == category_index(5, 1800)
    assert result.point_index == result.category_index and result.completeness == "partial"
    assert (
        result.city_index.value is None and not result.ranking_eligible and result.geometry is None
    )
    assert result.uncomputed_point_ids == ("other",) and result.point.population == 10
    assert "CANONICAL_ACCESS_MODEL" in result.limitations


@pytest.mark.parametrize("day", [9, 11])
def test_observed_catalog_does_not_extend_to_other_days(day: int) -> None:
    data = fixture()
    result = project(data, datetime(2026, 11, day, 12, tzinfo=UTC))
    assert result.category_index.value is None and result.minimum_seconds.value is None
    assert result.best_known_model_seconds == 5 and result.point_index.value is None
    assert not result.minimum_proven_in_model and result.city_index.value is None
    assert result.model_profile_id != project(data).model_profile_id


def test_city_timezone_controls_observation_day_and_missing_categories_block_point() -> None:
    data = fixture()
    data["accepted"]["territories"][0]["timezone"] = "Europe/Kaliningrad"
    data["compatibility"]["selected_pair_invariants"]["administrative_city"]["timezone"] = (
        "Europe/Kaliningrad"
    )
    data["compatibility"]["target_canonical_content_sha256"] = digest(data["accepted"])
    assert project(data, datetime(2026, 11, 9, 22, 30, tzinfo=UTC)).category_index.value is not None
    assert project(data, datetime(2026, 11, 10, 22, 30, tzinfo=UTC)).category_index.value is None
    result = project(data, selected_categories={"airport": "medium", "school": "high"})
    assert result.category_index.value is not None and result.point_index.value is None


@pytest.mark.parametrize(
    "attack", ["target", "point", "population", "area", "official", "source_relabel", "time"]
)
def test_snapshot_source_scope_or_model_time_changes_are_rejected(attack: str) -> None:
    data = fixture()
    if attack == "target":
        data["accepted"]["snapshot_id"] = "wrong-target"
    elif attack == "point":
        data["accepted"]["sample_points"][0]["location"] = {"lon": 3.0, "lat": 3.0}
    elif attack == "population":
        data["accepted"]["population_cells"][0]["population"] = 999
    elif attack == "area":
        data["accepted"]["territories"][0]["area_km2"] = 100
    elif attack == "official":
        data["accepted"]["evidence"][0]["verified"] = False
    elif attack == "source_relabel":
        artifact = json.loads(data["source_artifact_json"])
        artifact["build"]["snapshot_id"] = "derivative-snapshot"
        data["source_artifact_json"] = json.dumps(artifact)
    else:
        data["minimum"] = replace(
            data["minimum"], lower_seconds=Fraction(1), upper_seconds=Fraction(1)
        )
    if attack not in ("target", "source_relabel"):
        data["compatibility"]["target_canonical_content_sha256"] = digest(data["accepted"])
    with pytest.raises(ValueError):
        project(data)


def test_an_unproved_second_airport_prevents_nearest_category_promotion() -> None:
    data = fixture()
    data["accepted"]["destinations"].append(
        {
            **data["accepted"]["destinations"][0],
            "destination_id": "second",
            "object_identity": "second-airport",
        }
    )
    data["compatibility"]["target_canonical_content_sha256"] = digest(data["accepted"])
    assert project(data).category_index.value is None


def test_requested_mode_and_hash_tampering_cannot_reuse_the_score() -> None:
    data = fixture()
    with pytest.raises(ValueError):
        project(data, traffic_mode="typical")
    with pytest.raises(ValueError):
        project(data, passenger_mode="private_car")
    with pytest.raises(ValueError):
        project(data, compatibility_sha256="f" * 64)


@pytest.mark.parametrize("changed", ["native", "model", "physical"])
def test_reviewed_receipt_loader_requires_registered_exact_hashes(changed: str) -> None:
    hashes = {key: sha("{}") for key in ("native", "model", "physical")}
    hashes[changed] = "f" * 64
    with pytest.raises(ValueError, match="CONTENT_CHANGED"):
        load_reviewed_common_access_model(
            "{}",
            "{}",
            "{}",
            "{}",
            native_receipt_sha256=hashes["native"],
            model_receipt_sha256=hashes["model"],
            physical_receipt_sha256=hashes["physical"],
        )
