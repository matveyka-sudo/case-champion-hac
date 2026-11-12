"""Passenger score selection over whole event chains, independently of frequency."""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Literal

from a2_engine.intercity.analysis.frequency import departure_frequencies
from a2_engine.intercity.analysis.identity import normalize_physical_services
from a2_engine.intercity.analysis.models import (
    AcceptedIntercityCar,
    BoardingReadiness,
    IntercityAnalysisScope,
    IntercityChainResult,
    IntercityModeResult,
    IntercityPassengerAnalysis,
    PhysicalService,
    TransportModeBinding,
)
from a2_engine.intercity.passenger import (
    PassengerChain,
    intercity_car_index,
    intercity_passenger_index,
)
from a2_engine.intercity.reference import ReferenceComparison, reference_time_from_result
from a2_engine.routing.reference.search import ReferenceSearchResult
from a2_engine.routing.road import road_hash
from a2_engine.routing.scheduled import ScheduledRoadAccess, _search_scheduled_chains
from a2_engine.routing.timetable_models import Timetable, utc
from a2_engine.shared.config import IntercityPassengerRules, PassengerConfig


def analyze_intercity_passenger(
    scope: IntercityAnalysisScope,
    requested_at: datetime,
    timetable: Timetable,
    transport_modes: tuple[TransportModeBinding, ...],
    physical_services: tuple[PhysicalService, ...],
    reference: ReferenceSearchResult,
    *,
    config: PassengerConfig,
    rules: IntercityPassengerRules,
    frequency_start_at: datetime,
    frequency_end_at: datetime,
    readiness: tuple[BoardingReadiness, ...] = (),
    car: AcceptedIntercityCar | None = None,
    resource_limit: int | None = None,
) -> IntercityPassengerAnalysis:
    """Evaluate the existing scheduled passenger scope."""
    return analyze_intercity_passenger_with_access(
        scope,
        requested_at,
        timetable,
        transport_modes,
        physical_services,
        reference,
        config=config,
        rules=rules,
        frequency_start_at=frequency_start_at,
        frequency_end_at=frequency_end_at,
        readiness=readiness,
        car=car,
        resource_limit=resource_limit,
    )


