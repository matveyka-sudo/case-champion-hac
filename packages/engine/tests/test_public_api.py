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
    "ValhallaBuild": "(snapshot_id: str, source_graph_version: str, build_id: str, graph_version: str, "
    "engine_version: str, profile_version: str, pedestrian_options_json: str, evidence_refs: "
    "tuple[str, ...]) -> None",
    "ValhallaCorrelation": "(build: ValhallaBuild, access_node_id: str, source_location: WalkCoordinate, "
    "correlated_location: WalkCoordinate, correlation_id: str, evidence_refs: tuple[str, "
    "...]) -> None",
    "WalkCoordinate": "(lon: float, lat: float) -> None",
    "WalkConnector": "(snapshot_id: str, source_graph_version: str, physical_node_id: str, access_node_id: "
    "str, physical_location: WalkCoordinate, access_location: WalkCoordinate, seconds: float "
    "| None, traversable: bool | None, time_basis: str, evidence_refs: tuple[str, ...]) -> "
    "None",
    "WalkRouteRequest": "(request_id: str, point_id: str, destination_id: str, build: ValhallaBuild, "
    "search_parameters_id: str, origin: ValhallaCorrelation, destination: "
    "ValhallaCorrelation, payload_json: str, evidence_refs: tuple[str, ...], path_scope: "
    "Literal['network_only'] = 'network_only') -> None",
    "WalkResponseEnvelope": "(request_id: str, point_id: str, destination_id: str, build: ValhallaBuild, "
    "request_payload_hash: str, origin_correlation_id: str, destination_correlation_id: str, "
    "outcome: Literal['response', 'timeout', 'http_error', 'limit', 'no_correlation'], "
    "response_json: str | None, evidence_refs: tuple[str, ...], search_complete: bool = "
    "False, minimum_time_evidence_refs: tuple[str, ...] = (), unreachable_evidence_refs: "
    "tuple[str, ...] = (), pure_walk_evidence_refs: tuple[str, ...] = (), matched_origin: "
    "WalkCoordinate | None = None, matched_destination: WalkCoordinate | None = None, "
    "endpoint_evidence_refs: tuple[str, ...] = ()) -> None",
    "WalkRouteResult": "(request: WalkRouteRequest, state: Literal['reachable', 'confirmed_unreachable', "
    "'unknown'], network_seconds: float | None, network_distance_m: float | None, "
    "network_geometry: UrbanGeometry | None, minimum_proven: bool, reason_code: str | None, "
    "evidence_refs: tuple[str, ...], response_hash: str | None = None) -> None",
    "normalize_walk_response": "(request: WalkRouteRequest, envelope: WalkResponseEnvelope) -> WalkRouteResult",
    "WalkInputs": "(canonical_payload_hash: str, source_graph_version: str, context: UrbanContext, build: "
    "ValhallaBuild | None, points: tuple[UrbanPoint, ...], destinations: "
    "tuple[UrbanDestination, ...], point_endpoints: tuple[WalkEndpoint, ...], "
    "destination_endpoints: tuple[WalkEndpoint, ...], catalogs: tuple[WalkCatalog, ...], "
    "area: UrbanArea, population_coverage: UrbanPopulationCoverage, limitations: tuple[str, "
    "...]) -> None",
    "WalkRoutePlan": "(inputs: WalkInputs, point_ids: tuple[str, ...], categories: tuple[str, ...], requests: "
    "tuple[WalkRouteRequest, ...], issues: tuple[WalkPlanIssue, ...]) -> None",
    "WalkPlanIssue": "(point_id: str, destination_id: str, reasons: tuple[str, ...]) -> None",
    "WalkAnalysisResult": "(plan: WalkRoutePlan, routes: tuple[WalkRouteResult, ...], analysis: UrbanAnalysis) -> "
    "None",
    "prepare_walk_inputs": "(accepted: collections.abc.Mapping[str, typing.Any], *, city_id: str, departure_at: "
    "datetime.datetime, methodology_version: str, config_hash: str, search_parameters_id: "
    "str, build: ValhallaBuild | None = None, connectors: "
    "collections.abc.Sequence[WalkConnector] = (), correlations: "
    "collections.abc.Sequence[ValhallaCorrelation] = ()) -> WalkInputs",
    "plan_walk_routes": "(inputs: WalkInputs, *, point_ids: collections.abc.Sequence[str], categories: "
    "collections.abc.Sequence[str]) -> WalkRoutePlan",
    "analyze_walk_responses": "(plan: WalkRoutePlan, responses: collections.abc.Sequence[WalkResponseEnvelope], *, "
    "selected_categories: collections.abc.Mapping[str, str], config: UrbanConfig, rules: "
    "UrbanJourneyRules, thresholds: collections.abc.Sequence[UrbanThreshold] = ()) -> "
    "WalkAnalysisResult",
    "UrbanOriginalCell": "(cell_id: str, city_id: str, grid_version: str, population: float | None, "
    "population_year: int | None, population_method: str | None, point_id: str | None, "
    "excluded: bool, evidence_refs: tuple[str, ...]) -> None",
    "UrbanDistrict": "(district_id: str, city_id: str, name: str, boundary_version: str, evidence_refs: "
    "tuple[str, ...], administrative_level: str = 'district') -> None",
    "UrbanDistrictShare": "(cell_id: str, district_id: str, population: float | None, evidence_refs: tuple[str, "
    "...], area_share: float | None = None) -> None",
    "UrbanSharePrecision": "(cell_quantum: float, share_quantum: float, evidence_refs: tuple[str, ...], rule: str = "
    "'cell_nearest_share_floor') -> None",
    "UrbanDistrictCoverage": "(context: UrbanContext, catalog_version: str, distribution_version: str, grid_versions: "
    "frozenset[str], district_boundary_versions: collections.abc.Mapping[str, str], "
    "expected_district_ids: frozenset[str], expected_cell_ids: frozenset[str], "
    "complete_district_ids: frozenset[str], original_cells_complete: bool, evidence_refs: "
    "tuple[str, ...], precision: UrbanSharePrecision | None = None) -> None",
    "UrbanCellAllocation": "(cell_id: str, population: float | None, allocated_population: decimal.Decimal | None, "
    "residual_population: decimal.Decimal | None, documented_precision: bool, reasons: "
    "tuple[str, ...]) -> None",
    "UrbanDistrictPopulation": "(district_id: str, population: decimal.Decimal | None, excluded_population: "
    "decimal.Decimal | None, reasons: tuple[str, ...]) -> None",
    "UrbanDistrictComparison": "(context: UrbanContext, passenger_mode: str, category: str | None, selected_categories: "
    "tuple[tuple[str, str], ...], h_seconds: MetricResult, catalog_version: str, "
    "distribution_version: str, grid_versions: frozenset[str], district_boundary_versions: "
    "tuple[tuple[str, str], ...], expected_district_ids: tuple[str, ...], "
    "administrative_level: str = 'district') -> None",
    "UrbanDistrictValue": "(district_id: str, index: MetricResult, deficit: MetricResult, ranking_eligible: bool, "
    "reasons: tuple[str, ...]) -> None",
    "UrbanDistrictRank": "(district_id: str, index: float, rank: int) -> None",
    "UrbanDistrictRanking": "(scope: UrbanDistrictComparison, entries: tuple[UrbanDistrictRank, ...], excluded: "
    "tuple[tuple[str, tuple[str, ...]], ...], complete: bool, positions_scope: str) -> None",
    "UrbanDistrictMetric": "(scope: UrbanDistrictComparison, city_index: MetricResult, districts: "
    "tuple[UrbanDistrictValue, ...], gap: MetricResult, ranking: UrbanDistrictRanking) -> "
    "None",
    "UrbanDistrictModeResult": "(passenger_mode: str, overall: UrbanDistrictMetric, categories: "
    "tuple[UrbanDistrictMetric, ...]) -> None",
    "UrbanDistrictAnalysis": "(source_analysis: UrbanAnalysis, original_cells: tuple[UrbanOriginalCell, ...], "
    "district_catalog: tuple[UrbanDistrict, ...], population_shares: "
    "tuple[UrbanDistrictShare, ...], coverage: UrbanDistrictCoverage, cell_qa: "
    "tuple[UrbanCellAllocation, ...], district_populations: tuple[UrbanDistrictPopulation, "
    "...], city_population: decimal.Decimal | None, allocated_population: decimal.Decimal | "
    "None, residual_population: decimal.Decimal | None, excluded_population: decimal.Decimal "
    "| None, modes: tuple[UrbanDistrictModeResult, ...], evidence_refs: tuple[str, ...]) -> "
    "None",
    "analyze_urban_districts": "(urban_analysis: UrbanAnalysis, original_cells: "
    "collections.abc.Sequence[UrbanOriginalCell], district_catalog: "
    "collections.abc.Sequence[UrbanDistrict], population_shares: "
    "collections.abc.Sequence[UrbanDistrictShare], coverage: UrbanDistrictCoverage) -> "
    "UrbanDistrictAnalysis",
    "UrbanDistrictInputs": "(original_cells: tuple[UrbanOriginalCell, ...], district_catalog: tuple[UrbanDistrict, "
    "...], population_shares: tuple[UrbanDistrictShare, ...], coverage: "
    "UrbanDistrictCoverage) -> None",
    "prepare_urban_district_inputs": "(urban_analysis: UrbanAnalysis, accepted: collections.abc.Mapping[str, typing.Any], "
    "supplement: collections.abc.Mapping[str, typing.Any], *, catalog_version: str, "
    "distribution_version: str, complete_district_ids: collections.abc.Sequence[str], "
    "original_cells_complete: bool, evidence_refs: collections.abc.Sequence[str], precision: "
    "UrbanSharePrecision | None = None) -> UrbanDistrictInputs",
}

