"""Hash-pinned runtime inventory. Only the operator can select input files."""

import hashlib
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, fields, is_dataclass
from datetime import timedelta
from math import fsum
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from a2_contracts.common import canonical_hash
from a2_contracts.v2.api import AreaOption, Capabilities, TerritoryOption
from a2_contracts.v2.base import AnalysisKind, ContractModel, Identifier, Timestamp, TrafficMode
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.requests import (
    AnalysisRequest,
    CargoProfile,
    IntercityCargoRequest,
    IntercityPassengerRequest,
    UrbanCargoRequest,
    UrbanPassengerRequest,
)
from a2_contracts.v2.results import EvidenceRef
from pydantic import Field

from a2_api.adapters.product_inputs import (
    INPUT_ADAPTER,
    DeliveryInputs,
    EngineInputs,
    InputDocument,
    IntercityInputs,
    PipelineInputs,
    UrbanCargoInputs,
    UrbanInputs,
    candidate_is_static,
)
from a2_api.core.errors import DomainError
from a2_api.core.registry import ProductRegistry

MAX_DOCUMENT_BYTES = 32 * 1024 * 1024
MAX_RUNTIME_BYTES = 64 * 1024 * 1024


class FileRef(ContractModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern="^[a-f0-9]{64}$")


class SnapshotRef(FileRef):
    snapshot_id: Identifier
    content_hash: str = Field(pattern="^[a-f0-9]{64}$")


class Profile(ContractModel):
    profile_id: Identifier
    analysis_kind: AnalysisKind
    city_id: Identifier | None = None
    origin_id: Identifier | None = None
    destination_id: Identifier | None = None
    territory_level: Literal["city", "region"] = "city"
    passenger_modes: list[Literal["walk", "public_transport", "private_car"]] = Field(
        default_factory=list
    )
    categories: list[str] = Field(default_factory=list)
    cargo_profile_id: Identifier | None = None
    direction: Literal["inbound", "outbound"] | None = None
    cargo_transport: Literal["multimodal", "pipeline"] = "multimodal"
    pipeline_product: Literal["oil", "oil_products", "gas"] | None = None
    traffic_modes: list[TrafficMode] = ["static"]
    valid_from: Timestamp
    valid_to: Timestamp
    snapshot_id: Identifier
    snapshot_hash: str = Field(pattern="^[a-f0-9]{64}$")
    graph_fingerprint: str = Field(pattern="^[a-f0-9]{64}$")
    routing_profile_fingerprint: str = Field(pattern="^[a-f0-9]{64}$")
    executor: Literal["accepted_inputs", "walk_pilot"] = "accepted_inputs"
    inputs: FileRef | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    static_observations: bool = False
    max_route_pairs: int = Field(gt=0, le=100000)


class Manifest(ContractModel):
    schema_version: Literal["waydex-product-runtime/1"]
    snapshots: list[SnapshotRef] = Field(max_length=128)
    territories: list[TerritoryOption] = Field(max_length=10000)
    areas: list[AreaOption] = Field(default_factory=list, max_length=10000)
    cargo_profiles: list[CargoProfile] = Field(default_factory=list, max_length=128)
    profiles: list[Profile] = Field(max_length=128)
    national_catalog_complete: bool = False


def unavailable(reason: str) -> DomainError:
    return DomainError(503, "data_unavailable", reason, retryable=True)


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def read_document(path: Path, expected_hash: str, budget: list[int] | None = None) -> Any:
    with path.open("rb") as source:
        limit = min(MAX_DOCUMENT_BYTES, budget[0]) if budget is not None else MAX_DOCUMENT_BYTES
        content = source.read(limit + 1)
    if budget is not None:
        budget[0] -= len(content)
        if budget[0] < 0:
            raise ValueError("RUNTIME_CUMULATIVE_BYTE_BUDGET_EXCEEDED")
    if len(content) > MAX_DOCUMENT_BYTES or hashlib.sha256(content).hexdigest() != expected_hash:
        raise ValueError("RUNTIME_DOCUMENT_HASH_OR_SIZE_MISMATCH")
    return json.loads(
        content,
        object_pairs_hook=_unique,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError("NONFINITE_JSON")),
    )


def _referenced(root: Path, reference: FileRef, budget: list[int]) -> Any:
    path = (root / reference.path).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("RUNTIME_REFERENCE_OUTSIDE_MANIFEST_DIRECTORY")
    return read_document(path, reference.sha256, budget)


