"""Replay a saved SYNTHETIC Valhalla response; no network and no real build claims."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from a2_engine import (
    ValhallaBuild,
    ValhallaCorrelation,
    WalkConnector,
    WalkCoordinate,
    WalkResponseEnvelope,
    analyze_walk_responses,
    load_urban_config,
    load_urban_journey_rules,
    plan_walk_routes,
    prepare_walk_inputs,
)
from a2_engine.urban.valhalla import digest
from a2_engine.urban.walk import WalkAnalysisResult


def demo_plan() -> Any:
    refs = ["synthetic-proof"]
    geo = {"version": "synthetic-geometry"}
    # Minimal engine projection input; in production BACKEND validates the full AcceptedData.
    raw = {
        "schema_version": "2",
        "snapshot_id": "synthetic-snapshot",
        "qa": {"acceptance_state": "accepted"},
        "valid_period": {"start": None, "end": None},
        "provenance": {
            "graph_version": "synthetic-source-graph",
            "calendar_version": "not_collected",
            "population_version": "synthetic-population",
            "geometry_version": geo["version"],
        },
        "territories": [
            {
                "territory_id": "synthetic-city",
                "timezone": "Europe/Kaliningrad",
                "area_km2": 200,
                "source_refs": refs,
            }
        ],
        "population_cells": [
            {
                "cell_id": "cell",
                "territory_id": "synthetic-city",
                "sample_status": "included",
                "population": 100,
                "population_year": 2026,
                "population_method": "synthetic",
                "source_refs": refs,
            }
        ],
        "sample_points": [
            {
                "point_id": "p",
                "cell_id": "cell",
                "territory_id": "synthetic-city",
                "location": {"lon": 20.5, "lat": 54.7},
                "access_node_id": "pa",
                "access_distance_m": 85,
                "source_refs": refs,
            }
        ],
        "destinations": [
            {
                "destination_id": "d",
                "object_identity": "physical-object",
                "categories": ["airport"],
                "territory_id": "synthetic-city",
                "access_node_id": "da",
                "location": {"lon": 20.52, "lat": 54.71},
                "source_refs": refs,
            }
        ],
        "access_nodes": [
            {
                "node_id": key,
                "territory_id": "synthetic-city",
                "location": {"lon": lon, "lat": 54.7},
                "source_refs": refs,
            }
            for key, lon in [("pa", 20.501), ("da", 20.519)]
        ],
        "coverage_by_mode_date_category": [
            {
                "territory_ids": ["synthetic-city"],
                "categories": ["airport"],
                "modes": ["walk"],
                "valid_period": {"start": None, "end": None},
                "completeness": "full",
                "source_refs": refs,
            }
        ],
    }
    build = ValhallaBuild(
        "synthetic-snapshot",
        "synthetic-source-graph",
        "synthetic-build",
        "synthetic-valhalla-graph",
        "3.5.1",
        "synthetic-profile-v1",
        '{"walking_speed":5.1,"type":"foot","shortest":false}',
        ("synthetic-build-proof",),
    )
    connectors = [
        WalkConnector(
            build.snapshot_id,
            build.source_graph_version,
            physical,
            access,
            WalkCoordinate(*start),
            WalkCoordinate(*end),
            seconds,
            True,
            "synthetic-measured-seconds-v1",
            ("synthetic-connector-proof",),
        )
        for physical, access, start, end, seconds in [
            ("physical:point:p", "pa", (20.5, 54.7), (20.501, 54.7), 60),
            ("physical:destination:d", "da", (20.52, 54.71), (20.519, 54.7), 120),
        ]
    ]
    correlations = [
        ValhallaCorrelation(
            build,
            c.access_node_id,
            c.access_location,
            c.access_location,
            "synthetic-correlate:" + c.access_node_id,
            ("synthetic-correlation-proof",),
        )
        for c in connectors
    ]
    inputs = prepare_walk_inputs(
        raw,
        city_id="synthetic-city",
        departure_at=datetime(2026, 11, 8, 12, tzinfo=UTC),
        methodology_version=load_urban_config().methodology_version,
        config_hash="synthetic-config",
        search_parameters_id="synthetic-search",
        build=build,
        connectors=connectors,
        correlations=correlations,
    )
    return plan_walk_routes(inputs, point_ids=["p"], categories=["airport"])


def run_demo() -> WalkAnalysisResult:
    plan = demo_plan()
    request = plan.requests[0]
    raw_response = Path(__file__).with_name("synthetic_walk_response.json").read_text()
    envelope = WalkResponseEnvelope(
        request.request_id,
        "p",
        "d",
        request.build,
        digest(request.payload()),
        request.origin.correlation_id,
        request.destination.correlation_id,
        "response",
        raw_response,
        ("synthetic-response:sha256:" + digest(json.loads(raw_response)),),
        True,
        ("synthetic-minimum-time-audit",),
        (),
        ("synthetic-pure-walk-audit",),
        request.origin.correlated_location,
        request.destination.correlated_location,
        ("synthetic-actual-endpoint-audit",),
    )
    result = analyze_walk_responses(
        plan,
        [envelope],
        selected_categories={"airport": "high"},
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
    )
    assert result.analysis.journeys[0].total_seconds == 1800
    assert result.analysis.modes[0].points[0].index.value == 50
    return result


if __name__ == "__main__":
    analysis = run_demo().analysis
    print(
        json.dumps(
            {
                "synthetic": True,
                "legs_seconds": [60, 1620, 120],
                "total_seconds": analysis.journeys[0].total_seconds,
                "h_seconds": analysis.h_seconds.value,
                "index": analysis.modes[0].points[0].index.value,
                "count_status": analysis.modes[0].points[0].categories[0].count.status,
            }
        )
    )
