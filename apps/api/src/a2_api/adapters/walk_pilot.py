"""Hash-pinned, deliberately partial Kaliningrad pedestrian integration.

The source snapshot and built routing graph have separate identities. Source
versions remain canonical; build/profile/HTTP observations are saved as evidence.
No response from this adapter proves a minimum time or a city-wide index.
"""

import asyncio
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import httpx
from a2_contracts.common import canonical_hash
from a2_contracts.v2.api import TerritoryOption
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.journeys import JourneyResult, Leg
from a2_contracts.v2.map_data import AnalysisMapData, MapFeature, PointGeometry
from a2_contracts.v2.requests import AnalysisRequest, UrbanPassengerRequest
from a2_contracts.v2.results import AnalysisResult, EvidenceRef, Metric, Score, SubjectRef
from a2_data_pipeline.contract_v2 import load_accepted_data, registry_options
from a2_data_pipeline.store import load_bundle
from a2_engine.shared.config import load_urban_config, load_urban_journey_rules
from a2_engine.urban.valhalla.models import (
    ValhallaBuild,
    ValhallaCorrelation,
    WalkConnector,
    WalkCoordinate,
    WalkResponseEnvelope,
    WalkRouteRequest,
)
from a2_engine.urban.walk import (
    WalkAnalysisResult,
    analyze_walk_responses,
    plan_walk_routes,
    prepare_walk_inputs,
)
from pydantic import HttpUrl

from a2_api.core.config import RuntimeSettings
from a2_api.core.errors import DomainError
from a2_api.core.jobs import ClaimedJob, V2JobContext
from a2_api.core.registry import ProductRegistry

MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
PILOT_BUNDLE_SCHOOL_LIMITS = {
    "kaliningrad-urban-2026-11-10.2": 76,
    "kaliningrad-urban-2026-11-10.3": 77,
}


def _unavailable(message: str) -> DomainError:
    return DomainError(503, "engine_unavailable", message, retryable=True)


def _coordinate(row: dict[str, Any]) -> WalkCoordinate:
    return WalkCoordinate(row["lon"], row["lat"])


@dataclass(frozen=True)
class Runtime:
    digest: str
    bundle_id: str
    endpoint_url: str
    point_ids: tuple[str, ...]
    destination_ids: tuple[str, ...]
    build: ValhallaBuild
    connectors: tuple[WalkConnector, ...]
    correlations: tuple[ValhallaCorrelation, ...]
    evidence: tuple[EvidenceRef, ...]
    evidence_dir: Path
    status_expectation: dict[str, Any]


