import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from a2_api.core.registry import ProductRegistry
from a2_api.services import DomainError
from a2_contracts.v2.api import TerritoryOption
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.requests import DeliveryCargoProfile, parse_analysis_request


def cases():
    folder = Path(__file__).resolve().parents[3] / "packages/contracts/fixtures/v2"
    return [
        json.loads((folder / (kind + "-request.json")).read_text())["payload"]
        for kind in ("urban_passenger", "intercity_passenger", "urban_cargo", "intercity_cargo")
    ]


def registry():
    return ProductRegistry(
        territories=[
            TerritoryOption(
                territory_id="demo-city",
                level="city",
                name="Калининград",
                timezone="Europe/Kaliningrad",
            ),
            TerritoryOption(
                territory_id="demo-a", level="city", name="Москва", timezone="Europe/Moscow"
            ),
            TerritoryOption(
                territory_id="demo-b", level="city", name="Новороссийск", timezone="Europe/Moscow"
            ),
        ]
    )


def test_registry_binds_only_published_methodology_without_client_coefficients():
    selected = registry()
    normalized = selected.normalize(parse_analysis_request(cases()[0]))
    assert normalized.methodology.version == "a2-methodology-v3"
    assert normalized.methodology.config_hash == selected.config_hash
    assert selected.capabilities().urban_city_names == ["Москва", "Калининград", "Новороссийск"]
    assert all(
        c.contract_available and not c.calculation_available
        for c in selected.capabilities().analyses
    )
    with pytest.raises(DomainError) as error:
        selected.normalize(
            parse_analysis_request(
                cases()[0]
                | {
                    "methodology": dict(
                        version="a2-methodology-v3", config_hash="arbitrary-user-config"
                    )
                }
            )
        )
    assert error.value.status_code == 409


def test_territory_ids_and_levels_are_resolved_instead_of_guessed_from_ui_aliases():
    selected = registry()
    for request in (cases()[0] | {"city_id": "kgd"}, cases()[1] | {"territory_level": "region"}):
        with pytest.raises(DomainError):
            selected.normalize(parse_analysis_request(request))
    assert [t.territory_id for t in selected.territories("city", "Калин")] == ["demo-city"]


def test_missing_national_catalog_is_explicit_and_never_three_fake_cities():
    selected = ProductRegistry()
    assert selected.capabilities().territory_catalog_available is False
    assert selected.capabilities().national_catalog_complete is False
    with pytest.raises(DomainError) as error:
        selected.normalize(parse_analysis_request(cases()[0]))
    assert error.value.status_code == 503


def test_traffic_is_explicit_and_missing_data_never_silently_becomes_static():
    selected = registry()
    with pytest.raises(DomainError) as error:
        selected.normalize(parse_analysis_request(cases()[0] | {"traffic_mode": "typical"}))
    assert error.value.code == "unsupported_traffic_mode"
    assert (
        parse_analysis_request(cases()[0] | {"traffic_mode": "typical"}).traffic_mode == "typical"
    )


def test_cargo_profile_must_be_registered_server_side():
    selected = registry()
    with pytest.raises(DomainError):
        selected.normalize(parse_analysis_request(cases()[2]))
    selected.register_cargo_profile(
        DeliveryCargoProfile(
            profile_id="demo-general",
            kind="delivery",
            food_class="nonfood",
            urgency="ordinary",
            temperature_requirements=dict(controlled=False, min_c=None, max_c=None),
            handling_requirements=[],
            vehicle_constraints=dict(max_height_m=None, max_weight_tonnes=None, restrictions=[]),
        )
    )
    assert selected.normalize(parse_analysis_request(cases()[2])).cargo_profile_id == "demo-general"


