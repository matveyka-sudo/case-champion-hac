"""Exact bounded label search with waiting as a resource, plus static walk access."""

import heapq
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal

from a2_engine.routing.timetable_models import (
    Timetable,
    TimetableFootpath,
    TimetablePath,
    TimetableRun,
    TimetableSearchResult,
    TimetableStep,
    TimetableTransfer,
    duration_delta,
    microseconds,
    refs,
    utc,
)
from a2_engine.shared.results import finite_number


def check_limit(resource_limit: int | None) -> None:
    if resource_limit is not None and (
        isinstance(resource_limit, bool)
        or not isinstance(resource_limit, int)
        or resource_limit < 0
    ):
        raise ValueError("NONNEGATIVE_RESOURCE_LIMIT_REQUIRED")


def data_reasons(timetable: Timetable, allow_transit: bool) -> set[str]:
    scope = timetable.scope
    reasons = set(scope.limitations)
    if not scope.evidence_refs:
        reasons.add("TIMETABLE_COVERAGE_UNCONFIRMED")
    if not scope.footpaths_complete:
        reasons.add("FOOTPATH_COVERAGE_INCOMPLETE")
    if any(not path.usable for path in timetable.footpaths):
        reasons.add("FOOTPATH_UNCONFIRMED")
    if allow_transit:
        for flag, reason in [
            ("schedule_complete", "SCHEDULE_INCOMPLETE"),
            ("calendar_complete", "CALENDAR_INCOMPLETE"),
            ("transfers_complete", "TRANSFER_COVERAGE_INCOMPLETE"),
            ("continuations_complete", "CONTINUATIONS_INCOMPLETE"),
        ]:
            if not getattr(scope, flag):
                reasons.add(reason)
        for run in timetable.runs:
            if run.active is None or not run.calendar_evidence_refs:
                reasons.add("RUN_CALENDAR_UNCONFIRMED")
            if run.active is True:
                if any(not e.evidence_refs for e in run.events):
                    reasons.add("STOP_EVENT_UNCONFIRMED")
                if (
                    any(
                        scope.start_at <= e.departure_at <= scope.end_at and e.pickup
                        for e in run.events[:-1]
                    )
                    and run.events[-1].arrival_at > scope.end_at
                ):
                    reasons.add("CONTINUATION_OUTSIDE_HORIZON")
        if any(
            r.allowed is None or not r.evidence_refs or (r.allowed and r.minimum_seconds is None)
            for r in timetable.transfers
        ):
            reasons.add("TRANSFER_RULE_UNCONFIRMED")
    return reasons


def usable_runs(timetable: Timetable) -> tuple[TimetableRun, ...]:
    return tuple(
        r
        for r in timetable.runs
        if r.active is True and r.calendar_evidence_refs and all(e.evidence_refs for e in r.events)
    )


def transfer_rule(
    timetable: Timetable,
    previous: TimetableRun,
    alight_index: int,
    current: TimetableRun,
    board_index: int,
) -> TimetableTransfer | None:
    origin = previous.events[alight_index].node_id
    target = current.events[board_index].node_id
    matches = [
        r
        for r in timetable.transfers
        if r.from_node_id == origin
        and r.to_node_id == target
        and r.from_run_id in (None, previous.run_id)
        and r.to_run_id in (None, current.run_id)
    ]
    if not matches:
        return None
    specificity = max((r.from_run_id is not None) + (r.to_run_id is not None) for r in matches)
    chosen = [
        r for r in matches if (r.from_run_id is not None) + (r.to_run_id is not None) == specificity
    ]
    if len(chosen) != 1:
        raise ValueError("AMBIGUOUS_TRANSFER_RULE")
    return chosen[0]


@dataclass(frozen=True)
class _Label:
    node: str
    at: datetime
    waiting: int
    onboard: tuple[str, int] | None
    alight: tuple[str, int] | None
    steps: tuple[TimetableStep, ...]
    boardings: tuple[tuple[str, int, datetime], ...]

    @property
    def key(self) -> tuple[object, ...]:
        # Alight event fixes both previous vehicle and minimum-transfer time anchor.
        return ("aboard", *self.onboard) if self.onboard else ("off", self.node, self.alight)


