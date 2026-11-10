"""Honest dynamic traffic -> UrbanCandidate, independent of the static boundary."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from a2_engine.routing.traffic.evaluation import (
    TrafficEndpoint,
    TrafficPathResult,
    TrafficRequest,
    validate_traffic_result,
)
from a2_engine.routing.traffic.models import TrafficSnapshot, traffic_utc
from a2_engine.shared.config import UrbanConfig, UrbanJourneyRules
from a2_engine.urban.analysis.calculation import analyze_urban_candidates
from a2_engine.urban.analysis.models import UrbanAnalysis
from a2_engine.urban.models import (
    UrbanArea,
    UrbanCandidate,
    UrbanContext,
    UrbanCoverage,
    UrbanDestination,
    UrbanLeg,
    UrbanPoint,
    UrbanPopulationCoverage,
    UrbanSearchScope,
    UrbanThreshold,
    references,
)


def check_traffic_context(context: UrbanContext, snapshot: TrafficSnapshot) -> None:
    scope, versions = snapshot.scope, context.versions
    if (
        context.traffic_mode,
        versions.snapshot_id,
        versions.graph_version,
        versions.traffic_version,
        versions.calendar_version,
        context.city_id,
    ) != (
        scope.mode,
        scope.snapshot_id,
        scope.graph_version,
        snapshot.version_key,
        scope.calendar_version,
        scope.territory_id,
    ):
        raise ValueError("URBAN_TRAFFIC_CONTEXT_MISMATCH")


@dataclass(frozen=True)
class TrafficCarInputs:
    context: UrbanContext
    snapshot: TrafficSnapshot
    checked_at: datetime
    search_scope_id: str
    points: tuple[UrbanPoint, ...]
    destinations: tuple[UrbanDestination, ...]
    entries: tuple[tuple[str, TrafficEndpoint], ...]
    exits: tuple[tuple[str, TrafficEndpoint], ...]
    area: UrbanArea
    population: UrbanPopulationCoverage
    catalogs: tuple[tuple[str, bool, tuple[str, ...]], ...]

    def __post_init__(self) -> None:
        check_traffic_context(self.context, self.snapshot)
        object.__setattr__(self, "checked_at", traffic_utc(self.checked_at))
        if not self.search_scope_id or self.population.context != self.context:
            raise ValueError("TRAFFIC_CAR_INPUT_SCOPE_MISMATCH")
        for name in ("points", "destinations", "entries", "exits", "catalogs"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if len({p.point_id for p in self.points}) != len(self.points) or len(
            {d.destination_id for d in self.destinations}
        ) != len(self.destinations):
            raise ValueError("DUPLICATE_TRAFFIC_CAR_ENTITY")
        for rows, entities, entry in (
            (self.entries, self.points, True),
            (self.exits, self.destinations, False),
        ):
            table = {key: e for key, e in rows}
            if len(table) != len(rows):
                raise ValueError("DUPLICATE_TRAFFIC_CAR_ENDPOINT")
            expected = {
                e.point_id if isinstance(e, UrbanPoint) else e.destination_id: e for e in entities
            }
            if table.keys() - expected.keys():
                raise ValueError("UNKNOWN_TRAFFIC_CAR_ENDPOINT")
            for key, endpoint in rows:
                e = expected[key]
                physical = (
                    e.origin_node_id
                    if isinstance(e, UrbanPoint)
                    else (e.physical_node_id or e.access_node_id)
                )
                if endpoint.scope != self.snapshot.scope or (
                    endpoint.physical_node_id,
                    endpoint.access_node_id,
                    endpoint.direction,
                ) != (physical, e.access_node_id, "entry" if entry else "exit"):
                    raise ValueError("TRAFFIC_CAR_BOUNDARY_MISMATCH")
        if len({c for c, _, _ in self.catalogs}) != len(self.catalogs) or any(
            type(full) is not bool or full and not refs for _, full, refs in self.catalogs
        ):
            raise ValueError("INVALID_TRAFFIC_CATALOG_PROOF")


def traffic_car_requests(
    inputs: TrafficCarInputs, *, categories: Sequence[str]
) -> tuple[TrafficRequest, ...]:
    if not categories or len(set(categories)) != len(categories):
        raise ValueError("INVALID_TRAFFIC_CATEGORY_SELECTION")
    entries, exits = dict(inputs.entries), dict(inputs.exits)
    return tuple(
        TrafficRequest(
            inputs.snapshot,
            p.point_id,
            d.destination_id,
            entries[p.point_id],
            exits[d.destination_id],
            inputs.context.requested_departure_at,
            inputs.checked_at,
            inputs.context.search_parameters_id,
            inputs.search_scope_id,
        )
        for p in sorted(inputs.points, key=lambda p: p.point_id)
        for d in sorted(inputs.destinations, key=lambda d: d.destination_id)
        if d.in_service_scope
        and d.categories.intersection(categories)
        and p.point_id in entries
        and d.destination_id in exits
    )


@dataclass(frozen=True)
class TrafficCarAnalysisResult:
    inputs: TrafficCarInputs
    routes: tuple[TrafficPathResult, ...]
    analysis: UrbanAnalysis


def analyze_traffic_car(
    inputs: TrafficCarInputs,
    results: Sequence[TrafficPathResult],
    *,
    selected_categories: Mapping[str, str],
    config: UrbanConfig,
    rules: UrbanJourneyRules,
    thresholds: Sequence[UrbanThreshold] = (),
) -> TrafficCarAnalysisResult:
    requests = {
        r.request_id: r for r in traffic_car_requests(inputs, categories=tuple(selected_categories))
    }
    unique: dict[tuple[str, str], TrafficPathResult] = {}
    pairs: dict[tuple[str, str], list[TrafficPathResult]] = {}
    for result in results:
        request = requests.get(result.request.request_id)
        if request is None or request != result.request:
            raise ValueError("FOREIGN_TRAFFIC_CAR_RESULT")
        validate_traffic_result(result)
        key = (request.request_id, result.path.path_hash)
        if key in unique and unique[key] != result:
            raise ValueError("CONFLICTING_TRAFFIC_CAR_REPLAY")
        unique[key] = result
    routes = tuple(unique[key] for key in sorted(unique))
    candidates = []
    destinations = {d.destination_id: d for d in inputs.destinations}
    for result in routes:
        request = result.request
        pairs.setdefault((request.origin_id, request.destination_id), []).append(result)
        if result.known_seconds is None:
            continue
        legs = tuple(
            UrbanLeg(
                "walk" if step.kind in ("entry", "exit") else "private_car",
                "walk" if step.kind in ("entry", "exit") else "ride",
                step.from_node_id,
                step.to_node_id,
                step.start_at,
                step.end_at,
                step.duration_seconds,
                None,
                step.evidence_refs,
            )
            for step in result.steps
        )
        for category in sorted(
            destinations[request.destination_id].categories.intersection(selected_categories)
        ):
            candidates.append(
                UrbanCandidate(
                    request.request_id + ":" + result.path.path_hash + ":" + category,
                    request.origin_id,
                    request.destination_id,
                    category,
                    "private_car",
                    inputs.context,
                    request.origin.physical_node_id,
                    request.destination.physical_node_id,
                    request.departure_at,
                    result.steps[-1].end_at,
                    legs,
                    references(result.evidence_refs + result.minimum_evidence_refs),
                    None,
                )
            )
    for values in pairs.values():
        proven = [r.known_seconds for r in values if r.minimum_proven]
        known = [r.known_seconds for r in values if r.known_seconds is not None]
        if proven and (len(set(proven)) != 1 or min(known) != proven[0]):
            raise ValueError("TRAFFIC_MINIMUM_CONTRADICTS_KNOWN_PATH")
    coverage = []
    catalogs = {c: (full, refs) for c, full, refs in inputs.catalogs}
    for point in inputs.points:
        for category in selected_categories:
            roster = frozenset(
                d.destination_id
                for d in inputs.destinations
                if d.in_service_scope and category in d.categories
            )
            values = [
                r
                for destination in sorted(roster)
                for r in pairs.get((point.point_id, destination), ())
            ]
            full, refs = catalogs.get(category, (False, ()))
            complete = all(
                any(r.minimum_proven for r in pairs.get((point.point_id, destination), ()))
                for destination in roster
            )
            evidence = references(
                refs + tuple(e for r in values for e in r.evidence_refs + r.minimum_evidence_refs)
            )
            coverage.append(
                UrbanCoverage(
                    UrbanSearchScope(
                        inputs.context,
                        point.point_id,
                        category,
                        "private_car",
                        rules.max_total_wait_seconds,
                    ),
                    full,
                    complete and bool(evidence),
                    evidence,
                    roster,
                )
            )
    # No policy for live/historical baseline tau is accepted in this stage.
    usable_thresholds = thresholds if inputs.context.traffic_mode == "typical" else ()
    analysis = analyze_urban_candidates(
        inputs.context,
        inputs.points,
        inputs.destinations,
        candidates,
        coverage,
        area=inputs.area,
        selected_categories=selected_categories,
        modes=("private_car",),
        population_coverage=inputs.population,
        config=config,
        rules=rules,
        thresholds=usable_thresholds,
    )
    return TrafficCarAnalysisResult(inputs, routes, analysis)