def load_runtime() -> Runtime:
    """Validate the operator's artifact on every boundary, including spawned workers."""
    path = os.environ.get("A2_WALK_PILOT_ARTIFACT_PATH")
    expected = os.environ.get("A2_WALK_PILOT_ARTIFACT_SHA256", "")
    evidence_dir = os.environ.get("A2_WALK_PILOT_EVIDENCE_DIR")
    if not path or len(expected) != 64 or not evidence_dir:
        raise _unavailable("Hash-pinned walk runtime and evidence directory are required")
    try:
        with Path(path).open("rb") as source:
            content = source.read(MAX_ARTIFACT_BYTES + 1)
        digest = hashlib.sha256(content).hexdigest()
        if len(content) > MAX_ARTIFACT_BYTES or digest != expected:
            raise ValueError("runtime artifact hash/size mismatch")
        raw = json.loads(content)
        school_limit = PILOT_BUNDLE_SCHOOL_LIMITS.get(raw["bundle_id"])
        if school_limit is None:
            raise ValueError("unsupported pilot bundle")
        endpoint = urlparse(raw["endpoint_url"])
        if endpoint.scheme not in ("http", "https") or not endpoint.hostname:
            raise ValueError("invalid operator routing URL")
        build = ValhallaBuild(**raw["build"])
        if build.snapshot_id != raw["bundle_id"] or build.profile_version != (
            "waydex-walk-static-5kmh-v1"
        ):
            raise ValueError("build snapshot/profile mismatch")
        options = json.loads(build.pedestrian_options_json)
        if options.get("walking_speed") != 5 or options.get("type") != "foot":
            raise ValueError("pilot requires its declared 5 km/h foot model")
        status = raw["status_expectation"]
        if (
            not isinstance(status, dict)
            or status.get("version") != "3.5.1"
            or ("tileset_last_modified" not in status)
        ):
            raise ValueError("pinned server version and tileset status are required")
        connectors = tuple(
            WalkConnector(
                **{
                    **row,
                    "physical_location": _coordinate(row["physical_location"]),
                    "access_location": _coordinate(row["access_location"]),
                }
            )
            for row in raw["connectors"]
        )
        correlations = tuple(
            ValhallaCorrelation(
                **{
                    **row,
                    "build": build,
                    "source_location": _coordinate(row["source_location"]),
                    "correlated_location": _coordinate(row["correlated_location"]),
                }
            )
            for row in raw["correlations"]
        )
        points = tuple(raw["point_ids"])
        destinations = tuple(raw["destination_ids"])
        if len(points) != 1 or not 1 <= len(destinations) <= school_limit:
            raise ValueError("pilot must declare one point and bounded school subset")
        if len(set(destinations)) != len(destinations):
            raise ValueError("duplicate pilot destination")
        evidence = tuple(EvidenceRef.model_validate(row) for row in raw["evidence_refs"])
        evidence_ids = {row.evidence_id for row in evidence}
        if len(evidence_ids) != len(evidence):
            raise ValueError("duplicate runtime evidence")
        required = set(build.evidence_refs)
        for connector in connectors:
            required.update(connector.evidence_refs)
        for correlation in correlations:
            required.update(correlation.evidence_refs)
            required.update(getattr(correlation, "serialization_evidence_refs", ()))
        snapshot = cast(AcceptedData, load_accepted_data(raw["bundle_id"]))
        known = evidence_ids | {e.evidence_id for e in snapshot.evidence}
        if not required <= known or build.source_graph_version != snapshot.provenance.graph_version:
            raise ValueError("runtime evidence or source graph mismatch")
        destination_map = {row.destination_id: row for row in snapshot.destinations}
        school_ids = {
            row.destination_id for row in snapshot.destinations if "school" in row.categories
        }
        if len(school_ids) > school_limit:
            raise ValueError("accepted school inventory exceeds reviewed pilot bound")
        if any(
            identity not in destination_map or "school" not in destination_map[identity].categories
            for identity in destinations
        ) or not set(points) <= {p.point_id for p in snapshot.sample_points}:
            raise ValueError("unknown pilot endpoint or non-school destination")
        # A writable observation store is needed before accepting new queue work.
        folder = Path(evidence_dir)
        if not folder.is_dir() or not os.access(folder, os.W_OK):
            raise ValueError("observation directory is not writable")
        return Runtime(
            digest,
            raw["bundle_id"],
            raw["endpoint_url"].rstrip("/"),
            points,
            destinations,
            build,
            connectors,
            correlations,
            evidence,
            folder,
            status,
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise _unavailable("Walk runtime artifact is unavailable or inconsistent") from exc


def registry() -> ProductRegistry:
    runtime = load_runtime()
    snapshot = cast(AcceptedData, load_accepted_data(runtime.bundle_id))
    options = registry_options(load_bundle(runtime.bundle_id))
    return ProductRegistry(
        territories=[TerritoryOption.model_validate(row) for row in options["territories"]],
        snapshots=[snapshot],
        # District calculations are outside this explicitly sampled pilot.
        areas=[],
        ready_kinds=["urban_passenger"],
    )


def _scope(request: AnalysisRequest) -> UrbanPassengerRequest:
    if not isinstance(request, UrbanPassengerRequest) or (
        request.city_id != "kaliningrad"
        or request.passenger_mode != "walk"
        or request.traffic_mode != "static"
        or request.categories != ["school"]
        or request.area_id is not None
    ):
        raise DomainError(
            422, "invalid_request", "This runtime supports Kaliningrad walk/static school only"
        )
    return request


async def preflight(request: AnalysisRequest, snapshot: AcceptedData) -> None:
    _scope(request)
    runtime = await asyncio.to_thread(load_runtime)
    code = RuntimeSettings().code_version or ""
    if runtime.digest not in code or snapshot.snapshot_id != runtime.bundle_id:
        raise DomainError(
            409, "incompatible_versions", "Walk runtime fingerprint differs from configuration"
        )
    if snapshot.provenance.graph_version != runtime.build.source_graph_version:
        raise DomainError(
            409, "incompatible_versions", "Walk source graph differs from accepted snapshot"
        )
    # Validate the full supplied supplements and prove configured pilot pairs are
    # executable without doing routing/CPU analysis in the HTTP request.
    try:
        inputs = await asyncio.to_thread(
            prepare_walk_inputs,
            snapshot.model_dump(mode="json"),
            city_id="kaliningrad",
            departure_at=request.departure_at,
            methodology_version=ProductRegistry().methodology.version,
            config_hash=ProductRegistry().config_hash,
            search_parameters_id="walk-readiness-" + runtime.digest,
            build=runtime.build,
            connectors=runtime.connectors,
            correlations=runtime.correlations,
        )
        plan = plan_walk_routes(inputs, point_ids=runtime.point_ids, categories=["school"])
    except (ValueError, KeyError, TypeError) as exc:
        raise _unavailable("Walk runtime supplements do not match the accepted snapshot") from exc
    executable = {route.destination_id for route in plan.requests}
    if not set(runtime.destination_ids) <= executable:
        raise _unavailable("Configured pilot pairs lack confirmed connectors/correlations")
    await asyncio.to_thread(check_status, runtime)


def _record(runtime: Runtime, path: str, payload: dict[str, Any], body: Any) -> EvidenceRef:
    observation = {
        "build": runtime.build.__dict__,
        "path": path,
        "request": payload,
        "response": body,
    }
    content = json.dumps(observation, sort_keys=True, separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(content.encode()).hexdigest()
    target = runtime.evidence_dir / (digest + ".json")
    # Content addressing makes observations immutable and safe for repeated jobs.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=runtime.evidence_dir, delete=False
        ) as output:
            temporary = Path(output.name)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.read_text() != content:
                raise _unavailable("Immutable routing observation changed") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return EvidenceRef(
        evidence_id="valhalla-observation-" + digest,
        source_id="valhalla-" + runtime.build.build_id,
        url=HttpUrl(runtime.endpoint_url + path),
        retrieved_at=datetime.now(UTC),
        observed_at=None,
        section=path.lstrip("/"),
    )


def _post(client: httpx.Client, runtime: Runtime, path: str, payload: dict[str, Any]) -> Any:
    with client.stream("POST", runtime.endpoint_url + path, json=payload) as response:
        if response.headers.get("content-encoding", "identity") != "identity":
            raise _unavailable("Compressed routing responses are unsupported")
        content = bytearray()
        for chunk in response.iter_raw():
            content.extend(chunk)
            if len(content) > MAX_RESPONSE_BYTES:
                raise _unavailable("Routing response exceeds the observation limit")
        body = json.loads(content)
        if response.status_code >= 400 and not (isinstance(body, dict) and "error_code" in body):
            response.raise_for_status()
        return body


def check_status(runtime: Runtime) -> EvidenceRef:
    """Check deployment-pinned status; tile SHA/image proof remains in build evidence."""
    try:
        with httpx.Client(
            timeout=5, headers={"Accept-Encoding": "identity"}, trust_env=False
        ) as client:
            body = _post(client, runtime, "/status", {})
        if (
            not isinstance(body, dict)
            or any(body.get(key) != value for key, value in runtime.status_expectation.items())
            or body.get("has_tiles") is False
        ):
            raise _unavailable("Routing server differs from the pinned build status")
        return _record(runtime, "/status", {}, body)
    except (httpx.HTTPError, ValueError) as exc:
        raise _unavailable("Pinned routing server is unavailable") from exc


def _pure_walk_response(body: Any) -> bool:
    """Validate nested provider containers before attesting pedestrian mode.

    The engine owns route semantics. Invalid containers must never reach its
    semantic checks or be mistaken for evidence of an unreachable destination.
    Provider error objects and non-object bodies retain their existing handling.
    """
    if not isinstance(body, dict) or "error_code" in body:
        return False
    trip = body.get("trip")
    if not isinstance(trip, dict):
        raise ValueError("Invalid routing trip object")
    summary, locations, legs = trip.get("summary"), trip.get("locations"), trip.get("legs")
    if (
        not isinstance(summary, dict)
        or not isinstance(locations, list)
        or any(not isinstance(row, dict) for row in locations)
        or not isinstance(legs, list)
    ):
        raise ValueError("Invalid routing trip containers")
    maneuvers = []
    for leg in legs:
        if (
            not isinstance(leg, dict)
            or not isinstance(leg.get("summary"), dict)
            or not isinstance(leg.get("maneuvers"), list)
            or any(not isinstance(row, dict) for row in leg["maneuvers"])
        ):
            raise ValueError("Invalid routing leg containers")
        maneuvers.extend(leg["maneuvers"])
    return (
        bool(maneuvers)
        and summary.get("has_ferry") is False
        and all(
            row.get("travel_mode") == "pedestrian"
            and row.get("travel_type") == "foot"
            and not row.get("ferry", False)
            and row.get("type") not in range(28, 37)
            for row in maneuvers
        )
    )


def execute_route(
    client: httpx.Client, runtime: Runtime, request: WalkRouteRequest
) -> tuple[WalkResponseEnvelope, list[EvidenceRef]]:
    """Observe actual locate projections; route input echoes never attest matching."""
    refs: list[EvidenceRef] = []
    matched: list[WalkCoordinate] = []
    endpoint_refs: list[str] = []
    try:
        route_payload = request.payload()
        locate_payload = {
            "locations": route_payload["locations"],
            "costing": "pedestrian",
            "costing_options": route_payload["costing_options"],
            "verbose": True,
        }
        located = _post(client, runtime, "/locate", locate_payload)
        locate_ref = _record(runtime, "/locate", locate_payload, located)
        refs.append(locate_ref)
        if isinstance(located, list) and len(located) == 2:
            for row, correlation in zip(
                located, (request.origin, request.destination), strict=True
            ):
                edges = row.get("edges", []) if isinstance(row, dict) else []
                if not edges or any(
                    not isinstance(edge, dict)
                    or "correlated_lon" not in edge
                    or "correlated_lat" not in edge
                    for edge in edges
                ):
                    continue
                projections = {(edge["correlated_lon"], edge["correlated_lat"]) for edge in edges}
                expected = correlation.correlated_location
                # All observed candidate edges must agree with the accepted projection.
                if projections == {(expected.lon, expected.lat)} and not row.get("warnings"):
                    matched.append(expected)
            if len(matched) == 2:
                endpoint_refs.append(locate_ref.evidence_id)
        body = _post(client, runtime, "/route", route_payload)
        route_ref = _record(runtime, "/route", route_payload, body)
        refs.append(route_ref)
        # Independent response inspection; never claim minimum-time search completion.
        pure = _pure_walk_response(body)
        envelope = WalkResponseEnvelope(
            request.request_id,
            request.point_id,
            request.destination_id,
            request.build,
            request.payload_hash,
            request.origin.correlation_id,
            request.destination.correlation_id,
            "response",
            json.dumps(body),
            (route_ref.evidence_id,),
            search_complete=False,
            minimum_time_evidence_refs=(),
            pure_walk_evidence_refs=(route_ref.evidence_id,) if pure else (),
            matched_origin=matched[0] if len(matched) == 2 else None,
            matched_destination=matched[1] if len(matched) == 2 else None,
            endpoint_evidence_refs=tuple(endpoint_refs),
        )
    except (httpx.HTTPError, TimeoutError, ValueError, TypeError, KeyError) as exc:
        envelope = WalkResponseEnvelope(
            request.request_id,
            request.point_id,
            request.destination_id,
            request.build,
            request.payload_hash,
            request.origin.correlation_id,
            request.destination.correlation_id,
            "timeout" if isinstance(exc, (httpx.TimeoutException, TimeoutError)) else "http_error",
            None,
            tuple(ref.evidence_id for ref in refs),
        )
    return envelope, refs


def calculate(claim: ClaimedJob) -> AnalysisResult:
    if claim.kind != "analysis" or not isinstance(claim.context, V2JobContext):
        raise DomainError(
            422, "invalid_request", "Walk runtime accepts canonical analysis jobs only"
        )
    context = claim.context
    request = _scope(context.normalized_request)
    runtime = load_runtime()
    snapshot = cast(AcceptedData, load_accepted_data(runtime.bundle_id))
    current = ProductRegistry()
    if (
        runtime.digest not in context.versions.code_version
        or context.snapshot_id != runtime.bundle_id
        or context.snapshot_hash != canonical_hash(snapshot.model_dump(mode="json"))
        or context.versions.graph_version != runtime.build.source_graph_version
        or context.versions.config_hash != current.config_hash
        or context.versions.methodology_version != current.methodology.version
    ):
        raise DomainError(
            409, "incompatible_versions", "Queued walk inputs or runtime artifact changed"
        )
    inputs = prepare_walk_inputs(
        snapshot.model_dump(mode="json"),
        city_id=request.city_id,
        departure_at=request.departure_at,
        methodology_version=context.versions.methodology_version,
        config_hash=context.versions.config_hash,
        search_parameters_id="waydex-walk-pilot-" + runtime.digest,
        build=runtime.build,
        connectors=runtime.connectors,
        correlations=runtime.correlations,
    )
    # Preserve the accepted catalog in the plan; omitted executions stay unknown.
    plan = plan_walk_routes(inputs, point_ids=runtime.point_ids, categories=request.categories)
    responses, observations = [], [check_status(runtime)]
    with httpx.Client(
        timeout=15, headers={"Accept-Encoding": "identity"}, trust_env=False
    ) as client:
        for route in plan.requests:
            if route.destination_id in runtime.destination_ids:
                envelope, evidence = execute_route(client, runtime, route)
                responses.append(envelope)
                observations.extend(evidence)
    analysis = analyze_walk_responses(
        plan,
        responses,
        selected_categories={
            str(category): str(priority) for category, priority in request.priorities.items()
        },
        config=load_urban_config(),
        rules=load_urban_journey_rules(),
    )
    return project_result(claim, snapshot, runtime, analysis, observations)


def project_result(
    claim: ClaimedJob,
    snapshot: AcceptedData,
    runtime: Runtime,
    calculated: WalkAnalysisResult,
    observations: list[EvidenceRef],
) -> AnalysisResult:
    """Project ML1 calculations, retaining nullable minimum scores and honest sample scope."""
    assert isinstance(claim.context, V2JobContext)
    context = claim.context
    request = _scope(context.normalized_request)
    evidence = {
        row.evidence_id: EvidenceRef(
            evidence_id=row.evidence_id,
            source_id=row.source_id,
            url=row.url,
            retrieved_at=row.retrieved_at,
            observed_at=None,
            section="accepted-snapshot",
        )
        for row in snapshot.evidence
    }
    for row in (*runtime.evidence, *observations):
        if row.evidence_id in evidence and evidence[row.evidence_id] != row:
            raise DomainError(
                409,
                "incompatible_versions",
                "Runtime evidence identity collides with accepted evidence",
            )
        evidence[row.evidence_id] = row
    subject = SubjectRef(kind="territory", territory_id=request.city_id, mode="walk")
    h = calculated.analysis.h_seconds
    metrics = [
        Metric(
            metric_id="urban-h",
            name="urban_h_seconds",
            value=h.value,
            unit="seconds",
            status=h.status,
            reason_code=h.reason_code,
            source_refs=list(calculated.analysis.area.source_refs),
            subject=subject,
            completeness="full" if h.status == "available" else "partial",
        )
    ]
    journeys = []
    for timing in calculated.analysis.journeys:
        candidate = timing.candidate
        journey_evidence = sorted(
            set(candidate.evidence_refs)
            | {reference for leg in candidate.legs for reference in leg.evidence_refs}
        )
        journeys.append(
            JourneyResult(
                journey_id=candidate.journey_id,
                scope="urban",
                origin_node_id=candidate.origin_node_id,
                destination_node_id=candidate.destination_node_id,
                requested_departure_at=request.departure_at,
                departure_at=candidate.departure_at,
                arrival_at=candidate.arrival_at,
                total_seconds=timing.total_seconds,
                initial_wait_seconds=timing.initial_wait_seconds,
                path_seconds=timing.total_seconds,
                total_wait_seconds=timing.total_wait_seconds,
                legs=[Leg.model_validate(leg.__dict__) for leg in candidate.legs],
                # Network-only Valhalla geometry cannot represent the complete physical journey.
                geometry=None,
                geometry_kind="none",
                status="available",
                reason_code="MINIMUM_TIME_UNPROVEN",
                snapshot_id=context.snapshot_id,
                completeness="partial",
                traffic_mode="static",
                time_kind="movement",
                reference_id=None,
                destination_object_id=candidate.destination_id,
                point_id=candidate.point_id,
                evidence_refs=journey_evidence,
            )
        )
        metrics.append(
            Metric(
                metric_id="observed-time-" + candidate.journey_id,
                name="best_observed_journey_seconds",
                value=timing.total_seconds,
                unit="seconds",
                status="available",
                reason_code="MINIMUM_TIME_UNPROVEN",
                source_refs=journey_evidence,
                subject=SubjectRef(
                    kind="sample_point",
                    territory_id=request.city_id,
                    point_id=candidate.point_id,
                    destination_id=candidate.destination_id,
                    category="school",
                    mode="walk",
                ),
                completeness="partial",
                time_kind="movement",
                journey_id=candidate.journey_id,
            )
        )
    scores = [
        Score(
            score_id="urban-pilot-index",
            name="urban_accessibility_index",
            value=None,
            status="unknown",
            completeness="partial",
            methodology_version=context.versions.methodology_version,
            reason_code="PILOT_SAMPLE_ONLY",
            components=[],
            evidence_refs=list(runtime.build.evidence_refs),
            subject=subject,
            ranking_eligible=False,
        )
    ]
    point_map = {row.point_id: row for row in snapshot.sample_points}
    cells = {row.cell_id: row for row in snapshot.population_cells}
    destination_names = {
        row.destination_id: row.name
        for row in load_bundle(runtime.bundle_id).destinations
        if row.status == "accepted"
    }
    features = []
    for identity in runtime.point_ids:
        point = point_map[identity]
        cell = cells[point.cell_id]
        features.append(
            MapFeature(
                entity_kind="sample_point",
                entity_id=identity,
                territory_id=request.city_id,
                point_id=identity,
                geometry=PointGeometry(
                    type="Point", coordinates=(point.location.lon, point.location.lat)
                ),
                geometry_kind="actual",
                source_refs=sorted(set(point.source_refs) | set(cell.source_refs)),
                population=cell.population,
                population_year=cell.population_year,
                population_method=cell.population_method,
            )
        )
    for destination in snapshot.destinations:
        if destination.destination_id not in runtime.destination_ids:
            continue
        features.append(
            MapFeature(
                entity_kind="destination",
                entity_id=destination.destination_id,
                territory_id=request.city_id,
                categories=["school"],
                name=destination_names[destination.destination_id],
                geometry=PointGeometry(
                    type="Point", coordinates=(destination.location.lon, destination.location.lat)
                ),
                geometry_kind="actual",
                source_refs=destination.source_refs,
            )
        )
    limitations = set(calculated.analysis.limitations) | {
        "PILOT_SAMPLE_ONLY",
        "MINIMUM_TIME_UNPROVEN",
        "PARTIAL_SCHOOL_CATALOG",
        "INCOMPLETE_POPULATION_COVERAGE",
        "WALKING_SPEED_MODEL_5_KMH",
        "NETWORK_ONLY_GEOMETRY",
    }
    limitations.update(route.reason_code for route in calculated.routes if route.reason_code)
    if any(getattr(row, "coordinate_precision_digits", None) for row in runtime.correlations):
        limitations.add("ENDPOINT_SERIALIZATION_PRECISION_6")
    payload = dict(
        schema_version="2",
        analysis_id=context.analysis_id,
        analysis_kind="urban_passenger",
        request=request,
        snapshot_id=context.snapshot_id,
        methodology_version=context.versions.methodology_version,
        config_hash=context.versions.config_hash,
        traffic_mode="static",
        status="partial" if any(metric.value is not None for metric in metrics) else "unknown",
        metrics=metrics,
        scores=scores,
        evidence_refs=list(evidence.values()),
        limitations=sorted(limitations),
        versions=context.versions,
        journeys=journeys,
        references=[],
        counts=[],
        last_returns=[],
        map_data=AnalysisMapData(
            status="available",
            completeness="partial",
            reason_code="PILOT_SAMPLE_ONLY",
            features=features,
        ),
        result_hash="pending-publication",
    )
    result = AnalysisResult.model_validate(payload)
    # Repository recomputes this hash inside its fenced atomic publication as well.
    encoded = result.model_dump(mode="json", exclude={"result_hash"})
    return result.model_copy(update={"result_hash": canonical_hash(encoded)})
