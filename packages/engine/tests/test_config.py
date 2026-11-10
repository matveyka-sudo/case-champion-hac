import copy
import json
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any, cast

import pytest
from a2_engine import (
    AreaHBand,
    MetricResult,
    UrbanConfig,
    category_index,
    is_low_accessibility,
    load_urban_config,
    point_index,
    select_h_seconds,
    urban_config_from_mapping,
)


def test_published_configuration(config: UrbanConfig) -> None:
    assert config.methodology_version == "a2-methodology-v3"
    assert config.priority_weights == {"low": 1, "medium": 2, "high": 3}
    assert config.low_accessibility_below == 50
    assert config.area_h_bands == (
        AreaHBand(100, 1200),
        AreaHBand(300, 1800),
        AreaHBand(1000, 2400),
        AreaHBand(None, 3600),
    )
    assert len(config.categories) == 9
    assert config.modes == {"walk", "public_transport", "private_car"}


def test_configuration_is_source_of_all_parameters(product: dict[str, Any]) -> None:
    # Synthetic configuration changes: prove runtime code has no independent defaults.
    urban = product["methodologies"]["urban_passenger"]
    product["methodology_version"] = urban["version"] = "synthetic-test-version"
    urban["parameters"]["area_h_bands"] = [
        {"max_area_km2": 5, "h_minutes": 7},
        {"max_area_km2": None, "h_minutes": 9},
    ]
    urban["parameters"]["low_accessibility_below"] = 60
    product["priority_weights"] = {"low": 2, "medium": 4, "high": 8}
    config = urban_config_from_mapping(product)
    assert config.methodology_version == "synthetic-test-version"
    assert select_h_seconds(5, bands=config.area_h_bands).value == 420
    assert select_h_seconds(6, bands=config.area_h_bands).value == 540
    assert is_low_accessibility(category_index(420, 420), threshold=config.low_accessibility_below)
    assert (
        point_index(
            {"school": MetricResult(100, "available"), "airport": MetricResult(0, "available")},
            {"school": "low", "airport": "high"},
            config=config,
        ).value
        == 20
    )


def test_config_does_not_mutate_or_alias_product(product: dict[str, Any]) -> None:
    before = copy.deepcopy(product)
    config = urban_config_from_mapping(product)
    assert product == before
    product["priority_weights"]["low"] = 100
    product["methodologies"]["urban_passenger"]["parameters"]["area_h_bands"][0]["h_minutes"] = 1
    product["categories"].clear()
    assert config.priority_weights["low"] == 1
    assert config.area_h_bands[0].h_seconds == 1200
    assert len(config.categories) == 9
    with pytest.raises(TypeError):
        cast(dict[str, float], config.priority_weights)["low"] = 42
    with pytest.raises(FrozenInstanceError):
        config.low_accessibility_below = 42  # type: ignore[misc]


def test_config_and_real_import_from_different_cwd(tmp_path: Path) -> None:
    # Test actual Python import, independently of pytest discovery and repository cwd.
    source = Path(__file__).resolve().parents[1] / "src"
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from a2_engine import load_urban_config, category_index; "
            "assert load_urban_config().methodology_version == 'a2-methodology-v3'; "
            "assert category_index(1200, 1200).value == 50",
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(source)},
        check=True,
        capture_output=True,
        text=True,
    )


def test_explicit_configuration_path(product: dict[str, Any], tmp_path: Path) -> None:
    path = tmp_path / "published.json"
    path.write_text(json.dumps(product), encoding="utf-8")
    assert load_urban_config(path) == urban_config_from_mapping(product)
    with pytest.raises(FileNotFoundError):
        load_urban_config(tmp_path / "missing.json")
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_urban_config(path)
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        load_urban_config(path)


@pytest.mark.parametrize(
    "field",
    [
        "methodologies",
        "methodology_version",
        "priority_weights",
        "priority_levels",
        "categories",
        "urban_modes",
    ],
)
def test_missing_configuration_fields(product: dict[str, Any], field: str) -> None:
    del product[field]
    with pytest.raises(ValueError, match="Missing configuration field"):
        urban_config_from_mapping(product)


@pytest.mark.parametrize(
    "field, bad",
    [
        ("priority_weights", {"low": 0, "medium": 2, "high": 3}),
        ("priority_weights", {"low": -1, "medium": 2, "high": 3}),
        ("priority_weights", {"low": float("inf"), "medium": 2, "high": 3}),
        ("priority_weights", {"low": float("nan"), "medium": 2, "high": 3}),
        ("priority_weights", {"low": "1", "medium": 2, "high": 3}),
        ("priority_weights", {"low": True, "medium": 2, "high": 3}),
        ("priority_weights", {"low": 1}),
        ("priority_weights", []),
        ("priority_levels", ["low", "low"]),
        ("categories", []),
        ("categories", ["school", "school"]),
        ("categories", [None]),
        ("urban_modes", []),
        ("methodologies", []),
    ],
)
def test_invalid_product_fields(product: dict[str, Any], field: str, bad: object) -> None:
    product[field] = bad
    with pytest.raises(ValueError):
        urban_config_from_mapping(product)


@pytest.mark.parametrize("status, version", [("pending", "a2-methodology-v3"), ("approved", "v2")])
def test_unapproved_or_mismatched_methodology(
    product: dict[str, Any], status: str, version: str
) -> None:
    urban = product["methodologies"]["urban_passenger"]
    urban["status"], urban["version"] = status, version
    with pytest.raises(ValueError):
        urban_config_from_mapping(product)


@pytest.mark.parametrize(
    "bands",
    [
        [],
        None,
        [{"max_area_km2": 100, "h_minutes": 20}],
        [{"max_area_km2": None, "h_minutes": 20}, {"max_area_km2": 100, "h_minutes": 30}],
        [
            {"max_area_km2": 100, "h_minutes": 20},
            {"max_area_km2": 100, "h_minutes": 30},
            {"max_area_km2": None, "h_minutes": 40},
        ],
        [
            {"max_area_km2": 100, "h_minutes": 20},
            {"max_area_km2": 50, "h_minutes": 30},
            {"max_area_km2": None, "h_minutes": 40},
        ],
        [{"max_area_km2": None, "h_minutes": 0}],
        [{"max_area_km2": None, "h_minutes": "20"}],
        [{"max_area_km2": None, "h_minutes": float("inf")}],
        [{"max_area_km2": None, "h_minutes": 1e308}],
        [{"max_area_km2": -1, "h_minutes": 20}, {"max_area_km2": None, "h_minutes": 30}],
        [{}],
    ],
)
def test_invalid_area_bands(product: dict[str, Any], bands: object) -> None:
    product["methodologies"]["urban_passenger"]["parameters"]["area_h_bands"] = bands
    with pytest.raises(ValueError):
        urban_config_from_mapping(product)


@pytest.mark.parametrize("threshold", [-1, 101, float("nan"), float("inf"), True, "50"])
def test_invalid_threshold_in_config(product: dict[str, Any], threshold: object) -> None:
    product["methodologies"]["urban_passenger"]["parameters"]["low_accessibility_below"] = threshold
    with pytest.raises(ValueError):
        urban_config_from_mapping(product)


def test_direct_config_construction_is_validated(config: UrbanConfig) -> None:
    with pytest.raises(ValueError):
        replace(config, priority_weights={"low": 0})
    with pytest.raises(ValueError):
        replace(config, area_h_bands=())
    with pytest.raises(ValueError):
        replace(config, methodology_version="")
