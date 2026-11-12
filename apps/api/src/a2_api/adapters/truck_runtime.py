"""Fill missing accepted freight OD pairs inside the existing calculation worker."""

import time
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING

import httpx
from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.requests import UrbanCargoRequest
from a2_contracts.v2.results import EvidenceRef
from a2_engine import CargoCompatibility, TruckRoute, TruckSearchReceipt
from a2_engine.shared.normalized import content_hash
from pydantic import HttpUrl

from a2_api.adapters.product_inputs import UrbanCargoInputs
from a2_api.adapters.truck_graph import verify_graph
from a2_api.core.jobs import V2JobContext
from a2_api.providers.road_observations import network_seconds, observation_store, observe

if TYPE_CHECKING:
    from a2_api.adapters.product_runtime import Profile


def validate_binding(profile: "Profile", inputs: UrbanCargoInputs, snapshot: AcceptedData) -> None:
    binding = profile.truck_routing
    if binding is None:
        return
    if (
        profile.executor != "accepted_inputs"
        or profile.analysis_kind != "urban_cargo"
        or profile.traffic_modes != ["static"]
        or not profile.static_observations
        or profile.city_id not in snapshot.geographic_scope
        or inputs.coverage.context.build_id != binding.graph_manifest_sha256
        or inputs.coverage.context.graph_version != snapshot.provenance.graph_version
        or inputs.receipts
        or profile.scenarios
        or inputs.profile.vehicle.vehicle_class != "truck"
    ):
        raise ValueError("TRUCK_EXECUTOR_SCOPE_OR_GRAPH_MISMATCH")
    if len(inputs.sites) * len(inputs.nodes) > min(profile.max_route_pairs, binding.max_pairs):
        raise ValueError("TRUCK_EXECUTOR_PAIR_LIMIT")
    accepted_sites = {s.site_id: s for s in snapshot.freight_sites}
    accepted_nodes = {n.freight_node_id: n for n in snapshot.freight_nodes}
    if any(
        s.site_id not in accepted_sites or accepted_sites[s.site_id].territory_id != profile.city_id
        for s in inputs.sites
    ) or any(
        n.node_id not in accepted_nodes or accepted_nodes[n.node_id].territory_id != profile.city_id
        for n in inputs.nodes
    ):
        raise ValueError("TRUCK_EXECUTOR_PHYSICAL_LOCATION_REQUIRED")


def route_scope_id(route: TruckRoute) -> str:
    return content_hash(
        (
            route.context,
            route.requirements_hash,
            route.from_id,
            route.to_id,
            route.requested_at,
            route.direction,
        )
    )


def bind_prepared_routes(
    inputs: UrbanCargoInputs, departure_at: datetime
) -> tuple[tuple[TruckRoute, ...], tuple[TruckSearchReceipt, ...]]:
    """Reuse accepted static costs, preserving the original compatibility interval."""
    original = {route.route_id: route for route in inputs.routes}
    routes = tuple(replace(route, requested_at=departure_at) for route in inputs.routes)
    rebound = {route.route_id: route for route in routes}
    receipts = []
    for receipt in inputs.receipts:
        route = original.get(receipt.route_id)
        if (
            route is None
            or receipt.route_fingerprint != route.fingerprint
            or receipt.search_scope_id != route_scope_id(route)
        ):
            raise ValueError("TRUCK_SEARCH_RECEIPT_MISMATCH")
        current = rebound[receipt.route_id]
        receipts.append(
            replace(
                receipt,
                route_fingerprint=current.fingerprint,
                search_scope_id=route_scope_id(current),
            )
        )
    return routes, tuple(receipts)