def solve_timetable(
    timetable: Timetable,
    origin_node_id: str,
    destination_node_id: str,
    departure_at: datetime,
    *,
    max_wait_seconds: float,
    allow_transit: bool = True,
    require_transit: bool = False,
    first_boarding: tuple[str, int] | None = None,
    resource_limit: int | None = None,
) -> TimetableSearchResult:
    check_limit(resource_limit)
    if (
        not isinstance(allow_transit, bool)
        or not isinstance(require_transit, bool)
        or (require_transit and not allow_transit)
        or (first_boarding is not None and (not allow_transit or not require_transit))
    ):
        raise ValueError("INCONSISTENT_TIMETABLE_SEARCH_MODE")
    departure = utc(departure_at)
    scope = timetable.scope
    if not scope.start_at <= departure <= scope.end_at:
        raise ValueError("DEPARTURE_OUTSIDE_TIMETABLE_SCOPE")
    if origin_node_id not in scope.node_ids or destination_node_id not in scope.node_ids:
        raise ValueError("SEARCH_ENDPOINT_OUTSIDE_TIMETABLE_SCOPE")
    max_wait = finite_number(max_wait_seconds, "max_wait_seconds")
    wait_budget = int((Decimal(str(max_wait)) * 1000000).to_integral_value(rounding=ROUND_FLOOR))
    runs = {r.run_id: r for r in usable_runs(timetable)}
    if first_boarding is not None and (
        first_boarding[0] not in runs
        or not any(e.sequence == first_boarding[1] for e in runs[first_boarding[0]].events)
    ):
        raise ValueError("UNKNOWN_FIRST_BOARDING_EVENT")
    board_at: dict[str, list[tuple[TimetableRun, int]]] = {}
    for run in runs.values():
        for i, event in enumerate(run.events[:-1]):
            if event.pickup:
                board_at.setdefault(event.node_id, []).append((run, i))
    for boardings in board_at.values():
        boardings.sort(
            key=lambda pair: (pair[0].events[pair[1]].departure_at, pair[0].run_id, pair[1])
        )
    walks: dict[str, list[TimetableFootpath]] = {}
    for path in timetable.footpaths:
        if path.usable:
            walks.setdefault(path.from_node_id, []).append(path)
    reasons = data_reasons(timetable, allow_transit)
    evidence = list(scope.evidence_refs)
    for run in timetable.runs:
        evidence.extend(run.calendar_evidence_refs)
        for event in run.events:
            evidence.extend(event.evidence_refs)
    evidence.extend(ref for path in timetable.footpaths for ref in path.evidence_refs)
    evidence.extend(ref for rule in timetable.transfers for ref in rule.evidence_refs)
    labels: dict[tuple[object, ...], list[_Label]] = {}
    queue: list[tuple[datetime, int, _Label]] = []
    serial = 0
    best: TimetablePath | None = None

    def reached(label: _Label) -> bool:
        return (
            label.onboard is None
            and label.node == destination_node_id
            and (bool(label.boardings) or not require_transit)
        )

    def dominates(a: _Label, b: _Label) -> bool:
        return a.at <= b.at and a.waiting + microseconds(b.at - a.at) <= b.waiting

    def add(label: _Label) -> None:
        nonlocal serial, best
        if label.at > scope.end_at or label.waiting > wait_budget:
            return
        bucket = labels.setdefault(label.key, [])
        if any(dominates(old, label) for old in bucket):
            return
        bucket[:] = [old for old in bucket if not dominates(label, old)]
        bucket.append(label)
        serial += 1
        heapq.heappush(queue, (label.at, serial, label))
        if reached(label):
            path = TimetablePath(
                origin_node_id,
                destination_node_id,
                departure,
                label.at,
                label.steps,
                label.boardings,
            )
            if best is None or path.arrival_at < best.arrival_at:
                best = path

    add(_Label(origin_node_id, departure, 0, None, None, (), ()))
    expanded = 0
    interrupted = False
    while queue:
        _, _, label = heapq.heappop(queue)
        if label not in labels[label.key]:
            continue
        if resource_limit is not None and expanded >= resource_limit:
            interrupted = True
            break
        expanded += 1
        if reached(label):
            break  # All edge times nonnegative: first settled target is exact within the scope.
        if label.onboard is not None:
            run_id, index = label.onboard
            run = runs[run_id]
            event = run.events[index]
            if event.dropoff:
                add(
                    _Label(
                        label.node,
                        label.at,
                        label.waiting,
                        None,
                        (run_id, index),
                        label.steps,
                        label.boardings,
                    )
                )
            if index + 1 < len(run.events):
                end = run.events[index + 1]
                # Includes this intermediate stop's dwell, without a fresh boarding.
                leg = TimetableStep(
                    "ride",
                    event.node_id,
                    end.node_id,
                    label.at,
                    end.arrival_at,
                    run.transport_type,
                    run_id,
                    refs(event.evidence_refs + end.evidence_refs + run.calendar_evidence_refs),
                )
                add(
                    _Label(
                        end.node_id,
                        end.arrival_at,
                        label.waiting,
                        (run_id, index + 1),
                        None,
                        label.steps + (leg,),
                        label.boardings,
                    )
                )
            continue
        for path in walks.get(label.node, []):
            assert path.seconds is not None
            try:
                end_at = label.at + duration_delta(path.seconds)
            except OverflowError:
                continue  # Outside the finite representable/search horizon.
            step = TimetableStep(
                "walk",
                label.node,
                path.to_node_id,
                label.at,
                end_at,
                "walk",
                None,
                path.evidence_refs,
                path.seconds,
                path.path_id,
            )
            add(
                _Label(
                    path.to_node_id,
                    end_at,
                    label.waiting,
                    None,
                    label.alight,
                    label.steps + (step,),
                    label.boardings,
                )
            )
        if not allow_transit:
            continue
        for run, index in board_at.get(label.node, []):
            event = run.events[index]
            if event.departure_at < label.at:
                continue
            if (
                not label.boardings
                and first_boarding is not None
                and (run.run_id, event.sequence) != first_boarding
            ):
                continue
            extra_evidence: tuple[str, ...] = ()
            if label.alight is not None:
                prior_id, prior_index = label.alight
                # Reboarding the same event cannot improve staying aboard, and would fake a transfer.
                if (prior_id, prior_index) == (run.run_id, index):
                    continue
                prior = runs[prior_id]
                rule = transfer_rule(timetable, prior, prior_index, run, index)
                if (
                    rule is None
                    or rule.allowed is None
                    or not rule.evidence_refs
                    or (rule.allowed and rule.minimum_seconds is None)
                ):
                    reasons.add("TRANSFER_RULE_UNAVAILABLE")
                    continue
                if not rule.allowed:
                    continue
                assert rule.minimum_seconds is not None
                available = microseconds(event.departure_at - prior.events[prior_index].arrival_at)
                if Decimal(available) < Decimal(str(rule.minimum_seconds)) * 1000000:
                    continue
                extra_evidence = rule.evidence_refs
            waiting = microseconds(event.departure_at - label.at)
            if label.waiting + waiting > wait_budget:
                continue
            steps = label.steps
            if waiting:
                steps += (
                    TimetableStep(
                        "wait_transfer" if label.boardings else "wait_initial",
                        label.node,
                        label.node,
                        label.at,
                        event.departure_at,
                        run.transport_type,
                        run.run_id,
                        refs(event.evidence_refs + extra_evidence),
                    ),
                )
            following = run.events[index + 1]
            steps += (
                TimetableStep(
                    "ride",
                    event.node_id,
                    following.node_id,
                    event.departure_at,
                    following.arrival_at,
                    run.transport_type,
                    run.run_id,
                    refs(
                        event.evidence_refs
                        + following.evidence_refs
                        + run.calendar_evidence_refs
                        + extra_evidence
                    ),
                ),
            )
            add(
                _Label(
                    following.node_id,
                    following.arrival_at,
                    label.waiting + waiting,
                    (run.run_id, index + 1),
                    None,
                    steps,
                    label.boardings + ((run.run_id, event.sequence, event.departure_at),),
                )
            )
    if interrupted:
        reasons.add("RESOURCE_LIMIT_REACHED")
    proven = not reasons
    return TimetableSearchResult(
        timetable,
        origin_node_id,
        destination_node_id,
        departure,
        "reachable"
        if best is not None and proven
        else "confirmed_unreachable"
        if proven
        else "unknown",
        best,
        proven,
        tuple(sorted(reasons)),
        refs(tuple(evidence)),
        expanded,
        interrupted,
        max_wait,
        allow_transit,
        require_transit,
        first_boarding,
        resource_limit,
    )


