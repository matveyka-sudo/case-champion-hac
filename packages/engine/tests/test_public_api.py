"""Compatibility checks for the domain-package move, without duplicating formula tests."""

import ast
import importlib
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import is_dataclass
from pathlib import Path
from typing import Any

import a2_engine
import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src"

# Captured from the pre-refactor API. Only relocated type-module prefixes are omitted.
PUBLIC_SIGNATURES: dict[str, str] = {
    "AreaHBand": "(max_area_km2: float | None, h_seconds: float) -> None",
    "CarResult": "(chain_id: str, reference: ReferenceTime, path_index: MetricResult, "
    "completeness: Literal['full', 'partial'], diagnostics: tuple[str, ...]) -> None",
    "CargoDeliveryResult": "(cargo_profile_id: str, urgency: str, movement_seconds: MetricResult, "
    "total_seconds: MetricResult, index: MetricResult) -> None",
    "CargoScope": "(node_type: str, direction: Literal['inbound', 'outbound'], cargo_profile_id: "
    "str) -> None",
    "CargoSiteResult": "(site_id: str, scope: CargoScope, index: MetricResult) -> None",
    "ComparisonContext": "(comparable: bool, verification_id: str) -> None",
    "DeltaResult": "(value: float | None, status: Literal['available', 'unknown', "
    "'not_applicable'], reason_code: str | None = None) -> None",
    "FreightConfig": "(methodology_version: str, area_h_bands: tuple[AreaHBand, ...], "
    "delivery_half_life_seconds: collections.abc.Mapping[str, float], "
    "pipeline_products: frozenset[str]) -> None",
    "MetricResult": "(value: float | None, status: Literal['available', 'unknown', "
    "'not_applicable'], reason_code: str | None = None) -> None",
    "PassengerChain": "(chain_id: str, path_seconds: float | None, initial_wait_seconds: float | "
    "None) -> None",
    "PassengerConfig": "(methodology_version: str, waiting_half_life_seconds: float, "
    "reference_profile: str) -> None",
    "PassengerResult": "(chain: PassengerChain, reference: ReferenceTime, path_index: "
    "MetricResult, waiting_index: MetricResult, index: MetricResult, "
    "completeness: Literal['full', 'partial'], diagnostics: tuple[str, ...]) "
    "-> None",
    "PipelineBasis": "(product: str, period: str, unit: str, connection_id: str, direction: str) "
    "-> None",
    "PipelineResult": "(utilization_percent: MetricResult, diagnostics: tuple[str, ...]) -> None",
    "ReferenceTime": "(reference_id: str, seconds: float | None, profile_version: str, "
    "completeness: Literal['full', 'partial'], applicability_confirmed: bool) -> "
    "None",
    "TravelTime": "(seconds: float | None, route_exists: bool | None) -> None",
    "UrbanConfig": "(methodology_version: str, area_h_bands: tuple[AreaHBand, ...], "
    "priority_weights: collections.abc.Mapping[str, float], categories: "
    "frozenset[str], modes: frozenset[str], low_accessibility_below: float) -> "
    "None",
    "category_index": "(travel_seconds: float | None, h_seconds: float | None, *, "
    "confirmed_no_route: bool = False) -> MetricResult",
    "delta_index": "(before: MetricResult, after: MetricResult, *, comparison: ComparisonContext) "
    "-> DeltaResult",
    "freight_config_from_mapping": "(product: collections.abc.Mapping[str, object]) -> "
    "FreightConfig",
    "intercity_car_index": "(chain_id: str, path_seconds: float | None, reference: ReferenceTime, "
    "*, config: PassengerConfig) -> CarResult",
    "intercity_cargo_index": "(*, cargo_profile_id: str, urgency: str, initial_wait_seconds: "
    "float | None, movement_seconds: float | None, handling_seconds: "
    "float | None, transfer_wait_seconds: float | None, "
    "compatibility_confirmed: bool, config: FreightConfig) -> "
    "CargoDeliveryResult",
    "intercity_passenger_index": "(chain: PassengerChain, reference: ReferenceTime, *, config: "
    "PassengerConfig) -> PassengerResult",
    "is_low_accessibility": "(index: MetricResult, *, threshold: float) -> bool | None",
    "load_urban_config": "(path: str | pathlib.Path | None = None) -> UrbanConfig",
    "load_freight_config": "(path: str | pathlib.Path | None = None) -> FreightConfig",
    "load_passenger_config": "(path: str | pathlib.Path | None = None) -> PassengerConfig",
    "passenger_config_from_mapping": "(product: collections.abc.Mapping[str, object]) -> "
    "PassengerConfig",
    "pipeline_utilization": "(volume: float | None, capacity: float | None, *, volume_basis: "
    "PipelineBasis, capacity_basis: PipelineBasis, connection_confirmed: "
    "bool, config: FreightConfig) -> PipelineResult",
    "point_index": "(category_indices: collections.abc.Mapping[str, MetricResult], "
    "selected_categories: collections.abc.Mapping[str, str], *, config: "
    "UrbanConfig) -> MetricResult",
    "point_indices_by_mode": "(category_indices_by_mode: collections.abc.Mapping[str, "
    "collections.abc.Mapping[str, MetricResult]], selected_categories: "
    "collections.abc.Mapping[str, str], *, config: UrbanConfig) -> "
    "dict[str, MetricResult]",
    "select_h_seconds": "(area_km2: float | None, *, bands: collections.abc.Sequence[AreaHBand]) "
    "-> MetricResult",
    "territory_index": "(point_indices: collections.abc.Mapping[str, MetricResult], populations: "
    "collections.abc.Mapping[str, float | None]) -> MetricResult",
    "time_saved_seconds": "(before: TravelTime, after: TravelTime, *, comparison: "
    "ComparisonContext) -> DeltaResult",
    "urban_cargo_site_index": "(site_id: str, travel_seconds: float | None, h_seconds: float | "
    "None, *, scope: CargoScope, compatibility_confirmed: bool, "
    "confirmed_no_route: bool = False) -> CargoSiteResult",
    "urban_cargo_territory_index": "(sites: collections.abc.Sequence[CargoSiteResult], *, scope: "
    "CargoScope) -> MetricResult",
    "urban_config_from_mapping": "(product: collections.abc.Mapping[str, object]) -> UrbanConfig",
}