def accepted_snapshot(snapshot_id="demo-snapshot", start="2026-11-01", end="2026-11-30"):
    """Empty synthetic tables only; readiness is a separate declared engine capability."""
    period = {"start": start, "end": end}
    linked = {"source_refs": ["demo-evidence"]}
    return AcceptedData.model_validate(
        {
            "schema_version": "2",
            "snapshot_id": snapshot_id,
            "source_versions": {"demo-source": "demo-version"},
            "geographic_scope": ["demo-city", "demo-a", "demo-b"],
            "valid_period": period,
            "timezone": "Europe/Kaliningrad",
            "provenance": {
                "graph_version": "demo-graph",
                "geometry_version": "demo-geometry",
                "calendar_version": "demo-calendar",
                "population_version": None,
            },
            "qa": {
                "acceptance_state": "accepted",
                "accepted_by": "demo-reviewer",
                "accepted_at": "2026-11-08T10:00:00Z",
                "checks": ["synthetic_only"],
            },
            "evidence": [
                {
                    "evidence_id": "demo-evidence",
                    "source_id": "demo-source",
                    "source_version": "demo-version",
                    "url": "https://example.org/synthetic",
                    "retrieved_at": "2026-11-08T10:00:00Z",
                    "valid_period": period,
                    "license": "synthetic_only",
                    "content_hash": "demo-hash",
                    "verified": True,
                }
            ],
            "registry_evidence": [],
            "territories": [
                {
                    "territory_id": t.territory_id,
                    "level": t.level,
                    "name": t.name,
                    "timezone": t.timezone,
                    "area_km2": None,
                    "geometry": {
                        "geometry_id": "demo-geometry-" + t.territory_id,
                        "version": "demo-geometry",
                        "bbox": None,
                        **linked,
                    },
                    **linked,
                }
                for t in registry().catalog.values()
            ],
            "population_cells": [],
            "sample_points": [],
            "destinations": [],
            "access_nodes": [],
            "scheduled_services": [],
            "transfers": [],
            "freight_sites": [],
            "freight_nodes": [],
            "freight_compatibility": [],
            "pipeline_connections": [],
            "reference_edges": [],
            "minimum_transfers": [],
            "traffic_profiles": [],
            "category_ownership": [],
            "coverage_by_mode_date_category": [],
            "confirmed_absence_scope": [],
        }
    )


def registry_with(snapshots=(), **kwargs):
    return ProductRegistry(
        territories=list(registry().catalog.values()), snapshots=snapshots, **kwargs
    )


def test_engine_readiness_is_declared_per_kind_without_enabling_other_kinds():
    selected = registry_with([accepted_snapshot()], ready_kinds=["urban_passenger"])
    capabilities = {item.analysis_kind: item for item in selected.capabilities().analyses}
    assert selected.is_ready("urban_passenger") is True
    assert capabilities["urban_passenger"].calculation_available is True
    assert all(
        not item.calculation_available
        for kind, item in capabilities.items()
        if kind != "urban_passenger"
    )
    assert ProductRegistry(ready_kinds=["urban_passenger"]).is_ready("urban_passenger") is False


@pytest.mark.parametrize("explicit", [False, True])
def test_explicit_and_automatic_snapshots_both_require_scope_and_date(explicit):
    raw = accepted_snapshot().model_dump(mode="json")
    raw["geographic_scope"] = ["demo-a"]
    selected = registry_with([AcceptedData.model_validate(raw)])
    data = cases()[0] | ({"snapshot_id": "demo-snapshot"} if explicit else {})
    with pytest.raises(DomainError):
        selected.resolve_snapshot(parse_analysis_request(data))
    selected = registry_with([accepted_snapshot(start="2026-10-01", end="2026-10-31")])
    with pytest.raises(DomainError):
        selected.resolve_snapshot(parse_analysis_request(data))


@pytest.mark.parametrize("start,end", [(None, "2026-11-30"), ("2026-11-01", None), (None, None)])
def test_unknown_snapshot_period_boundaries_are_not_open_ended_validity(start, end):
    selected = registry_with([accepted_snapshot(start=start, end=end)])
    with pytest.raises(DomainError):
        selected.resolve_snapshot(
            parse_analysis_request(cases()[0] | {"snapshot_id": "demo-snapshot"})
        )


def test_snapshot_selection_uses_local_territory_day_and_filters_before_ambiguity():
    selected = registry_with(
        [
            accepted_snapshot("old", start="2026-11-08", end="2026-11-08"),
            accepted_snapshot("current", start="2026-11-09", end="2026-11-09"),
        ]
    )
    request = parse_analysis_request(cases()[0] | {"departure_at": "2026-11-08T23:15:00Z"})
    assert selected.resolve_snapshot(request).snapshot_id == "current"


