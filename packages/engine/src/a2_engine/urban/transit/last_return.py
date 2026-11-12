"""transit / last_return — canonical urban implementation."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from a2_engine.routing.timetable import (
    check_limit,
    data_reasons,
    solve_timetable,
    usable_runs,
    walk_access_paths,
)
from a2_engine.routing.timetable_models import refs, utc
from a2_engine.shared.config import UrbanJourneyRules
from a2_engine.urban.journeys import summarize_urban_legs
from a2_engine.urban.models import BOUNDED_URBAN_SEARCH, UrbanDestination, UrbanPoint
from a2_engine.urban.transit.journeys import check_urban_inputs, path_legs
from a2_engine.urban.transit.models import (
    UrbanLastReturnResult,
    UrbanReturnJourney,
    UrbanTransitNetwork,
)


def _midnight(day: date, zone: ZoneInfo) -> datetime:
    naive = datetime.combine(day, time())
    valid = {
        instant
        for fold in (0, 1)
        if (instant := naive.replace(tzinfo=zone, fold=fold).astimezone(UTC))
        .astimezone(zone)
        .replace(tzinfo=None)
        == naive
    }
    if len(valid) != 1:
        raise ValueError("UNRESOLVED_LOCAL_DAY_BOUNDARY")
    return next(iter(valid))


def search_urban_last_return(
    network: UrbanTransitNetwork,
    point: UrbanPoint,
    selected_destination: UrbanDestination,
    *,
    local_day: date,
    rules: UrbanJourneyRules,
    lower_leave_at: datetime | None = None,
    passenger_mode: Literal["public_transport", "walk"] = "public_transport",
    resource_limit: int | None = None,
) -> UrbanLastReturnResult:
    bounded = network.context.search_parameters_id == BOUNDED_URBAN_SEARCH
    check_limit(resource_limit)
    if type(local_day) is not date:
        raise ValueError("EXPLICIT_LOCAL_RETURN_DAY_REQUIRED")
    if passenger_mode not in ("public_transport", "walk"):
        raise ValueError("UNSUPPORTED_RETURN_MODE")
    origin, target = check_urban_inputs(network, point, selected_destination, rules, reverse=True)
    lower_argument = utc(lower_leave_at) if lower_leave_at is not None else None
    timetable = network.timetable
    scope = timetable.scope
    evidence = list(
        network.evidence_refs
        + scope.evidence_refs
        + point.source_refs
        + selected_destination.source_refs
    )
    if passenger_mode == "walk":
        return UrbanLastReturnResult(
            network,
            point.point_id,
            selected_destination.destination_id,
            local_day,
            scope.timezone,
            "not_applicable",
            "walk_only",
            False,
            None,
            ("WALK_MODE_HAS_NO_LAST_SERVICE",),
            refs(tuple(evidence)),
            0,
            False,
            rules,
            lower_argument,
            resource_limit,
        )
    zone = ZoneInfo(scope.timezone)
    day_start, day_end = _midnight(local_day, zone), _midnight(local_day + timedelta(days=1), zone)
    lower = scope.start_at if lower_argument is None else max(lower_argument, scope.start_at)
    reasons = data_reasons(timetable, True)
    if scope.start_at > day_start or scope.end_at < day_end:
        reasons.add("FIRST_BOARDING_DAY_NOT_FULLY_COVERED")
    access, expanded, interrupted = walk_access_paths(
        timetable, origin, resource_limit=resource_limit
    )
    best: UrbanReturnJourney | None = None
    events = sorted(
        (event.departure_at, run.run_id, event.sequence, event.node_id)
        for run in usable_runs(timetable)
        for event in run.events[:-1]
        if event.pickup and event.departure_at.astimezone(zone).date() == local_day
    )
    if bounded:
        events.sort(
            key=lambda event: (
                event[0] - timedelta(microseconds=access[event[3]][0])
                if event[3] in access
                else scope.start_at
            ),
            reverse=True,
        )
    for first_at, run_id, sequence, node in events:
        if interrupted:
            break
        if resource_limit is not None and expanded >= resource_limit:
            interrupted = True
            break
        expanded += 1  # Also account for finite first-event enumeration.
        if node not in access:
            continue
        walking, _ = access[node]
        try:
            leave = first_at - timedelta(microseconds=walking)
        except OverflowError:
            continue
        if leave < lower or leave > scope.end_at:
            continue
        if bounded and best is not None and leave < best.leave_at:
            break
        remaining = None if resource_limit is None else resource_limit - expanded
        result = solve_timetable(
            timetable,
            origin,
            target,
            leave,
            max_wait_seconds=rules.max_total_wait_seconds,
            require_transit=True,
            first_boarding=(run_id, sequence),
            resource_limit=remaining,
        )
        expanded += result.expanded_states
        interrupted = interrupted or result.interrupted
        reasons.update(result.reasons)
        evidence.extend(result.evidence_refs)
        if result.best_known is None:
            continue
        path = result.best_known
        legs = path_legs(path, refs(tuple(evidence)))
        totals = summarize_urban_legs(
            legs,
            passenger_mode="public_transport",
            departure_at=leave,
            arrival_at=path.arrival_at,
            origin_node_id=origin,
            destination_node_id=target,
            rules=rules,
        )
        if not totals.admissible:
            raise ValueError("TIMETABLE_WAIT_BUDGET_INCONSISTENCY")
        journey = UrbanReturnJourney(
            point.point_id,
            selected_destination.destination_id,
            leave,
            first_at,
            path.arrival_at,
            legs,
            totals,
            path,
        )
        if (
            best is None
            or journey.leave_at > best.leave_at
            or (journey.leave_at == best.leave_at and journey.arrival_at < best.arrival_at)
        ):
            best = journey
    if interrupted:
        reasons.add("RESOURCE_LIMIT_REACHED")
    bounded_proven = (
        bounded
        and best is not None
        and not interrupted
        and "FIRST_BOARDING_DAY_NOT_FULLY_COVERED" not in reasons
    )
    proven = not reasons
    if bounded_proven:
        reasons.add("BOUNDED_URBAN_SAMPLE")
    if best is not None:
        status: Literal["available", "unknown", "not_applicable"] = (
            "available" if proven or bounded_proven else "unknown"
        )
        state: Literal["reachable", "confirmed_unreachable", "unknown", "walk_only"] = (
            "reachable" if proven or bounded_proven else "unknown"
        )
    elif proven:
        status = "not_applicable" if target in access else "unknown"
        state = "walk_only" if target in access else "confirmed_unreachable"
        reasons.add("WALK_ONLY_RETURN" if target in access else "NO_FEASIBLE_RETURN_WITHIN_SCOPE")
    else:
        status = "unknown"
        state = "unknown"
    return UrbanLastReturnResult(
        network,
        point.point_id,
        selected_destination.destination_id,
        local_day,
        scope.timezone,
        status,
        state,
        proven,
        best,
        tuple(sorted(reasons)),
        refs(tuple(evidence)),
        expanded,
        interrupted,
        rules,
        lower_argument,
        resource_limit,
    )