CANONICAL_MODULES = {
    "a2_engine.shared.config": (
        "AreaHBand",
        "FreightConfig",
        "PassengerConfig",
        "UrbanConfig",
        "freight_config_from_mapping",
        "load_freight_config",
        "load_passenger_config",
        "load_urban_config",
        "passenger_config_from_mapping",
        "urban_config_from_mapping",
    ),
    "a2_engine.shared.results": ("MetricResult",),
    "a2_engine.shared.scoring": ("category_index",),
    "a2_engine.urban.indices": (
        "is_low_accessibility",
        "point_index",
        "point_indices_by_mode",
        "select_h_seconds",
        "territory_index",
    ),
    "a2_engine.intercity.passenger": (
        "CarResult",
        "PassengerChain",
        "PassengerResult",
        "ReferenceTime",
        "intercity_car_index",
        "intercity_passenger_index",
    ),
    "a2_engine.freight.urban": (
        "CargoScope",
        "CargoSiteResult",
        "urban_cargo_site_index",
        "urban_cargo_territory_index",
    ),
    "a2_engine.freight.intercity": ("CargoDeliveryResult", "intercity_cargo_index"),
    "a2_engine.pipeline.utilization": ("PipelineBasis", "PipelineResult", "pipeline_utilization"),
    "a2_engine.comparison.deltas": (
        "ComparisonContext",
        "DeltaResult",
        "TravelTime",
        "delta_index",
        "time_saved_seconds",
    ),
}


def test_public_names_signatures_and_defaults_are_unchanged() -> None:
    assert tuple(a2_engine.__all__) == tuple(PUBLIC_SIGNATURES)
    for name, expected in PUBLIC_SIGNATURES.items():
        signature = str(inspect.signature(getattr(a2_engine, name)))
        signature = re.sub(r"\ba2_engine(?:\.[a-z_]+)+\.([A-Z][A-Za-z0-9_]*)", r"\1", signature)
        assert signature == expected, name


