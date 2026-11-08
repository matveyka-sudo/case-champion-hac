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
    "UrbanJourneyRules": "(methodology_version: str, max_total_wait_seconds: float) -> None",
    "load_urban_journey_rules": "(path: str | pathlib.Path | None = None) -> UrbanJourneyRules",
    "urban_journey_rules_from_mapping": "(product: collections.abc.Mapping[str, object]) -> "
    "UrbanJourneyRules",
    "SelectedUrbanObject": "(destination: UrbanDestination, journey: UrbanJourneyTiming) -> None",
    "UrbanAnalysis": "(context: UrbanContext, area: UrbanArea, selected_categories: "
    "collections.abc.Mapping[str, str], h_seconds: MetricResult, journeys: "
    "tuple[UrbanJourneyTiming, ...], modes: tuple[UrbanTerritoryResult, ...], "
    "population_coverage: UrbanPopulationCoverage, status: Literal['available', "
    "'partial', 'unknown'], limitations: tuple[str, ...]) -> None",
    "UrbanArea": "(city_id: str, area_km2: float | None, geometry_version: str, source_refs: "
    "tuple[str, ...]) -> None",
    "UrbanCandidate": "(journey_id: str, point_id: str, destination_id: str, category: str, "
    "passenger_mode: str, context: UrbanContext, origin_node_id: str, "
    "destination_node_id: str, departure_at: datetime.datetime, arrival_at: "
    "datetime.datetime, legs: tuple[UrbanLeg, ...], evidence_refs: tuple[str, "
    "...], geometry: UrbanGeometry | None = None) -> None",
    "UrbanCategoryResult": "(point_id: str, category: str, passenger_mode: str, best: "
    "SelectedUrbanObject | None, objects: tuple[SelectedUrbanObject, ...], "
    "rejected: tuple[UrbanJourneyTiming, ...], index: MetricResult, count: "
    "MetricResult, threshold: UrbanThreshold | None, coverage: "
    "UrbanCoverage | None, minimum_proven: bool, completeness: "
    "Literal['full', 'partial'], evidence_refs: tuple[str, ...], "
    "limitations: tuple[str, ...]) -> None",
    "UrbanContext": "(city_id: str, territory_id: str, requested_departure_at: datetime.datetime, "
    "traffic_mode: Literal['static', 'typical', 'live', 'historical'], versions: "
    "UrbanVersions, search_parameters_id: str) -> None",
    "UrbanCoverage": "(scope: UrbanSearchScope, catalog_complete: bool, search_complete: bool, "
    "evidence_refs: tuple[str, ...], catalog_destination_ids: frozenset[str], "
    "provider_error: str | None = None) -> None",
    "UrbanDestination": "(destination_id: str, object_identity: str, categories: frozenset[str], access_node_id: "
    "str, territory_id: str, eligible: bool | None, in_service_scope: bool, source_refs: "
    "tuple[str, ...], *, physical_node_id: str | None = None) -> None",
    "UrbanGeometry": "(coordinates: tuple[tuple[float, float], ...], evidence_refs: tuple[str, "
    "...]) -> None",
    "UrbanJourneyTiming": "(candidate: UrbanCandidate, total_seconds: float, walking_seconds: "
    "float, riding_seconds: float, initial_wait_seconds: float, "
    "transfer_wait_seconds: float, total_wait_seconds: float, "
    "waiting_share: MetricResult, admissible: bool, reason_code: str | "
    "None) -> None",
    "UrbanLeg": "(mode: str, kind: Literal['walk', 'ride', 'wait_initial', 'wait_transfer'], "
    "from_node_id: str, to_node_id: str, start_at: datetime.datetime, end_at: "
    "datetime.datetime, duration_seconds: float, service_id: str | None, "
    "evidence_refs: tuple[str, ...]) -> None",
    "UrbanPoint": "(point_id: str, cell_id: str, origin_node_id: str, access_node_id: str, "
    "access_distance_m: float, territory_id: str, population: float | None, "
    "population_year: int | None, population_method: str | None, source_refs: "
    "tuple[str, ...], population_share_id: str | None = None) -> None",
    "UrbanPointResult": "(point: UrbanPoint, passenger_mode: str, categories: "
    "tuple[UrbanCategoryResult, ...], index: MetricResult, completeness: "
    "Literal['full', 'partial']) -> None",
    "UrbanPopulationCoverage": "(context: UrbanContext, complete: bool, excluded_population: "
    "float | None, evidence_refs: tuple[str, ...], point_ids: "
    "frozenset[str]) -> None",
    "UrbanSearchScope": "(context: UrbanContext, point_id: str, category: str, passenger_mode: "
    "str, max_total_wait_seconds: float) -> None",
    "UrbanTerritoryResult": "(passenger_mode: str, points: tuple[UrbanPointResult, ...], index: "
    "MetricResult, completeness: Literal['full', 'partial'], "
    "ranking_eligible: bool, evidence_refs: tuple[str, ...]) -> None",
    "UrbanThreshold": "(city_id: str, passenger_mode: str, versions: UrbanVersions, traffic_mode: "
    "str, search_parameters_id: str, seconds: float | None, evidence_refs: "
    "tuple[str, ...]) -> None",
    "UrbanVersions": "(snapshot_id: str, graph_version: str, calendar_version: str, "
    "population_version: str | None, geometry_version: str, methodology_version: "
    "str, config_hash: str, traffic_version: str) -> None",
    "analyze_urban_candidates": "(context: UrbanContext, points: "
    "collections.abc.Sequence[UrbanPoint], destinations: "
    "collections.abc.Sequence[UrbanDestination], candidates: "
    "collections.abc.Sequence[UrbanCandidate], coverage: "
    "collections.abc.Sequence[UrbanCoverage], *, area: UrbanArea, "
    "selected_categories: collections.abc.Mapping[str, str], modes: "
    "collections.abc.Sequence[str], population_coverage: "
    "UrbanPopulationCoverage, config: UrbanConfig, rules: "
    "UrbanJourneyRules, thresholds: "
    "collections.abc.Sequence[UrbanThreshold] = ()) -> UrbanAnalysis",
    "validate_urban_journey": "(candidate: UrbanCandidate, *, rules: UrbanJourneyRules) -> "
    "UrbanJourneyTiming",
    "UrbanTauRules": "(methodology_version: str, tau_days: int, tau_times: tuple[datetime.time, ...], "
    "max_total_wait_seconds: float, max_lookback_weeks: int = 8) -> None",
    "load_urban_tau_rules": "(path: str | pathlib.Path | None = None) -> UrbanTauRules",
    "urban_tau_rules_from_mapping": "(product: collections.abc.Mapping[str, object]) -> UrbanTauRules",
    "UrbanODScope": "(city_id: str, passenger_mode: Literal['walk', 'public_transport', 'private_car'], "
    "versions: UrbanVersions, traffic_mode: Literal['static', 'typical', 'live', "
    "'historical'], search_parameters_id: str, timezone: str, point_ids: frozenset[str], "
    "max_total_wait_seconds: float) -> None",
    "UrbanODPair": "(origin_point_id: str, destination_point_id: str, state: Literal['reachable', "
    "'confirmed_unreachable', 'unknown'], seconds: float | None, search_complete: bool, "
    "rules_confirmed: bool, evidence_refs: tuple[str, ...], reason_code: str | None = None) "
    "-> None",
    "UrbanTauDay": "(service_date: datetime.date, ordinary: bool | None, is_holiday: bool | None, "
    "calendar_confirmed: bool, service_calendar_confirmed: bool, evidence_refs: tuple[str, "
    "...]) -> None",
    "UrbanTauSlice": "(scope: UrbanODScope, service_date: datetime.date, local_time: datetime.time, "
    "departure_at: datetime.datetime, pairs: tuple[UrbanODPair, ...], coverage_confirmed: "
    "bool, evidence_refs: tuple[str, ...]) -> None",
    "UrbanTauWeek": "(week_id: str, scope: UrbanODScope, week_start: datetime.date, calendar: "
    "tuple[UrbanTauDay, ...], slices: tuple[UrbanTauSlice, ...], evidence_refs: tuple[str, "
    "...], typical_profile_accepted: bool, traffic_evidence_refs: tuple[str, ...]) -> None",
    "UrbanTauSliceResult": "(service_date: datetime.date, local_time: datetime.time, departure_at: "
    "datetime.datetime, mean_seconds: MetricResult, reachable_weight: fractions.Fraction, "
    "confirmed_unreachable_weight: fractions.Fraction, unknown_weight: fractions.Fraction, "
    "weight_denominator: fractions.Fraction, unreachable_weight_share: fractions.Fraction | "
    "None, reasons: tuple[str, ...], evidence_refs: tuple[str, ...]) -> None",
    "UrbanTauWeekResult": "(week_id: str, week_start: datetime.date, scope: UrbanODScope, tau_seconds: "
    "MetricResult, slices: tuple[UrbanTauSliceResult, ...], reasons: tuple[str, ...], "
    "evidence_refs: tuple[str, ...]) -> None",
    "UrbanTauResult": "(baseline_context: UrbanContext, passenger_mode: Literal['walk', 'public_transport', "
    "'private_car'], timezone: str, reference_at: datetime.datetime, rules: UrbanTauRules, "
    "points: tuple[UrbanPoint, ...], population_coverage: UrbanPopulationCoverage, "
    "tau_seconds: MetricResult, selected_week: UrbanTauWeekResult | None, weeks: "
    "tuple[UrbanTauWeekResult, ...], reasons: tuple[str, ...], evidence_refs: tuple[str, "
    "...]) -> None",
    "calculate_urban_tau": "(baseline_context: UrbanContext, points: collections.abc.Sequence[UrbanPoint], "
    "population_coverage: UrbanPopulationCoverage, weeks: "
    "collections.abc.Sequence[UrbanTauWeek], *, passenger_mode: Literal['walk', "
    "'public_transport', 'private_car'], timezone: str, reference_at: datetime.datetime, "
    "rules: UrbanTauRules) -> UrbanTauResult",
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
        "UrbanJourneyRules",
        "load_urban_journey_rules",
        "urban_journey_rules_from_mapping",
        "UrbanTauRules",
        "load_urban_tau_rules",
        "urban_tau_rules_from_mapping",
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
    "a2_engine.urban.models": (
        "UrbanArea",
        "UrbanCandidate",
        "UrbanContext",
        "UrbanCoverage",
        "UrbanDestination",
        "UrbanGeometry",
        "UrbanLeg",
        "UrbanPoint",
        "UrbanPopulationCoverage",
        "UrbanSearchScope",
        "UrbanThreshold",
        "UrbanVersions",
    ),
    "a2_engine.urban.journeys": ("UrbanJourneyTiming", "validate_urban_journey"),
    "a2_engine.urban.selection": ("SelectedUrbanObject",),
    "a2_engine.urban.analysis": (
        "UrbanAnalysis",
        "UrbanCategoryResult",
        "UrbanPointResult",
        "UrbanTerritoryResult",
        "analyze_urban_candidates",
    ),
    "a2_engine.urban.tau_models": (
        "UrbanODScope",
        "UrbanODPair",
        "UrbanTauDay",
        "UrbanTauSlice",
        "UrbanTauWeek",
    ),
    "a2_engine.urban.tau": (
        "UrbanTauSliceResult",
        "UrbanTauWeekResult",
        "UrbanTauResult",
        "calculate_urban_tau",
    ),
    "a2_engine.urban.valhalla": (
        "ValhallaBuild",
        "ValhallaCorrelation",
        "WalkCoordinate",
        "WalkConnector",
        "WalkRouteRequest",
        "WalkResponseEnvelope",
        "WalkRouteResult",
        "normalize_walk_response",
    ),
    "a2_engine.urban.walk": (
        "WalkInputs",
        "WalkRoutePlan",
        "WalkPlanIssue",
        "WalkAnalysisResult",
        "prepare_walk_inputs",
        "plan_walk_routes",
        "analyze_walk_responses",
    ),
}