def input_fingerprints(
    profile: Profile, inputs: EngineInputs, registry: ProductRegistry
) -> tuple[str, str]:
    """Bind actual normalized facts and costing semantics, not symbolic labels.

    This hash is not a claim about a deployed routing tile store. Live executors
    must additionally verify their own physical build, as walk_pilot does.
    """
    graph = canonical_hash(INPUT_ADAPTER.dump_python(inputs, mode="json"))
    costing = canonical_hash(
        {
            "config_hash": registry.config_hash,
            "kind": inputs.kind,
            "traffic_modes": profile.traffic_modes,
            "static_observations": profile.static_observations,
            "passenger_modes": profile.passenger_modes,
        }
    )
    return graph, costing


def _walk(value: Any) -> Iterator[Any]:
    if is_dataclass(value) and not isinstance(value, type):
        yield value
        for field in fields(value):
            yield from _walk(getattr(value, field.name))
    elif isinstance(value, ContractModel):
        yield value
        for name in type(value).model_fields:
            yield from _walk(getattr(value, name))
    elif isinstance(value, (tuple, list, set, frozenset)):
        for item in value:
            yield from _walk(item)


def validate_inputs(
    profile: Profile, inputs: EngineInputs, snapshot: AcceptedData, registry: ProductRegistry
) -> None:
    expected_kind = "pipeline" if profile.cargo_transport == "pipeline" else profile.analysis_kind
    if inputs.kind != expected_kind:
        raise ValueError("RUNTIME_INPUT_KIND_MISMATCH")
    if (profile.graph_fingerprint, profile.routing_profile_fingerprint) != input_fingerprints(
        profile, inputs, registry
    ):
        raise ValueError("RUNTIME_ACTUAL_INPUT_OR_PROFILE_FINGERPRINT_MISMATCH")
    known_evidence = {row.evidence_id for row in snapshot.evidence}
    verified_evidence = {row.evidence_id for row in snapshot.evidence if row.verified}
    known_evidence.update(row.evidence_id for row in profile.evidence)
    for item in _walk(inputs):
        if hasattr(item, "snapshot_id") and item.snapshot_id != snapshot.snapshot_id:
            raise ValueError("RUNTIME_INPUT_SNAPSHOT_MISMATCH")
        if hasattr(item, "graph_version") and (
            item.graph_version != snapshot.provenance.graph_version
        ):
            raise ValueError("RUNTIME_INPUT_GRAPH_MISMATCH")
        if hasattr(item, "methodology_version") and (
            item.methodology_version != registry.methodology.version
        ):
            raise ValueError("RUNTIME_INPUT_METHODOLOGY_MISMATCH")
        if hasattr(item, "config_hash") and item.config_hash != registry.config_hash:
            raise ValueError("RUNTIME_INPUT_CONFIG_MISMATCH")
        if (
            hasattr(item, "calendar_version")
            and item.calendar_version != snapshot.provenance.calendar_version
        ):
            raise ValueError("RUNTIME_INPUT_CALENDAR_MISMATCH")
        if (
            hasattr(item, "population_version")
            and item.population_version != snapshot.provenance.population_version
        ):
            raise ValueError("RUNTIME_INPUT_POPULATION_MISMATCH")
        for name in ("evidence_refs", "source_refs", "mapping_evidence_refs"):
            refs = getattr(item, name, ())
            if refs and not set(refs) <= known_evidence:
                raise ValueError("RUNTIME_INPUT_EVIDENCE_UNRESOLVED")
        complete_flags = (
            "complete",
            "catalog_complete",
            "search_complete",
            "roster_complete",
            "modes_complete",
            "schedule_complete",
            "calendar_complete",
            "footpaths_complete",
            "transfers_complete",
            "continuations_complete",
            "coverage_complete",
            "minimum_confirmed",
        )
        if (
            any(getattr(item, flag, None) is True for flag in complete_flags)
            or getattr(item, "coverage", None) == "full"
        ):
            proof = set(getattr(item, "evidence_refs", ())) | set(getattr(item, "source_refs", ()))
            if not proof or not proof <= verified_evidence:
                raise ValueError("FULL_SCOPE_REQUIRES_VERIFIED_CANONICAL_EVIDENCE")
    if isinstance(inputs, UrbanInputs):
        if (
            inputs.context.city_id != profile.city_id
            or inputs.context.traffic_mode not in profile.traffic_modes
            or inputs.area.city_id != profile.city_id
        ):
            raise ValueError("RUNTIME_URBAN_SCOPE_MISMATCH")
        points = {p.point_id: p for p in snapshot.sample_points}
        destinations = {d.destination_id: d for d in snapshot.destinations}
        cells = {cell.cell_id: cell for cell in snapshot.population_cells}
        if (
            not {p.point_id for p in inputs.points} <= points.keys()
            or not {d.destination_id for d in inputs.destinations} <= destinations.keys()
        ):
            raise ValueError("RUNTIME_URBAN_ENDPOINT_OUTSIDE_SNAPSHOT")
        for point in inputs.points:
            original = points[point.point_id]
            cell = cells[original.cell_id]
            if (
                point.cell_id,
                point.access_node_id,
                point.access_distance_m,
                point.territory_id,
                point.population,
                point.population_year,
                point.population_method,
                point.origin_node_id,
            ) != (
                original.cell_id,
                original.access_node_id,
                original.access_distance_m,
                original.territory_id,
                cell.population,
                cell.population_year,
                cell.population_method,
                "physical:point:" + point.point_id,
            ):
                raise ValueError("RUNTIME_POINT_SEMANTICS_DIFFER_FROM_ACCEPTED_SNAPSHOT")
        for destination in inputs.destinations:
            original_destination = destinations[destination.destination_id]
            if destination.eligible is not True:
                raise ValueError("ACCEPTED_DESTINATION_ELIGIBILITY_CANNOT_BE_OVERRIDDEN")
            if destination.in_service_scope is not True:
                raise ValueError("ACCEPTED_DESTINATION_SERVICE_SCOPE_CANNOT_BE_OVERRIDDEN")
            if (
                destination.object_identity,
                destination.categories,
                destination.access_node_id,
                destination.territory_id,
                destination.physical_node_id,
            ) != (
                original_destination.object_identity,
                frozenset(original_destination.categories),
                original_destination.access_node_id,
                original_destination.territory_id,
                "physical:destination:" + destination.destination_id,
            ):
                raise ValueError("RUNTIME_DESTINATION_SEMANTICS_DIFFER_FROM_ACCEPTED_SNAPSHOT")
        selected_points = {point.point_id for point in inputs.points}
        original_points = {
            point.point_id
            for point in snapshot.sample_points
            if point.territory_id == profile.city_id
        }
        selected_cells = {point.cell_id for point in inputs.points}
        relevant_cells = [
            cell for cell in snapshot.population_cells if cell.territory_id == profile.city_id
        ]
        omitted_cells = [cell for cell in relevant_cells if cell.cell_id not in selected_cells]
        excluded_population = (
            None
            if any(cell.population is None for cell in omitted_cells)
            else fsum(cell.population for cell in omitted_cells if cell.population is not None)
        )
        if (
            inputs.population_coverage.excluded_population != excluded_population
            or inputs.population_coverage.point_ids != frozenset(selected_points)
        ):
            raise ValueError("RUNTIME_ORIGINAL_POPULATION_BASIS_MISMATCH")
        if inputs.population_coverage.complete and (
            selected_points != original_points
            or excluded_population != 0
            or any(cell.population is None for cell in relevant_cells)
        ):
            raise ValueError("EXCLUDED_OR_UNKNOWN_POPULATION_CANNOT_CLAIM_COMPLETE")
        zone = ZoneInfo(snapshot.timezone)
        first_day = profile.valid_from.astimezone(zone).date()
        last_day = (profile.valid_to - timedelta(microseconds=1)).astimezone(zone).date()
        for coverage in inputs.coverage:
            expected_destinations = {
                row.destination_id
                for row in snapshot.destinations
                if coverage.scope.category in row.categories
            }
            full_source_scopes = [
                scope
                for scope in snapshot.coverage_by_mode_date_category
                if profile.city_id in scope.territory_ids
                and coverage.scope.passenger_mode in scope.modes
                and (not scope.categories or coverage.scope.category in scope.categories)
                and scope.completeness == "full"
                and bool(scope.source_refs)
                and set(scope.source_refs) <= verified_evidence
                and scope.valid_period.start is not None
                and scope.valid_period.end is not None
                and scope.valid_period.start <= first_day <= last_day <= scope.valid_period.end
                and set(scope.source_refs) <= set(coverage.evidence_refs)
            ]
            if coverage.catalog_complete and not full_source_scopes:
                raise ValueError("PARTIAL_SOURCE_COVERAGE_CANNOT_CLAIM_COMPLETE_CATALOG")
            if (
                coverage.catalog_complete
                and coverage.catalog_destination_ids != expected_destinations
            ):
                raise ValueError("PARTIAL_DESTINATION_CATALOG_CANNOT_CLAIM_COMPLETE")
        territory = next(t for t in snapshot.territories if t.territory_id == profile.city_id)
        if inputs.area.area_km2 != territory.area_km2:
            raise ValueError("RUNTIME_ADMINISTRATIVE_AREA_MISMATCH")
        if not inputs.points or not inputs.destinations or not inputs.coverage:
            raise ValueError("RUNTIME_URBAN_FACTS_REQUIRED")
        if any(c.passenger_mode not in profile.passenger_modes for c in inputs.candidates):
            raise ValueError("RUNTIME_URBAN_MODE_MISMATCH")
        if inputs.candidates and (
            not profile.static_observations or profile.traffic_modes != ["static"]
        ):
            raise ValueError("STATIC_OBSERVATION_VALIDITY_PROOF_REQUIRED")
        if any(not candidate_is_static(candidate) for candidate in inputs.candidates):
            raise ValueError("SCHEDULED_CANDIDATES_REQUIRE_DATED_TIMETABLE_RECOMPUTATION")
        if "public_transport" in profile.passenger_modes and inputs.timetable is None:
            raise ValueError("DATED_TIMETABLE_REQUIRED_FOR_PUBLIC_TRANSPORT")
    elif isinstance(inputs, IntercityInputs):
        scope = inputs.scope.comparison_scope
        if (scope.origin_territory_id, scope.destination_territory_id) != (
            profile.origin_id,
            profile.destination_id,
        ):
            raise ValueError("RUNTIME_INTERCITY_SCOPE_MISMATCH")
        if (
            inputs.scope.territory_kind
            != {"city": "cities", "region": "regions"}[profile.territory_level]
        ):
            raise ValueError("RUNTIME_INTERCITY_LEVEL_MISMATCH")
    elif isinstance(inputs, UrbanCargoInputs):
        if inputs.profile.profile_id != profile.cargo_profile_id or (
            inputs.coverage.direction != profile.direction
        ):
            raise ValueError("RUNTIME_CARGO_PROFILE_OR_DIRECTION_MISMATCH")
        accepted_ids = {
            site.site_id for site in snapshot.freight_sites if site.territory_id == profile.city_id
        }
        if not set(inputs.coverage.expected_site_ids) <= accepted_ids:
            raise ValueError("RUNTIME_CARGO_SITE_OUTSIDE_SNAPSHOT")
        if inputs.coverage.roster_complete and (
            set(inputs.coverage.expected_site_ids) != accepted_ids
            or {site.site_id for site in inputs.sites} != accepted_ids
        ):
            raise ValueError("OMITTED_ACCEPTED_SITE_CANNOT_CLAIM_COMPLETE_ROSTER")
        if {site.site_id for site in inputs.sites} - set(inputs.coverage.expected_site_ids):
            raise ValueError("RUNTIME_CARGO_SITE_OUTSIDE_DECLARED_ROSTER")
        territory = next(t for t in snapshot.territories if t.territory_id == profile.city_id)
        if inputs.area_km2 != territory.area_km2:
            raise ValueError("RUNTIME_CARGO_ADMINISTRATIVE_AREA_MISMATCH")
        nodes = {
            node.freight_node_id: node
            for node in snapshot.freight_nodes
            if node.territory_id == profile.city_id
        }
        for node in inputs.nodes:
            if node.node_id not in nodes or node.canonical_kind != nodes[node.node_id].kind:
                raise ValueError("RUNTIME_FREIGHT_NODE_SEMANTICS_MISMATCH")
        canonical_kinds = {
            "port": "port",
            "rail_freight_station": "rail_terminal",
            "terminal": "logistics_terminal",
            "logistics_center": "logistics_terminal",
        }
        for kind in inputs.coverage.complete_node_types:
            expected_nodes = {
                node.freight_node_id
                for node in nodes.values()
                if node.kind == canonical_kinds.get(kind)
            }
            supplied_nodes = {node.node_id for node in inputs.nodes if node.analysis_type == kind}
            if supplied_nodes != expected_nodes:
                raise ValueError("OMITTED_ACCEPTED_NODE_CANNOT_CLAIM_COMPLETE_NODE_TYPE")
        zone = ZoneInfo(snapshot.timezone)
        first_day = profile.valid_from.astimezone(zone).date()
        last_day = (profile.valid_to - timedelta(microseconds=1)).astimezone(zone).date()
        if inputs.coverage.roster_complete or inputs.coverage.complete_node_types:
            full_sources = [
                scope
                for scope in snapshot.coverage_by_mode_date_category
                if profile.city_id in scope.territory_ids
                and "road" in scope.modes
                and not scope.categories
                and scope.completeness == "full"
                and bool(scope.source_refs)
                and set(scope.source_refs) <= verified_evidence
                and scope.valid_period.start is not None
                and scope.valid_period.end is not None
                and scope.valid_period.start <= first_day <= last_day <= scope.valid_period.end
                and set(scope.source_refs) <= set(inputs.coverage.evidence_refs)
            ]
            if not full_sources:
                raise ValueError("PARTIAL_CARGO_SOURCE_CANNOT_CLAIM_COMPLETE_INVENTORY")
        if any(route.requested_at != inputs.routes[0].requested_at for route in inputs.routes):
            raise ValueError("MIXED_STATIC_ROUTE_OBSERVATIONS")
    elif isinstance(inputs, (DeliveryInputs, PipelineInputs)):
        if (inputs.scope.origin_territory_id, inputs.scope.destination_territory_id) != (
            profile.origin_id,
            profile.destination_id,
        ):
            raise ValueError("RUNTIME_CARGO_BOUNDARY_MISMATCH")
        if (
            isinstance(inputs, DeliveryInputs)
            and inputs.profile.profile_id != profile.cargo_profile_id
        ):
            raise ValueError("RUNTIME_CARGO_PROFILE_MISMATCH")
        if isinstance(inputs, PipelineInputs) and inputs.scope.product != profile.pipeline_product:
            raise ValueError("RUNTIME_PIPELINE_PRODUCT_MISMATCH")
        access_nodes = {node.node_id: node for node in snapshot.access_nodes}
        if (
            not set(inputs.scope.origin_node_ids) <= access_nodes.keys()
            or not set(inputs.scope.destination_node_ids) <= access_nodes.keys()
        ):
            raise ValueError("RUNTIME_CARGO_BOUNDARY_OUTSIDE_ACCEPTED_SNAPSHOT")
        if isinstance(inputs, PipelineInputs):
            accepted = {row.connection_id: row for row in snapshot.pipeline_connections}
            for observation in inputs.observations:
                connection = observation.connection
                original_connection = accepted.get(connection.connection_id)
                if original_connection is None or (
                    connection.product,
                    connection.origin.node_id,
                    connection.destination.node_id,
                ) != (
                    original_connection.product,
                    original_connection.from_node_id,
                    original_connection.to_node_id,
                ):
                    raise ValueError("RUNTIME_PIPELINE_CONNECTION_SEMANTICS_MISMATCH")
                if connection.state == "confirmed" and not (
                    original_connection.connection_confirmed is True
                    and original_connection.direction_confirmed is True
                ):
                    raise ValueError("RUNTIME_PIPELINE_CONNECTION_UNCONFIRMED")
                for measurement, quantity in (
                    (observation.volume, original_connection.volume),
                    (observation.capacity, original_connection.capacity),
                ):
                    if measurement.value is not None and (
                        quantity is None
                        or (
                            measurement.value,
                            measurement.basis.unit,
                            measurement.basis.product,
                            measurement.period_start_at.date(),
                            measurement.period_end_at.date(),
                        )
                        != (
                            quantity.value,
                            quantity.unit,
                            quantity.product,
                            quantity.period.start,
                            quantity.period.end,
                        )
                    ):
                        raise ValueError("RUNTIME_PIPELINE_QUANTITY_SEMANTICS_MISMATCH")
    if isinstance(inputs, (DeliveryInputs, UrbanCargoInputs)):
        accepted_profile = registry.cargo_profiles.get(inputs.profile.profile_id)
        if (
            accepted_profile is None
            or accepted_profile.kind != "delivery"
            or (
                inputs.profile.goods,
                inputs.profile.urgency,
                inputs.profile.temperature.controlled,
                inputs.profile.temperature.min_c,
                inputs.profile.temperature.max_c,
                set(inputs.profile.handling_requirements),
            )
            != (
                accepted_profile.food_class,
                accepted_profile.urgency,
                accepted_profile.temperature_requirements.controlled,
                accepted_profile.temperature_requirements.min_c,
                accepted_profile.temperature_requirements.max_c,
                set(accepted_profile.handling_requirements),
            )
        ):
            raise ValueError("RUNTIME_CARGO_PROFILE_CONTENT_MISMATCH")


