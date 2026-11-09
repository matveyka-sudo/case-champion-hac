"""Finite event search preserving first departure and movement-mode history.

Unlike urban earliest-arrival search this returns a frontier for EVERY first
boarding and mode set. There is no urban wait budget and no initial/final walk.
"""

import heapq
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from a2_engine.routing.road import road_hash
from a2_engine.routing.timetable import check_limit, data_reasons, transfer_rule, usable_runs
from a2_engine.routing.timetable_models import (
    Timetable,
    TimetablePath,
    TimetableStep,
    duration_delta,
    microseconds,
    refs,
    utc,
)


@dataclass(frozen=True)
class ScheduledChain:
    path: TimetablePath
    modes: tuple[str, ...]
    action_ids: tuple[str, ...]

    @property
    def chain_id(self) -> str:
        return "chain:" + road_hash((self.path, self.modes, self.action_ids))


@dataclass(frozen=True)
class ScheduledSearchResult:
    timetable: Timetable
    candidates: tuple[ScheduledChain, ...]
    reasons: tuple[str, ...]
    interrupted: bool
    expanded_states: int
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class _State:
    first: tuple[str, int]
    node: str
    at: datetime
    onboard: tuple[str, int] | None
    alight: tuple[str, int] | None
    modes: tuple[str, ...]
    steps: tuple[TimetableStep, ...]
    boardings: tuple[tuple[str, int, datetime], ...]
    actions: tuple[str, ...]

    @property
    def key(self) -> tuple[object, ...]:
        return self.first, self.onboard, self.alight, self.node, self.modes

    @property
    def order(self) -> tuple[int, tuple[str, ...]]:
        # Wait intervals do not affect this order: an earlier ready state can wait
        # to the same departure without changing the canonical action order.
        return len(self.actions), self.actions