NEW_PUBLIC_NAMES = {
    "WalkConnector",
    "WalkResponseEnvelope",
    "WalkInputs",
    "WalkRoutePlan",
    "normalize_walk_response",
    "WalkAnalysisResult",
    "prepare_walk_inputs",
    "ValhallaBuild",
    "analyze_walk_responses",
    "plan_walk_routes",
    "WalkRouteResult",
    "WalkRouteRequest",
    "WalkPlanIssue",
    "WalkCoordinate",
    "ValhallaCorrelation",
}


def test_public_names_signatures_and_defaults_are_unchanged() -> None:
    assert tuple(a2_engine.__all__[: len(PUBLIC_SIGNATURES)]) == tuple(PUBLIC_SIGNATURES)
    assert set(a2_engine.__all__) == set(PUBLIC_SIGNATURES) | NEW_PUBLIC_NAMES
    assert len(a2_engine.__all__) == len(PUBLIC_SIGNATURES) + len(NEW_PUBLIC_NAMES)
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
                    domain = path.relative_to(package).parts[0]
                    assert node.module.startswith(("a2_engine.shared.", f"a2_engine.{domain}.")), (
                        path,
                        node.module,
                    )
            elif isinstance(node, ast.Import):
                domain = path.relative_to(package).parts[0]
                for alias in node.names:
                    if alias.name.startswith("a2_engine"):
                        assert alias.name.startswith(
                            ("a2_engine.shared.", f"a2_engine.{domain}.")
                        ), path
    for domain in ("urban", "intercity", "freight", "pipeline", "comparison", "shared"):
        assert not (package / f"{domain}.py").exists()