def hydrate_routes(
    profile: "Profile", inputs: UrbanCargoInputs, snapshot: AcceptedData, context: V2JobContext
) -> tuple[UrbanCargoInputs, list[EvidenceRef]]:
    validate_binding(profile, inputs, snapshot)
    binding = profile.truck_routing
    assert binding is not None
    request = context.normalized_request
    assert isinstance(request, UrbanCargoRequest)
    selected = set(request.freight_node_types or (n.analysis_type for n in inputs.nodes))
    existing = {(r.from_id, r.to_id) for r in inputs.routes}
    site_locations = {s.site_id: s.location for s in snapshot.freight_sites}
    node_locations = {n.freight_node_id: n.location for n in snapshot.freight_nodes}
    missing = []
    for site in sorted(inputs.sites, key=lambda s: s.site_id):
        for node in sorted(inputs.nodes, key=lambda n: n.node_id):
            if node.analysis_type not in selected:
                continue
            ends = (
                (site.site_id, site_locations[site.site_id]),
                (node.node_id, node_locations[node.node_id]),
            )
            if request.direction == "inbound":
                ends = ends[::-1]
            if (ends[0][0], ends[1][0]) not in existing:
                missing.append(ends)
    if not missing:
        return inputs, []
    started = time.monotonic()
    deadline = started + binding.max_seconds
    verify_graph(binding)
    vehicle = inputs.profile.vehicle
    routes, evidence = list(inputs.routes), []
    consecutive_failures = 0
    identity_context = {
        "snapshot_hash": context.snapshot_hash,
        "versions": context.versions.model_dump(mode="json"),
        "access_scope": context.access_scope,
        "requirements_hash": inputs.profile.requirements_hash,
        "routing_profile_fingerprint": profile.routing_profile_fingerprint,
        "departure_at": request.departure_at.isoformat(),
        "direction": request.direction,
    }
    with (
        observation_store(deadline) as store,
        httpx.Client(
            headers={"Accept-Encoding": "identity"}, trust_env=False, follow_redirects=False
        ) as client,
    ):
        for ends in missing:
            payload = {
                "locations": [{"lat": location.lat, "lon": location.lon} for _, location in ends],
                "costing": "truck",
                "costing_options": {
                    "truck": {
                        "speed_types": ["freeflow"],
                        "weight": vehicle.mass_kg / 1000,
                        "height": vehicle.height_m,
                        "width": vehicle.width_m,
                        "length": vehicle.length_m,
                    }
                },
                "units": "kilometers",
            }
            observation = observe(
                client,
                binding,
                store,
                {**identity_context, "from_id": ends[0][0], "to_id": ends[1][0]},
                payload,
                deadline,
            )
            seconds = network_seconds(observation)
            evidence_id = "road-observation-" + canonical_hash(observation.model_dump(mode="json"))
            evidence.append(
                EvidenceRef(
                    evidence_id=evidence_id,
                    source_id="valhalla-truck-" + binding.graph_manifest_sha256,
                    url=HttpUrl(str(binding.endpoint_url).rstrip("/") + "/route"),
                    retrieved_at=observation.retrieved_at,
                    observed_at=None,
                    section="truck-network-observation",
                )
            )
            route_id = "truck-" + observation.identity
            compatibility = CargoCompatibility(
                route_id + ":compatibility-unknown",
                inputs.coverage.context,
                inputs.profile.profile_id,
                inputs.profile.version,
                inputs.profile.requirements_hash,
                ends[0][0],
                ends[1][0],
                "road-network-model",
                request.direction,
                profile.valid_from,
                profile.valid_to,
                "unknown",
                (evidence_id,),
            )
            routes.append(
                TruckRoute(
                    route_id,
                    inputs.coverage.context,
                    inputs.profile.requirements_hash,
                    vehicle,
                    ends[0][0],
                    ends[1][0],
                    "road-network-model",
                    request.direction,
                    request.departure_at,
                    seconds,
                    None,
                    None,
                    "reachable" if seconds is not None else "unknown",
                    compatibility,
                    (evidence_id,),
                )
            )
            consecutive_failures = consecutive_failures + 1 if seconds is None else 0
            if consecutive_failures >= 3:
                # Leave the unqueried pairs unknown in ML1, rather than spending
                # the whole job deadline repeatedly probing an unavailable router.
                break
    return inputs.model_copy(
        update={"routes": tuple(replace(r, requested_at=request.departure_at) for r in routes)}
    ), evidence
