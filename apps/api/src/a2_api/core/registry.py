"""Published product configuration and explicit catalogs; no invented production data."""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo

from a2_contracts.common import canonical_hash, valid_timezone
from a2_contracts.v2.api import (
    AreaOption,
    Capabilities,
    KindCapability,
    SnapshotAvailability,
    TerritoryOption,
)
from a2_contracts.v2.base import (
    AnalysisKind,
    Category,
    ContractModel,
    MethodologyRef,
    PipelineProduct,
    Priority,
    TrafficMode,
)
from a2_contracts.v2.dataset import AcceptedData, Period, TrafficProfile
from a2_contracts.v2.requests import (
    AnalysisRequest,
    CargoProfile,
    DeliveryCargoProfile,
    IntercityCargoRequest,
    IntercityPassengerRequest,
    PipelineCargoProfile,
    UrbanCargoRequest,
    UrbanPassengerRequest,
)
from pydantic import JsonValue, model_validator

from a2_api.core.errors import DomainError


class MethodologyConfig(ContractModel):
    status: Literal["approved"]
    version: Literal["a2-methodology-v3"]
    parameters: dict[str, JsonValue]


class ProductConfig(ContractModel):
    spec_version: Literal["2026-11-08.1"]
    schema_version: Literal["2"]
    methodology_version: Literal["a2-methodology-v3"]
    intracity_cities: list[str]
    analysis_kinds: list[AnalysisKind]
    urban_modes: list[Literal["walk", "public_transport", "private_car"]]
    categories: list[Category]
    priority_levels: list[Priority]
    priority_weights: dict[Priority, int]
    time_step_minutes: Literal[15]
    grid_spacing_m: Literal[1000]
    cargo_modes: list[str]
    pipeline_products: list[PipelineProduct]
    methodologies: dict[AnalysisKind, MethodologyConfig]
    assistant: dict[str, JsonValue]
    traffic_modes: list[TrafficMode]
    traffic_default: TrafficMode

    @model_validator(mode="after")
    def complete_published_methods(self) -> Self:
        expected = {"urban_passenger", "intercity_passenger", "urban_cargo", "intercity_cargo"}
        if set(self.analysis_kinds) != expected or set(self.methodologies) != expected:
            raise ValueError("Published configuration must define all four approved methodologies")
        return self