def search_scheduled_chains(
    timetable: Timetable,
    origin_node_ids: tuple[str, ...],
    destination_node_ids: tuple[str, ...],
    requested_at: datetime,
    first_departures_end_at: datetime,
    mode_map: tuple[tuple[str, str], ...],
    *,
    ready_at: tuple[tuple[str, datetime], ...] = (),
    resource_limit: int | None = None,
) -> ScheduledSearchResult:
    """Pareto labels (arrival, canonical action order) at sufficient event states.

    For each first boarding and exact mode set, earliest final arrival maximizes
    every fixed-reference passenger score. Pareto order also preserves final
    tie-breaking when an earlier ready label has a less desirable prefix.
    All continuations within scope.end_at are explored, not just the first target.
    """
    check_limit(resource_limit)
    requested, end = utc(requested_at), utc(first_departures_end_at)
    if requested >= end or requested < timetable.scope.start_at:
        raise ValueError("INVALID_FIRST_DEPARTURE_WINDOW")
    if (
        not origin_node_ids
        or not destination_node_ids
        or (set(origin_node_ids) | set(destination_node_ids)) - timetable.scope.node_ids
        or set(origin_node_ids) & set(destination_node_ids)
    ):
        raise ValueError("INVALID_SCHEDULED_BOUNDARIES")
    modes = dict(mode_map)
    if len(modes) != len(mode_map) or any(not a or not b for a, b in mode_map):
        raise ValueError("INVALID_SCHEDULED_MODE_MAPPING")
    readiness = {node: utc(at) for node, at in ready_at}
    if len(readiness) != len(ready_at) or set(readiness) - set(origin_node_ids):
        raise ValueError("INVALID_BOARDING_READINESS")
    reasons = data_reasons(timetable, True)
    if timetable.scope.end_at < end:
        reasons.add("FIRST_DEPARTURE_WINDOW_UNCOVERED")
    if any(run.transport_type not in modes for run in timetable.runs):
        reasons.add("TRANSPORT_TYPE_UNMAPPED")
    runs = {r.run_id: r for r in usable_runs(timetable) if r.transport_type in modes}
    board_at: dict[str, list[tuple[str, int]]] = {}
    for run_id, run in runs.items():
        for i, event in enumerate(run.events[:-1]):
            if event.pickup:
                board_at.setdefault(event.node_id, []).append((run_id, i))
    walks = [p for p in timetable.footpaths if p.usable]
    labels: dict[tuple[object, ...], list[_State]] = {}
    queue: list[tuple[datetime, tuple[int, tuple[str, ...]], int, _State]] = []
    serial = 0
    finals: dict[tuple[tuple[str, int], tuple[str, ...]], ScheduledChain] = {}

    def add(state: _State) -> None:
        nonlocal serial
        if state.at > timetable.scope.end_at:
            reasons.add("CONTINUATION_OUTSIDE_HORIZON")
            return
        bucket = labels.setdefault(state.key, [])
        if any(old.at <= state.at and old.order <= state.order for old in bucket):
            return
        bucket[:] = [old for old in bucket if not (state.at <= old.at and state.order <= old.order)]
        bucket.append(state)
        serial += 1
        heapq.heappush(queue, (state.at, state.order, serial, state))

    def board(state: _State | None, run_id: str, index: int, extra: tuple[str, ...] = ()) -> None:
        run = runs[run_id]
        event, following = run.events[index : index + 2]
        at = state.at if state is not None else requested
        if event.departure_at < at:
            return
        steps = state.steps if state is not None else ()
        if at < event.departure_at:
            steps += (
                TimetableStep(
                    "wait_transfer" if state else "wait_initial",
                    event.node_id,
                    event.node_id,
                    at,
                    event.departure_at,
                    run.transport_type,
                    run_id,
                    refs(event.evidence_refs + extra),
                ),
            )
        steps += (
            TimetableStep(
                "ride",
                event.node_id,
                following.node_id,
                event.departure_at,
                following.arrival_at,
                run.transport_type,
                run_id,
                refs(
                    event.evidence_refs
                    + following.evidence_refs
                    + run.calendar_evidence_refs
                    + extra
                ),
            ),
        )
        first = state.first if state else (run_id, event.sequence)
        history = state.boardings if state else ()
        used = tuple(sorted(set(state.modes if state else ()) | {modes[run.transport_type]}))
        actions = (state.actions if state else ()) + (f"board:{run_id}:{event.sequence}",)
        add(
            _State(
                first,
                following.node_id,
                following.arrival_at,
                (run_id, index + 1),
                None,
                used,
                steps,
                history + ((run_id, event.sequence, event.departure_at),),
                actions,
            )
        )

    for run_id, run in sorted(runs.items()):
        for i, event in enumerate(run.events[:-1]):
            if (
                event.node_id in origin_node_ids
                and event.pickup
                and requested <= event.departure_at < end
                and event.departure_at >= readiness.get(event.node_id, requested)
            ):
                board(None, run_id, i)
    expanded = 0
    interrupted = False
    while queue:
        _, _, _, state = heapq.heappop(queue)
        if state not in labels[state.key]:
            continue
        if resource_limit is not None and expanded >= resource_limit:
            interrupted = True
            reasons.add("RESOURCE_LIMIT_REACHED")
            break
        expanded += 1
        if state.onboard is not None:
            run_id, index = state.onboard
            run = runs[run_id]
            event = run.events[index]
            if event.dropoff:
                off = _State(
                    state.first,
                    state.node,
                    state.at,
                    None,
                    (run_id, index),
                    state.modes,
                    state.steps,
                    state.boardings,
                    state.actions + (f"alight:{run_id}:{event.sequence}",),
                )
                add(off)
                if state.node in destination_node_ids:
                    first_node = state.steps[0].from_node_id
                    candidate = ScheduledChain(
                        TimetablePath(
                            first_node,
                            state.node,
                            requested,
                            state.at,
                            state.steps,
                            state.boardings,
                        ),
                        state.modes,
                        off.actions,
                    )
                    key = state.first, state.modes
                    old = finals.get(key)
                    if old is None or (
                        candidate.path.arrival_at,
                        len(candidate.action_ids),
                        candidate.action_ids,
                    ) < (old.path.arrival_at, len(old.action_ids), old.action_ids):
                        finals[key] = candidate
            if index + 1 < len(run.events):
                following = run.events[index + 1]
                step = TimetableStep(
                    "ride",
                    event.node_id,
                    following.node_id,
                    state.at,
                    following.arrival_at,
                    run.transport_type,
                    run_id,
                    refs(
                        event.evidence_refs + following.evidence_refs + run.calendar_evidence_refs
                    ),
                )
                add(
                    _State(
                        state.first,
                        following.node_id,
                        following.arrival_at,
                        (run_id, index + 1),
                        None,
                        state.modes,
                        state.steps + (step,),
                        state.boardings,
                        state.actions + (f"stay:{run_id}:{event.sequence}",),
                    )
                )
            continue
        for walk in walks:
            if walk.from_node_id != state.node:
                continue
            assert walk.seconds is not None
            try:
                arrival = state.at + duration_delta(walk.seconds)
            except OverflowError:
                reasons.add("CONTINUATION_OUTSIDE_HORIZON")
                continue
            step = TimetableStep(
                "walk",
                state.node,
                walk.to_node_id,
                state.at,
                arrival,
                "walk",
                None,
                walk.evidence_refs,
                walk.seconds,
                walk.path_id,
            )
            add(
                _State(
                    state.first,
                    walk.to_node_id,
                    arrival,
                    None,
                    state.alight,
                    state.modes,
                    state.steps + (step,),
                    state.boardings,
                    state.actions + (f"walk:{walk.path_id}",),
                )
            )
        assert state.alight is not None
        prior_id, prior_index = state.alight
        for run_id, index in board_at.get(state.node, ()):
            if (prior_id, prior_index) == (run_id, index):
                continue
            run, prior = runs[run_id], runs[prior_id]
            departure = run.events[index].departure_at
            if departure < state.at:
                continue
            rule = transfer_rule(timetable, prior, prior_index, run, index)
            if (
                rule is None
                or rule.allowed is None
                or not rule.evidence_refs
                or (rule.allowed and rule.minimum_seconds is None)
            ):
                reasons.add("TRANSFER_RULE_UNAVAILABLE")
                continue
            if rule.allowed is False:
                continue
            assert rule.minimum_seconds is not None
            available = microseconds(departure - prior.events[prior_index].arrival_at)
            if Decimal(available) < Decimal(str(rule.minimum_seconds)) * 1000000:
                continue
            board(state, run_id, index, rule.evidence_refs)
    evidence = set(timetable.scope.evidence_refs)
    for run in timetable.runs:
        evidence.update(run.calendar_evidence_refs)
        for event in run.events:
            evidence.update(event.evidence_refs)
    for path in timetable.footpaths:
        evidence.update(path.evidence_refs)
    for rule in timetable.transfers:
        evidence.update(rule.evidence_refs)
    return ScheduledSearchResult(
        timetable,
        tuple(sorted(finals.values(), key=lambda c: c.chain_id)),
        tuple(sorted(reasons)),
        interrupted,
        expanded,
        tuple(sorted(evidence)),
    )