CANONICAL_MODULES = {
    "a2_engine.shared.config": (
        "AreaHBand",
        "FreightConfig",
        "PassengerConfig",
        "UrbanConfig",
        "freight_config_from_mapping",
        "load_urban_config",
        "load_freight_config",
        "load_passenger_config",
        "passenger_config_from_mapping",
        "urban_config_from_mapping",
        "UrbanJourneyRules",
        "load_urban_journey_rules",
        "urban_journey_rules_from_mapping",
        "UrbanTauRules",
        "load_urban_tau_rules",
        "urban_tau_rules_from_mapping",
    ),
    "a2_engine.intercity.passenger": (
        "CarResult",
        "PassengerChain",
        "PassengerResult",
        "ReferenceTime",
        "intercity_car_index",
        "intercity_passenger_index",
    ),
    "a2_engine.freight.intercity": ("CargoDeliveryResult", "intercity_cargo_index"),
    "a2_engine.freight.urban": (
        "CargoScope",
        "CargoSiteResult",
        "urban_cargo_site_index",
        "urban_cargo_territory_index",
    ),
    "a2_engine.comparison.deltas": (
        "ComparisonContext",
        "DeltaResult",
        "TravelTime",
        "delta_index",
        "time_saved_seconds",
    ),
    "a2_engine.shared.results": ("MetricResult",),
    "a2_engine.pipeline.utilization": ("PipelineBasis", "PipelineResult", "pipeline_utilization"),
    "a2_engine.shared.scoring": ("category_index",),
    "a2_engine.urban.indices": (
        "is_low_accessibility",
        "point_index",
        "point_indices_by_mode",
        "select_h_seconds",
        "territory_index",
    ),
    "a2_engine.urban.selection": ("SelectedUrbanObject",),
    "a2_engine.urban.analysis.models": (
        "UrbanAnalysis",
        "UrbanCategoryResult",
        "UrbanPointResult",
        "UrbanTerritoryResult",
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
    "a2_engine.urban.analysis.calculation": ("analyze_urban_candidates",),
    "a2_engine.urban.tau.models": (
        "UrbanODScope",
        "UrbanODPair",
        "UrbanTauDay",
        "UrbanTauSlice",
        "UrbanTauWeek",
        "UrbanTauSliceResult",
        "UrbanTauWeekResult",
        "UrbanTauResult",
    ),
    "a2_engine.urban.tau.calculation": ("calculate_urban_tau",),
    "a2_engine.urban.valhalla.models": (
        "ValhallaBuild",
        "ValhallaCorrelation",
        "WalkCoordinate",
        "WalkConnector",
        "WalkRouteRequest",
        "WalkResponseEnvelope",
        "WalkRouteResult",
    ),
    "a2_engine.urban.valhalla.responses": ("normalize_walk_response",),
    "a2_engine.urban.walk.models": (
        "WalkInputs",
        "WalkRoutePlan",
        "WalkPlanIssue",
        "WalkAnalysisResult",
    ),
    "a2_engine.urban.walk.inputs": ("prepare_walk_inputs",),
    "a2_engine.urban.walk.analysis": ("plan_walk_routes", "analyze_walk_responses"),
    "a2_engine.urban.districts.models": (
        "UrbanOriginalCell",
        "UrbanDistrict",
        "UrbanDistrictShare",
        "UrbanSharePrecision",
        "UrbanDistrictCoverage",
        "UrbanCellAllocation",
        "UrbanDistrictPopulation",
        "UrbanDistrictComparison",
        "UrbanDistrictValue",
        "UrbanDistrictRank",
        "UrbanDistrictRanking",
        "UrbanDistrictMetric",
        "UrbanDistrictModeResult",
        "UrbanDistrictAnalysis",
        "UrbanDistrictInputs",
    ),
    "a2_engine.urban.districts.calculation": ("analyze_urban_districts",),
    "a2_engine.urban.districts.inputs": ("prepare_urban_district_inputs",),
    "a2_engine.routing.timetable_models": (
        "TimetableEvent",
        "TimetableRun",
        "TimetableFootpath",
        "TimetableTransfer",
        "TimetableScope",
        "Timetable",
        "TimetableStep",
        "TimetablePath",
        "TimetableSearchResult",
    ),
    "a2_engine.routing.timetable": ("solve_timetable",),
    "a2_engine.urban.transit.models": (
        "UrbanTransitNetwork",
        "UrbanTransitResult",
        "UrbanReturnJourney",
        "UrbanLastReturnResult",
    ),
    "a2_engine.urban.transit.search": ("search_urban_transit",),
    "a2_engine.urban.transit.last_return": ("search_urban_last_return",),
    "a2_engine.routing.road": (
        "RoadBuild",
        "RoadCoordinate",
        "RoadEndpoint",
        "RoadRequest",
        "RoadResponse",
        "RoadResult",
        "normalize_road_response",
    ),
    "a2_engine.urban.car.models": ("CarInputs", "CarPlan", "CarAnalysisResult"),
    "a2_engine.urban.car.analysis": ("plan_car_routes", "analyze_car_responses"),
    "a2_engine.urban.od.models": ("UrbanODTask", "UrbanODPlan", "UrbanODObservation"),
    "a2_engine.urban.od.collection": (
        "UrbanODCollector",
        "assemble_urban_od_week",
        "od_road_observation",
        "od_timetable_observation",
    ),
    "a2_engine.urban.od.planning": ("plan_urban_od_week", "od_road_request"),
}

NEW_PUBLIC_NAMES = {
    "TimetableStep",
    "TimetablePath",
    "solve_timetable",
    "TimetableRun",
    "UrbanTransitNetwork",
    "search_urban_last_return",
    "TimetableTransfer",
    "UrbanTransitResult",
    "UrbanLastReturnResult",
    "search_urban_transit",
    "TimetableFootpath",
    "Timetable",
    "TimetableSearchResult",
    "TimetableScope",
    "UrbanReturnJourney",
    "TimetableEvent",
}


NEW_PUBLIC_NAMES.update(
    {
        "RoadResponse",
        "RoadEndpoint",
        "assemble_urban_od_week",
        "RoadBuild",
        "UrbanODPlan",
        "CarAnalysisResult",
        "RoadCoordinate",
        "plan_urban_od_week",
        "UrbanODTask",
        "od_road_request",
        "normalize_road_response",
        "analyze_car_responses",
        "UrbanODObservation",
        "RoadRequest",
        "od_road_observation",
        "CarPlan",
        "UrbanODCollector",
        "plan_car_routes",
        "od_timetable_observation",
        "CarInputs",
        "RoadResult",
    }
)


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
    for domain in ("urban", "intercity", "freight", "pipeline", "comparison", "routing"):
        package = importlib.import_module(f"a2_engine.{domain}")
        for name in package.__all__:
            assert getattr(package, name) is getattr(a2_engine, name)


def test_fresh_import_orders_and_all_default_loaders_from_another_cwd(tmp_path: Path) -> None:
    modules = [
        "a2_engine.urban",
        "a2_engine.routing",
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
                    allowed: tuple[str, ...] = ("a2_engine.shared.", f"a2_engine.{domain}.")
                    if domain == "urban":
                        allowed += ("a2_engine.routing.",)
                    assert node.module.startswith(allowed), (
                        path,
                        node.module,
                    )
            elif isinstance(node, ast.Import):
                domain = path.relative_to(package).parts[0]
                for alias in node.names:
                    if alias.name.startswith("a2_engine"):
                        import_allowed: tuple[str, ...] = (
                            "a2_engine.shared.",
                            f"a2_engine.{domain}.",
                        )
                        if domain == "urban":
                            import_allowed += ("a2_engine.routing.",)
                        assert alias.name.startswith(import_allowed), path
    for domain in ("urban", "intercity", "freight", "pipeline", "comparison", "shared"):
        assert not (package / f"{domain}.py").exists()


def test_all_141_pre_structure_signatures_fields_and_defaults() -> None:
    """Snapshot captured at cd7572e before extracting any urban definition."""
    import hashlib
    from dataclasses import MISSING, fields

    expected = json.loads(
        (Path(__file__).parent / "fixtures/public_api_structure.json").read_text()
    )
    assert list(expected) == a2_engine.__all__
    assert len(expected) == 141
    for name, saved in expected.items():
        obj = getattr(a2_engine, name)

        def normalize(value: str) -> str:
            return re.sub(r"\ba2_engine(?:\.[a-z_]+)+\.([A-Z][A-Za-z0-9_]*)", r"\1", value)

        assert normalize(str(inspect.signature(obj))) == saved["signature"], name
        metadata = []
        if is_dataclass(obj):
            metadata = [
                {
                    "name": f.name,
                    "type": normalize(str(f.type)),
                    "default": "<required>" if f.default is MISSING else normalize(repr(f.default)),
                    "factory": "<none>"
                    if f.default_factory is MISSING
                    else normalize(str(f.default_factory)),
                    "kw_only": f.kw_only,
                    "init": f.init,
                }
                for f in fields(obj)
            ]
            assert getattr(obj, "__dataclass_params__").frozen == saved["frozen"], name
        assert (
            hashlib.sha256(
                json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            == saved["fields_hash"]
        ), name


def test_legacy_urban_imports_are_canonical_aliases() -> None:
    aliases = json.loads((Path(__file__).parent / "fixtures/urban_legacy_imports.json").read_text())
    for legacy, name, canonical in aliases:
        assert getattr(importlib.import_module(legacy), name) is getattr(
            importlib.import_module(canonical), name
        ), (legacy, name)


def test_fresh_urban_submodule_import_orders(tmp_path: Path) -> None:
    aliases = json.loads((Path(__file__).parent / "fixtures/urban_legacy_imports.json").read_text())
    modules = sorted({module for legacy, _, canonical in aliases for module in (legacy, canonical)})
    script = """
import importlib,json,sys
for module in json.loads(sys.argv[1]):
    importlib.import_module(module)
for legacy,name,canonical in json.loads(sys.argv[2]):
    assert getattr(importlib.import_module(legacy),name) is getattr(importlib.import_module(canonical),name)
import a2_engine
assert len(a2_engine.__all__)==141
"""
    for order in (modules, list(reversed(modules)), modules[::2] + modules[1::2]):
        subprocess.run(
            [sys.executable, "-c", script, json.dumps(order), json.dumps(aliases)],
            cwd=tmp_path,
            env=os.environ | {"PYTHONPATH": str(SOURCE)},
            check=True,
            capture_output=True,
            text=True,
        )


def test_urban_implementation_imports_concrete_modules() -> None:
    urban = SOURCE / "a2_engine/urban"
    packages = {"analysis", "valhalla", "walk", "car", "transit", "tau", "od", "districts"}
    compatibility = {"district_models", "district_inputs", "tau_models", "last_return"}
    for name in packages:
        assert (urban / name / "__init__.py").is_file()
        assert not (urban / (name + ".py")).exists()
    for name in compatibility:
        tree = ast.parse((urban / (name + ".py")).read_text())
        assert not any(isinstance(n, (ast.FunctionDef, ast.ClassDef)) for n in ast.walk(tree))
    forbidden = {"a2_engine.urban." + n for n in packages | compatibility}
    for path in urban.rglob("*.py"):
        if path.name == "__init__.py" or path.parent == urban and path.stem in compatibility:
            continue
        for n in ast.walk(ast.parse(path.read_text())):
            if isinstance(n, ast.ImportFrom):
                assert n.module not in forbidden, (path, n.module)