@dataclass(frozen=True)
class Runtime:
    digest: str
    manifest: Manifest
    snapshots: dict[str, AcceptedData]
    profiles: dict[str, tuple[Profile, EngineInputs | None]]
    unavailable_kinds: frozenset[str]

    def select(
        self, request: AnalysisRequest, snapshot: AcceptedData
    ) -> tuple[Profile, EngineInputs | None]:
        matches = []
        for profile, inputs in self.profiles.values():
            if (
                profile.analysis_kind != request.analysis_kind
                or profile.snapshot_id != snapshot.snapshot_id
                or request.traffic_mode not in profile.traffic_modes
                or not profile.valid_from <= request.departure_at < profile.valid_to
            ):
                continue
            if isinstance(request, UrbanPassengerRequest):
                match = (
                    request.city_id == profile.city_id
                    and request.passenger_mode in profile.passenger_modes
                    and set(request.categories) <= set(profile.categories)
                    and request.area_id is None
                )
            elif isinstance(request, UrbanCargoRequest):
                match = (
                    request.city_id == profile.city_id
                    and request.cargo_profile_id == profile.cargo_profile_id
                    and request.direction == profile.direction
                    and request.area_id is None
                )
            else:
                match = (
                    request.origin_id == profile.origin_id
                    and request.destination_id == profile.destination_id
                    and request.territory_level == profile.territory_level
                )
                if isinstance(request, IntercityCargoRequest):
                    match = match and (
                        request.cargo_profile_id == profile.cargo_profile_id
                        and request.cargo_transport == profile.cargo_transport
                        and request.pipeline_product == profile.pipeline_product
                    )
            if match:
                matches.append((profile, inputs))
        if not matches:
            raise unavailable("RUNTIME_INPUTS_UNAVAILABLE_FOR_REQUEST")
        if len(matches) != 1:
            raise unavailable("RUNTIME_PROFILE_SCOPE_AMBIGUOUS")
        profile, inputs = matches[0]
        if isinstance(inputs, UrbanInputs) and isinstance(request, UrbanPassengerRequest):
            destinations = [
                d for d in inputs.destinations if set(request.categories) & d.categories
            ]
            if len(inputs.points) * len(destinations) > profile.max_route_pairs:
                raise unavailable("APPROVED_ROUTE_PAIR_BUDGET_EXCEEDED")
        if isinstance(inputs, UrbanCargoInputs):
            if not profile.static_observations or any(
                route.requested_at != request.departure_at for route in inputs.routes
            ):
                raise unavailable("STATIC_TRUCK_OBSERVATION_DEPARTURE_OR_INVARIANCE_UNAVAILABLE")
        if isinstance(inputs, IntercityInputs) and isinstance(request, IntercityPassengerRequest):
            wants_car = request.passenger_modes is None or "private_car" in request.passenger_modes
            if (
                wants_car
                and inputs.car is not None
                and (
                    inputs.car_departure_at != request.departure_at
                    or inputs.car.traffic_mode != request.traffic_mode
                )
            ):
                if request.passenger_modes == ["private_car"]:
                    raise unavailable("ACCEPTED_CAR_OBSERVATION_DEPARTURE_UNAVAILABLE")
            if request.passenger_modes == ["private_car"]:
                if inputs.car is None:
                    raise unavailable("ACCEPTED_PRIVATE_CAR_INPUTS_UNAVAILABLE")
                if request.origin_node_ids is not None and (
                    inputs.car.origin_node_id not in request.origin_node_ids
                    or inputs.car.destination_node_id not in (request.destination_node_ids or ())
                ):
                    raise unavailable("ACCEPTED_PRIVATE_CAR_BOUNDARY_UNAVAILABLE")
                return profile, inputs
            if request.passenger_modes is not None and (
                "mixed" in request.passenger_modes
                or not set(request.passenger_modes) & set(inputs.scope.applicable_modes)
            ):
                raise unavailable("REQUESTED_MODE_SELECTION_INPUTS_UNAVAILABLE")
        if isinstance(inputs, DeliveryInputs) and isinstance(request, IntercityCargoRequest):
            if request.cargo_modes is not None and (
                "mixed" in request.cargo_modes
                or not set(request.cargo_modes) & set(inputs.scope.allowed_modes)
            ):
                raise unavailable("REQUESTED_MODE_SELECTION_INPUTS_UNAVAILABLE")
        return profile, inputs


