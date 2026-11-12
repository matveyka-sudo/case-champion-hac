"""Public selection constraints derived from already verified runtime profiles."""

from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from a2_contracts.v2.api import AnalysisPreset, CompactAnalysisScope, UrbanPassengerScope
from a2_contracts.v2.base import TrafficMode
from a2_contracts.v2.requests import (
    AnalysisRequest,
    IntercityCargoRequest,
    IntercityPassengerRequest,
    UrbanCargoRequest,
    UrbanPassengerRequest,
)

from a2_api.adapters.product_inputs import (
    DeliveryInputs,
    EngineInputs,
    IntercityInputs,
    PipelineInputs,
    UrbanCargoInputs,
)
from a2_api.adapters.product_scenario_inputs import ScenarioPlan, document_matches_request
from a2_api.core.errors import DomainError
from a2_api.core.registry import ProductRegistry

if TYPE_CHECKING:
    from a2_api.adapters.product_runtime import Runtime


def analysis_presets(runtime: "Runtime") -> list[AnalysisPreset]:
    traffic: set[TrafficMode] = {"static"}
    for profile, _ in runtime.profiles.values():
        traffic.update(profile.traffic_modes)
    registry = ProductRegistry(
        territories=runtime.manifest.territories,
        areas=runtime.manifest.areas,
        snapshots=list(runtime.snapshots.values()),
        cargo_profiles=runtime.manifest.cargo_profiles,
        supported_traffic=sorted(traffic),
    )
    result = []
    for preset in runtime.manifest.presets:
        snapshot = runtime.snapshots.get(preset.request.snapshot_id or "")
        if snapshot is None:
            continue
        try:
            if registry.normalize(preset.request) != preset.request:
                continue
            registry.resolve_snapshot(preset.request)
            profile, _ = runtime.select(preset.request, snapshot)
        except DomainError:
            continue
        operations = {
            plan.operation.template_id if isinstance(plan, ScenarioPlan) else plan.operation_type
            for document in runtime.scenario_documents.get(profile.profile_id, ())
            if document_matches_request(document, preset.request)
            for plan in document.plans
        }
        if (
            isinstance(preset.request, UrbanPassengerRequest)
            and preset.request.passenger_mode != "public_transport"
        ):
            operations.clear()
        result.append(
            preset.model_copy(
                update={
                    "scenario_operations": [
                        op for op in preset.scenario_operations if op in operations
                    ],
                }
            )
        )
    return result


def urban_passenger_scopes(
    runtime: "Runtime", *, certified_service_date: date | None = None
) -> list[UrbanPassengerScope]:
    """No routing, files or provider calls; omit unregistered or unbound inputs.

    The caller supplies the date from the verified certified proof, never from
    a user request. Request preflight remains authoritative (including budgets
    and overlapping-profile rejection). These scopes make no coverage claim.
    """
    scopes = []
    for profile, _ in sorted(runtime.profiles.values(), key=lambda row: row[0].profile_id):
        if profile.analysis_kind != "urban_passenger" or profile.city_id is None:
            continue
        snapshot = runtime.snapshots.get(profile.snapshot_id)
        if snapshot is None:
            continue
        city = next((t for t in snapshot.territories if t.territory_id == profile.city_id), None)
        if city is None:
            continue
        zone = ZoneInfo(city.timezone)
        start, end = profile.valid_from, profile.valid_to
        if snapshot.valid_period.start is not None:
            start = max(start, datetime.combine(snapshot.valid_period.start, time.min, zone))
        if snapshot.valid_period.end is not None:
            end = min(
                end,
                datetime.combine(snapshot.valid_period.end + timedelta(days=1), time.min, zone),
            )
        if profile.executor == "certified_walk_model":
            if certified_service_date is None:
                continue
            start = max(start, datetime.combine(certified_service_date, time.min, zone))
            end = min(
                end,
                datetime.combine(certified_service_date + timedelta(days=1), time.min, zone),
            )
        if start >= end:
            continue
        scopes.append(
            UrbanPassengerScope.model_validate(
                {
                    "scope_id": profile.profile_id,
                    "snapshot_id": profile.snapshot_id,
                    "city_id": profile.city_id,
                    "passenger_modes": profile.passenger_modes,
                    "categories": profile.categories,
                    "traffic_modes": profile.traffic_modes,
                    "valid_from": start.astimezone(UTC),
                    "valid_to": end.astimezone(UTC),
                    # Runtime.select currently accepts only area_id=None.
                    "whole_city": True,
                    "area_ids": [],
                }
            )
        )
    return scopes


def compact_modes(inputs: EngineInputs | None) -> list[str]:
    if isinstance(inputs, IntercityInputs):
        if inputs.mixed_only:
            return [inputs.mixed_request_mode]
        scheduled = inputs.scope.applicable_modes if inputs.timetable.runs else ()
        return sorted(set(scheduled) | ({"private_car"} if inputs.car is not None else set()))
    if isinstance(inputs, DeliveryInputs):
        return sorted(inputs.scope.allowed_modes)
    if isinstance(inputs, PipelineInputs):
        return ["pipeline"]
    if isinstance(inputs, UrbanCargoInputs):
        return sorted({node.analysis_type for node in inputs.nodes})
    return []


def compact_transport_supported(inputs: EngineInputs | None, request: AnalysisRequest) -> bool:
    modes = set(compact_modes(inputs))
    if isinstance(request, IntercityPassengerRequest):
        return bool(modes) and set(request.passenger_modes or ()) <= modes
    if isinstance(request, IntercityCargoRequest):
        return bool(modes) and set(request.cargo_modes or ()) <= modes
    if isinstance(request, UrbanCargoRequest):
        return bool(modes) and set(request.freight_node_types or ()) <= modes
    return True


def compact_scopes(runtime: "Runtime") -> list[CompactAnalysisScope]:
    return [
        CompactAnalysisScope.model_validate(
            {
                **profile.model_dump(
                    include={
                        "analysis_kind",
                        "snapshot_id",
                        "valid_from",
                        "valid_to",
                        "city_id",
                        "territory_level",
                        "origin_id",
                        "destination_id",
                        "categories",
                        "cargo_profile_id",
                        "cargo_example",
                        "direction",
                        "cargo_transport",
                        "pipeline_product",
                    }
                ),
                "modes": profile.passenger_modes or compact_modes(inputs),
            }
        )
        for profile, inputs in runtime.profiles.values()
        if profile.compact
    ]