def traffic_snapshot(mode="typical", requested_at=None, expires_at=None):
    requested = requested_at or datetime.fromisoformat("2026-11-09T08:15:00+02:00")
    start, end = (
        (requested.date() - timedelta(days=2)).isoformat(),
        (requested.date() + timedelta(days=2)).isoformat(),
    )
    raw = accepted_snapshot(start=start, end=end).model_dump(mode="json")
    linked = {"source_refs": ["demo-evidence"]}
    raw["access_nodes"] = [
        {
            "node_id": node,
            "territory_id": "demo-city",
            "location": {"lon": 0, "lat": 0},
            "kind": "road",
            **linked,
        }
        for node in ("demo-node-a", "demo-node-b")
    ]
    raw["reference_edges"] = [
        {
            "edge_id": "demo-edge",
            "from_node_id": "demo-node-a",
            "to_node_id": "demo-node-b",
            "mode": "road",
            "length_m": None,
            "running_time_seconds": None,
            "calibration_method": "synthetic_only",
            "geometry": None,
            **linked,
        }
    ]
    raw["coverage_by_mode_date_category"] = [
        {
            "territory_ids": ["demo-city"],
            "modes": ["private_car"],
            "categories": ["school"],
            "valid_period": raw["valid_period"],
            "completeness": "full",
            **linked,
        }
    ]
    from zoneinfo import ZoneInfo

    local = requested.astimezone(ZoneInfo("Europe/Kaliningrad"))
    dates = [requested.date() - timedelta(days=n) for n in range(5)]
    raw["traffic_profiles"] = [
        {
            "profile_id": "demo-traffic",
            "directed_edge_id": "demo-edge",
            "graph_version": "demo-graph",
            "traffic_mode": mode,
            "status": "available",
            "speed_kph": 30,
            "is_closed": False,
            "weekday_type": "synthetic_day_type",
            "local_15min_bin": local.hour * 4 + local.minute // 15,
            "season": "synthetic_season",
            "observed_period": {"start": dates[-1].isoformat(), "end": dates[0].isoformat()},
            "observations": [
                {
                    "date": day.isoformat(),
                    "weekday_type": "synthetic_day_type",
                    "season": "synthetic_season",
                    "graph_version": "demo-graph",
                }
                for day in dates
            ],
            "sample_count": 5,
            "observed_at": (requested - timedelta(minutes=1)).isoformat()
            if mode == "live"
            else None,
            "valid_until": expires_at.isoformat() if expires_at else None,
            "method": "synthetic_only",
            "confidence": None,
            **linked,
        }
    ]
    return AcceptedData.model_validate(raw)


def test_typical_traffic_requires_matching_snapshot_time_and_declared_coverage():
    snapshot = traffic_snapshot()
    selected = registry_with([snapshot], supported_traffic=["static", "typical"])
    request = parse_analysis_request(
        cases()[0]
        | {
            "passenger_mode": "private_car",
            "traffic_mode": "typical",
            "departure_at": "2026-11-09T08:15:00+02:00",
        }
    )
    assert selected.resolve_snapshot(request).snapshot_id == snapshot.snapshot_id
    bad = snapshot.model_dump(mode="json")
    bad["traffic_profiles"][0]["local_15min_bin"] = 0
    with pytest.raises(DomainError):
        registry_with(
            [AcceptedData.model_validate(bad)], supported_traffic=["static", "typical"]
        ).resolve_snapshot(request)
    bad["traffic_profiles"][0]["local_15min_bin"] = 33
    bad["coverage_by_mode_date_category"] = []
    with pytest.raises(DomainError):
        registry_with(
            [AcceptedData.model_validate(bad)], supported_traffic=["static", "typical"]
        ).resolve_snapshot(request)


def test_live_cache_deadline_comes_from_matched_profile_and_expired_live_is_rejected():
    now = datetime.now(UTC)
    requested = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
    expires = now + timedelta(hours=1)
    snapshot = traffic_snapshot("live", requested_at=requested, expires_at=expires)
    selected = registry_with([snapshot], supported_traffic=["static", "live"])
    request = parse_analysis_request(
        cases()[0]
        | {
            "passenger_mode": "private_car",
            "traffic_mode": "live",
            "departure_at": requested.isoformat(),
        }
    )
    assert selected.required_cache_valid_until(request, snapshot) == expires
    expired = traffic_snapshot(
        "live",
        requested_at=requested - timedelta(hours=2),
        expires_at=requested - timedelta(hours=1),
    )
    with pytest.raises(DomainError):
        selected.required_cache_valid_until(request, expired)


def test_capabilities_expose_snapshot_dates_and_versions_without_claiming_route_readiness():
    snapshot = accepted_snapshot()
    capabilities = registry_with([snapshot]).capabilities()
    metadata = capabilities.snapshots[0]
    assert metadata.snapshot_id == snapshot.snapshot_id
    assert metadata.territory_ids == snapshot.geographic_scope
    assert metadata.valid_from.isoformat() == "2026-11-01"
    assert metadata.valid_to.isoformat() == "2026-11-30"
    assert metadata.graph_version == snapshot.provenance.graph_version
    assert metadata.calendar_version == snapshot.provenance.calendar_version
    assert all(not item.calculation_available for item in capabilities.analyses)


def test_capabilities_keep_unknown_snapshot_date_boundary_null():
    metadata = registry_with([accepted_snapshot(end=None)]).capabilities().snapshots[0]
    assert metadata.model_dump(mode="json")["valid_to"] is None


def test_capabilities_publish_configured_area_ids_without_inventing_districts():
    from a2_contracts.v2.api import AreaOption

    selected = registry()
    selected.areas["synthetic-area"] = AreaOption(
        area_id="synthetic-area", city_id="demo-city", name="Synthetic district"
    )
    assert selected.capabilities().areas[0].area_id == "synthetic-area"
    assert ProductRegistry().capabilities().areas == []