def test_root_and_domain_exports_are_the_same_canonical_objects() -> None:
    assert {name for names in CANONICAL_MODULES.values() for name in names} == set(
        a2_engine.__all__
    )
    for module_name, names in CANONICAL_MODULES.items():
        module = importlib.import_module(module_name)
        for name in names:
            canonical = getattr(module, name)
            assert getattr(a2_engine, name) is canonical
            assert canonical.__module__ == module_name
            if is_dataclass(canonical):
                assert getattr(canonical, "__dataclass_params__").frozen
    for domain in ("urban", "intercity", "freight", "pipeline", "comparison"):
        package = importlib.import_module(f"a2_engine.{domain}")
        for name in package.__all__:
            assert getattr(package, name) is getattr(a2_engine, name)


def test_fresh_import_orders_and_all_default_loaders_from_another_cwd(tmp_path: Path) -> None:
    modules = [
        "a2_engine.urban",
        "a2_engine.intercity",
        "a2_engine.freight",
        "a2_engine.pipeline",
        "a2_engine.comparison",
        "a2_engine.shared.scoring",
    ]
    script = """
import importlib
import json
import sys
for name in json.loads(sys.argv[1]):
    importlib.import_module(name)
from a2_engine import load_urban_config, load_passenger_config, load_freight_config
assert load_urban_config().area_h_bands[0].h_seconds == 1200
assert load_passenger_config().waiting_half_life_seconds == 43200
assert load_freight_config().delivery_half_life_seconds['urgent'] == 86400
"""
    for order in (modules, list(reversed(modules))):
        subprocess.run(
            [sys.executable, "-c", script, json.dumps(order)],
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(SOURCE)},
            check=True,
            capture_output=True,
            text=True,
        )


def test_explicit_config_path_with_package_outside_checkout(
    tmp_path: Path, product: dict[str, Any]
) -> None:
    deployment = tmp_path / "deployment"
    shutil.copytree(
        SOURCE / "a2_engine", deployment / "a2_engine", ignore=shutil.ignore_patterns("__pycache__")
    )
    config_path = tmp_path / "published.json"
    config_path.write_text(json.dumps(product), encoding="utf-8")
    script = """
from pathlib import Path
import sys
import a2_engine
from a2_engine import load_urban_config, load_passenger_config, load_freight_config
assert Path(a2_engine.__file__).resolve().is_relative_to(Path(sys.argv[2]))
assert load_urban_config(sys.argv[1]).area_h_bands[0].h_seconds == 1200
assert load_passenger_config(sys.argv[1]).waiting_half_life_seconds == 43200
assert load_freight_config(sys.argv[1]).delivery_half_life_seconds['urgent'] == 86400
"""
    subprocess.run(
        [sys.executable, "-c", script, str(config_path), str(deployment)],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(deployment)},
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "loader",
    [a2_engine.load_urban_config, a2_engine.load_passenger_config, a2_engine.load_freight_config],
)
def test_explicit_loader_errors_are_preserved(
    loader: Callable[[str | Path | None], object],
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError):
        loader(tmp_path / "missing.json")
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{broken", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        loader(malformed)


def test_internal_imports_follow_shared_dependency_direction() -> None:
    package = SOURCE / "a2_engine"
    for path in package.rglob("*.py"):
        if path.name == "__init__.py":
            continue  # Facades explicitly re-export canonical definitions.
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert node.level == 0, path
                assert node.module != "a2_engine", path
                if node.module is not None and node.module.startswith("a2_engine."):
                    assert node.module.startswith("a2_engine.shared."), (path, node.module)
            elif isinstance(node, ast.Import):
                assert not any(alias.name.startswith("a2_engine") for alias in node.names), path
    for domain in ("urban", "intercity", "freight", "pipeline", "comparison", "shared"):
        assert not (package / f"{domain}.py").exists()