class ProductRegistry:
    def __init__(
        self,
        *,
        product_path: Path | None = None,
        territories: Sequence[TerritoryOption] = (),
        areas: Sequence[AreaOption] = (),
        snapshots: Sequence[AcceptedData] = (),
        cargo_profiles: Sequence[CargoProfile] = (),
        supported_traffic: Sequence[TrafficMode] = ("static",),
        national_catalog_complete: bool = False,
        ready_kinds: Sequence[AnalysisKind] = (),
    ) -> None:
        path = product_path or Path(__file__).resolve().parents[5] / "config/product.json"
        raw = json.loads(path.read_text())
        self.product = ProductConfig.model_validate(raw)
        self.config_hash = canonical_hash(raw)
        self.methodology = MethodologyRef(
            version=self.product.methodology_version, config_hash=self.config_hash
        )
        self.catalog = {t.territory_id: t for t in territories}
        self.areas = {area.area_id: area for area in areas}
        self.snapshots = {s.snapshot_id: s for s in snapshots}
        self.cargo_profiles = {p.profile_id: p for p in cargo_profiles}
        if (
            len(self.catalog) != len(territories)
            or len(self.snapshots) != len(snapshots)
            or len(self.areas) != len(areas)
            or len(self.cargo_profiles) != len(cargo_profiles)
        ):
            raise ValueError("Duplicate catalog identity")
        for territory in self.catalog.values():
            valid_timezone(territory.timezone)
        self.supported_traffic = set(supported_traffic)
        if (
            not self.supported_traffic <= set(self.product.traffic_modes)
            or self.product.traffic_default not in self.supported_traffic
        ):
            raise ValueError(
                "Supported traffic must contain the published default and only published modes"
            )
        self.national_catalog_complete = national_catalog_complete
        self.ready_kinds = frozenset(ready_kinds)
        if not self.ready_kinds <= set(self.product.analysis_kinds):
            raise ValueError("Readiness must identify only published analysis kinds")

    def is_ready(self, kind: AnalysisKind) -> bool:
        """Explicit engine declaration plus data registration, never a full-coverage claim."""
        return kind in self.ready_kinds and bool(self.snapshots)

    def register_cargo_profile(self, profile: CargoProfile) -> None:
        if profile.profile_id in self.cargo_profiles:
            raise ValueError("Cargo profile identity already registered")
        self.cargo_profiles[profile.profile_id] = profile

    def capabilities(self) -> Capabilities:
        return Capabilities(
            schema_version="2",
            spec_version=self.product.spec_version,
            methodology=self.methodology,
            analyses=[
                KindCapability(
                    analysis_kind=kind,
                    contract_available=True,
                    calculation_available=self.is_ready(kind),
                    reason_code=None if self.is_ready(kind) else "DATA_OR_ENGINE_UNAVAILABLE",
                )
                for kind in self.product.analysis_kinds
            ],
            urban_city_names=self.product.intracity_cities,
            categories=self.product.categories,
            traffic_modes=sorted(self.supported_traffic),
            default_traffic_mode=self.product.traffic_default,
            pipeline_products=self.product.pipeline_products,
            territory_catalog_available=bool(self.catalog),
            national_catalog_complete=self.national_catalog_complete,
            cargo_profiles=list(self.cargo_profiles.values()),
            areas=list(self.areas.values()),
            snapshots=[
                SnapshotAvailability(
                    snapshot_id=snapshot.snapshot_id,
                    territory_ids=snapshot.geographic_scope,
                    valid_from=snapshot.valid_period.start,
                    valid_to=snapshot.valid_period.end,
                    graph_version=snapshot.provenance.graph_version,
                    calendar_version=snapshot.provenance.calendar_version,
                )
                for snapshot in self.snapshots.values()
            ],
            limitations=[]
            if self.national_catalog_complete
            else ["NATIONAL_TERRITORY_CATALOG_NOT_ACCEPTED"],
        )

    def territories(
        self, level: str | None = None, query: str | None = None
    ) -> list[TerritoryOption]:
        if not self.catalog:
            raise DomainError(
                503, "data_unavailable", "Territory catalog is not configured", retryable=True
            )
        return [
            t
            for t in self.catalog.values()
            if (level is None or t.level == level)
            and (query is None or query.casefold() in t.name.casefold())
        ]

    def territory(self, territory_id: str) -> TerritoryOption:
        if not self.catalog:
            raise DomainError(
                503, "data_unavailable", "Territory catalog is not configured", retryable=True
            )
        if territory_id not in self.catalog:
            raise DomainError(404, "unknown_id", "Unknown territory ID")
        return self.catalog[territory_id]

    def normalize(self, request: AnalysisRequest) -> AnalysisRequest:
        if request.methodology is not None and request.methodology != self.methodology:
            raise DomainError(
                409,
                "incompatible_versions",
                "Only published methodology/configuration is permitted",
            )
        if request.traffic_mode not in self.supported_traffic:
            raise DomainError(
                422,
                "unsupported_traffic_mode",
                "Requested traffic mode unavailable; select a separate supported run",
            )
        if isinstance(request, (UrbanPassengerRequest, UrbanCargoRequest)):
            city = self.territory(request.city_id)
            if city.level != "city" or city.name not in self.product.intracity_cities:
                raise DomainError(
                    422, "invalid_request", "Urban analysis city is outside the published scope"
                )
            if request.area_id is not None and (
                request.area_id not in self.areas
                or self.areas[request.area_id].city_id != request.city_id
            ):
                raise DomainError(404, "unknown_id", "Unknown area of the selected city")
        else:
            for territory_id in (request.origin_id, request.destination_id):
                if self.territory(territory_id).level != request.territory_level:
                    raise DomainError(
                        422, "invalid_request", "Both territories must have the selected level"
                    )
        if isinstance(request, (UrbanCargoRequest, IntercityCargoRequest)):
            profile = self.cargo_profiles.get(request.cargo_profile_id)
            if profile is None:
                raise DomainError(404, "unknown_id", "Unknown cargo profile")
            if isinstance(request, IntercityCargoRequest) and request.cargo_transport == "pipeline":
                if (
                    not isinstance(profile, PipelineCargoProfile)
                    or profile.product != request.pipeline_product
                ):
                    raise DomainError(
                        409, "incompatible_versions", "Pipeline product/profile mismatch"
                    )
            elif not isinstance(profile, DeliveryCargoProfile):
                raise DomainError(
                    409, "incompatible_versions", "Delivery analysis requires a delivery profile"
                )
        return request.model_copy(update={"methodology": self.methodology})

    def resolve_snapshot(self, request: AnalysisRequest) -> AcceptedData:
        if not self.snapshots:
            raise DomainError(
                503, "data_unavailable", "No accepted snapshot is configured", retryable=True
            )
        if request.snapshot_id is not None:
            selected = self.snapshots.get(request.snapshot_id)
            if selected is None:
                raise DomainError(404, "unknown_id", "Unknown immutable snapshot")
            if not self._snapshot_applies(request, selected):
                raise DomainError(
                    409,
                    "incompatible_versions",
                    "Selected snapshot does not prove the requested geography/date",
                )
            self.required_cache_valid_until(request, selected)
            return selected
        matches = [s for s in self.snapshots.values() if self._snapshot_applies(request, s)]
        if len(matches) != 1:
            raise DomainError(
                409 if len(matches) > 1 else 503,
                "data_unavailable",
                "Select an unambiguous accepted snapshot covering this scope",
                retryable=not matches,
            )
        selected = matches[0]
        self.required_cache_valid_until(request, selected)
        return selected

    def validate_group_child(self, request: UrbanPassengerRequest) -> None:
        """Check child-specific selectors after the group has pinned its accepted snapshot.

        The base registry accepts the child's unchanged scope and narrower category list.
        """

    def _request_days(self, request: AnalysisRequest) -> dict[str, date]:
        identifiers = (
            {request.city_id}
            if isinstance(request, (UrbanPassengerRequest, UrbanCargoRequest))
            else {request.origin_id, request.destination_id}
        )
        return {
            identifier: request.departure_at.astimezone(
                ZoneInfo(self.territory(identifier).timezone)
            ).date()
            for identifier in identifiers
        }

    @staticmethod
    def _period_contains(period: Period, day: date) -> bool:
        # A missing boundary is unknown applicability, never an infinite interval.
        return (
            period.start is not None
            and period.end is not None
            and period.start <= day <= period.end
        )

    def _snapshot_applies(self, request: AnalysisRequest, snapshot: AcceptedData) -> bool:
        days = self._request_days(request)
        return set(days) <= set(snapshot.geographic_scope) and all(
            self._period_contains(snapshot.valid_period, day) for day in days.values()
        )

    def required_cache_valid_until(
        self, request: AnalysisRequest, snapshot: AcceptedData
    ) -> datetime | None:
        """Preflight dynamic traffic; return source expiry for a live cache entry.

        This validates declared scope/date, graph, time bin and live freshness.
        It does not prove every traversed edge or choose a holiday/season model:
        the routing adapter must validate those against its actual path. No speed
        is synthesized, bus schedules are not modified, and static is never mixed
        into an unavailable requested traffic mode. The request date check does
        not claim coverage of the entire intercity departure/arrival horizon.
        """
        if request.traffic_mode not in self.supported_traffic:
            raise DomainError(
                422, "unsupported_traffic_mode", "Requested traffic mode is not configured"
            )
        if not self._snapshot_applies(request, snapshot):
            raise DomainError(
                409, "incompatible_versions", "Snapshot does not prove traffic scope/date"
            )
        if request.traffic_mode == "static":
            return None
        days = self._request_days(request)
        modes: set[str] = (
            {request.passenger_mode} if isinstance(request, UrbanPassengerRequest) else {"road"}
        )
        if "private_car" in modes:
            modes.add("road")
        if isinstance(request, IntercityPassengerRequest):
            modes = set(
                request.passenger_modes or ["road", "rail", "air", "water", "mixed", "private_car"]
            )
        elif isinstance(request, IntercityCargoRequest):
            modes = set(
                request.cargo_modes
                or (
                    ["pipeline"]
                    if request.cargo_transport == "pipeline"
                    else ["road", "rail", "air", "water", "mixed"]
                )
            )
        categories = (
            set(request.categories) if isinstance(request, UrbanPassengerRequest) else set()
        )
        for territory_id, day in days.items():
            scopes = [
                scope
                for scope in snapshot.coverage_by_mode_date_category
                if territory_id in scope.territory_ids
                and scope.completeness == "full"
                and set(scope.modes) & modes
                and self._period_contains(scope.valid_period, day)
            ]
            covered = {category for scope in scopes for category in scope.categories}
            if not scopes or (
                categories
                and not any(not s.categories for s in scopes)
                and not categories <= covered
            ):
                raise DomainError(
                    422,
                    "unsupported_traffic_mode",
                    "No accepted traffic coverage for requested scope/date",
                )
        local = request.departure_at.astimezone(ZoneInfo(snapshot.timezone))
        time_bin = local.hour * 4 + local.minute // 15
        nodes = {node.node_id: node for node in snapshot.access_nodes}
        edges = {edge.edge_id: edge for edge in snapshot.reference_edges}

        def relevant(profile: TrafficProfile) -> bool:
            edge = edges.get(profile.directed_edge_id)
            if edge is None:
                return False
            for node_id in (edge.from_node_id, edge.to_node_id):
                node = nodes.get(node_id)
                if node is None:
                    continue
                territory = self.catalog.get(node.territory_id)
                if node.territory_id in days or (territory and territory.parent_region_id in days):
                    return True
            return False

        profiles = [
            profile
            for profile in snapshot.traffic_profiles
            if profile.traffic_mode == request.traffic_mode
            and profile.status == "available"
            and profile.graph_version == snapshot.provenance.graph_version
            and profile.local_15min_bin == time_bin
            and relevant(profile)
        ]
        if request.traffic_mode == "historical":
            profiles = [
                p
                for p in profiles
                if self._period_contains(p.observed_period, local.date())
                and any(o.date == local.date() for o in p.observations)
            ]
        if not profiles:
            raise DomainError(
                422,
                "unsupported_traffic_mode",
                "No matching accepted traffic profile; select a separate static run",
            )
        if request.traffic_mode != "live":
            return None
        now = datetime.now(UTC)
        instant = request.departure_at.astimezone(UTC)
        deadlines = []
        for profile in profiles:
            if (
                profile.observed_at is None
                or profile.valid_until is None
                or not (
                    profile.observed_at <= instant < profile.valid_until
                    and profile.observed_at <= now < profile.valid_until
                )
            ):
                raise DomainError(
                    422,
                    "unsupported_traffic_mode",
                    "Live traffic is stale or does not cover the requested moment",
                )
            deadlines.append(profile.valid_until)
        return min(deadlines)
