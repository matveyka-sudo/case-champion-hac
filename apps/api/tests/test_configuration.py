import json

import pytest
from a2_api.api.legacy import create_app
from a2_api.core.catalog import CatalogConfig, load_catalog
from a2_api.services.catalog import CatalogServices
from fastapi.testclient import TestClient
from pydantic import ValidationError


def area():
    return dict(
        area_id="demo-leningrad",
        area_version=1,
        region_ids=["RU-47"],
        city_ids=["demo:a", "demo:b", "demo:c", "demo:d"],
        demo_city_ids=["demo:a"],
        timezone="Europe/Moscow",
        basis="scenario",
        evidence_ids=[],
    )


def profile():
    return dict(
        profile_id="intercity",
        profile_version=1,
        study_area_id="demo-leningrad",
        study_area_version=1,
        metric_kind="time_decay",
        start_date="2026-11-09",
        end_date="2026-11-15",
        timezone="Europe/Moscow",
        start_times=["06:00:00"],
        destination_ids=["demo:a"],
        service_type_ids=[],
        modes=["rail", "walk"],
        rules=dict(
            max_one_way_minutes=None,
            max_transfers=2,
            max_access_walk_minutes=20,
            budget=None,
            require_step_free=False,
        ),
        weighting="uniform",
        basis="scenario",
        assumption_ids=["demo-only"],
        evidence_ids=[],
        half_life_minutes=60,
        horizon_hours=48,
    )


def test_region_catalog_is_read_from_file_without_city_count_limit(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(dict(study_areas=[area()], profiles=[profile()], task_sets=[])))
    catalog = load_catalog(path)
    client = TestClient(create_app(CatalogServices(catalog)))
    response = client.get("/api/v1/study-areas")
    assert response.status_code == 200
    assert response.json()[0]["city_ids"] == ["demo:a", "demo:b", "demo:c", "demo:d"]
    assert client.get("/api/v1/profiles").json()[0]["timezone"] == "Europe/Moscow"
    assert client.get("/api/v1/coverage", params={"bundle_id": "demo"}).status_code == 503


@pytest.mark.parametrize(
    "update",
    [
        {"study_area_id": "unknown"},
        {"study_area_version": 2},
        {"timezone": "Europe/Kaliningrad"},
    ],
)
def test_profile_cannot_reference_other_scope_or_timezone(update):
    with pytest.raises(ValidationError):
        CatalogConfig(study_areas=[area()], profiles=[profile() | update], task_sets=[])


def test_config_rejects_duplicate_profile_versions():
    with pytest.raises(ValidationError):
        CatalogConfig(study_areas=[area()], profiles=[profile(), profile()], task_sets=[])
