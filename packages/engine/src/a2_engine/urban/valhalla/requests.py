"""valhalla / requests — canonical urban implementation."""

import json

from a2_engine.urban.models import references
from a2_engine.urban.valhalla.codec import canonical_json, digest
from a2_engine.urban.valhalla.models import ValhallaBuild, ValhallaCorrelation, WalkRouteRequest


def make_route_request(
    point_id: str,
    destination_id: str,
    build: ValhallaBuild,
    search_parameters_id: str,
    origin: ValhallaCorrelation,
    destination: ValhallaCorrelation,
) -> WalkRouteRequest:
    if origin.build != build or destination.build != build:
        raise ValueError("CORRELATION_BUILD_MISMATCH")
    locations = [
        dict(
            c.correlated_location.location(),
            type="break",
            radius=0,
            search_cutoff=1,
            node_snap_tolerance=0,
            search_filter={"exclude_ferry": True},
        )
        for c in (origin, destination)
    ]
    payload = {
        "locations": locations,
        "costing": "pedestrian",
        "costing_options": {"pedestrian": json.loads(build.pedestrian_options_json)},
        "units": "kilometers",
        "format": "json",
        "alternates": 0,
        "directions_type": "maneuvers",
    }
    request_id = digest(
        [
            point_id,
            destination_id,
            build.__dict__,
            search_parameters_id,
            origin.correlation_id,
            destination.correlation_id,
            payload,
        ]
    )
    payload["id"] = request_id
    return WalkRouteRequest(
        request_id,
        point_id,
        destination_id,
        build,
        search_parameters_id,
        origin,
        destination,
        canonical_json(payload),
        references(build.evidence_refs + origin.evidence_refs + destination.evidence_refs),
    )
