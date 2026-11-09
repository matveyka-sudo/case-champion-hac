import copy
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from a2_engine import (
    CargoScope,
    CargoSiteResult,
    ComparisonContext,
    FreightConfig,
    MetricResult,
    PassengerChain,
    PassengerConfig,
    PipelineBasis,
    ReferenceTime,
    TravelTime,
    delta_index,
    freight_config_from_mapping,
    intercity_cargo_index,
    intercity_passenger_index,
    passenger_config_from_mapping,
    pipeline_utilization,
    select_h_seconds,
    time_saved_seconds,
    urban_cargo_territory_index,
)


def test_extended_parameters_come_from_product_json(product: dict[str, Any]) -> None:
    before = copy.deepcopy(product)
    passenger = passenger_config_from_mapping(product)
    freight = freight_config_from_mapping(product)
    assert passenger.waiting_half_life_seconds == 43200
    assert passenger.reference_profile == "edge-calibrated-v1"
    assert freight.delivery_half_life_seconds == {
        "urgent": 86400,
        "ordinary": 259200,
        "nonurgent": 2678400,
    }
    assert product == before
    product["methodologies"]["intercity_passenger"]["parameters"][
        "initial_wait_half_life_hours"
    ] = 2
    product["methodologies"]["intercity_cargo"]["parameters"]["delivery_half_life_hours"][
        "urgent"
    ] = 1
    product["methodologies"]["urban_passenger"]["parameters"]["area_h_bands"][0]["h_minutes"] = 7
    product["methodologies"]["intercity_passenger"]["parameters"]["reference_profile"] = (
        "synthetic-profile"
    )
    product["methodology_version"] = "synthetic-v4"
    for method in product["methodologies"].values():
        method["version"] = "synthetic-v4"
    changed_passenger = passenger_config_from_mapping(product)
    changed_freight = freight_config_from_mapping(product)
    assert (
        changed_passenger.methodology_version
        == changed_freight.methodology_version
        == "synthetic-v4"
    )
    assert changed_passenger.reference_profile == "synthetic-profile"
    assert select_h_seconds(100, bands=changed_freight.area_h_bands).value == 420
    result = intercity_passenger_index(
        PassengerChain("r", 7200, 7200),
        ReferenceTime("ref", 3600, "synthetic-profile", "full", True),
        config=changed_passenger,
    )
    assert result.waiting_index.value == 50
    assert (
        intercity_cargo_index(
            cargo_profile_id="test",
            urgency="urgent",
            initial_wait_seconds=0,
            movement_seconds=3600,
            handling_seconds=0,
            transfer_wait_seconds=0,
            compatibility_confirmed=True,
            config=changed_freight,
        ).index.value
        == 50
    )
    assert passenger.waiting_half_life_seconds == 43200
    assert freight.delivery_half_life_seconds["urgent"] == 86400
    with pytest.raises(TypeError):
        cast(dict[str, float], freight.delivery_half_life_seconds)["urgent"] = 1


@pytest.mark.parametrize("bad", [0, -1, True, "12", float("nan"), float("inf"), 1e308])
def test_invalid_half_life_configuration(product: dict[str, Any], bad: object) -> None:
    product["methodologies"]["intercity_passenger"]["parameters"][
        "initial_wait_half_life_hours"
    ] = bad
    product["methodologies"]["intercity_cargo"]["parameters"]["delivery_half_life_hours"][
        "urgent"
    ] = bad
    with pytest.raises(ValueError):
        passenger_config_from_mapping(product)
    with pytest.raises(ValueError):
        freight_config_from_mapping(product)


@pytest.mark.parametrize("kind", ["intercity_passenger", "intercity_cargo", "urban_cargo"])
def test_unapproved_or_mismatched_extended_method(product: dict[str, Any], kind: str) -> None:
    loader = (
        passenger_config_from_mapping
        if kind == "intercity_passenger"
        else freight_config_from_mapping
    )
    product["methodologies"][kind]["status"] = "pending"
    with pytest.raises(ValueError):
        loader(product)
    product["methodologies"][kind]["status"] = "approved"
    product["methodologies"][kind]["version"] = "old"
    with pytest.raises(ValueError):
        loader(product)


def test_unsupported_configuration_semantics(product: dict[str, Any]) -> None:
    changed = copy.deepcopy(product)
    changed["methodologies"]["intercity_passenger"]["parameters"]["reference"] = "B"
    with pytest.raises(ValueError):
        passenger_config_from_mapping(changed)
    changed = copy.deepcopy(product)
    changed["methodologies"]["intercity_cargo"]["parameters"]["pipeline_in_total"] = True
    with pytest.raises(ValueError):
        freight_config_from_mapping(changed)
    del product["methodologies"]["intercity_cargo"]["parameters"]["delivery_half_life_hours"]
    with pytest.raises(ValueError, match="Missing configuration field"):
        freight_config_from_mapping(product)


def test_extended_loaders_and_imports_from_other_cwd(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "src"
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from a2_engine import load_passenger_config, load_freight_config; "
            "assert load_passenger_config().waiting_half_life_seconds == 43200; "
            "assert load_freight_config().delivery_half_life_seconds['urgent'] == 86400",
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(source)},
        check=True,
        capture_output=True,
        text=True,
    )


def test_all_new_calculations_preserve_inputs(
    passenger_config: PassengerConfig,
    freight_config: FreightConfig,
) -> None:
    # Frozen inputs and caller-owned collections survive all calculations unchanged.
    reference = ReferenceTime("ref", 3600, passenger_config.reference_profile, "full", True)
    chain = PassengerChain("r", 7200, 43200)
    scope = CargoScope("port", "inbound", "test")
    sites = [
        CargoSiteResult("a", scope, MetricResult(100, "available")),
        CargoSiteResult("b", scope, MetricResult(0, "available")),
    ]
    basis = PipelineBasis("oil", "2026", "tonnes", "test", "a->b")
    comparison = ComparisonContext(True, "test-fixed-inputs")
    times = [TravelTime(1800, True), TravelTime(2100, True)]
    before = copy.deepcopy((chain, reference, sites, basis, comparison, times))
    weights = dict(freight_config.delivery_half_life_seconds)
    intercity_passenger_index(chain, reference, config=passenger_config)
    urban_cargo_territory_index(sites, scope=scope)
    pipeline_utilization(
        120,
        100,
        volume_basis=basis,
        capacity_basis=basis,
        connection_confirmed=True,
        config=freight_config,
    )
    delta_index(sites[0].index, sites[1].index, comparison=comparison)
    time_saved_seconds(times[0], times[1], comparison=comparison)
    intercity_cargo_index(
        cargo_profile_id="test",
        urgency="urgent",
        initial_wait_seconds=0,
        movement_seconds=86400,
        handling_seconds=0,
        transfer_wait_seconds=0,
        compatibility_confirmed=True,
        config=freight_config,
    )
    assert before == (chain, reference, sites, basis, comparison, times)
    assert weights == dict(freight_config.delivery_half_life_seconds)