def analyze_intercity_passenger_with_access(
    scope: IntercityAnalysisScope,
    requested_at: datetime,
    timetable: Timetable,
    transport_modes: tuple[TransportModeBinding, ...],
    physical_services: tuple[PhysicalService, ...],
    reference: ReferenceSearchResult,
    *,
    config: PassengerConfig,
    rules: IntercityPassengerRules,
    frequency_start_at: datetime,
    frequency_end_at: datetime,
    readiness: tuple[BoardingReadiness, ...] = (),
    car: AcceptedIntercityCar | None = None,
    resource_limit: int | None = None,
    road_access: tuple[ScheduledRoadAccess, ScheduledRoadAccess] | None = None,
) -> IntercityPassengerAnalysis:
    """Maximize the existing scalar index; missing A has no fabricated winner.

    `scope` comes independently from the request/accepted analysis data. It is
    checked against the timetable before it is compared to the reference scope.
    Frequency has its own explicit first-departure period and search completeness.
    """
    transport_modes = tuple(sorted(transport_modes, key=lambda b: b.transport_type))
    physical_services = tuple(sorted(physical_services, key=lambda s: s.physical_service_id))
    requested = utc(requested_at)
    try:
        window_end = requested + timedelta(days=rules.departure_window_days)
    except OverflowError as exc:
        raise ValueError("INTERCITY_WINDOW_OVERFLOW") from exc
    start, end = utc(frequency_start_at), utc(frequency_end_at)
    if start >= end:
        raise ValueError("INVALID_FREQUENCY_PERIOD")
    context = scope.comparison_scope
    if (
        context.network.snapshot_id != timetable.scope.snapshot_id
        or context.network.graph_version != timetable.scope.graph_version
        or context.network.compatibility_version != timetable.scope.transfer_version
        or context.network.methodology_version != rules.methodology_version
        or rules.methodology_version != config.methodology_version
    ):
        raise ValueError("ANALYSIS_TIMETABLE_CONTEXT_MISMATCH")
    if len({r.node_id for r in readiness}) != len(readiness):
        raise ValueError("DUPLICATE_BOARDING_READINESS")
    normalized, mode_map, physical_ids = normalize_physical_services(
        timetable, transport_modes, physical_services
    )
    mapping = dict(mode_map)
    selected_map = tuple((key, mode) for key, mode in mode_map if mode in scope.applicable_modes)
    # Explicitly inapplicable modes are excluded, not mistaken for missing mappings.
    selected_runs = tuple(
        r
        for r in normalized.runs
        if r.transport_type not in mapping or mapping[r.transport_type] in scope.applicable_modes
    )
    selected_ids = {r.run_id for r in selected_runs}

    normalized = replace(
        normalized,
        runs=selected_runs,
        transfers=tuple(
            r
            for r in normalized.transfers
            if (r.from_run_id is None or r.from_run_id in selected_ids)
            and (r.to_run_id is None or r.to_run_id in selected_ids)
        ),
    )
    search_details = _search_scheduled_chains(
        normalized,
        context.origin_node_ids,
        context.destination_node_ids,
        requested,
        window_end,
        selected_map,
        ready_at=tuple((r.node_id, r.ready_at) for r in readiness),
        resource_limit=resource_limit,
        road_access=road_access,
    )
    search = search_details.result
    reasons = set(search.reasons)
    if not scope.modes_complete:
        reasons.add("PUBLIC_MODE_COVERAGE_INCOMPLETE")
    reference_probe = reference_time_from_result(
        reference,
        ReferenceComparison(context, context.origin_node_ids[0], context.destination_node_ids[0]),
        config=config,
    )
    if not reference_probe.applicability_confirmed:
        reasons.add("REFERENCE_NOT_APPLICABLE")
    if reference_probe.seconds is None:
        reasons.add("REFERENCE_UNAVAILABLE")
    if reference_probe.completeness == "partial":
        reasons.add("PARTIAL_REFERENCE")
    candidates = []
    for route in search.candidates:
        first_at = (
            next(s.start_at for s in route.path.steps if s.kind == "ride")
            if road_access
            else route.path.boardings[0][2]
        )
        path_seconds = (route.path.arrival_at - first_at).total_seconds()
        if path_seconds <= 0:
            reasons.add("NONPOSITIVE_PASSENGER_PATH")
            continue
        wait = (first_at - requested).total_seconds()
        scalar = reference_time_from_result(
            reference,
            ReferenceComparison(context, route.path.origin_node_id, route.path.destination_node_id),
            config=config,
        )
        evaluation = intercity_passenger_index(
            PassengerChain(route.chain_id, path_seconds, wait), scalar, config=config
        )
        riding = sum(s.duration_seconds for s in route.path.steps if s.kind == "ride")
        walking = sum(s.duration_seconds for s in route.path.steps if s.kind == "walk")
        transfer_wait = sum(
            s.duration_seconds for s in route.path.steps if s.kind == "wait_transfer"
        )
        category = route.modes[0] if len(route.modes) == 1 else "mixed"
        boarding = route.path.boardings[0]
        candidates.append(
            IntercityChainResult(
                route,
                evaluation,
                wait,
                path_seconds,
                route.path.total_seconds,
                riding,
                walking,
                transfer_wait,
                len(route.path.boardings),
                len(route.path.boardings) - 1,
                category,
                physical_ids.get((boarding[0], boarding[1])),
                all((b[0], b[1]) in physical_ids for b in route.path.boardings),
                True,
            )
        )
    # Scheduled search already orders its candidates by this same chain ID.
    rows = tuple(candidates)
    data_complete = (
        not (set(search.reasons) - {"RESOURCE_LIMIT_REACHED"})
        and "NONPOSITIVE_PASSENGER_PATH" not in reasons
    )
    selection_complete = not reasons
    categories = tuple(scope.applicable_modes) + ("mixed",)

    def select(category: str) -> IntercityModeResult:
        available = [
            c
            for c in rows
            if (category == "public" or c.category == category)
            and c.evaluation.index.value is not None
        ]
        winner = (
            min(
                available,
                key=lambda c: (
                    -float(c.evaluation.index.value or 0),
                    c.route.path.arrival_at,
                    len(c.route.action_ids),
                    c.route.action_ids,
                    c.evaluation.chain.chain_id,
                ),
            )
            if available
            else None
        )
        return IntercityModeResult(category, winner, selection_complete, tuple(sorted(reasons)))

    public = select("public")
    frequency_details = (
        search_details
        if requested <= start < end <= window_end
        else _search_scheduled_chains(
            normalized,
            context.origin_node_ids,
            context.destination_node_ids,
            start,
            end,
            selected_map,
            ready_at=tuple((r.node_id, r.ready_at) for r in readiness),
            resource_limit=resource_limit,
            road_access=road_access,
        )
    )
    frequencies = departure_frequencies(
        frequency_details, physical_ids, categories, start, end, modes_complete=scope.modes_complete
    )
    car_result = None
    if car is not None:
        if car.comparison_scope != context:
            raise ValueError("CAR_ANALYSIS_SCOPE_MISMATCH")
        scalar = reference_time_from_result(
            reference,
            ReferenceComparison(
                car.comparison_scope, car.origin_node_id, car.destination_node_id, car.traffic_mode
            ),
            config=config,
        )
        car_result = intercity_car_index(car.chain_id, car.path_seconds, scalar, config=config)
    evidence = set(search.evidence_refs + scope.evidence_refs + reference.evidence_refs)
    for binding in transport_modes:
        evidence.update(binding.evidence_refs)
    for service in physical_services:
        evidence.update(service.evidence_refs)
        for departure in service.departures:
            evidence.update(departure.evidence_refs)
    for ready in readiness:
        evidence.update(ready.evidence_refs)
    for capital in scope.capital_boundaries:
        evidence.update(capital.evidence_refs)
    if car is not None:
        evidence.update(car.evidence_refs)
    connection: Literal["reachable", "confirmed_absent", "unknown"] = (
        "reachable"
        if rows
        else "confirmed_absent"
        if data_complete and scope.modes_complete and not search.interrupted
        else "unknown"
    )
    identity = road_hash(
        (
            "intercity-event-score-v1",
            scope,
            requested,
            timetable,
            transport_modes,
            tuple(sorted(physical_services, key=lambda s: s.physical_service_id)),
            reference.reference_id,
            config,
            rules,
            start,
            end,
            tuple(sorted(readiness, key=lambda r: r.node_id)),
            car,
            resource_limit,
            *((road_access,) if road_access else ()),
        )
    )
    return IntercityPassengerAnalysis(
        "analysis:" + identity,
        scope,
        transport_modes,
        physical_services,
        requested,
        window_end,
        rows,
        tuple(select(c) for c in categories),
        public,
        connection,
        search,
        frequency_details.result,
        reference.completeness,
        data_complete,
        scope.modes_complete,
        frequencies,
        car_result,
        car is not None
        and car.complete
        and car_result is not None
        and car_result.completeness == "full",
        selection_complete and public.best_known is not None,
        tuple(sorted(reasons)),
        tuple(sorted(evidence)),
    )