def load_runtime() -> Runtime:
    path = os.environ.get("A2_PRODUCT_RUNTIME_PATH", "")
    digest = os.environ.get("A2_PRODUCT_RUNTIME_SHA256", "")
    if not path or len(digest) != 64:
        raise unavailable("HASH_PINNED_PRODUCT_RUNTIME_REQUIRED")
    try:
        source = Path(path)
        budget = [MAX_RUNTIME_BYTES]
        manifest = Manifest.model_validate(read_document(source, digest, budget))
        snapshots = {}
        for ref in manifest.snapshots:
            snapshot = AcceptedData.model_validate(_referenced(source.parent, ref, budget))
            if (
                snapshot.snapshot_id != ref.snapshot_id
                or (canonical_hash(snapshot.model_dump(mode="json")) != ref.content_hash)
                or snapshot.snapshot_id in snapshots
            ):
                raise ValueError("RUNTIME_SNAPSHOT_BINDING_MISMATCH")
            snapshots[snapshot.snapshot_id] = snapshot
        base = ProductRegistry(
            territories=manifest.territories,
            areas=manifest.areas,
            snapshots=list(snapshots.values()),
            cargo_profiles=manifest.cargo_profiles,
        )
        profiles = {}
        missing = set()
        seen = set()
        for profile in manifest.profiles:
            key = profile.profile_id
            if key in seen:
                raise ValueError("DUPLICATE_RUNTIME_REQUEST_PROFILE")
            seen.add(key)
            try:
                snapshot = snapshots[profile.snapshot_id]
                if canonical_hash(snapshot.model_dump(mode="json")) != profile.snapshot_hash:
                    raise ValueError("RUNTIME_PROFILE_SNAPSHOT_HASH_MISMATCH")
                if profile.valid_from >= profile.valid_to:
                    raise ValueError("RUNTIME_PROFILE_VALIDITY_REQUIRED")
                inputs = None
                if profile.executor == "accepted_inputs":
                    if profile.inputs is None:
                        raise ValueError("RUNTIME_SUPPLEMENT_REQUIRED")
                    inputs = InputDocument.model_validate(
                        _referenced(source.parent, profile.inputs, budget)
                    ).inputs
                    validate_inputs(profile, inputs, snapshot, base)
                else:
                    from a2_api.adapters import walk_pilot

                    if (
                        profile.city_id != "kaliningrad"
                        or profile.categories != ["school"]
                        or profile.passenger_modes != ["walk"]
                        or profile.traffic_modes != ["static"]
                        or walk_pilot.load_runtime().bundle_id != profile.snapshot_id
                    ):
                        raise ValueError("WALK_PILOT_SNAPSHOT_MISMATCH")
                profiles[key] = profile, inputs
            except (OSError, ValueError, TypeError, KeyError, DomainError):
                missing.add(profile.analysis_kind)
        return Runtime(digest, manifest, snapshots, profiles, frozenset(missing))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise unavailable("PRODUCT_RUNTIME_UNAVAILABLE_OR_INCONSISTENT") from exc


class RuntimeRegistry(ProductRegistry):
    def capabilities(self) -> Capabilities:
        result = super().capabilities()
        for item in result.analyses:
            if not item.calculation_available:
                item.reason_code = "RUNTIME_INPUTS_UNAVAILABLE"
        return result


def registry() -> ProductRegistry:
    runtime = load_runtime()
    traffic: set[TrafficMode] = {"static"}
    for profile, _ in runtime.profiles.values():
        traffic.update(profile.traffic_modes)
    return RuntimeRegistry(
        territories=runtime.manifest.territories,
        areas=runtime.manifest.areas,
        snapshots=list(runtime.snapshots.values()),
        cargo_profiles=runtime.manifest.cargo_profiles,
        national_catalog_complete=runtime.manifest.national_catalog_complete,
        ready_kinds=sorted({p.analysis_kind for p, _ in runtime.profiles.values()}),
        supported_traffic=sorted(traffic),
    )