def walk_access_paths(
    timetable: Timetable, origin: str, *, resource_limit: int | None = None
) -> tuple[dict[str, tuple[int, tuple[TimetableFootpath, ...]]], int, bool]:
    """Shortest static walk to each first boarding. JIT departure then has zero initial wait."""
    check_limit(resource_limit)
    adjacency: dict[str, list[TimetableFootpath]] = {}
    for footpath in timetable.footpaths:
        if footpath.usable:
            adjacency.setdefault(footpath.from_node_id, []).append(footpath)
    queue: list[tuple[int, str, int, tuple[TimetableFootpath, ...]]] = [(0, origin, 0, ())]
    best: dict[str, tuple[int, tuple[TimetableFootpath, ...]]] = {}
    serial = 0
    while queue:
        elapsed, node, _, path = heapq.heappop(queue)
        if node in best:
            continue
        if resource_limit is not None and len(best) >= resource_limit:
            return best, len(best), True
        best[node] = (elapsed, path)
        for edge in adjacency.get(node, []):
            assert edge.seconds is not None
            if edge.to_node_id in best:
                continue
            serial += 1
            heapq.heappush(
                queue,
                (
                    elapsed + microseconds(duration_delta(edge.seconds)),
                    edge.to_node_id,
                    serial,
                    path + (edge,),
                ),
            )
    return best, len(best), False
